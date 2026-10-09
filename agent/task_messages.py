"""Durable delivery receipts and a FIFO follow-up dispatcher."""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent.conversation_events import ConversationEventStore
from agent.database import TaskMessageConflict
from agent.permissions import VALID_PROFILES
from agent.project_lifecycle import project_operation
from agent.redaction import redact_sensitive_value
from agent.task_settings import inherited_execution_context, model_selection, resolve_task_settings

logger = logging.getLogger(__name__)


def _latest_revision(conn, message_id: int):
    return conn.execute("SELECT * FROM task_message_revisions WHERE message_id=? ORDER BY revision DESC LIMIT 1",
                        (message_id,)).fetchone()


def _editable_source(conn, parent) -> bool:
    if parent['status'] not in {'queued', 'running', 'awaiting_approval', 'paused', 'succeeded'}:
        return False
    return conn.execute("""SELECT 1 FROM conversation_turns r JOIN conversations c ON c.id=r.conversation_id
        WHERE r.task_id=? AND r.user_id=? AND r.project_id=? AND r.conversation_id=?
          AND c.user_id=r.user_id AND c.project_id=r.project_id""",
        (parent['id'], parent['user_id'], parent['project_id'], parent['conversation_id'])).fetchone() is not None


def _parent(conn, task_id: str, user_id: str):
    return conn.execute("""SELECT t.*, r.status AS turn_status FROM tasks t
        LEFT JOIN conversation_turns r ON r.task_id=t.id AND r.user_id=t.user_id
        WHERE t.id=? AND t.user_id=?""", (task_id, user_id)).fetchone()


def _blocked(parent) -> str | None:
    if parent["turn_status"] == "interrupted" or parent["status"] == "interrupted":
        return "parent_interrupted"
    if parent["status"] == "canceled" or parent["cancel_requested"]:
        return "parent_canceled"
    if parent["status"] == "failed":
        return "parent_failed"
    return None


def _link(conn, message_id: int, parent):
    return conn.execute("""SELECT f.task_id, f.turn_id, t.status, t.cancel_requested, r.status AS turn_status
        FROM task_message_followups f JOIN tasks t ON t.id=f.task_id
        JOIN conversation_turns r ON r.id=f.turn_id AND r.task_id=t.id
        WHERE f.message_id=? AND t.user_id=? AND t.project_id=? AND t.conversation_id=?
          AND r.user_id=t.user_id AND r.conversation_id=t.conversation_id""",
        (message_id, parent["user_id"], parent["project_id"], parent["conversation_id"])).fetchone()


@dataclass(frozen=True)
class _FollowUpGate:
    state: str
    reason: str | None
    blocking_job_id: str | None = None
    blocking_turn_id: str | None = None


def _blocked_gate(conn, parent, reason: str, task_id: str, turn_id: str | None = None) -> _FollowUpGate:
    """Expose a blocker only when both source and target have complete scope.

    This lookup does not decide whether the queue may run. Missing or corrupt
    historical links retain the gate's decision without supplying navigation.
    """
    row = conn.execute("""SELECT b.id AS task_id, r.id AS turn_id
        FROM tasks s JOIN conversations c ON c.id=s.conversation_id
          AND c.user_id=s.user_id AND c.project_id=s.project_id
        JOIN conversation_turns source_turn ON source_turn.task_id=s.id
          AND source_turn.user_id=s.user_id AND source_turn.project_id=s.project_id
          AND source_turn.conversation_id=s.conversation_id
        JOIN tasks b ON b.id=? AND b.user_id=s.user_id AND b.project_id=s.project_id
          AND b.conversation_id=s.conversation_id
        JOIN conversation_turns r ON r.task_id=b.id AND r.user_id=b.user_id
          AND r.project_id=b.project_id AND r.conversation_id=b.conversation_id
        WHERE s.id=? AND s.user_id=? AND s.project_id=? AND s.conversation_id=?
          AND b.id!='' AND r.id!='' AND (? IS NULL OR r.id=?)""",
        (task_id, parent['id'], parent['user_id'], parent['project_id'], parent['conversation_id'], turn_id, turn_id)).fetchone()
    return _FollowUpGate('blocked', reason, row['task_id'] if row else None, row['turn_id'] if row else None)


