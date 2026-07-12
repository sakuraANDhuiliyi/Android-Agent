from __future__ import annotations

import shutil
import threading
import time
import uuid
from typing import Any

from agent.changes import compare_snapshots, snapshot_workspace
from agent.config import Settings, load_settings
from agent.database import TaskStore
from agent.loop import CancellationRequested, run_agent
from agent.paths import latest_apk_path, user_builds_dir, workspace_path
from agent.project import load_project_meta


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


def list_jobs(user_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
    return _store.list_tasks(user_id, project_id)


def job_to_dict(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job)
    result["result"] = result.get("final_message")
    result["error"] = result.get("error_message")
    return result


def request_cancel(job_id: str, user_id: str) -> bool:
    changed = _store.request_cancel(job_id, user_id)
    if changed:
        _store.add_event(job_id, "cancel_requested", {"message": "已请求停止任务"})
    return changed


def start_ask_job(user_id: str, project_id: str, prompt: str, settings: Settings | None = None) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    settings = settings or load_settings()
    key = (user_id, project_id)
    with _lock:
        if key in _project_locks:
            raise RuntimeError("该项目已有任务正在运行")
        _project_locks.add(key)
    task_id = uuid.uuid4().hex[:12]
    _store.create_task({
        "id": task_id, "user_id": user_id, "project_id": project_id,
        "prompt": prompt, "status": "queued", "provider": settings.provider,
        "model": settings.model, "created_at": time.time(),
    })
    threading.Thread(
        target=_run_job,
        args=(task_id, user_id, project_id, prompt, settings),
        daemon=True,
    ).start()
    return _store.get_task(task_id, user_id) or {}


def _run_job(task_id: str, user_id: str, project_id: str, prompt: str, settings: Settings) -> None:
    workspace = workspace_path(user_id, project_id)
    before = snapshot_workspace(workspace)
    task_started = time.time()
    build_state = {"attempted": False, "succeeded": False}
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    _store.update_task(task_id, status="running", started_at=time.time())
    _store.add_event(task_id, "started", {"message": "任务开始", "project_id": project_id})
    _store.add_event(task_id, "plan", {"message": "读取工程 -> 修改允许文件 -> assembleDebug -> 失败时有限修复"})

    def check_cancel() -> None:
        if _store.is_cancel_requested(task_id):
            raise CancellationRequested("用户已请求停止任务")

    def on_event(event_type: str, data: Any) -> None:
        payload = data if isinstance(data, dict) else {"message": str(data)}
        _store.add_event(task_id, event_type, payload)
        if event_type == "tool_result" and payload.get("name") == "run_gradle":
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

    try:
        check_cancel()
        answer = run_agent(
            settings, workspace, user_id, project_id, prompt,
            on_event=on_event, cancel_check=check_cancel,
        )
        check_cancel()
        if not build_state["attempted"]:
            raise RuntimeError("Agent 未执行 assembleDebug，本任务不判定为成功")
        if not build_state["succeeded"]:
            raise RuntimeError("assembleDebug 未成功，请查看构建日志和已尝试的修复")
        apk = latest_apk_path(user_id, project_id)
        task_apk = None
        if apk.is_file() and apk.stat().st_mtime >= task_started:
            task_apk = user_builds_dir(user_id) / project_id / f"{task_id}.apk"
            shutil.copy2(apk, task_apk)
        else:
            raise RuntimeError("构建报告成功，但未找到本任务生成的 APK")
        logs = sorted((user_builds_dir(user_id) / project_id).glob("*.log"), key=lambda p: p.stat().st_mtime)
        _store.update_task(
            task_id, status="succeeded", finished_at=time.time(), final_message=answer,
            apk_path=str(task_apk) if task_apk else None,
            build_log_path=str(logs[-1]) if logs else None,
        )
        _store.add_event(task_id, "completed", {"message": "任务完成", "result": answer})
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
