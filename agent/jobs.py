from __future__ import annotations

import shutil
import threading
import time
import uuid
from typing import Any

from agent.approvals import get_pending_approvals, reject_job_approvals, resolve_approval
from agent.changes import compare_snapshots, snapshot_workspace
from agent.config import Settings, load_settings
from agent.database import TaskStore
from agent.honesty import sanitize_final_answer
from agent.loop import CancellationRequested, run_agent
from agent.paths import latest_apk_path, user_builds_dir, workspace_path
from agent.project import load_project_meta
from agent.tools import cancel_gradle, dispatch_tool


_store = TaskStore()
_store.recover_interrupted()
_project_locks: set[tuple[str, str]] = set()
_lock = threading.Lock()


def configure_task_store(store: TaskStore) -> None:
    global _store
    _store = store
    _store.recover_interrupted()


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
    return result


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
            prior_turns = _store.get_conversation_turns(conversation_id)
        else:
            prior_turns = []
            if reset_session and conversation_id:
                _store.update_conversation(conversation_id, user_id, turns=[])
    except Exception:
        with _lock:
            _project_locks.discard(key)
        raise

    task_id = uuid.uuid4().hex[:12]
    _store.create_task({
        "id": task_id,
        "user_id": user_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "prompt": prompt,
        "status": "queued",
        "provider": settings.provider,
        "model": settings.model,
        "created_at": time.time(),
    })
    threading.Thread(
        target=_run_job,
        args=(task_id, user_id, project_id, conversation_id, prompt, settings, prior_turns),
        daemon=True,
    ).start()
    return _store.get_task(task_id, user_id) or {}


def _run_job(
    task_id: str,
    user_id: str,
    project_id: str,
    conversation_id: str | None,
    prompt: str,
    settings: Settings,
    prior_turns: list[dict[str, Any]],
) -> None:
    workspace = workspace_path(user_id, project_id)
    before = snapshot_workspace(workspace)
    task_started = time.time()
    build_state = {"attempted": False, "succeeded": False}
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    edit_state: dict[str, Any] = {"successful_edits": 0, "approval_decisions": []}
    _store.update_task(task_id, status="running", started_at=time.time())
    _store.add_event(
        task_id,
        "started",
        {"message": "任务开始", "project_id": project_id, "conversation_id": conversation_id},
    )
    if prior_turns:
        _store.add_event(
            task_id,
            "session",
            {
                "message": f"续接对话（已有 {len(prior_turns)} 轮）",
                "turn_count": len(prior_turns),
                "conversation_id": conversation_id,
            },
        )
    _store.add_event(
        task_id,
        "plan",
        {"message": "理解需求 -> 定位/修改代码 -> 需要时再 assembleDebug"},
    )

    def check_cancel() -> None:
        if _store.is_cancel_requested(task_id):
            cancel_gradle(user_id, project_id)
            reject_job_approvals(task_id, user_id, reason="canceled")
            raise CancellationRequested("用户已请求停止任务")

    def set_status(status: str) -> None:
        _store.update_task(task_id, status=status)

    def on_event(event_type: str, data: Any) -> None:
        payload = data if isinstance(data, dict) else {"message": str(data)}
        _store.add_event(task_id, event_type, payload)
        if (
            event_type == "tool_result"
            and payload.get("name") in {"write_file", "str_replace"}
            and payload.get("ok")
        ):
            edit_state["successful_edits"] += 1
        if event_type == "approval_resolved":
            decision = payload.get("decision")
            if decision:
                edit_state["approval_decisions"].append(str(decision))
        if event_type == "tool_result" and payload.get("name") == "run_gradle":
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

    answer = ""
    try:
        check_cancel()
        answer = run_agent(
            settings,
            workspace,
            user_id,
            project_id,
            prompt,
            on_event=on_event,
            cancel_check=check_cancel,
            prior_turns=prior_turns,
            task_id=task_id,
            set_status=set_status,
        )
        check_cancel()

        if settings.auto_build_after_edit and not build_state["attempted"]:
            _store.add_event(
                task_id,
                "tool_call",
                {"message": "auto_build_after_edit", "name": "run_gradle", "input": {"task": "assembleDebug"}},
            )
            result = dispatch_tool(
                workspace,
                user_id,
                project_id,
                "run_gradle",
                {"task": "assembleDebug"},
                cancel_check=check_cancel,
                settings=settings,
                on_event=on_event,
                task_id=task_id,
                set_status=set_status,
            )
            build_state["attempted"] = True
            build_state["succeeded"] = result.ok
            _store.add_event(
                task_id,
                "tool_result",
                {
                    "message": "auto build",
                    "name": "run_gradle",
                    "ok": result.ok,
                    "preview": str(result.output)[:2000],
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
        _store.update_task(
            task_id,
            status="succeeded",
            finished_at=time.time(),
            final_message=answer,
            apk_path=str(task_apk) if task_apk else None,
            build_log_path=str(logs[-1]) if logs and logs[-1].stat().st_mtime >= task_started else None,
        )
        _store.add_event(task_id, "completed", {"message": "本轮完成", "result": answer})
    except CancellationRequested as exc:
        _store.update_task(task_id, status="canceled", finished_at=time.time(), error_message=str(exc))
        _store.add_event(task_id, "canceled", {"message": str(exc)})
    except Exception as exc:
        _store.update_task(task_id, status="failed", finished_at=time.time(), error_message=str(exc))
        _store.add_event(task_id, "failed", {"message": "任务失败", "error": str(exc)})
    finally:
        logs = sorted(
            (user_builds_dir(user_id) / project_id).glob("*.log"),
            key=lambda path: path.stat().st_mtime,
        )
        if logs and logs[-1].stat().st_mtime >= task_started:
            _store.update_task(task_id, build_log_path=str(logs[-1]))
        after = snapshot_workspace(workspace)
        changed, diff = compare_snapshots(workspace, before, after)
        _store.update_task(task_id, changed_files=changed, diff=diff)
        _store.add_event(task_id, "changes", {"message": f"改动 {len(changed)} 个文件", "files": changed})
        if conversation_id:
            try:
                final = answer or (_store.get_task(task_id, user_id) or {}).get("error_message") or ""
                _store.append_conversation_turn(
                    conversation_id,
                    user=prompt,
                    assistant=final,
                    changed_files=changed,
                )
            except Exception:
                pass
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
