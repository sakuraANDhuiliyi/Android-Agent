from __future__ import annotations

import logging
import json
import sqlite3
import copy
import shutil
import threading
import time
import uuid
from contextlib import nullcontext
from typing import Any
from pathlib import Path

from agent.approvals import (
    ApprovalEventPersistenceError,
    get_pending_approvals,
    reject_job_approvals,
    resolve_approval,
)
from agent.changes import compare_snapshots, diff_stats, snapshot_workspace
from agent.config import Settings, load_settings
from agent.conversation_events import (
    ConversationEventStore,
    ConversationEventType as EventType,
)
from agent.conversation_summary import create_semantic_checkpoint
from agent.database import TaskStore
from agent.honesty import sanitize_final_answer
from agent.feedback import FeedbackStore, capture_gradle_result, persist_gradle_receipt, run_feedback_cycle, feedback_summary
from agent.governance import prune_old_files
from agent.loop import CancellationRequested, dispatch_agent_tool, run_agent
from agent.paths import latest_apk_path, user_builds_dir, workspace_path
from agent.project import load_project_meta
from agent.project_lifecycle import project_operation
from agent.redaction import redact_sensitive_value
from agent.tools import ToolResult, cancel_gradle
from agent.worker import PauseRequested, TaskLeaseLost, TaskWorker
from agent.workspace import WorkspaceRepository
from agent.subagents import configure_subagent_store, run_subagent_job
from agent.task_settings import inherited_execution_context, model_selection, resolve_task_settings


logger = logging.getLogger(__name__)


_store = TaskStore()
_worker: TaskWorker | None = None
_worker_pool: list[TaskWorker] = []
_worker_lock = threading.Lock()
_lock = threading.Lock()
_project_locks: set[tuple[str, str]] = set()
_configured_worker_settings: tuple[TaskStore, Settings] | None = None


def _release_project_lock(user_id: str, project_id: str) -> None:
    with _lock:
        _project_locks.discard((user_id, project_id))