def _follow_up_gate(conn, message, parent) -> _FollowUpGate:
    reason = _blocked(parent)
    if reason:
        return _blocked_gate(conn, parent, reason, parent['id'])
    if parent["status"] != "succeeded":
        return _FollowUpGate("pending", "parent_paused" if parent["status"] == "paused" else "awaiting_parent_completion")
    # Only dispatch the first outstanding instruction queued on this source task.
    # The previous child must have succeeded, not merely have been enqueued.
    prior = conn.execute("""SELECT m.* FROM task_messages m JOIN tasks t ON t.id=m.task_id
        WHERE m.task_id=? AND m.type='follow_up' AND m.id<?
          AND NOT EXISTS (SELECT 1 FROM task_message_withdrawals w WHERE w.message_id=m.id) ORDER BY m.id""",
        (parent["id"], message["id"])).fetchall()
    for previous in prior:
        previous_parent = _parent(conn, previous["task_id"], parent["user_id"])
        child = _link(conn, previous["id"], previous_parent)
        if child:
            reason = _blocked(child)
            if reason:
                return _blocked_gate(conn, parent, reason, child['task_id'], child['turn_id'])
            if child["status"] != "succeeded":
                return _FollowUpGate("pending", "awaiting_dispatch")
        elif previous["consumed_at"] is not None or conn.execute(
            "SELECT 1 FROM task_message_followups WHERE message_id=?", (previous['id'],)
        ).fetchone():
            return _FollowUpGate("blocked", "legacy_missing_receipt")
        else:
            reason = _blocked(previous_parent)
            return _blocked_gate(conn, parent, reason, previous_parent['id']) if reason else _FollowUpGate("pending", "awaiting_dispatch")
    return _FollowUpGate("pending", "awaiting_dispatch")


def message_receipt(store, message: dict, user_id: str, *, _conn=None) -> dict:
    from contextlib import nullcontext
    result = {key: message.get(key) for key in ("id", "task_id", "message_key", "type", "payload", "created_at", "consumed_at")}
    result.update(schema_version=1, delivery_state="unknown", context_message_id=None,
                  follow_up_job_id=None, follow_up_turn_id=None, reason="legacy_missing_receipt",
                  withdrawn_at=None, can_withdraw=False, revision=0, edited_at=None, can_edit=False)
    result.update(blocking_job_id=None, blocking_turn_id=None)
    with nullcontext(_conn) if _conn is not None else store._connect() as conn:
        if _conn is None:
            conn.execute("BEGIN")
        fresh = conn.execute("SELECT * FROM task_messages WHERE id=? AND task_id=?", (message["id"], message["task_id"])).fetchone()
        if fresh:
            message = store._row_to_message(fresh)
            result.update({key: message.get(key) for key in ("payload", "consumed_at")})
        parent = _parent(conn, message["task_id"], user_id)
        if parent is None:
            return result
        if message["type"] == "follow_up":
            latest = _latest_revision(conn, message['id'])
            if latest:
                if latest['user_id'] != user_id:
                    return result
                result.update(payload=json.loads(latest['payload']), revision=latest['revision'], edited_at=latest['created_at'])
            raw_link = conn.execute("SELECT 1 FROM task_message_followups WHERE message_id=?", (message['id'],)).fetchone()
            withdrawal = conn.execute("SELECT * FROM task_message_withdrawals WHERE message_id=?", (message['id'],)).fetchone()
            if withdrawal:
                if not raw_link and message.get("consumed_at") is None and withdrawal['user_id'] == user_id:
                    result.update(delivery_state="withdrawn", withdrawn_at=withdrawal['created_at'], reason=None)
                return result
            child = _link(conn, message["id"], parent)
            if child and message.get("consumed_at") is not None:
                result.update(delivery_state="follow_up_created", follow_up_job_id=child["task_id"], follow_up_turn_id=child["turn_id"], reason=None)
            elif not raw_link and message.get("consumed_at") is None:
                gate = _follow_up_gate(conn, message, parent)
                result.update(delivery_state=gate.state, reason=gate.reason, can_withdraw=True,
                              can_edit=gate.state == 'pending' and _editable_source(conn, parent),
                              blocking_job_id=gate.blocking_job_id, blocking_turn_id=gate.blocking_turn_id)
        elif message["type"] == "steer":
            event = conn.execute("""SELECT e.payload_json FROM conversation_events e JOIN conversation_turns r ON r.id=e.turn_id
                WHERE e.event_key=? AND r.task_id=? AND r.user_id=? AND e.role='user' AND e.context_visible=1""",
                (f"steer:{message['task_id']}:{message['id']}", message["task_id"], user_id)).fetchone()
            identity = json.loads(event[0]).get("message_id") if event else None
            if message.get("consumed_at") is not None and identity:
                result.update(delivery_state="consumed", context_message_id=identity, reason=None)
            elif message.get("consumed_at") is None:
                reason = _blocked(parent)
                if reason or parent["status"] == "succeeded":
                    result.update(delivery_state="unapplied", reason=reason or "turn_finished_before_consumption")
                else:
                    result.update(delivery_state="pending", reason="parent_paused" if parent["status"] == "paused" else "awaiting_safe_boundary")
    return result


