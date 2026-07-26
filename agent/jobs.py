from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from typing import Any

from agent.approvals import (
    ApprovalEventPersistenceError,
    get_pending_approvals,
    reject_job_approvals,
    resolve_approval,
)
from agent.changes import compare_snapshots, snapshot_workspace
from agent.config import Settings, load_settings
from agent.conversation_events import (
    ConversationEventStore,
    ConversationEventType as EventType,
)
from agent.conversation_summary import create_semantic_checkpoint
from agent.database import TaskStore
from agent.honesty import sanitize_final_answer
from agent.loop import CancellationRequested, dispatch_agent_tool, run_agent
from agent.paths import latest_apk_path, user_builds_dir, workspace_path
from agent.project import load_project_meta
from agent.redaction import redact_sensitive_value
from agent.tools import ToolResult, cancel_gradle


logger = logging.getLogger(__name__)


_store = TaskStore()
_project_locks: set[tuple[str, str]] = set()
_lock = threading.Lock()


def configure_task_store(
    store: TaskStore | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    global _store
    if store is not None:
        _store = store
    recovered = _store.recover_interrupted()
    if settings is not None:
        _schedule_recovery_jobs(recovered, settings)
    return recovered


def get_job(job_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
    return _store.get_task(job_id, user_id)


def list_jobs(
    user_id: str,
    project_id: str | None = None,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    return _store.list_tasks(user_id, project_id, conversation_id)


def job_to_dict(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job)
    result["result"] = result.get("final_message")
    result["error"] = result.get("error_message")
    return dict(redact_sensitive_value(result))


def resolve_job_approval(
    job_id: str,
    approval_id: str,
    user_id: str,
    *,
    approved: bool,
) -> dict[str, Any] | None:
    job = get_job(job_id, user_id=user_id)
    if not job:
        return None
    result = resolve_approval(approval_id, user_id, approved=approved)
    if not result or result.get("job_id") != job_id:
        return None
    return result


def list_job_approvals(job_id: str, user_id: str) -> list[dict[str, Any]] | None:
    job = get_job(job_id, user_id=user_id)
    if not job:
        return None
    return get_pending_approvals(job_id, user_id)


def request_cancel(job_id: str, user_id: str) -> bool:
    job = _store.get_task(job_id, user_id)
    changed = _store.request_cancel(job_id, user_id)
    if changed:
        _store.add_event(job_id, "cancel_requested", {"message": "已请求停止任务"})
        reject_job_approvals(job_id, user_id, reason="canceled")
        if job:
            cancel_gradle(job["user_id"], job["project_id"])
    return changed


# —— Conversations ——

def list_conversations(user_id: str, project_id: str) -> list[dict[str, Any]]:
    load_project_meta(user_id, project_id)
    return _store.list_conversations(user_id, project_id)


def create_conversation(user_id: str, project_id: str, title: str = "新对话") -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    return _store.create_conversation(user_id, project_id, title=title or "新对话")


def get_conversation(conversation_id: str, user_id: str) -> dict[str, Any] | None:
    return _store.get_conversation(conversation_id, user_id)


def list_conversation_events(
    conversation_id: str,
    user_id: str,
    *,
    after_seq: int | None = None,
    limit: int = 200,
    context_only: bool = False,
) -> list[dict[str, Any]] | None:
    event_store = ConversationEventStore(_store)
    if not event_store.has_conversation(conversation_id, user_id):
        return None
    return event_store.list_events(
        conversation_id,
        user_id=user_id,
        after_seq=after_seq,
        limit=limit,
        context_only=context_only,
    )


def update_conversation(conversation_id: str, user_id: str, **values: Any) -> dict[str, Any] | None:
    conv = _store.get_conversation(conversation_id, user_id)
    if not conv:
        return None
    allowed = {}
    if "title" in values and values["title"] is not None:
        allowed["title"] = str(values["title"]).strip()[:80] or conv["title"]
    if "status" in values and values["status"] in {"active", "archived"}:
        allowed["status"] = values["status"]
    return _store.update_conversation(conversation_id, user_id, **allowed)


def delete_conversation(conversation_id: str, user_id: str) -> bool:
    return _store.delete_conversation(conversation_id, user_id)


def clear_project_session(user_id: str, project_id: str) -> None:
    """Compatibility: create a fresh conversation instead of wiping project memory only."""
    load_project_meta(user_id, project_id)
    _store.clear_session(user_id, project_id)
    _store.create_conversation(user_id, project_id, title="新对话")


def get_project_session(user_id: str, project_id: str) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    conv = _store.get_or_create_default_conversation(user_id, project_id)
    return {
        "user_id": user_id,
        "project_id": project_id,
        "conversation_id": conv["id"],
        "turns": conv.get("turns") or [],
        "turn_count": conv.get("turn_count") or 0,
    }


def start_ask_job(
    user_id: str,
    project_id: str,
    prompt: str,
    settings: Settings | None = None,
    *,
    conversation_id: str | None = None,
    continue_session: bool = True,
    reset_session: bool = False,
) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    settings = settings or load_settings()
    key = (user_id, project_id)
    with _lock:
        if key in _project_locks:
            raise RuntimeError("该项目已有任务正在运行")
        _project_locks.add(key)

    task_id = uuid.uuid4().hex[:12]
    turn_id: str | None = None
    task_created = False
    event_store = ConversationEventStore(_store)
    try:
        if conversation_id:
            conv = _store.get_conversation(conversation_id, user_id)
            if not conv or conv["project_id"] != project_id:
                raise RuntimeError("对话不存在或不属于该项目")
        elif reset_session:
            conv = _store.create_conversation(user_id, project_id, title="新对话")
            conversation_id = conv["id"]
        else:
            conv = _store.get_or_create_default_conversation(user_id, project_id)
            conversation_id = conv["id"]

        if continue_session and not reset_session:
            create_semantic_checkpoint(
                event_store,
                conversation_id,
                user_id,
            )

        created_at = time.time()
        _store.create_task({
            "id": task_id,
            "user_id": user_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "prompt": prompt,
            "status": "queued",
            "provider": settings.provider,
            "model": settings.model,
            "created_at": created_at,
        })
        task_created = True
        turn = event_store.create_turn(
            conversation_id,
            user_id,
            project_id,
            task_id=task_id,
            status="queued",
            provider=settings.provider,
            model=settings.model,
            created_at=created_at,
        )
        turn_id = turn["id"]
        message_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"android-agent:turn:{turn_id}:user_message",
        ).hex
        event_store.append_event_idempotent(
            conversation_id,
            turn_id,
            EventType.USER_MESSAGE,
            f"turn:{turn_id}:user_message",
            {
                "message_id": message_id,
                "content": [{"type": "text", "text": prompt}],
                "source": "user",
            },
            task_id=task_id,
            role="user",
            context_visible=True,
            created_at=created_at,
        )
        if conv.get("title") in {"新对话", "默认对话", ""} and prompt.strip():
            _store.update_conversation(
                conversation_id,
                user_id,
                title=prompt.strip()[:40],
            )
        if continue_session and not reset_session:
            history_events = event_store.list_events(
                conversation_id,
                user_id=user_id,
            )
        else:
            history_events = event_store.list_turn_events(
                turn_id,
                user_id=user_id,
            )
        prior_turn_count = len(
            {
                event["turn_id"]
                for event in history_events
                if event["turn_id"] != turn_id
            }
        )
        threading.Thread(
            target=_run_job,
            args=(
                task_id,
                user_id,
                project_id,
                conversation_id,
                turn_id,
                prompt,
                settings,
                history_events,
                prior_turn_count,
                None,
                False,
            ),
            daemon=True,
        ).start()
    except Exception as exc:
        if task_created:
            _store.update_task(
                task_id,
                status="failed",
                finished_at=time.time(),
                error_message=f"任务初始化失败: {exc}",
            )
        if turn_id:
            try:
                event_store.update_turn_status(
                    turn_id,
                    "failed",
                    user_id=user_id,
                    finished_at=time.time(),
                    error_message=str(exc),
                )
            except Exception as status_exc:
                with _lock:
                    _project_locks.discard(key)
                raise RuntimeError(
                    f"任务初始化失败: {exc}; Turn 状态写入失败: {status_exc}"
                ) from exc
        with _lock:
            _project_locks.discard(key)
        raise
    return _store.get_task(task_id, user_id) or {}


def _schedule_recovery_jobs(
    recovered: list[dict[str, Any]],
    settings: Settings,
) -> None:
    for candidate in recovered:
        try:
            start_recovery_job(candidate, settings)
        except Exception:
            logger.exception(
                "Failed to schedule recovery for interrupted task %s",
                candidate.get("original_task_id"),
            )


def start_recovery_job(
    recovery: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    user_id = str(recovery["user_id"])
    project_id = str(recovery["project_id"])
    conversation_id = str(recovery["conversation_id"])
    load_project_meta(user_id, project_id)
    key = (user_id, project_id)
    with _lock:
        if key in _project_locks:
            raise RuntimeError("该项目已有任务正在运行，暂不能自动恢复")
        _project_locks.add(key)

    task_id = uuid.uuid4().hex[:12]
    turn_id: str | None = None
    task_created = False
    event_store = ConversationEventStore(_store)
    try:
        conv = _store.get_conversation(conversation_id, user_id)
        if not conv or conv["project_id"] != project_id:
            raise RuntimeError("中断任务的 Conversation 已不存在")
        created_at = time.time()
        attempt = int(recovery.get("recovery_attempt") or 1)
        root_task_id = str(
            recovery.get("recovery_root_task_id")
            or recovery["original_task_id"]
        )
        _store.create_task(
            {
                "id": task_id,
                "user_id": user_id,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "prompt": f"自动恢复中断任务（第 {attempt} 次）",
                "status": "queued",
                "provider": settings.provider,
                "model": settings.model,
                "created_at": created_at,
                "recovery_of_task_id": root_task_id,
                "recovery_attempt": attempt,
            }
        )
        task_created = True
        turn = event_store.create_turn(
            conversation_id,
            user_id,
            project_id,
            task_id=task_id,
            status="queued",
            provider=settings.provider,
            model=settings.model,
            created_at=created_at,
        )
        turn_id = turn["id"]
        event_store.append_event_idempotent(
            conversation_id,
            turn_id,
            EventType.RECOVERY_NOTE,
            f"recovery:{turn_id}:resume",
            {
                "content": (
                    "Agent 服务已重启。请根据已保存的完整上下文继续任务。"
                    "中断前未完成的工具调用已记录为失败；不要假设它已成功。"
                    "只读工具可以重新调用，有副作用的相同工具调用必须重新获得用户确认。"
                ),
                "source": "automatic_service_recovery",
                "interrupted_turn_id": recovery["interrupted_turn_id"],
                "original_task_id": recovery["original_task_id"],
                "recovery_attempt": attempt,
            },
            task_id=task_id,
            context_visible=True,
            created_at=created_at,
        )
        history_events = event_store.list_events(
            conversation_id,
            user_id=user_id,
        )
        replay_guard = _recovery_replay_guard(
            history_events,
            str(recovery["interrupted_turn_id"]),
        )
        prior_turn_count = len(
            {
                event["turn_id"]
                for event in history_events
                if event["turn_id"] != turn_id
            }
        )
        threading.Thread(
            target=_run_job,
            args=(
                task_id,
                user_id,
                project_id,
                conversation_id,
                turn_id,
                "",
                settings,
                history_events,
                prior_turn_count,
                replay_guard,
                True,
            ),
            daemon=True,
        ).start()
    except Exception as exc:
        if task_created:
            _store.update_task(
                task_id,
                status="failed",
                finished_at=time.time(),
                error_message=f"自动恢复初始化失败: {exc}",
            )
        if turn_id:
            event_store.update_turn_status(
                turn_id,
                "failed",
                user_id=user_id,
                finished_at=time.time(),
                error_message=str(exc),
            )
        with _lock:
            _project_locks.discard(key)
        raise
    return _store.get_task(task_id, user_id) or {}


def _recovery_replay_guard(
    events: list[dict[str, Any]],
    interrupted_turn_id: str,
) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    guarded_ids: set[str] = set()
    for event in events:
        if event.get("turn_id") != interrupted_turn_id:
            continue
        payload = event.get("payload") or {}
        tool_call_id = payload.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            continue
        if event.get("event_type") == EventType.TOOL_CALL:
            calls[tool_call_id] = payload
        elif (
            event.get("event_type") == EventType.TOOL_RESULT
            and payload.get("interrupted") is True
        ):
            guarded_ids.add(tool_call_id)
    return [
        {
            "tool_call_id": tool_call_id,
            "name": calls[tool_call_id].get("name"),
            "input": calls[tool_call_id].get("input") or {},
        }
        for tool_call_id in sorted(guarded_ids)
        if tool_call_id in calls
    ]


def _run_job(
    task_id: str,
    user_id: str,
    project_id: str,
    conversation_id: str | None,
    turn_id: str,
    prompt: str,
    settings: Settings,
    history_events: list[dict[str, Any]],
    prior_turn_count: int,
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
) -> None:
    if not conversation_id:
        raise RuntimeError("任务缺少 conversation_id")
    event_store = ConversationEventStore(_store)
    workspace = workspace_path(user_id, project_id)
    before = snapshot_workspace(workspace)
    task_started = time.time()
    build_state = {"attempted": False, "succeeded": False}
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    edit_state: dict[str, Any] = {"successful_edits": 0, "approval_decisions": []}
    changes_recorded = False

    def check_cancel() -> None:
        if _store.is_cancel_requested(task_id):
            cancel_gradle(user_id, project_id)
            reject_job_approvals(task_id, user_id, reason="canceled")
            raise CancellationRequested("用户已请求停止任务")

    def set_status(status: str) -> None:
        _store.update_task(task_id, status=status)
        if status in {"queued", "running", "awaiting_approval"}:
            event_store.update_turn_status(
                turn_id,
                status,
                user_id=user_id,
                started_at=time.time() if status == "running" else None,
            )

    def append_canonical(
        event_type: str,
        payload: dict[str, Any],
        *,
        role: str | None = None,
        context_visible: bool = False,
        event_key: str | None = None,
    ) -> dict[str, Any]:
        kwargs = {
            "task_id": task_id,
            "role": role,
            "context_visible": context_visible,
            "provider": payload.get("provider"),
            "model": payload.get("model"),
        }
        if event_key:
            return event_store.append_event_idempotent(
                conversation_id,
                turn_id,
                event_type,
                event_key,
                payload,
                **kwargs,
            )
        return event_store.append_event(
            conversation_id,
            turn_id,
            event_type,
            payload,
            **kwargs,
        )

    def on_event(event_type: str, data: Any) -> None:
        payload = data if isinstance(data, dict) else {"message": str(data)}
        ui_payload = dict(payload)
        ui_payload.pop("model_output", None)
        ui_payload.pop("structured_output", None)
        _store.add_event(task_id, event_type, ui_payload)

        if event_type == EventType.ASSISTANT_MESSAGE:
            message_id = payload.get("message_id")
            if not message_id:
                raise RuntimeError("assistant_message 缺少 message_id")
            append_canonical(
                event_type,
                payload,
                role="assistant",
                context_visible=True,
                event_key=f"assistant:{message_id}",
            )
        elif event_type == EventType.TOOL_CALL and payload.get("tool_call_id"):
            append_canonical(
                event_type,
                payload,
                context_visible=True,
                event_key=f"tool_call:{payload['tool_call_id']}",
            )
        elif event_type == EventType.TOOL_RESULT and payload.get("tool_call_id"):
            append_canonical(
                event_type,
                payload,
                context_visible=True,
                event_key=f"tool_result:{payload['tool_call_id']}",
            )
        elif event_type == EventType.APPROVAL_REQUIRED:
            approval_id = payload.get("approval_id")
            tool_call_id = payload.get("tool_call_id")
            if not approval_id or not tool_call_id:
                raise RuntimeError(
                    "approval_required 缺少 approval_id 或 tool_call_id"
                )
            append_canonical(
                event_type,
                payload,
                event_key=f"approval:{approval_id}:required",
            )
        elif event_type == EventType.APPROVAL_RESOLVED:
            approval_id = payload.get("approval_id")
            tool_call_id = payload.get("tool_call_id")
            if not approval_id or not tool_call_id:
                raise RuntimeError(
                    "approval_resolved 缺少 approval_id 或 tool_call_id"
                )
            append_canonical(
                event_type,
                payload,
                event_key=f"approval:{approval_id}:resolved",
            )
        elif event_type in {
            EventType.USAGE,
            EventType.PROVIDER_SWITCH,
            EventType.MODEL_SWITCH,
        }:
            append_canonical(event_type, payload)

        if (
            event_type == EventType.TOOL_RESULT
            and payload.get("name") in {"write_file", "str_replace"}
            and payload.get("ok")
        ):
            edit_state["successful_edits"] += 1
        if event_type == EventType.APPROVAL_RESOLVED:
            decision = payload.get("decision")
            if decision:
                edit_state["approval_decisions"].append(str(decision))
        if (
            event_type == EventType.TOOL_RESULT
            and payload.get("name") == "run_gradle"
        ):
            task_name = (payload.get("input") or {}).get("task") or "assembleDebug"
            # Only assembleDebug counts toward the success gate
            if task_name == "assembleDebug":
                build_state["attempted"] = True
                build_state["succeeded"] = bool(payload.get("ok"))
        usage = payload.get("usage")
        if isinstance(usage, dict):
            for key in token_usage:
                value = usage.get(key)
                if isinstance(value, int):
                    token_usage[key] += value
            _store.update_task(
                task_id,
                input_tokens=token_usage["input_tokens"],
                output_tokens=token_usage["output_tokens"],
                total_tokens=token_usage["total_tokens"],
            )

    def record_changes() -> list[dict[str, Any]]:
        nonlocal changes_recorded
        if changes_recorded:
            task = _store.get_task(task_id, user_id) or {}
            return list(task.get("changed_files") or [])
        after = snapshot_workspace(workspace)
        changed, diff = compare_snapshots(workspace, before, after)
        _store.update_task(task_id, changed_files=changed, diff=diff)
        _store.add_event(
            task_id,
            EventType.CHANGES,
            {"message": f"改动 {len(changed)} 个文件", "files": changed},
        )
        append_canonical(
            EventType.CHANGES,
            {"files": changed},
            event_key=f"turn:{turn_id}:changes",
        )
        changes_recorded = True
        return changed

    def ensure_final_assistant(final_answer: str) -> None:
        turn_events = event_store.list_turn_events(turn_id, user_id=user_id)
        if any(
            event["event_type"] == EventType.ASSISTANT_MESSAGE
            and event["payload"].get("is_final") is True
            for event in turn_events
        ):
            return
        message_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"android-agent:turn:{turn_id}:final_assistant",
        ).hex
        append_canonical(
            EventType.ASSISTANT_MESSAGE,
            {
                "message_id": message_id,
                "text_blocks": [
                    {
                        "block_index": 0,
                        "type": "text",
                        "text": final_answer,
                    }
                ],
                "finish_reason": "stop",
                "is_final": True,
                "streamed": False,
                "provider": settings.provider,
                "model": settings.model,
                "response_id": None,
                "source": "job_fallback",
            },
            role="assistant",
            context_visible=True,
            event_key=f"assistant:{message_id}",
        )

    def mark_failed(exc: Exception) -> None:
        error = str(exc)
        terminal_errors: list[str] = []
        try:
            append_canonical(
                EventType.TURN_FAILED,
                {"error": error},
                event_key=f"turn:{turn_id}:failed",
            )
        except Exception as terminal_exc:
            terminal_errors.append(f"turn_failed 写入失败: {terminal_exc}")
        try:
            event_store.update_turn_status(
                turn_id,
                "failed",
                user_id=user_id,
                finished_at=time.time(),
                error_message=error,
            )
        except Exception as terminal_exc:
            terminal_errors.append(f"Turn 状态写入失败: {terminal_exc}")
        if terminal_errors:
            error = error + "; " + "; ".join(terminal_errors)
        _store.update_task(
            task_id,
            status="failed",
            finished_at=time.time(),
            error_message=error,
        )
        _store.add_event(
            task_id,
            "failed",
            {"message": "任务失败", "error": error},
        )

    answer = ""
    try:
        started_at = time.time()
        _store.update_task(task_id, status="running", started_at=started_at)
        event_store.update_turn_status(
            turn_id,
            "running",
            user_id=user_id,
            started_at=started_at,
        )
        append_canonical(
            EventType.TURN_STARTED,
            {
                "status": "running",
                "provider": settings.provider,
                "model": settings.model,
            },
            event_key=f"turn:{turn_id}:started",
        )
        _store.add_event(
            task_id,
            "started",
            {
                "message": "任务开始",
                "project_id": project_id,
                "conversation_id": conversation_id,
            },
        )
        if prior_turn_count:
            _store.add_event(
                task_id,
                "session",
                {
                    "message": f"续接对话（已有 {prior_turn_count} 轮）",
                    "turn_count": prior_turn_count,
                    "conversation_id": conversation_id,
                },
            )
        _store.add_event(
            task_id,
            "plan",
            {"message": "理解需求 -> 定位/修改代码 -> 需要时再 assembleDebug"},
        )
        check_cancel()
        answer = run_agent(
            settings,
            workspace,
            user_id,
            project_id,
            prompt,
            on_event=on_event,
            cancel_check=check_cancel,
            task_id=task_id,
            set_status=set_status,
            conversation_events=history_events,
            turn_id=turn_id,
            recovery_replays=recovery_replays,
            recovery_mode=recovery_mode,
        )
        check_cancel()

        if settings.auto_build_after_edit and not build_state["attempted"]:
            message_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"android-agent:turn:{turn_id}:job_auto_build",
            ).hex
            tool_call_id = f"call_{message_id[:24]}"
            on_event(
                EventType.ASSISTANT_MESSAGE,
                {
                    "message_id": message_id,
                    "text_blocks": [],
                    "finish_reason": "tool_calls",
                    "is_final": False,
                    "streamed": False,
                    "provider": "system",
                    "model": None,
                    "response_id": None,
                },
            )
            on_event(
                EventType.TOOL_CALL,
                {
                    "message": "auto_build_after_edit",
                    "message_id": message_id,
                    "tool_call_id": tool_call_id,
                    "block_index": 0,
                    "name": "run_gradle",
                    "input": {"task": "assembleDebug", "auto": True},
                },
            )
            auto_started = time.monotonic()
            auto_error_type = None
            try:
                result = dispatch_agent_tool(
                    workspace,
                    user_id,
                    project_id,
                    "run_gradle",
                    {"task": "assembleDebug"},
                    cancel_check=check_cancel,
                    settings=settings,
                    on_event=on_event,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    set_status=set_status,
                    recovery_replays=recovery_replays,
                    recovery_mode=recovery_mode,
                )
            except CancellationRequested as exc:
                on_event(
                    EventType.TOOL_RESULT,
                    {
                        "message": f"auto build interrupted: {exc}",
                        "tool_call_id": tool_call_id,
                        "name": "run_gradle",
                        "ok": False,
                        "model_output": str(exc),
                        "structured_output": None,
                        "duration_ms": round(
                            (time.monotonic() - auto_started) * 1000
                        ),
                        "error_type": exc.__class__.__name__,
                        "interrupted": True,
                        "input": {"task": "assembleDebug", "auto": True},
                        "preview": str(exc),
                    },
                )
                raise
            except ApprovalEventPersistenceError:
                raise
            except Exception as exc:
                auto_error_type = exc.__class__.__name__
                result = ToolResult(
                    False,
                    f"工具 run_gradle 执行异常: {exc}",
                )
            build_state["attempted"] = True
            build_state["succeeded"] = result.ok
            model_output = (
                result.output
                if isinstance(result.output, str)
                else str(result.output)
            )
            on_event(
                EventType.TOOL_RESULT,
                {
                    "message": "auto build",
                    "tool_call_id": tool_call_id,
                    "name": "run_gradle",
                    "ok": result.ok,
                    "model_output": model_output,
                    "structured_output": (
                        result.output
                        if not isinstance(result.output, str)
                        else None
                    ),
                    "duration_ms": round(
                        (time.monotonic() - auto_started) * 1000
                    ),
                    "error_type": (
                        auto_error_type
                        if auto_error_type
                        else None if result.ok else "ToolExecutionError"
                    ),
                    "interrupted": False,
                    "input": {"task": "assembleDebug", "auto": True},
                    "preview": model_output[:2000],
                },
            )

        # Relaxed gate: only fail if gradle was attempted and failed
        if build_state["attempted"] and not build_state["succeeded"]:
            raise RuntimeError("assembleDebug 未成功，请查看构建日志和已尝试的修复")

        task_apk = None
        if build_state["succeeded"]:
            apk = latest_apk_path(user_id, project_id)
            if apk.is_file() and apk.stat().st_mtime >= task_started:
                task_apk = user_builds_dir(user_id) / project_id / f"{task_id}.apk"
                shutil.copy2(apk, task_apk)

        logs = sorted(
            (user_builds_dir(user_id) / project_id).glob("*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        # Snapshot early so honesty check can use real disk changes
        after_preview = snapshot_workspace(workspace)
        changed_preview, _diff_preview = compare_snapshots(workspace, before, after_preview)
        answer = sanitize_final_answer(
            answer,
            changed_files=changed_preview,
            successful_edits=edit_state["successful_edits"],
            user_prompt=prompt,
            approval_decisions=edit_state["approval_decisions"],
        )
        changed = record_changes()
        ensure_final_assistant(answer)
        completed_at = time.time()
        append_canonical(
            EventType.TURN_COMPLETED,
            {"status": "succeeded", "result": answer},
            event_key=f"turn:{turn_id}:completed",
        )
        event_store.update_turn_status(
            turn_id,
            "succeeded",
            user_id=user_id,
            finished_at=completed_at,
        )
        _store.update_task(
            task_id,
            status="succeeded",
            finished_at=completed_at,
            final_message=answer,
            apk_path=str(task_apk) if task_apk else None,
            build_log_path=str(logs[-1]) if logs and logs[-1].stat().st_mtime >= task_started else None,
        )
        _store.add_event(task_id, "completed", {"message": "本轮完成", "result": answer})
    except CancellationRequested as exc:
        try:
            record_changes()
            canceled_at = time.time()
            append_canonical(
                EventType.TURN_CANCELED,
                {"error": str(exc)},
                event_key=f"turn:{turn_id}:canceled",
            )
            event_store.update_turn_status(
                turn_id,
                "canceled",
                user_id=user_id,
                finished_at=canceled_at,
                error_message=str(exc),
            )
            _store.update_task(
                task_id,
                status="canceled",
                finished_at=canceled_at,
                error_message=str(exc),
            )
            _store.add_event(task_id, "canceled", {"message": str(exc)})
        except Exception as terminal_exc:
            mark_failed(
                RuntimeError(
                    f"取消任务时规范事件写入失败: {terminal_exc}"
                )
            )
    except Exception as exc:
        try:
            record_changes()
        except Exception as changes_exc:
            exc = RuntimeError(f"{exc}; changes 写入失败: {changes_exc}")
        mark_failed(exc)
    finally:
        logs = sorted(
            (user_builds_dir(user_id) / project_id).glob("*.log"),
            key=lambda path: path.stat().st_mtime,
        )
        if logs and logs[-1].stat().st_mtime >= task_started:
            _store.update_task(task_id, build_log_path=str(logs[-1]))
        with _lock:
            _project_locks.discard((user_id, project_id))


def wait_for_job(job_id: str, timeout: float | None = None) -> dict[str, Any] | None:
    deadline = None if timeout is None else time.time() + timeout
    while True:
        job = get_job(job_id)
        if not job or job["status"] in {"succeeded", "failed", "canceled"}:
            return job
        if deadline is not None and time.time() >= deadline:
            return job
        time.sleep(0.2)