def configure_task_store(
    store: TaskStore | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    global _store, _configured_worker_settings
    if store is not None and store is not _store:
        stop_worker(wait=True, timeout=5.0)
        _store = store
    if settings is not None:
        _configured_worker_settings = (_store, settings)
        _store.max_events_per_conversation = int(
            getattr(settings, "max_events_per_conversation", 100_000)
        )
        _store.max_task_events_per_task = int(
            getattr(settings, "max_task_events_per_task", 20_000)
        )
    configure_subagent_store(_store)
    recovered = _store.recover_interrupted()
    return recovered


def worker_settings(settings: Settings | None = None) -> Settings:
    """Resolve the complete provider catalog configured for this task store."""
    if _configured_worker_settings and _configured_worker_settings[0] is _store:
        return _configured_worker_settings[1]
    return settings or load_settings()


def start_worker(settings: Settings) -> TaskWorker:
    global _worker, _worker_pool
    settings = worker_settings(settings)
    with _worker_lock:
        if _worker is not None and _worker.store is not _store:
            # Stop the old worker fully before replacing it, otherwise the old
            # thread may continue to access a stale store or run_agent patch.
            _worker.stop(wait=True, timeout=5.0)
            _worker = None
            for pooled in _worker_pool:
                pooled.stop(wait=True, timeout=5.0)
            _worker_pool = []
        if _worker is not None and _worker._thread is not None and _worker._thread.is_alive():
            return _worker
        _worker = TaskWorker(
            _store,
            _run_job,
            settings,
            project_lock_release=_release_project_lock,
        )
        _worker.start()
        _worker_pool = [_worker]
        for _ in range(2):
            pooled = TaskWorker(
                _store,
                _run_job,
                settings,
                project_lock_release=_release_project_lock,
            )
            pooled.start()
            _worker_pool.append(pooled)
    return _worker


def ensure_worker_started(settings: Settings | None = None) -> TaskWorker:
    from agent.config import load_settings as _load

    return start_worker(settings or _load())


def stop_worker(wait: bool = False, timeout: float | None = None) -> None:
    global _worker, _worker_pool
    with _worker_lock:
        workers = list(dict.fromkeys(_worker_pool + ([_worker] if _worker else [])))
        for worker in workers:
            worker.stop(wait=wait, timeout=timeout)
        _worker = None
        _worker_pool = []


def get_job(job_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
    return _store.get_task(job_id, user_id)


def list_jobs(
    user_id: str,
    project_id: str | None = None,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    return _store.list_tasks(user_id, project_id, conversation_id)


def _turn_id_for_task(task_id: str, *, _conn=None) -> str | None:
    """Resolve the conversation turn that owns a task (job)."""
    if not task_id:
        return None
    try:
        with nullcontext(_conn) if _conn is not None else _store._connect() as conn:
            row = conn.execute(
                "SELECT id FROM conversation_turns WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


def job_to_dict(job: dict[str, Any], *, _conn=None) -> dict[str, Any]:
    from agent.task_status import enrich_job_dict
    from agent.verification import verification_for_job

    private_fields = {
        "apk_path",
        "build_log_path",
        "context_json",
        "claim_owner",
        "claim_token",
        "lease_expires_at",
        "heartbeat_at",
        "write_lock_key",
        "recovery_source_task_id",
    }
    result = {
        key: value
        for key, value in dict(job).items()
        if key not in private_fields
    }
    result["result"] = result.get("final_message")
    result["error"] = result.get("error_message")
    ctx = job.get("context")
    if not isinstance(ctx, dict):
        ctx = {}
    result["run_mode"] = ctx.get("run_mode") or "workspace"
    if ctx.get("permission_profile"):
        result["permission_profile"] = ctx.get("permission_profile")
    task_id = result.get("id")
    # The desktop needs the turn identity to locate the Turn in the timeline
    # and to request the checkpoint-based diff review.
    result["turn_id"] = _turn_id_for_task(task_id, _conn=_conn)
    result.update(can_recover=False, recovery_job_id=None)
    try:
        result.update(_store.recovery_state(task_id, str(job.get("user_id") or ""), _conn=_conn))
    except sqlite3.Error:
        # A detached DTO (or an unavailable store) must not advertise an
        # unverified recovery action. The mutation always rechecks the DB.
        logger.debug("Recovery metadata unavailable for task %s", task_id, exc_info=True)
    result["has_apk"] = bool(job.get("apk_path"))
    result["verification"] = verification_for_job(job)
    result["has_build_log"] = bool(job.get("build_log_path"))
    result["apk_url"] = f"/api/jobs/{task_id}/apk" if job.get("apk_path") else None
    result["build_log_url"] = (
        f"/api/jobs/{task_id}/log" if job.get("build_log_path") else None
    )
    return dict(redact_sensitive_value(enrich_job_dict(result)))


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
    result = resolve_approval(approval_id, user_id, approved=approved, expected_job_id=job_id)
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
    cancelled_ids = _store.request_cancel_cascade(job_id, user_id)
    changed = bool(cancelled_ids)
    if changed:
        for tid in cancelled_ids:
            _store.add_event(tid, "cancel_requested", {"message": "已请求停止任务", "cascaded_from": job_id})
            _store.add_task_message(
                tid,
                message_key=f"cancel:{tid}:{time.time()}",
                type="cancel",
                payload={"source": "api", "cascaded_from": job_id},
            )
            reject_job_approvals(tid, user_id, reason="canceled")
            _finalize_canceled_paused_turn(tid, user_id)
        if job:
            cancel_gradle(job["user_id"], job["project_id"])
    return changed


def _finalize_canceled_paused_turn(task_id: str, user_id: str) -> None:
    """Paused tasks are terminalized by the store on cancel; mirror that into
    the conversation turn and events so clients see a consistent canceled
    timeline instead of a turn stuck on "paused"."""
    task = _store.get_task(task_id, user_id)
    if not task or task.get("status") != "canceled":
        return
    conversation_id = task.get("conversation_id") or ""
    turn_id = _turn_id_for_task(task_id)
    if not conversation_id or not turn_id:
        return
    try:
        ConversationEventStore(_store).finalize_lifecycle(
            conversation_id=conversation_id,
            turn_id=turn_id,
            task_id=task_id,
            user_id=user_id,
            event_type=EventType.TURN_CANCELED,
            event_key=f"turn:{turn_id}:canceled",
            event_payload={"error": "用户已请求停止任务"},
            status="canceled",
            finished_at=time.time(),
            error_message="用户已请求停止任务",
            task_event_type="canceled",
            task_event_payload={"message": "用户已请求停止任务"},
        )
    except Exception:
        logger.exception("Failed to finalize canceled paused task %s", task_id)


def add_job_message(
    job_id: str,
    user_id: str,
    message_key: str,
    type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    result = _store.admit_task_message(job_id, user_id, message_key, type, payload or {})
    return result[0] if result else None


def list_job_messages(
    job_id: str,
    user_id: str,
    include_consumed: bool = False,
) -> list[dict[str, Any]] | None:
    from agent.task_messages import message_page
    page = message_page(_store, job_id, user_id, include_consumed=include_consumed)
    return page['messages'] if page is not None else None


def pause_job(job_id: str, user_id: str) -> bool:
    return _store.pause_task(job_id, user_id)


def resume_job(job_id: str, user_id: str) -> bool:
    return _store.resume_task(job_id, user_id)


# —— Conversations ——

def list_conversations(user_id: str, project_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
    load_project_meta(user_id, project_id)
    return _store.list_conversations(user_id, project_id, include_archived=include_archived)


def conversation_list_previews(
    user_id: str, project_id: str, conversation_ids: list[str]
) -> dict[str, dict[str, Any]]:
    load_project_meta(user_id, project_id)
    return _store.conversation_list_previews(user_id, conversation_ids)


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
    before_seq: int | None = None,
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
        before_seq=before_seq,
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


def restore_conversation(conversation_id: str, user_id: str) -> dict[str, Any] | None:
    return _store.restore_conversation(conversation_id, user_id)


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


# —— Workspace / Git / Checkpoints ——


def workspace_status(user_id: str, project_id: str) -> dict[str, Any]:
    meta = load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    if repo.is_git():
        git = repo.git_status()
    else:
        checkpoints = repo.list_checkpoints()
        baseline = next((item for item in checkpoints if item.get("kind") == "before_turn"), None)
        diff = repo.checkpoint_diff(baseline["id"]) if baseline else {"files": []}
        files = diff.get("files") or []
        git = {
            "ok": True,
            "branch": meta.get("default_branch") or "workspace",
            "base_revision": baseline.get("id") if baseline else None,
            "files": [
                {"path": item.get("path"), "status": item.get("change", "modified")}
                for item in files
            ],
            "dirty": bool(files),
        }
    return {
        "user_id": user_id,
        "project_id": project_id,
        "source_kind": meta.get("source_kind"),
        "default_branch": meta.get("default_branch"),
        "is_git": repo.is_git(),
        "git": git,
    }


def workspace_diff(
    user_id: str,
    project_id: str,
    *,
    turn_id: str | None = None,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    if turn_id:
        return repo.turn_diff(turn_id)
    if checkpoint_id:
        return repo.checkpoint_diff(checkpoint_id)
    if repo.is_git():
        return repo.git_diff()
    checkpoints = repo.list_checkpoints()
    baseline = next((item for item in checkpoints if item.get("kind") == "before_turn"), None)
    if baseline:
        return repo.checkpoint_diff(baseline["id"])
    return {"ok": True, "status": "empty", "files": [], "diff": "", "truncated": False}


def workspace_diff_file(
    user_id: str,
    project_id: str,
    *,
    turn_id: str,
    path: str,
) -> dict[str, Any]:
    """Exact before/after content of one file, read from checkpoint blobs."""
    load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    return repo.turn_diff_file(turn_id, path)


def list_checkpoints(user_id: str, project_id: str) -> list[dict[str, Any]]:
    load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    return repo.list_checkpoints()


def restore_checkpoint(user_id: str, project_id: str, checkpoint_id: str) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    return repo.restore_checkpoint(checkpoint_id)


def detect_checkpoint_conflicts(
    user_id: str, project_id: str, checkpoint_id: str
) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    result = repo.detect_conflicts(checkpoint_id)
    checkpoint = repo.get_checkpoint(checkpoint_id)
    if checkpoint:
        files = checkpoint.get("files") or []
        result["file_count"] = len(files)
        result["kind"] = checkpoint.get("kind")
    return result


def restore_file(
    user_id: str, project_id: str, checkpoint_id: str, rel_path: str
) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    return repo.restore_file(checkpoint_id, rel_path)


def revert_hunk(user_id: str, project_id: str, rel_path: str, hunk: str) -> dict[str, Any]:
    load_project_meta(user_id, project_id)
    repo = WorkspaceRepository(user_id, project_id, task_store=_store)
    return repo.revert_hunk(rel_path, hunk)


RUN_MODES = frozenset({"read_only", "workspace", "ask"})

# Legacy run mode used as fallback/base when a permission profile is active.
PROFILE_BASE_RUN_MODE = {
    "safe": "read_only",
    "standard": "workspace",
    "full_access": "workspace",
}


def _normalize_run_mode(run_mode: str | None) -> str:
    """Validate a client-supplied run mode, falling back to the safe default."""
    if run_mode is None:
        return "workspace"
    value = str(run_mode).strip()
    if value not in RUN_MODES:
        raise RuntimeError(f"非法 run_mode: {run_mode}")
    return value


def _resolve_permission(
    user_id: str,
    project_id: str,
    run_mode: str | None,
) -> tuple[str, str | None]:
    """Return (run_mode, permission_profile) for a new task.

    An explicit client run_mode keeps the legacy behavior and disables the
    profile layer; otherwise the project's configured profile drives decisions.
    """
    if run_mode is not None:
        return _normalize_run_mode(run_mode), None
    from agent.project_settings import get_permission_profile

    profile = get_permission_profile(workspace_path(user_id, project_id))
    return PROFILE_BASE_RUN_MODE.get(profile, "workspace"), profile


def start_ask_job(
    user_id: str,
    project_id: str,
    prompt: str,
    settings: Settings | None = None,
    *,
    conversation_id: str | None = None,
    continue_session: bool = True,
    reset_session: bool = False,
    run_mode: str | None = None,
    contexts: list[dict[str, Any]] | None = None,
    feedback_requested: bool = False,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with project_operation(user_id, project_id):
        return _start_ask_job_unlocked(
            user_id,
            project_id,
            prompt,
            settings,
            conversation_id=conversation_id,
            continue_session=continue_session,
            reset_session=reset_session,
            run_mode=run_mode,
            contexts=contexts,
            feedback_requested=feedback_requested,
            execution_context=execution_context,
        )


def start_conversation_submission(
    user_id: str, project_id: str, conversation_id: str, prompt: str,
    settings: Settings, *, request_key: str, request_hash: str,
    run_mode: str | None = None, contexts: list[dict[str, Any]] | None = None,
    feedback_requested: bool = False,
) -> tuple[dict[str, Any], bool]:
    from agent.submissions import lookup_submission

    with project_operation(user_id, project_id):
        existing = lookup_submission(_store, user_id, conversation_id, request_key, expected_hash=request_hash)
        if existing is not None:
            return existing, False
        task, created = _initialize_ask_job_unlocked(
            user_id, project_id, prompt, settings, conversation_id=conversation_id,
            run_mode=run_mode, contexts=contexts, feedback_requested=feedback_requested,
            submission=(request_key, request_hash),
        )
        if created:
            _start_accepted_worker(settings, task["id"])
        result = lookup_submission(_store, user_id, conversation_id, request_key, expected_hash=request_hash)
        if result is None:  # A committed ledger must never be treated as a new request.
            raise RuntimeError("已提交任务的回执暂不可用")
        return result, created


def _start_accepted_worker(settings: Settings, task_id: str) -> None:
    try:
        start_worker(settings)
    except Exception:
        # Initialization is committed. A failed wakeup cannot revoke acceptance;
        # a normal worker startup will find the fully initialized queued task.
        logger.exception("Worker startup failed after accepting task %s", task_id)


def _start_ask_job_unlocked(
    user_id: str, project_id: str, prompt: str, settings: Settings | None = None, **options: Any,
) -> dict[str, Any]:
    settings = settings or load_settings()
    task, _ = _initialize_ask_job_unlocked(user_id, project_id, prompt, settings, **options)
    _start_accepted_worker(settings, task["id"])
    return _store.get_task(task["id"], user_id) or task


def _initialize_ask_job_unlocked(
    user_id: str,
    project_id: str,
    prompt: str,
    settings: Settings,
    *,
    conversation_id: str | None = None,
    continue_session: bool = True,
    reset_session: bool = False,
    run_mode: str | None = None,
    contexts: list[dict[str, Any]] | None = None,
    feedback_requested: bool = False,
    execution_context: dict[str, Any] | None = None,
    submission: tuple[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    from agent.submissions import lookup_submission, SubmissionNotFound, SubmissionQuotaExceeded

    load_project_meta(user_id, project_id)
    inherited = inherited_execution_context(execution_context or {})
    if execution_context is None:
        run_mode, permission_profile = _resolve_permission(user_id, project_id, run_mode)
    else:
        from agent.permissions import VALID_PROFILES
        run_mode = _normalize_run_mode(inherited.get("run_mode"))
        permission_profile = inherited.get("permission_profile")
        if permission_profile is not None and permission_profile not in VALID_PROFILES:
            raise RuntimeError("原任务权限档位无效，拒绝创建后续任务")

    event_store = ConversationEventStore(_store)
    if conversation_id:
        conv = _store.get_conversation(conversation_id, user_id)
        if not conv or conv["project_id"] != project_id:
            raise SubmissionNotFound("对话不存在或不属于该项目")
    elif reset_session:
        conv = _store.create_conversation(user_id, project_id, title="新对话")
        conversation_id = conv["id"]
    else:
        conv = _store.get_or_create_default_conversation(user_id, project_id)
        conversation_id = conv["id"]

    # These operations use their own connections. They precede the atomic task
    # initialization and are not part of its durability guarantee.
    feedback_options = inherited.get("feedback_options") or FeedbackStore(_store.db_path).settings(
        user_id, project_id, bool(getattr(settings, "auto_build_after_edit", False)))
    if continue_session and not reset_session:
        create_semantic_checkpoint(event_store, conversation_id, user_id)

    task_id = uuid.uuid4().hex[:12]
    created_at = time.time()
    write_lock_key = f"main:{user_id}:{project_id}"
    with _store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conv = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=? AND project_id=?",
                            (conversation_id, user_id, project_id)).fetchone()
        if conv is None:
            raise SubmissionNotFound("对话不存在或不属于该项目")
        if submission is not None:
            key, digest = submission
            existing = lookup_submission(_store, user_id, conversation_id, key, expected_hash=digest, _conn=conn)
            if existing is not None:
                return _store.get_task(existing["submission"]["job_id"], user_id, _conn=conn), False
            active = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status IN "
                                  "('queued','running','awaiting_approval','paused')", (user_id,)).fetchone()[0]
            if active >= settings.max_active_tasks_per_user:
                raise SubmissionQuotaExceeded(f"用户活动任务达到上限 ({settings.max_active_tasks_per_user})")
        _store.create_task({
            "id": task_id, "user_id": user_id, "project_id": project_id,
            "conversation_id": conversation_id, "prompt": prompt, "status": "queued",
            "provider": settings.provider, "model": settings.model, "created_at": created_at,
            "write_lock_key": write_lock_key,
            "context": {**inherited, "write_lock_key": write_lock_key, "run_mode": run_mode,
                "permission_profile": permission_profile,
                "attachments": (contexts if contexts is not None else inherited.get("attachments", []))[:20],
                "model_selection": model_selection(settings), "feedback_requested": feedback_requested,
                "feedback_options": feedback_options},
        }, _conn=conn)
        turn = event_store.create_turn(conversation_id, user_id, project_id, task_id=task_id,
            status="queued", provider=settings.provider, model=settings.model,
            trace_id=uuid.uuid4().hex, created_at=created_at, _conn=conn)
        turn_id = turn["id"]
        event_store.append_event_idempotent(conversation_id, turn_id, EventType.USER_MESSAGE,
            f"turn:{turn_id}:user_message", {
                "message_id": uuid.uuid5(uuid.NAMESPACE_URL, f"android-agent:turn:{turn_id}:user_message").hex,
                "content": [{"type": "text", "text": prompt}], "source": "user", "trace_id": turn["trace_id"],
            }, task_id=task_id, role="user", context_visible=True, created_at=created_at, _conn=conn)
        if submission is not None:
            conn.execute("INSERT INTO task_submissions "
                         "(user_id,request_key,request_hash,project_id,conversation_id,task_id,turn_id,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?)",
                         (user_id, key, digest, project_id, conversation_id, task_id, turn_id, created_at))
        if conv["title"] in {"新对话", "默认对话", ""} and prompt.strip():
            conn.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=? AND user_id=?",
                         (redact_sensitive_value(prompt.strip()[:40]), created_at, conversation_id, user_id))
        task = _store.get_task(task_id, user_id, _conn=conn)
    return task, True


def _schedule_recovery_jobs(
    recovered: list[dict[str, Any]],
    settings: Settings,
) -> None:
    for candidate in recovered:
        try:
            enqueue_recovery_task(candidate, settings)
        except Exception:
            logger.exception(
                "Failed to schedule recovery for interrupted task %s",
                candidate.get("original_task_id"),
            )


def _enqueue_recovery_task(
    recovery: dict[str, Any], settings: Settings,
) -> tuple[dict[str, Any], bool]:
    """Commit the source mapping, queued task, turn and note as one unit."""
    user_id = str(recovery["user_id"])
    project_id = str(recovery["project_id"])
    source_id = str(recovery["original_task_id"])
    load_project_meta(user_id, project_id)
    original = _store.get_task(source_id, user_id)
    if not original or original["project_id"] != project_id:
        raise RuntimeError("原任务不存在或不属于该项目")
    settings = resolve_task_settings(settings, original)
    event_store = ConversationEventStore(_store)
    with _store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = _store.recovery_state(source_id, user_id, _conn=conn)
        if state["recovery_job_id"]:
            row = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",
                               (state["recovery_job_id"], user_id)).fetchone()
            return _store._row_to_task(row), False
        if not state["can_recover"]:
            raise RuntimeError("任务不是可显式恢复的中断任务")
        original = _store._row_to_task(conn.execute(
            "SELECT * FROM tasks WHERE id=? AND user_id=?", (source_id, user_id)
        ).fetchone())
        conversation_id = original["conversation_id"]
        source_turn = conn.execute(
            "SELECT id FROM conversation_turns WHERE task_id=? AND user_id=?",
            (source_id, user_id),
        ).fetchone()
        interrupted_turn_id = source_turn["id"]
        root_id = original.get("recovery_of_task_id") or source_id
        attempt = conn.execute(
            "SELECT COALESCE(MAX(recovery_attempt),0)+1 FROM tasks "
            "WHERE user_id=? AND recovery_of_task_id=?", (user_id, root_id)
        ).fetchone()[0]
        task_id = uuid.uuid4().hex[:12]
        created_at = time.time()
        history = event_store.list_events(conversation_id, user_id=user_id)
        context = {
            **inherited_execution_context(original.get("context") or {}),
            "model_selection": model_selection(settings),
            "write_lock_key": f"main:{user_id}:{project_id}",
            "recovery_mode": True,
            "interrupted_turn_id": interrupted_turn_id,
            "recovery_replays": _recovery_replay_guard(history, interrupted_turn_id),
        }
        prompt = f"用户确认恢复中断任务（第 {attempt} 次）"
        _store.create_task({
            "id": task_id, "user_id": user_id, "project_id": project_id,
            "conversation_id": conversation_id, "prompt": prompt, "status": "queued",
            "provider": settings.provider, "model": settings.model, "created_at": created_at,
            "write_lock_key": context["write_lock_key"], "recovery_of_task_id": root_id,
            "recovery_source_task_id": source_id, "recovery_attempt": attempt, "context": context,
        }, _conn=conn)
        turn = event_store.create_turn(
            conversation_id, user_id, project_id, task_id=task_id,
            status="queued", provider=settings.provider, model=settings.model,
            created_at=created_at, _conn=conn,
        )
        # Persist the recovery request itself so later steers cannot be
        # mistaken for this turn's initial user prompt.
        event_store.append_event_idempotent(
            conversation_id, turn["id"], EventType.USER_MESSAGE,
            f"turn:{turn['id']}:user_message",
            {"message_id": f"recovery:{task_id}:prompt", "content": prompt, "source": "explicit_service_recovery"},
            task_id=task_id, role="user", context_visible=True, created_at=created_at, _conn=conn,
        )
        event_store.append_event_idempotent(
            conversation_id, turn["id"], EventType.RECOVERY_NOTE,
            f"recovery:{turn['id']}:resume",
            {"content": (
                "Agent 服务已重启。请根据已保存的完整上下文继续任务。"
                "中断前未完成的工具调用已记录为失败；不要假设它已成功。"
                "只读工具可以重新调用，有副作用的相同工具调用必须重新获得用户确认。"
             ), "source": "explicit_service_recovery", "interrupted_turn_id": interrupted_turn_id,
             "original_task_id": source_id, "recovery_attempt": attempt},
            task_id=task_id, context_visible=True, created_at=created_at, _conn=conn,
        )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        result = _store._row_to_task(row)
    return result, True


def enqueue_recovery_task(recovery: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return _enqueue_recovery_task(recovery, settings)[0]


def start_recovery_job(recovery: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Compatibility entrypoint that enqueues without starting a thread."""
    return enqueue_recovery_task(recovery, settings)


def recover_job_with_status(
    task_id: str, user_id: str, settings: Settings | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    original = _store.get_task(task_id, user_id)
    if not original:
        return None, False
    with project_operation(user_id, original["project_id"]):
        return _enqueue_recovery_task(
            {"user_id": user_id, "project_id": original["project_id"],
             "original_task_id": task_id}, settings or load_settings(),
        )


def recover_job_explicitly(
    task_id: str, user_id: str, settings: Settings | None = None,
) -> dict[str, Any] | None:
    return recover_job_with_status(task_id, user_id, settings)[0]


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


def _pick_task_log(logs: list[Path]) -> Path | None:
    """Newest log to attach as the task log; gradle logs win over the
    lazily persisted `cmd-*.log` command logs written by run_command."""
    for path in reversed(logs):
        if not path.name.startswith("cmd-"):
            return path
    return logs[-1] if logs else None


def _run_subagent_job_wrapper(
    task_id: str,
    user_id: str,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    prompt: str,
    settings: Settings,
) -> None:
    """Run a child subagent with isolated conversation and optional worktree."""
    event_store = ConversationEventStore(_store)
    started_at = time.time()
    _store.update_task(task_id, status="running", started_at=started_at)
    event_store.update_turn_status(
        turn_id, "running", user_id=user_id, started_at=started_at
    )

    def check_cancel() -> None:
        if _store.is_cancel_requested(task_id):
            raise CancellationRequested("用户已请求停止任务")

    def on_event(event_type: str, data: Any) -> None:
        payload = data if isinstance(data, dict) else {"message": str(data)}
        _store.add_event(task_id, event_type, payload)

    try:
        check_cancel()
        answer = run_subagent_job(
            task_id,
            user_id,
            project_id,
            conversation_id,
            turn_id,
            prompt,
            settings,
            on_event=on_event,
            cancel_check=check_cancel,
        )
        finished = time.time()
        event_store.append_event_idempotent(
            conversation_id,
            turn_id,
            EventType.ASSISTANT_MESSAGE,
            f"turn:{turn_id}:final_assistant",
            {
                "message_id": uuid.uuid5(
                    uuid.NAMESPACE_URL, f"android-agent:turn:{turn_id}:final"
                ).hex,
                "text_blocks": [{"type": "text", "text": answer}],
                "is_final": True,
            },
            role="assistant",
            context_visible=True,
            task_id=task_id,
        )
        event_store.append_event_idempotent(
            conversation_id,
            turn_id,
            EventType.TURN_COMPLETED,
            f"turn:{turn_id}:completed",
            {"status": "succeeded", "result": answer},
            task_id=task_id,
        )
        event_store.update_turn_status(
            turn_id, "succeeded", user_id=user_id, finished_at=finished
        )
        _store.update_task(
            task_id,
            status="succeeded",
            finished_at=finished,
            final_message=answer,
        )
        _store.add_event(task_id, "completed", {"message": "subagent 完成", "result": answer})
    except CancellationRequested as exc:
        finished = time.time()
        event_store.update_turn_status(
            turn_id,
            "canceled",
            user_id=user_id,
            finished_at=finished,
            error_message=str(exc),
        )
        _store.update_task(
            task_id,
            status="canceled",
            finished_at=finished,
            error_message=str(exc),
        )
        _store.add_event(task_id, "canceled", {"message": str(exc)})
    except Exception as exc:
        finished = time.time()
        event_store.update_turn_status(
            turn_id,
            "failed",
            user_id=user_id,
            finished_at=finished,
            error_message=str(exc),
        )
        _store.update_task(
            task_id,
            status="failed",
            finished_at=finished,
            error_message=str(exc),
        )
        _store.add_event(task_id, "failed", {"message": "subagent 失败", "error": str(exc)})


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
    lease_check: Any | None = None,
    defer_project_unlock: bool = False,
    run_mode: str = "workspace",
) -> None:
    if not conversation_id:
        raise RuntimeError("任务缺少 conversation_id")

    task_meta = _store.get_task(task_id, user_id) or {}
    task_context = task_meta.get("context") if isinstance(task_meta.get("context"), dict) else {}
    feedback_options = task_context.get("feedback_options") or {"build_after_changes": bool(getattr(settings, "auto_build_after_edit", False))}
    if task_meta.get("parent_task_id") or task_meta.get("role"):
        _run_subagent_job_wrapper(
            task_id,
            user_id,
            project_id,
            conversation_id,
            turn_id,
            prompt,
            settings,
        )
        return

    event_store = ConversationEventStore(_store)
    _turn_row = event_store.get_turn(turn_id, user_id=user_id) or {}
    trace_id = _turn_row.get("trace_id")
    workspace = workspace_path(user_id, project_id)
    settings = copy.copy(settings)
    settings.provider_fallbacks = [copy.copy(item) for item in getattr(settings, "provider_fallbacks", [])]
    # Build once after edits instead of once per write, including fallbacks.
    for provider_settings in [settings, *settings.provider_fallbacks]:
        provider_settings.auto_build_after_edit = False
    before = snapshot_workspace(workspace)
    invocation_started = time.time()
    build_state = {"attempted": False, "succeeded": False}
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    edit_state: dict[str, Any] = {"successful_edits": 0, "approval_decisions": []}
    prior_events = event_store.list_turn_events(turn_id, user_id=user_id)
    resumed = any(event["event_type"] == EventType.TURN_STARTED for event in prior_events)
    applied_events: set[str] = set()

    def apply_evidence(event: dict[str, Any]) -> bool:
        identity = str(event.get("event_key") or event.get("id"))
        if identity in applied_events:
            return False
        applied_events.add(identity)
        event_type, payload = event["event_type"], event.get("payload") or {}
        if event_type == EventType.TOOL_RESULT:
            if payload.get("name") in {"write_file", "str_replace"} and payload.get("ok"):
                edit_state["successful_edits"] += 1
            if payload.get("name") == "run_gradle" and (payload.get("input") or {}).get("task", "assembleDebug") == "assembleDebug":
                build_state.update(attempted=True, succeeded=bool(payload.get("ok")))
        elif event_type == EventType.APPROVAL_RESOLVED and payload.get("decision"):
            edit_state["approval_decisions"].append(str(payload["decision"]))
        if event_type == EventType.USAGE:
            usage = payload.get("usage") or {}
            for key in token_usage:
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    token_usage[key] += value
        return True

    for prior_event in prior_events:
        apply_evidence(prior_event)
    before_checkpoint = None
    changes_recorded = False
    after_checkpoint_done = False
    after_checkpoint_status: dict[str, Any] = {"diff_status": "preparing"}

    def create_checkpoint(kind: str, idempotency_key: str) -> dict[str, Any] | None:
        try:
            repo = WorkspaceRepository(user_id, project_id, task_store=_store)
            cp = repo.create_checkpoint(
                kind,
                conversation_id=conversation_id,
                turn_id=turn_id,
                task_id=task_id,
                idempotency_key=idempotency_key,
            )
            _store.add_event(
                task_id,
                "checkpoint",
                {
                    "checkpoint_id": cp["id"],
                    "kind": kind,
                    "file_count": cp["file_count"],
                },
            )
            return cp
        except Exception as exc:
            logger.warning(
                "Checkpoint creation failed for %s/%s: %s", user_id, project_id, exc
            )
            return None

    def check_cancel() -> None:
        if lease_check is not None:
            lease_check()
        messages = _store.get_pending_messages(task_id, types=["cancel"])
        for msg in messages:
            _store.consume_message(msg["id"])
            cancel_gradle(user_id, project_id)
            reject_job_approvals(task_id, user_id, reason="canceled")
            raise CancellationRequested("用户已请求停止任务")
        if _store.is_cancel_requested(task_id):
            cancel_gradle(user_id, project_id)
            reject_job_approvals(task_id, user_id, reason="canceled")
            raise CancellationRequested("用户已请求停止任务")

    def check_pause() -> None:
        if lease_check is not None:
            lease_check()
        messages = _store.get_pending_messages(task_id, types=["pause"])
        for msg in messages:
            _store.consume_message(msg["id"])
            raise PauseRequested("任务已暂停")

    def get_steers() -> list[str]:
        return event_store.consume_steers(task_id, turn_id, user_id)

    def set_status(status: str) -> None:
        _store.update_task(task_id, status=status)
        if status in {"queued", "running", "awaiting_approval"}:
            event_store.update_turn_status(
                turn_id,
                status,
                user_id=user_id,
            )

    def append_canonical(
        event_type: str,
        payload: dict[str, Any],
        *,
        role: str | None = None,
        context_visible: bool = False,
        event_key: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload)
        if trace_id:
            payload.setdefault("trace_id", trace_id)
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
        if lease_check is not None:
            lease_check()
        payload = data if isinstance(data, dict) else {"message": str(data)}
        ui_payload = dict(payload)
        ui_payload.pop("model_output", None)
        ui_payload.pop("structured_output", None)
        # UI identity fields: the desktop must never have to guess which
        # turn/conversation a live task event belongs to.
        ui_payload.setdefault("task_id", task_id)
        ui_payload.setdefault("turn_id", turn_id)
        ui_payload.setdefault("conversation_id", conversation_id)
        if trace_id:
            ui_payload.setdefault("trace_id", trace_id)
        _store.add_event(task_id, event_type, ui_payload)

        canonical = None
        if event_type == EventType.ASSISTANT_MESSAGE:
            message_id = payload.get("message_id")
            if not message_id:
                raise RuntimeError("assistant_message 缺少 message_id")
            canonical = append_canonical(
                event_type,
                payload,
                role="assistant",
                context_visible=True,
                event_key=f"assistant:{message_id}",
            )
        elif event_type == EventType.TOOL_CALL and payload.get("tool_call_id"):
            canonical = append_canonical(
                event_type,
                payload,
                context_visible=True,
                event_key=f"tool_call:{payload['tool_call_id']}",
            )
        elif event_type == EventType.TOOL_RESULT and payload.get("tool_call_id"):
            if (payload.get("name") == "run_gradle"
                    and f"tool_result:{payload['tool_call_id']}" not in applied_events):
                # Never make a completed result durable while its latest
                # verification receipt is still missing. Log/APK enrichment
                # below is optional; this primary receipt is not.
                persist_gradle_receipt(_store, user_id, task_id, payload)
            canonical = append_canonical(
                event_type,
                payload,
                context_visible=True,
                event_key=f"tool_result:{payload['tool_call_id']}",
            )
            # Domain Core v1：run_gradle 的结构化结果落为独立摘要事件，
            # 客户端不再自行解析 gradle 日志。
            summary = payload.get("summary")
            if (
                payload.get("name") == "run_gradle"
                and isinstance(summary, dict)
                and summary.get("schema_version")
            ):
                summary_type = (
                    EventType.TEST_SUMMARY
                    if summary.get("kind") == "test"
                    else EventType.BUILD_SUMMARY
                )
                summary_payload = dict(summary)
                summary_payload["tool_call_id"] = payload["tool_call_id"]
                append_canonical(
                    summary_type,
                    summary_payload,
                    event_key=f"turn:{turn_id}:summary:{payload['tool_call_id']}",
                )
                _store.add_event(task_id, summary_type, summary_payload)
        elif (
            event_type == EventType.MALFORMED_TOOL_CALL
            and payload.get("tool_call_id")
        ):
            canonical = append_canonical(
                event_type,
                payload,
                context_visible=False,
                event_key=f"malformed_tool_call:{payload['tool_call_id']}",
            )
        elif event_type == EventType.APPROVAL_REQUIRED:
            approval_id = payload.get("approval_id")
            tool_call_id = payload.get("tool_call_id")
            if not approval_id or not tool_call_id:
                raise RuntimeError(
                    "approval_required 缺少 approval_id 或 tool_call_id"
                )
            canonical = append_canonical(
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
            canonical = append_canonical(
                event_type,
                payload,
                event_key=f"approval:{approval_id}:resolved",
            )
        elif event_type in {
            EventType.USAGE,
            EventType.PROVIDER_SWITCH,
            EventType.MODEL_SWITCH,
        }:
            canonical = append_canonical(
                event_type, payload,
                event_key=(f"usage:{payload['message_id']}"
                           if event_type == EventType.USAGE and payload.get("message_id") else None),
            )
        elif event_type in {"subagent_spawned", "subagent_completed"} and payload.get("child_task_id"):
            canonical = append_canonical(
                EventType.SYSTEM_NOTE,
                {**payload, "agent_event": event_type},
                context_visible=False,
                event_key=f"subagent:{payload['child_task_id']}:{event_type}",
            )
        elif event_type == EventType.SYSTEM_NOTE:
            kind = payload.get("kind") or "note"
            canonical = append_canonical(
                event_type,
                payload,
                context_visible=False,
                event_key=f"system_note:{turn_id}:{kind}:{payload.get('skill') or 'rules'}",
            )

        if canonical is not None and apply_evidence(canonical):
            if event_type == EventType.TOOL_RESULT and payload.get("name") == "run_gradle":
                try:
                    capture_gradle_result(_store, user_id, project_id, task_id, payload, invocation_started)
                except (OSError, ValueError) as exc:
                    logger.warning("Build report unavailable for %s: %s", task_id, exc)
            if event_type == EventType.USAGE:
                _store.update_task(task_id, **token_usage)

    def current_changes() -> tuple[list[dict[str, Any]], str]:
        if before_checkpoint:
            repo = WorkspaceRepository(user_id, project_id, task_store=_store)
            result = repo.checkpoint_diff(before_checkpoint["id"])
            if not result.get("ok"):
                raise RuntimeError("本轮初始快照不可用，无法确认完整改动")
            return ([{"path": item["path"], "change": item["change"]}
                     for item in result["files"]], result["diff"])
        if resumed:
            raise RuntimeError("恢复任务缺少初始快照，无法确认完整改动")
        return compare_snapshots(workspace, before, snapshot_workspace(workspace))

    def record_changes(*, final: bool = True) -> list[dict[str, Any]]:
        nonlocal changes_recorded
        if changes_recorded:
            task = _store.get_task(task_id, user_id) or {}
            return list(task.get("changed_files") or [])
        changed, diff = current_changes()
        _store.update_task(task_id, changed_files=changed, diff=diff)
        stats = diff_stats(diff)
        _store.add_event(
            task_id,
            EventType.CHANGES,
            {"message": f"改动 {len(changed)} 个文件", "files": changed, **stats},
        )
        if final:
            append_canonical(
                EventType.CHANGES,
                {"files": changed, **stats},
                event_key=f"turn:{turn_id}:changes:final",
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

    def ensure_after_checkpoint() -> dict[str, Any]:
        """Create the after_turn checkpoint at most once and report diff readiness.

        This MUST run before the terminal lifecycle event becomes observable
        so the desktop can open an accurate diff review immediately after the
        turn reaches its terminal state.  Failures are never swallowed
        silently: the terminal payloads carry ``diff_status=unavailable`` plus
        a reason, and a diagnostic record is written.
        """
        nonlocal after_checkpoint_done
        if after_checkpoint_done:
            return dict(after_checkpoint_status)
        after_checkpoint_done = True
        from agent.workspace_inputs import capture_workspace_inputs
        saved_context = (_store.get_task(task_id, user_id) or {}).get("context") or {}
        _store.update_task(task_id, context={**saved_context, "verification_final_inputs": capture_workspace_inputs(workspace)})
        cp = create_checkpoint("after_turn", idempotency_key=f"after:{turn_id}:final")
        if cp and before_checkpoint:
            after_checkpoint_status.clear()
            after_checkpoint_status.update(
                {"diff_status": "ready", "after_checkpoint_id": cp["id"]}
            )
        else:
            after_checkpoint_status.clear()
            after_checkpoint_status.update(
                {
                    "diff_status": "unavailable",
                    "diff_reason": ("before_turn checkpoint 不可用" if not before_checkpoint
                                    else "after_turn checkpoint 创建失败"),
                }
            )
            try:
                from agent.diagnostics import get_diagnostic_store

                get_diagnostic_store(_store.db_path).record(
                    "jobs",
                    "after_turn_checkpoint_failed",
                    "after_turn checkpoint 创建失败，改动审查不可用",
                    severity="error",
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    turn_id=turn_id,
                )
            except Exception:
                logger.exception(
                    "Failed to record checkpoint diagnostic for %s", task_id
                )
        return dict(after_checkpoint_status)

    def mark_failed(exc: Exception) -> None:
        error = str(exc)
        diff_state = ensure_after_checkpoint() if not lease_lost else {}
        try:
            failed_at = time.time()
            event_store.finalize_lifecycle(
                conversation_id=conversation_id,
                turn_id=turn_id,
                task_id=task_id,
                user_id=user_id,
                event_type=EventType.TURN_FAILED,
                event_key=f"turn:{turn_id}:failed",
                event_payload={"error": error, **diff_state},
                status="failed",
                finished_at=failed_at,
                error_message=error,
                task_event_type="failed",
                task_event_payload={
                    "message": "任务失败",
                    "error": error,
                    **diff_state,
                },
            )
        except Exception as terminal_exc:
            from agent.diagnostics import get_diagnostic_store

            diagnostic = (
                f"任务失败且生命周期原子提交失败: {terminal_exc}"
            )
            get_diagnostic_store(_store.db_path).record(
                "jobs",
                "finalize_failed_task",
                diagnostic,
                severity="error",
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                turn_id=turn_id,
            )
            _store.update_task(
                task_id,
                status="failed",
                finished_at=time.time(),
                error_message=f"{error}; {diagnostic}",
            )

    answer = ""
    lease_lost = False
    try:
        with _lock:
            _project_locks.add((user_id, project_id))
        started_at = time.time()
        event_store.start_lifecycle(
            conversation_id=conversation_id,
            turn_id=turn_id,
            task_id=task_id,
            user_id=user_id,
            project_id=project_id,
            provider=settings.provider,
            model=settings.model,
            started_at=started_at,
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
        if resumed:
            before_checkpoint = WorkspaceRepository(
                user_id, project_id, task_store=_store
            ).get_checkpoint(f"before:{turn_id}")
        else:
            before_checkpoint = create_checkpoint(
                "before_turn", idempotency_key=f"before:{turn_id}"
            )
        check_cancel()
        check_pause()
        from agent.explicit_context import build_context_bundle

        context_budget_chars = int(getattr(settings, "max_prompt_chars", 100_000))
        try:
            context_bundle = build_context_bundle(
                user_id,
                project_id,
                prompt,
                task_context.get("attachments") or [],
                _store,
                include_automatic=True,
                task_id=task_id,
                budget_chars=context_budget_chars,
            )
        except Exception as context_exc:
            logger.warning("Context planning skipped for %s: %s", task_id, context_exc)
            context_bundle = {
                "explicit": [],
                "automatic": [],
                "memory": [],
                "memory_count": 0,
                "symbol_count": 0,
                "total_tokens": 0,
                "budget_tokens": max(1, context_budget_chars // 4),
                "model_context": "",
            }
        context_summary = {
            key: context_bundle[key]
            for key in (
                "explicit",
                "automatic",
                "memory",
                "memory_count",
                "symbol_count",
                "total_tokens",
                "budget_tokens",
            )
        }
        _store.update_task(task_id, context={**task_context, "summary": context_summary})
        _store.add_event(
            task_id,
            "context_plan",
            {
                "message": f"上下文 {context_bundle['total_tokens']} / {context_bundle['budget_tokens']} tokens",
                **context_summary,
            },
        )
        answer = "构建验证完成" if task_context.get("feedback_requested") else run_agent(
            settings,
            workspace,
            user_id,
            project_id,
            prompt,
            on_event=on_event,
            cancel_check=check_cancel,
            check_pause=check_pause,
            get_steers=get_steers,
            task_id=task_id,
            set_status=set_status,
            conversation_events=history_events,
            get_conversation_events=lambda: event_store.list_events(conversation_id, user_id=user_id),
            turn_id=turn_id,
            recovery_replays=recovery_replays,
            recovery_mode=recovery_mode,
            run_mode=run_mode,
            permission_profile=task_context.get("permission_profile"),
            extra_system_prompt=context_bundle.get("model_context") or None,
        )
        check_cancel()
        check_pause()

        def automatic_gradle(gradle_task: str) -> bool:
            check_cancel()
            check_pause()
            message_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"android-agent:turn:{turn_id}:feedback:{uuid.uuid4().hex}",
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
                    "input": {"task": gradle_task, "auto": True},
                },
            )
            auto_started = time.monotonic()
            try:
                result = dispatch_agent_tool(
                    workspace,
                    user_id,
                    project_id,
                    "run_gradle",
                    {"task": gradle_task},
                    cancel_check=check_cancel,
                    settings=settings,
                    on_event=on_event,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    set_status=set_status,
                    recovery_replays=recovery_replays,
                    recovery_mode=recovery_mode,
                    run_mode=run_mode,
                    permission_profile=task_context.get("permission_profile"),
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
                        "input": {"task": gradle_task, "auto": True},
                        "preview": str(exc),
                    },
                )
                raise
            except (ApprovalEventPersistenceError, PauseRequested, TaskLeaseLost):
                raise
            except Exception as exc:
                result = ToolResult(
                    False,
                    f"工具 run_gradle 执行异常: {exc}",
                    error_type=exc.__class__.__name__,
                )
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
                    "summary": result.summary,
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
                        result.error_type
                        if result.error_type
                        else None if result.ok else "ToolExecutionError"
                    ),
                    "interrupted": False,
                    "input": {"task": gradle_task, "auto": True},
                    "preview": model_output[:2000],
                },
            )

            if result.error_type in {"ApprovalDenied", "ApprovalCanceled", "PermissionDenied", "HookDenied", "PermissionError"}:
                raise RuntimeError(model_output)
            return result.ok

        options = feedback_options
        changes_now, _ = current_changes()

        def fix_feedback(attempt: int, failed_task: str) -> None:
            nonlocal answer
            current_context = (_store.get_task(task_id, user_id) or {}).get("context") or {}
            attempt = int(current_context.get("feedback_fix_attempts") or 0) + 1
            if attempt > 2:
                raise RuntimeError("已达到本轮自动修复上限 2 次，请查看 Problems")
            _store.update_task(task_id, context={**current_context, "feedback_fix_attempts": attempt})
            _store.add_event(task_id, "feedback", {"message": f"自动修复 {attempt}/2 · {failed_task}", "attempt": attempt})
            report = feedback_summary(_store, user_id, project_id, task_id)
            failures = "\n".join(p["message"] for p in report["problems"])[:24_000]
            answer = run_agent(
                settings, workspace, user_id, project_id,
                f"修复本轮 {failed_task} 失败（第 {attempt}/2 轮）。保留用户改动，只修复失败原因。\n{failures}",
                on_event=on_event, cancel_check=check_cancel, check_pause=check_pause,
                get_steers=get_steers, task_id=task_id, set_status=set_status,
                conversation_events=event_store.list_events(conversation_id, user_id=user_id),
                get_conversation_events=lambda: event_store.list_events(conversation_id, user_id=user_id),
                # run_agent uses this value to namespace deterministic message IDs;
                # on_event still persists every event under the original turn.
                turn_id=f"{turn_id}:feedback:{attempt}", recovery_replays=recovery_replays,
                recovery_mode=recovery_mode, run_mode=run_mode,
                permission_profile=task_context.get("permission_profile"),
                extra_system_prompt=context_bundle.get("model_context") or None,
            )

        if (task_context.get("feedback_requested")
                or (options.get("build_after_changes") and changes_now)
                or (build_state["succeeded"] and options.get("run_tests"))
                or (build_state["attempted"] and not build_state["succeeded"] and options.get("fix_failures"))):
            run_feedback_cycle(options, automatic_gradle, fix_feedback, check_cancel)

        # A successful build verifies the source revision it actually built.
        # Pausing releases the workspace writer, so another task may edit it.
        saved_job = _store.get_task(task_id, user_id) or {}
        build_runs = [run for run in (saved_job.get("context") or {}).get("feedback_runs", [])
                      if run.get("task") == "assembleDebug"]
        if build_state["succeeded"] and build_runs:
            from agent.workspace_inputs import capture_workspace_inputs, compare_workspace_inputs
            receipt = build_runs[-1].get("verification_receipt") or {}
            after_inputs = receipt.get("inputs_after")
            if "changed" in (compare_workspace_inputs(receipt.get("inputs_before"), after_inputs),
                              compare_workspace_inputs(after_inputs, capture_workspace_inputs(workspace))):
                _store.update_task(task_id, apk_path=None)
                raise RuntimeError("代码在最后一次成功构建后已变化，请重新构建以验证当前版本")
        # Relaxed gate: only fail if gradle was attempted and failed
        if build_state["attempted"] and not build_state["succeeded"]:
            raise RuntimeError("assembleDebug 未成功，请查看构建日志和已尝试的修复")

        saved_job = _store.get_task(task_id, user_id) or {}
        task_apk = None
        if build_state["succeeded"] and saved_job.get("apk_path"):
            candidate = Path(saved_job["apk_path"])
            expected = user_builds_dir(user_id) / project_id / f"{task_id}.apk"
            if candidate == expected and candidate.is_file():
                task_apk = candidate
        if task_apk is not None and task_apk.is_file():
            artifact_payload: dict[str, Any] = {
                "kind": "apk",
                "path": task_apk.name,
                "size_bytes": task_apk.stat().st_size,
                "url": f"/api/jobs/{task_id}/apk",
                "schema_version": 1,
            }
            _store.add_event(task_id, EventType.ARTIFACT, artifact_payload)
            append_canonical(
                EventType.ARTIFACT,
                artifact_payload,
                event_key=f"turn:{turn_id}:artifact:apk",
            )

        logs = sorted(
            (user_builds_dir(user_id) / project_id).glob("*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        # Snapshot early so honesty check can use real disk changes
        changed_preview, _diff_preview = current_changes()
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
        try:
            from agent.hooks import run_hooks

            run_hooks(
                "TurnCompleted",
                user_id=user_id,
                workspace=workspace,
                on_event=on_event,
            )
        except Exception as hook_exc:
            from agent.diagnostics import get_diagnostic_store

            get_diagnostic_store(_store.db_path).record(
                "hooks",
                "TurnCompleted",
                str(hook_exc),
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                turn_id=turn_id,
            )
        # after_turn checkpoint MUST exist before the terminal lifecycle event
        # is published, otherwise the desktop may offer "review changes" before
        # the diff data is ready.
        diff_state = ensure_after_checkpoint()
        try:
            from agent.memory_extract import generate_candidates_for_turn
            from agent.memory_store import get_memory_store
            import agent.paths as paths_mod

            turn_events = event_store.list_turn_events(turn_id, user_id=user_id)
            mem_store = get_memory_store(paths_mod.DATA_DIR / "agent.db")
            candidates = generate_candidates_for_turn(
                user_id=user_id,
                project_id=project_id,
                conversation_id=conversation_id,
                events=turn_events,
                user_prompt=prompt,
                final_answer=answer or "",
                changed_files=changed,
                store=mem_store,
            )
            if candidates:
                _store.add_event(
                    task_id,
                    "memory_candidates",
                    {
                        "message": f"生成 {len(candidates)} 条记忆候选",
                        "count": len(candidates),
                        "ids": [c["id"] for c in candidates],
                    },
                )
                on_event(
                    "memory_candidates",
                    {
                        "message": f"生成 {len(candidates)} 条记忆候选（需批准）",
                        "count": len(candidates),
                        "ids": [c["id"] for c in candidates],
                    },
                )
        except Exception as mem_exc:
            logger.warning("Memory candidate generation failed: %s", mem_exc)
        recent_logs = [p for p in logs if p.stat().st_mtime >= invocation_started]
        attached_log = _pick_task_log(recent_logs)
        saved_log = saved_job.get("build_log_path")
        if saved_log and Path(saved_log).is_file():
            attached_log = Path(saved_log)
        event_store.finalize_lifecycle(
            conversation_id=conversation_id,
            turn_id=turn_id,
            task_id=task_id,
            user_id=user_id,
            event_type=EventType.TURN_COMPLETED,
            event_key=f"turn:{turn_id}:completed",
            event_payload={"status": "succeeded", "result": answer, **diff_state},
            status="succeeded",
            finished_at=completed_at,
            final_message=answer,
            apk_path=str(task_apk) if task_apk else None,
            build_log_path=str(attached_log) if attached_log else None,
            task_event_type="completed",
            task_event_payload={"message": "本轮完成", "result": answer, **diff_state},
        )
    except TaskLeaseLost:
        lease_lost = True
        raise
    except CancellationRequested as exc:
        try:
            record_changes()
            diff_state = ensure_after_checkpoint()
            canceled_at = time.time()
            event_store.finalize_lifecycle(
                conversation_id=conversation_id,
                turn_id=turn_id,
                task_id=task_id,
                user_id=user_id,
                event_type=EventType.TURN_CANCELED,
                event_key=f"turn:{turn_id}:canceled",
                event_payload={"error": str(exc), **diff_state},
                status="canceled",
                finished_at=canceled_at,
                error_message=str(exc),
                task_event_type="canceled",
                task_event_payload={"message": str(exc), **diff_state},
            )
        except Exception as terminal_exc:
            mark_failed(
                RuntimeError(
                    f"取消任务时规范事件写入失败: {terminal_exc}"
                )
            )
    except PauseRequested as exc:
        record_changes(final=False)
        paused_at = time.time()
        event_store.update_turn_status(
            turn_id,
            "paused",
            user_id=user_id,
            finished_at=paused_at,
            error_message=str(exc),
        )
        _store.add_event(task_id, "paused", {"message": str(exc)})
        raise
    except Exception as exc:
        try:
            record_changes()
        except Exception as changes_exc:
            exc = RuntimeError(f"{exc}; changes 写入失败: {changes_exc}")
        mark_failed(exc)
    finally:
        if not lease_lost:
            try:
                from agent.hooks import run_hooks

                run_hooks(
                    "TaskStopped",
                    user_id=user_id,
                    workspace=workspace,
                    on_event=on_event,
                )
            except Exception as hook_exc:
                from agent.diagnostics import get_diagnostic_store

                get_diagnostic_store(_store.db_path).record(
                    "hooks",
                    "TaskStopped",
                    str(hook_exc),
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    turn_id=turn_id,
                )
            logs = sorted(
                (user_builds_dir(user_id) / project_id).glob("*.log"),
                key=lambda path: path.stat().st_mtime,
            )
            recent = [p for p in logs if p.stat().st_mtime >= invocation_started]
            chosen = _pick_task_log(recent)
            bound_log = (_store.get_task(task_id, user_id) or {}).get("build_log_path")
            if bound_log and Path(bound_log).is_file():
                chosen = Path(bound_log)
            if chosen:
                _store.update_task(task_id, build_log_path=str(chosen))
            keep = int(
                getattr(settings, "max_build_artifacts_per_project", 50)
            )
            artifact_dir = user_builds_dir(user_id) / project_id
            prune_old_files(artifact_dir, "*.log", keep=keep)
            prune_old_files(artifact_dir, "*.apk", keep=keep)
        if not defer_project_unlock:
            _release_project_lock(user_id, project_id)


def wait_for_job(job_id: str, timeout: float | None = None) -> dict[str, Any] | None:
    deadline = None if timeout is None else time.time() + timeout
    while True:
        job = get_job(job_id)
        if not job or job["status"] in {"succeeded", "failed", "canceled"}:
            return job
        if deadline is not None and time.time() >= deadline:
            return job
        time.sleep(0.2)