def _edit_receipt(row, task_id: str) -> dict:
    return dict(schema_version=1, task_id=task_id, message_id=row['message_id'], edit_key=row['edit_key'],
                expected_revision=row['revision'] - 1, revision=row['revision'],
                created_at=row['created_at'], payload=json.loads(row['payload']))


def edit_follow_up(store, task_id: str, message_id: int, user_id: str, *, edit_key: str,
                   expected_revision: int, payload: dict) -> tuple[dict, dict, bool] | None:
    """Append a version without changing original send identity or FIFO position."""
    if message_id <= 0 or message_id > 2**63 - 1:
        return None
    if (not isinstance(edit_key, str) or not edit_key.strip() or len(edit_key) > 200
            or type(expected_revision) is not int or not 0 <= expected_revision < 2**63 - 1
            or not isinstance(payload, dict) or set(payload) != {'text'}
            or not isinstance(payload['text'], str) or not payload['text'].strip() or len(payload['text']) > 100_000):
        raise ValueError('编辑必须包含稳定标识、有效版本和非空文本')
    fingerprint = store._message_hash('follow_up_edit', {'expected_revision': expected_revision, 'payload': payload})
    with store._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("""SELECT m.* FROM task_messages m JOIN tasks t ON t.id=m.task_id
            WHERE m.id=? AND m.task_id=? AND t.user_id=?""", (message_id, task_id, user_id)).fetchone()
        if row is None:
            return None
        existing = conn.execute('SELECT * FROM task_message_revisions WHERE message_id=? AND edit_key=?',
                                (message_id, edit_key)).fetchone()
        # A lost acknowledgement remains resolvable after dispatch, withdrawal,
        # or another edit. Eligibility only applies to a new operation.
        if existing:
            if existing['user_id'] != user_id or existing['request_hash'] != fingerprint:
                raise TaskMessageConflict('同一 edit_key 已用于不同编辑')
            return (message_receipt(store, store._row_to_message(row), user_id, _conn=conn),
                    _edit_receipt(existing, task_id), False)
        receipt = message_receipt(store, store._row_to_message(row), user_id, _conn=conn)
        if not receipt['can_edit']:
            raise TaskMessageConflict('追问已创建后续任务、撤回或被阻塞，无法编辑')
        if receipt['revision'] != expected_revision:
            raise TaskMessageConflict('追问已被更新，请重新核对当前版本')
        conn.execute('''INSERT INTO task_message_revisions(message_id,revision,edit_key,request_hash,payload,user_id,created_at)
            VALUES(?,?,?,?,?,?,?)''', (message_id, expected_revision + 1, edit_key, fingerprint,
                json.dumps(redact_sensitive_value(payload), ensure_ascii=False), user_id, time.time()))
        accepted = _latest_revision(conn, message_id)
        return (message_receipt(store, store._row_to_message(row), user_id, _conn=conn),
                _edit_receipt(accepted, task_id), True)


