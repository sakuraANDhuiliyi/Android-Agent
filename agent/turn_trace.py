from __future__ import annotations

"""Turn trace: per-turn observability across the full request lifecycle.

Every turn carries a trace_id (created with the turn) that is stamped onto
conversation events, task events and live WebSocket payloads. This module
rebuilds an ordered step timeline for a single turn so "why did this message
duplicate / why is the run stuck / why is approval out of sync" can be
answered from one place.
"""

from typing import Any

from agent.conversation_events import (
    ConversationEventStore,
    ConversationEventType as EventType,
)

TURN_TRACE_SCHEMA_VERSION = 1

_STEP_LABELS: dict[str, str] = {
    EventType.USER_MESSAGE: "用户消息",
    EventType.TURN_STARTED: "Worker 启动",
    EventType.TOOL_CALL: "工具调用",
    EventType.TOOL_RESULT: "工具结果",
    EventType.MALFORMED_TOOL_CALL: "畸形工具调用",
    EventType.APPROVAL_REQUIRED: "审批请求",
    EventType.APPROVAL_RESOLVED: "审批决议",
    EventType.ASSISTANT_MESSAGE: "模型输出",
    EventType.BUILD_SUMMARY: "构建摘要",
    EventType.TEST_SUMMARY: "测试摘要",
    EventType.CHANGES: "文件改动",
    EventType.ARTIFACT: "产物",
    EventType.USAGE: "用量",
    EventType.PROVIDER_SWITCH: "Provider 切换",
    EventType.MODEL_SWITCH: "模型切换",
    EventType.CONTEXT_CHECKPOINT: "上下文检查点",
    EventType.SYSTEM_NOTE: "系统备注",
    EventType.RECOVERY_NOTE: "恢复备注",
    EventType.TURN_COMPLETED: "Turn 完成",
    EventType.TURN_FAILED: "Turn 失败",
    EventType.TURN_CANCELED: "Turn 取消",
    EventType.TURN_INTERRUPTED: "Turn 中断",
    EventType.LIFECYCLE_RECONCILED: "生命周期对账",
}


def _content_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    text = payload.get("text")
    return text if isinstance(text, str) else ""


def _step_detail(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == EventType.USER_MESSAGE:
        text = _content_text(payload).strip().replace("\n", " ")
        return text[:120] or "（空）"
    if event_type == EventType.TOOL_CALL:
        return str(payload.get("name") or "未知工具")
    if event_type in {EventType.TOOL_RESULT, EventType.MALFORMED_TOOL_CALL}:
        ok = payload.get("ok")
        status = "OK" if ok else "FAIL"
        duration = payload.get("duration_ms")
        name = payload.get("name") or ""
        suffix = f" · {duration}ms" if isinstance(duration, (int, float)) else ""
        return f"{name} {status}{suffix}".strip()
    if event_type == EventType.ASSISTANT_MESSAGE:
        return "最终回答" if payload.get("is_final") else "流式输出"
    if event_type == EventType.APPROVAL_REQUIRED:
        kind = payload.get("kind") or payload.get("approval_kind") or "操作"
        return f"{kind}: {payload.get('tool_name') or payload.get('name') or ''}".strip(" :")
    if event_type == EventType.APPROVAL_RESOLVED:
        return "已通过" if payload.get("approved") else "已拒绝"
    if event_type in {EventType.BUILD_SUMMARY, EventType.TEST_SUMMARY}:
        task = payload.get("task") or ""
        success = payload.get("success")
        outcome = "成功" if success else "失败"
        duration = payload.get("duration_ms")
        tests = payload.get("tests") or {}
        extra = ""
        if isinstance(tests, dict) and tests.get("failed") is not None:
            extra = f" · {tests.get('passed')}/{(tests.get('passed') or 0) + (tests.get('failed') or 0)} 通过"
        return f"{task} {outcome} · {duration}ms{extra}".strip()
    if event_type == EventType.CHANGES:
        files = payload.get("files")
        count = len(files) if isinstance(files, list) else 0
        additions = payload.get("additions")
        deletions = payload.get("deletions")
        if additions is not None and deletions is not None:
            return f"{count} 个文件 · +{additions}/-{deletions}"
        return f"{count} 个文件"
    if event_type == EventType.ARTIFACT:
        size = payload.get("size_bytes")
        size_text = f" · {size / (1024 * 1024):.1f}MB" if isinstance(size, (int, float)) else ""
        return f"{payload.get('kind', 'artifact')}{size_text}"
    if event_type == EventType.USAGE:
        usage = payload.get("usage") or {}
        if isinstance(usage, dict):
            return f"in {usage.get('input_tokens')} / out {usage.get('output_tokens')}"
        return ""
    return str(payload.get("message") or "")[:120]


def build_turn_trace(
    event_store: ConversationEventStore,
    *,
    user_id: str,
    conversation_id: str,
    turn_id: str,
) -> dict[str, Any] | None:
    """Rebuild the ordered trace of one turn. Returns None when the turn
    does not exist or belongs to another user/conversation."""
    turn = event_store.get_turn(turn_id, user_id=user_id)
    if not turn or turn.get("conversation_id") != conversation_id:
        return None
    events = event_store.list_turn_events(turn_id, user_id=user_id)

    steps: list[dict[str, Any]] = []
    prev_at: float | None = None
    for event in events:
        payload = event.get("payload") or {}
        at = event.get("created_at")
        step: dict[str, Any] = {
            "seq": event.get("seq"),
            "at": at,
            "type": event.get("event_type"),
            "label": _STEP_LABELS.get(event.get("event_type") or "", event.get("event_type")),
            "detail": _step_detail(event.get("event_type") or "", payload),
            "event_id": event.get("id"),
            "tool_call_id": payload.get("tool_call_id"),
            "approval_id": payload.get("approval_id"),
        }
        if at is not None and prev_at is not None:
            step["duration_ms"] = round((at - prev_at) * 1000)
        else:
            step["duration_ms"] = 0
        steps.append(step)
        if at is not None:
            prev_at = at

    created = turn.get("created_at")
    started = turn.get("started_at")
    finished = turn.get("finished_at")
    return {
        "schema_version": TURN_TRACE_SCHEMA_VERSION,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "task_id": turn.get("task_id"),
        "job_id": turn.get("task_id"),
        "trace_id": turn.get("trace_id"),
        "status": turn.get("status"),
        "provider": turn.get("provider"),
        "model": turn.get("model"),
        "created_at": created,
        "started_at": started,
        "finished_at": finished,
        "queue_ms": round((started - created) * 1000) if created and started else None,
        "total_ms": round((finished - created) * 1000) if created and finished else None,
        "steps": steps,
    }
