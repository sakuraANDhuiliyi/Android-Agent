"""Durable delivery receipts and a FIFO follow-up dispatcher."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from agent.conversation_events import ConversationEventStore
from agent.permissions import VALID_PROFILES
from agent.project_lifecycle import project_operation
from agent.task_settings import inherited_execution_context, model_selection, resolve_task_settings

logger = logging.getLogger(__name__)


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


def _follow_up_gate(conn, message, parent) -> tuple[str, str | None]:
    reason = _blocked(parent)
    if reason:
        return "blocked", reason
    if parent["status"] != "succeeded":
        return "pending", "parent_paused" if parent["status"] == "paused" else "awaiting_parent_completion"
    # Only dispatch the first outstanding instruction queued on this source task.
    # The previous child must have succeeded, not merely have been enqueued.
    prior = conn.execute("""SELECT m.* FROM task_messages m JOIN tasks t ON t.id=m.task_id
        WHERE m.task_id=? AND m.type='follow_up' AND m.id<? ORDER BY m.id""",
        (parent["id"], message["id"])).fetchall()
    for previous in prior:
        previous_parent = _parent(conn, previous["task_id"], parent["user_id"])
        child = _link(conn, previous["id"], previous_parent)
        if child:
            reason = _blocked(child)
            if reason:
                return "blocked", reason
            if child["status"] != "succeeded":
                return "pending", "awaiting_dispatch"
        elif previous["consumed_at"] is not None:
            return "blocked", "legacy_missing_receipt"
        else:
            return ("blocked", _blocked(previous_parent)) if _blocked(previous_parent) else ("pending", "awaiting_dispatch")
    return "pending", "awaiting_dispatch"


def message_receipt(store, message: dict, user_id: str, *, _conn=None) -> dict:
    from contextlib import nullcontext
    result = {key: message.get(key) for key in ("id", "task_id", "message_key", "type", "payload", "created_at", "consumed_at")}
    result.update(schema_version=1, delivery_state="unknown", context_message_id=None,
                  follow_up_job_id=None, follow_up_turn_id=None, reason="legacy_missing_receipt")
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
            child = _link(conn, message["id"], parent)
            if child:
                result.update(delivery_state="follow_up_created", follow_up_job_id=child["task_id"], follow_up_turn_id=child["turn_id"], reason=None)
            elif message.get("consumed_at") is None:
                state, reason = _follow_up_gate(conn, message, parent)
                result.update(delivery_state=state, reason=reason)
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
        parent = _parent(conn, row["task_id"], row["user_id"])
        if not conn.execute("SELECT 1 FROM conversations WHERE id=? AND user_id=? AND project_id=?",
                            (parent["conversation_id"], parent["user_id"], parent["project_id"])).fetchone():
            raise ValueError("原任务会话不存在或不属于此项目")
        child = _link(conn, message_id, parent)
        if child:
            return child["task_id"]
        if row["consumed_at"] is not None or parent["status"] != "succeeded" or _blocked(parent):
            return None
        state, reason = _follow_up_gate(conn, row, parent)
        if state != "pending" or reason != "awaiting_dispatch":
            return None
        # A preceding pending or active follow-up returns the same display
        # reason; require all preceding instructions to have succeeded here.
        prior = conn.execute("""SELECT m.id FROM task_messages m JOIN tasks t ON t.id=m.task_id
            WHERE m.task_id=? AND m.type='follow_up' AND m.id<?""",
            (parent["id"], message_id)).fetchall()
        if any(not (link := _link(conn, previous["id"], parent)) or link["status"] != "succeeded" or _blocked(link) for previous in prior):
            return None
        payload = json.loads(row["payload"])
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
            {"message_id": identity, "content": [{"type": "text", "text": prompt}], "source": "user", "task_message_id": message_id},
            task_id=job_id, role="user", context_visible=True, created_at=now, _conn=conn)
        conn.execute("INSERT INTO task_message_followups VALUES(?,?,?)", (message_id, job_id, turn_id))
        conn.execute("UPDATE task_messages SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, message_id))
        return job_id


def dispatch_follow_ups(store, settings, *, task_id: str | None = None) -> None:
    with store._connect() as conn:
        clause = " AND m.task_id=?" if task_id else ""
        rows = conn.execute("""SELECT m.id FROM task_messages m JOIN tasks t ON t.id=m.task_id
            WHERE m.type='follow_up' AND m.consumed_at IS NULL AND t.status='succeeded' AND t.cancel_requested=0
            AND NOT EXISTS (SELECT 1 FROM task_messages earlier
                LEFT JOIN task_message_followups f ON f.message_id=earlier.id LEFT JOIN tasks child ON child.id=f.task_id
                WHERE earlier.task_id=m.task_id AND earlier.type='follow_up' AND earlier.id<m.id
                AND (f.message_id IS NULL OR child.status!='succeeded' OR child.cancel_requested=1))""" + clause + " ORDER BY m.id LIMIT 100",
            (task_id,) if task_id else ()).fetchall()
    for row in rows:
        try:
            enqueue_follow_up(store, row["id"], settings)
        except Exception:
            logger.exception("Follow-up dispatch failed for message %s", row["id"])