def withdraw_follow_up(store, task_id: str, message_id: int, user_id: str) -> dict | None:
    """Persist one immutable withdrawal, serialized with child creation."""
    if message_id <= 0 or message_id > 2**63 - 1:
        return None
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT m.* FROM task_messages m JOIN tasks t ON t.id=m.task_id
            WHERE m.id=? AND m.task_id=? AND t.user_id=?""", (message_id, task_id, user_id)).fetchone()
        if row is None:
            return None
        # Raw identity is authoritative even if a corrupt child cannot safely
        # be projected into this user's receipt. Never undo an existing task.
        raw_link = conn.execute("SELECT 1 FROM task_message_followups WHERE message_id=?", (message_id,)).fetchone()
        if row['type'] != 'follow_up' or row['consumed_at'] is not None or raw_link:
            raise TaskMessageConflict("追问已创建后续任务或缺少可靠关联，无法撤回")
        if not conn.execute("SELECT 1 FROM task_message_withdrawals WHERE message_id=?", (message_id,)).fetchone():
            conn.execute("INSERT INTO task_message_withdrawals(message_id,user_id,created_at) VALUES(?,?,?)",
                         (message_id, user_id, time.time()))
        return message_receipt(store, store._row_to_message(row), user_id, _conn=conn)


def enqueue_follow_up(store, message_id: int, settings) -> str | None:
    """Create the child and receipt together; retries and other workers reuse it."""
    with store._connect() as conn:
        source = conn.execute("SELECT t.user_id,t.project_id FROM task_messages m JOIN tasks t ON t.id=m.task_id WHERE m.id=?", (message_id,)).fetchone()
    if source is None:
        return None
    # Match explicit task creation's lock order: lifecycle lock before DB.
    with project_operation(source["user_id"], source["project_id"]):
        from agent.jobs import load_project_meta
        load_project_meta(source["user_id"], source["project_id"])
        return _enqueue_follow_up_locked(store, message_id, settings)


def _enqueue_follow_up_locked(store, message_id: int, settings) -> str | None:
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT m.*,t.user_id FROM task_messages m JOIN tasks t ON t.id=m.task_id WHERE m.id=? AND m.type='follow_up'", (message_id,)).fetchone()
        if not row:
            return None
        if conn.execute("SELECT 1 FROM task_message_withdrawals WHERE message_id=?", (message_id,)).fetchone():
            return None
        parent = _parent(conn, row["task_id"], row["user_id"])
        if not conn.execute("SELECT 1 FROM conversations WHERE id=? AND user_id=? AND project_id=?",
                            (parent["conversation_id"], parent["user_id"], parent["project_id"])).fetchone():
            raise ValueError("原任务会话不存在或不属于此项目")
        child = _link(conn, message_id, parent)
        if child:
            return child["task_id"]
        if conn.execute("SELECT 1 FROM task_message_followups WHERE message_id=?", (message_id,)).fetchone():
            return None
        if row["consumed_at"] is not None or parent["status"] != "succeeded" or _blocked(parent):
            return None
        gate = _follow_up_gate(conn, row, parent)
        if gate.state != "pending" or gate.reason != "awaiting_dispatch":
            return None
        # A preceding pending or active follow-up returns the same display
        # reason; require all preceding instructions to have succeeded here.
        prior = conn.execute("""SELECT m.id FROM task_messages m JOIN tasks t ON t.id=m.task_id
            WHERE m.task_id=? AND m.type='follow_up' AND m.id<?
              AND NOT EXISTS (SELECT 1 FROM task_message_withdrawals w WHERE w.message_id=m.id)""",
            (parent["id"], message_id)).fetchall()
        if any(not (link := _link(conn, previous["id"], parent)) or link["status"] != "succeeded" or _blocked(link) for previous in prior):
            return None
        latest = _latest_revision(conn, message_id)
        if latest and latest['user_id'] != row['user_id']:
            raise ValueError('追问版本不属于此用户')
        payload = json.loads(latest['payload'] if latest else row["payload"])
        revision = latest['revision'] if latest else 0
        prompt = payload.get("text") or payload.get("prompt") or payload.get("content")
        if not isinstance(prompt, str) or not prompt.strip():
            conn.execute("UPDATE task_messages SET consumed_at=? WHERE id=?", (time.time(), message_id))
            return None
        task = store._row_to_task(parent)
        selected = resolve_task_settings(settings, task)
        inherited = inherited_execution_context(task.get("context") or {})
        mode = inherited.get("run_mode", "workspace")
        profile = inherited.get("permission_profile")
        if mode not in {"read_only", "workspace", "ask"} or (profile is not None and profile not in VALID_PROFILES):
            raise ValueError("原任务权限无效，拒绝创建追问")
        now = time.time()
        job_id, turn_id = uuid.uuid4().hex[:12], uuid.uuid4().hex
        write_lock = f"main:{parent['user_id']}:{parent['project_id']}"
        store.create_task({"id": job_id, "user_id": parent["user_id"], "project_id": parent["project_id"],
            "conversation_id": parent["conversation_id"], "prompt": prompt, "status": "queued",
            "provider": selected.provider, "model": selected.model, "created_at": now, "write_lock_key": write_lock,
            "context": {**inherited, "run_mode": mode, "permission_profile": profile,
                "model_selection": model_selection(selected), "write_lock_key": write_lock}}, _conn=conn)
        events = ConversationEventStore(store)
        events.create_turn(parent["conversation_id"], parent["user_id"], parent["project_id"], task_id=job_id,
            turn_id=turn_id, status="queued", provider=selected.provider, model=selected.model, created_at=now, _conn=conn)
        identity = f"follow-up:{message_id}"
        events.append_event_idempotent(parent["conversation_id"], turn_id, "user_message", f"turn:{turn_id}:user_message",
            {"message_id": identity, "content": [{"type": "text", "text": prompt}], "source": "user",
             "task_message_id": message_id, "task_message_revision": revision},
            task_id=job_id, role="user", context_visible=True, created_at=now, _conn=conn)
        conn.execute("INSERT INTO task_message_followups VALUES(?,?,?)", (message_id, job_id, turn_id))
        conn.execute("UPDATE task_messages SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, message_id))
        return job_id


def dispatch_follow_ups(store, settings, *, task_id: str | None = None) -> None:
    with store._connect() as conn:
        clause = " AND m.task_id=?" if task_id else ""
        rows = conn.execute("""SELECT m.id FROM task_messages m JOIN tasks t ON t.id=m.task_id
            WHERE m.type='follow_up' AND m.consumed_at IS NULL AND t.status='succeeded' AND t.cancel_requested=0
            AND NOT EXISTS (SELECT 1 FROM task_message_withdrawals w WHERE w.message_id=m.id)
            AND NOT EXISTS (SELECT 1 FROM task_message_followups f WHERE f.message_id=m.id)
            AND NOT EXISTS (SELECT 1 FROM task_messages earlier
                LEFT JOIN task_message_followups f ON f.message_id=earlier.id LEFT JOIN tasks child ON child.id=f.task_id
                WHERE earlier.task_id=m.task_id AND earlier.type='follow_up' AND earlier.id<m.id
                AND NOT EXISTS (SELECT 1 FROM task_message_withdrawals w WHERE w.message_id=earlier.id)
                AND (f.message_id IS NULL OR child.status!='succeeded' OR child.cancel_requested=1))""" + clause + " ORDER BY m.id LIMIT 100",
            (task_id,) if task_id else ()).fetchall()
    for row in rows:
        try:
            enqueue_follow_up(store, row["id"], settings)
        except Exception:
            logger.exception("Follow-up dispatch failed for message %s", row["id"])
