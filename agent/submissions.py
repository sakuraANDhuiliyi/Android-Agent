"""Durable identities for explicit, non-guest conversation submissions."""
from __future__ import annotations

import hashlib
import json
import math
import re
from contextlib import nullcontext


class SubmissionConflict(RuntimeError):
    pass


class SubmissionNotFound(LookupError, RuntimeError):
    pass


class SubmissionQuotaExceeded(RuntimeError):
    pass


def validate_request_key(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", value) is None:
        raise ValueError("request_key 必须为 1..200 个 ASCII 字母、数字、下划线或短横线，且以字母或数字开头")
    return value


def submission_hash(user_id: str, project_id: str, conversation_id: str, parsed_body: dict) -> str:
    """Hash parsed defaults BEFORE redaction/resolution, never expose the digest."""
    encoded = json.dumps(["conversation_ask_v1", user_id, project_id, conversation_id, parsed_body],
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def lookup_submission(store, user_id: str, conversation_id: str, request_key: str, *,
                      expected_hash: str | None = None, _conn=None) -> dict | None:
    """A GET locates identity only; POST supplies the original-body hash too."""
    validate_request_key(request_key)
    with nullcontext(_conn) if _conn is not None else store._connect() as conn:
        if _conn is None:
            conn.execute("BEGIN")
        conv = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?",
                            (conversation_id, user_id)).fetchone()
        if conv is None:
            raise SubmissionNotFound("对话不存在")
        row = conn.execute("SELECT * FROM task_submissions WHERE user_id=? AND request_key=?",
                           (user_id, request_key)).fetchone()
        if row is None:
            return None
        if expected_hash is not None and row["request_hash"] != expected_hash:
            raise SubmissionConflict("request_key 已用于不同的提交正文或对话")
        def invalid():
            if expected_hash is not None:
                raise SubmissionConflict("已接受提交的任务关联无法验证，拒绝重新创建")
            raise SubmissionNotFound("提交记录不可用")

        if (not isinstance(row["created_at"], (int, float)) or not math.isfinite(row["created_at"])
                or row["created_at"] <= 0 or not isinstance(row["request_hash"], str)
                or re.fullmatch(r"[a-f0-9]{64}", row["request_hash"]) is None):
            invalid()
        try:
            task = store.get_task(row["task_id"], user_id, _conn=conn)
            # Generic task readers intentionally tolerate legacy context JSON.
            # A new identity receipt must not certify a corrupt accepted task.
            raw_task = conn.execute("SELECT context_json FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
            if (raw_task and not isinstance(json.loads(raw_task["context_json"] or "{}"), dict)
                    or task is not None and not isinstance(task.get("changed_files"), list)):
                invalid()
        except (ValueError, TypeError, KeyError):
            invalid()
        turn = conn.execute("SELECT * FROM conversation_turns WHERE id=?", (row["turn_id"],)).fetchone()
        event = conn.execute("""SELECT id,payload_json FROM conversation_events WHERE conversation_id=? AND turn_id=?
            AND task_id=? AND event_type='user_message' AND role='user' AND context_visible=1 AND event_key=?""",
            (conversation_id, row["turn_id"], row["task_id"], f"turn:{row['turn_id']}:user_message")).fetchone()
        valid = (row["conversation_id"] == conversation_id and row["project_id"] == conv["project_id"]
                 and task is not None and turn is not None and event is not None
                 and task["conversation_id"] == conversation_id and task["project_id"] == row["project_id"]
                 and turn["task_id"] == row["task_id"] and turn["user_id"] == user_id
                 and turn["conversation_id"] == conversation_id and turn["project_id"] == row["project_id"])
        if not valid:
            invalid()
        try:
            if not isinstance(json.loads(event["payload_json"]), dict):
                invalid()
            from agent.jobs import job_to_dict
            job = job_to_dict(task, _conn=conn)
            if (job.get("id") != row["task_id"] or job.get("turn_id") != row["turn_id"]
                    or job.get("project_id") != row["project_id"] or job.get("conversation_id") != conversation_id):
                invalid()
        except (ValueError, TypeError, KeyError, AttributeError):
            invalid()
        return {"schema_version": 1, "submission": {
            "schema_version": 1, "request_key": row["request_key"], "project_id": row["project_id"],
            "conversation_id": conversation_id, "job_id": row["task_id"], "turn_id": row["turn_id"],
            "created_at": row["created_at"],
        }, "job": job}
