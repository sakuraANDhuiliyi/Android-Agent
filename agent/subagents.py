from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from agent.conversation_events import ConversationEventStore
from agent.conversation_events import ConversationEventType as EventType
from agent.database import TaskStore
from agent.paths import workspace_path
from agent.project_lifecycle import project_operation
from agent.redaction import redact_sensitive_value
from agent.subagent_roles import (
    DEFAULT_MAX_SUBAGENTS,
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    get_role,
)
from agent.worktrees import (
    WorktreeInfo,
    build_diff_artifact,
    create_worktree,
    finalize_worktree,
    load_worktree,
    worktree_has_changes,
)


EventCallback = Callable[[str, Any], None]

_store: TaskStore | None = None


def configure_subagent_store(store: TaskStore | None) -> None:
    global _store
    _store = store


def _task_store() -> TaskStore:
    if _store is not None:
        return _store
    from agent import jobs

    return jobs._store  # noqa: SLF001


def spawn_subagent(
    *,
    user_id: str,
    project_id: str,
    parent_task_id: str,
    role_name: str,
    prompt: str,
    depends_on: list[str] | None = None,
    base_revision: str | None = None,
    settings: Any = None,
    on_event: EventCallback | None = None,
    max_children: int = DEFAULT_MAX_SUBAGENTS,
) -> dict[str, Any]:
    with project_operation(user_id, project_id):
        return _spawn_subagent_unlocked(
            user_id=user_id,
            project_id=project_id,
            parent_task_id=parent_task_id,
            role_name=role_name,
            prompt=prompt,
            depends_on=depends_on,
            base_revision=base_revision,
            settings=settings,
            on_event=on_event,
            max_children=max_children,
        )


def _spawn_subagent_unlocked(
    *,
    user_id: str,
    project_id: str,
    parent_task_id: str,
    role_name: str,
    prompt: str,
    depends_on: list[str] | None = None,
    base_revision: str | None = None,
    settings: Any = None,
    on_event: EventCallback | None = None,
    max_children: int = DEFAULT_MAX_SUBAGENTS,
) -> dict[str, Any]:
    """Create a child task. Main-agent only (caller must enforce)."""
    store = _task_store()
    parent = store.get_task(parent_task_id, user_id)
    if not parent:
        raise ValueError(f"父任务不存在: {parent_task_id}")
    if parent.get("parent_task_id"):
        raise PermissionError("Subagent 不能再创建 Subagent")
    if parent.get("role"):
        raise PermissionError("Subagent 不能再创建 Subagent")

    active = store.count_active_children(parent_task_id)
    if active >= max_children:
        raise RuntimeError(f"已达到并行 Subagent 上限 ({max_children})")

    role = get_role(role_name)
    workspace = workspace_path(user_id, project_id)
    worktree: WorktreeInfo | None = None
    worktree_id: str | None = None
    child_workspace = str(workspace)

    if role.isolation == "worktree":
        from agent.project import load_project_meta

        meta = load_project_meta(user_id, project_id)
        repo_root = Path(meta["repo_root"]) if meta.get("repo_root") else workspace
        worktree = create_worktree(
            user_id,
            project_id,
            workspace,
            base_revision=base_revision,
            repo_root=repo_root,
        )
        worktree_id = worktree.id
        child_workspace = str(worktree.path)

    write_lock = role.write_lock_key(user_id, project_id, worktree_id)
    # Main parent holds main lock; children use their own keys / None.

    child_id = uuid.uuid4().hex[:12]
    conv = store.create_conversation(
        user_id,
        project_id,
        title=f"subagent:{role.name}:{child_id}",
    )
    conversation_id = conv["id"]
    created_at = time.time()
    context = {
        "role": role.name,
        "permission_mode": role.permission_mode,
        "isolation": role.isolation,
        "allowed_tools": list(role.allowed_tools),
        "max_turns": role.max_turns,
        "context_budget_chars": role.context_budget_chars,
        "workspace_path": child_workspace,
        "worktree_id": worktree_id,
        "write_lock_key": write_lock,
        "parent_task_id": parent_task_id,
        "system_prompt": role.system_prompt,
        "summary_only": True,
    }
    provider = (settings.provider if settings else None) or parent.get("provider")
    model = role.model or (settings.model if settings else None) or parent.get("model")

    store.create_task(
        {
            "id": child_id,
            "user_id": user_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "prompt": prompt,
            "status": "queued",
            "provider": provider,
            "model": model,
            "created_at": created_at,
            "parent_task_id": parent_task_id,
            "role": role.name,
            "write_lock_key": write_lock,
            "context": context,
        }
    )
    for dep in depends_on or []:
        store.add_dependency(child_id, dep)

    event_store = ConversationEventStore(store)
    turn = event_store.create_turn(
        conversation_id,
        user_id,
        project_id,
        task_id=child_id,
        status="queued",
        provider=provider,
        model=model,
        created_at=created_at,
    )
    message_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"android-agent:turn:{turn['id']}:user_message",
    ).hex
    event_store.append_event_idempotent(
        conversation_id,
        turn["id"],
        EventType.USER_MESSAGE,
        f"turn:{turn['id']}:user_message",
        {
            "message_id": message_id,
            "content": [{"type": "text", "text": prompt}],
            "source": "parent_agent",
        },
        role="user",
        context_visible=True,
        task_id=child_id,
    )
    store.add_event(
        child_id,
        "subagent_spawned",
        redact_sensitive_value(
            {
                "message": f"spawned {role.name}",
                "parent_task_id": parent_task_id,
                "role": role.name,
                "worktree_id": worktree_id,
            }
        ),
    )
    if on_event:
        on_event(
            "subagent_spawned",
            {
                "message": f"spawned subagent {role.name}",
                "child_task_id": child_id,
                "role": role.name,
                "worktree_id": worktree_id,
            },
        )

    from agent import jobs

    jobs.ensure_worker_started()

    return {
        "ok": True,
        "child_task_id": child_id,
        "turn_id": turn["id"],
        "conversation_id": conversation_id,
        "role": role.name,
        "worktree_id": worktree_id,
        "workspace_path": child_workspace,
        "write_lock_key": write_lock,
        "status": "queued",
    }


def get_subagent(child_task_id: str, user_id: str) -> dict[str, Any]:
    store = _task_store()
    task = store.get_task(child_task_id, user_id)
    if not task:
        raise FileNotFoundError(f"subagent 任务不存在: {child_task_id}")
    ctx = task.get("context") or {}
    summary = ctx.get("summary") or _extract_summary(task)
    artifacts = ctx.get("artifacts") or []
    return {
        "ok": True,
        "child_task_id": child_task_id,
        "parent_task_id": task.get("parent_task_id"),
        "role": task.get("role") or ctx.get("role"),
        "status": task["status"],
        "summary": summary,
        "artifacts": artifacts,
        "error_message": task.get("error_message"),
        "worktree_id": ctx.get("worktree_id"),
        "final_message": task.get("final_message"),
        # Intentionally omit raw conversation events / tool dumps.
    }


def wait_subagents(
    child_ids: list[str],
    user_id: str,
    *,
    timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    poll_interval: float = 0.05,
) -> dict[str, Any]:
    deadline = time.time() + max(0.1, timeout_seconds)
    remaining = list(child_ids)
    results: list[dict[str, Any]] = []
    store = _task_store()
    # The global worker pool executes children concurrently. Tests and embedded
    # callers without that pool get a temporary bounded pool.
    from agent import jobs
    from agent.config import load_settings
    from agent.worker import TaskWorker

    worker = getattr(jobs, "_worker", None)
    local_workers: list[TaskWorker] = []
    if worker is None:
        for _ in range(min(DEFAULT_MAX_SUBAGENTS, max(1, len(child_ids)))):
            local = TaskWorker(store, jobs._run_job, load_settings())
            local.start()
            local_workers.append(local)

    try:
        while remaining and time.time() < deadline:
            still: list[str] = []
            for cid in remaining:
                task = store.get_task(cid, user_id)
                if not task:
                    results.append({"child_task_id": cid, "status": "missing", "ok": False})
                    continue
                if task["status"] in {"succeeded", "failed", "canceled"}:
                    results.append(get_subagent(cid, user_id))
                else:
                    still.append(cid)
            remaining = still
            if remaining:
                time.sleep(poll_interval)
    finally:
        for local in local_workers:
            local.stop(wait=True, timeout=2.0)
    for cid in remaining:
        info = get_subagent(cid, user_id)
        info["timed_out"] = True
        results.append(info)
    by_id = {r["child_task_id"]: r for r in results}
    ordered = [by_id[cid] for cid in child_ids if cid in by_id]
    return {"ok": True, "results": ordered}


def _extract_summary(task: dict[str, Any]) -> dict[str, Any]:
    ctx = task.get("context") or {}
    if isinstance(ctx.get("summary"), dict):
        return ctx["summary"]
    text = task.get("final_message") or ""
    return {
        "text": text[:2000],
        "status": task.get("status"),
        "changed_files": task.get("changed_files") or [],
    }


def run_subagent_job(
    task_id: str,
    user_id: str,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    prompt: str,
    settings: Any,
    *,
    on_event: EventCallback | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> str:
    """Execute a restricted subagent turn and persist its bounded summary."""
    store = _task_store()
    task = store.get_task(task_id, user_id) or {}
    ctx = dict(task.get("context") or {})
    role_name = task.get("role") or ctx.get("role") or "explore"
    role = get_role(role_name)
    workspace = Path(ctx.get("workspace_path") or workspace_path(user_id, project_id))

    if cancel_check:
        cancel_check()

    summary = _execute_subagent_agent(
        settings,
        workspace,
        user_id,
        project_id,
        prompt,
        role_name=role.name,
        max_turns=role.max_turns,
        on_event=on_event,
        cancel_check=cancel_check,
        task_id=task_id,
        turn_id=turn_id,
    )
    return _finalize_subagent(
        store,
        task_id,
        user_id,
        project_id,
        conversation_id,
        turn_id,
        role.name,
        workspace,
        ctx,
        summary,
        on_event=on_event,
    )


def _execute_subagent_agent(
    settings: Any,
    workspace: Path,
    user_id: str,
    project_id: str,
    prompt: str,
    *,
    role_name: str,
    max_turns: int,
    on_event: EventCallback | None,
    cancel_check: Callable[[], None] | None,
    task_id: str,
    turn_id: str,
) -> dict[str, Any]:
    """Run the restricted child loop; tests replace this function with a fake."""
    from dataclasses import replace

    from agent.loop import run_agent

    limited = settings
    try:
        limited = replace(settings, max_turns=max_turns, max_auto_continuations=0)
    except (TypeError, ValueError):
        pass

    answer = run_agent(
        limited,
        workspace,
        user_id,
        project_id,
        prompt,
        on_event=on_event,
        cancel_check=cancel_check,
        task_id=task_id,
        conversation_events=[],
        turn_id=turn_id,
        allowed_tools=frozenset(role.allowed_tools),
        extra_system_prompt=(
            f"你是受限 Subagent，角色为 {role.name}。\n"
            f"{role.system_prompt}\n"
            "只能使用已提供的工具；不得尝试越权、创建子 Agent 或扩大任务范围。"
        ),
        run_mode=role.permission_mode,
    )
    return {
        "text": (answer or "")[:2000],
        "role": role_name,
        "changed_files": [],
    }


def _finalize_subagent(
    store: TaskStore,
    task_id: str,
    user_id: str,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    role: str,
    workspace: Path,
    ctx: dict[str, Any],
    summary: dict[str, Any],
    *,
    on_event: EventCallback | None,
) -> str:
    artifacts: list[dict[str, Any]] = list(summary.get("artifacts") or [])
    worktree_id = ctx.get("worktree_id")
    if worktree_id:
        info = load_worktree(user_id, project_id, worktree_id)
        if info:
            diff_text = build_diff_artifact(info)
            has_changes = worktree_has_changes(info) or bool(diff_text.strip())
            artifacts.append(
                {
                    "type": "worktree_diff",
                    "worktree_id": worktree_id,
                    "path": info.diff_artifact,
                    "has_changes": has_changes,
                }
            )
            summary["has_changes"] = has_changes
            summary["worktree_id"] = worktree_id
            if not has_changes:
                finalize_worktree(info, "discard")
                summary["worktree_action"] = "auto_cleaned"
            else:
                summary["worktree_action"] = "awaiting_decision"
                summary["pending_finalize"] = True

    ctx = dict(ctx)
    ctx["summary"] = {
        "text": summary.get("text") or summary.get("message") or "",
        "role": role,
        "changed_files": summary.get("changed_files") or [],
        "has_changes": summary.get("has_changes"),
        "worktree_id": worktree_id,
        "worktree_action": summary.get("worktree_action"),
        "findings": summary.get("findings") or [],
    }
    ctx["artifacts"] = artifacts

    text = ctx["summary"]["text"] or f"[{role}] completed"
    store.update_task(
        task_id,
        context=ctx,
        final_message=text,
        changed_files=summary.get("changed_files") or [],
    )
    store.add_event(
        task_id,
        "subagent_completed",
        redact_sensitive_value(
            {
                "message": "subagent completed",
                "summary": ctx["summary"],
                "artifacts": artifacts,
            }
        ),
    )
    if on_event:
        on_event(
            "subagent_completed",
            {
                "message": "subagent completed",
                "child_task_id": task_id,
                "summary": ctx["summary"],
            },
        )
    return text


def resolve_worktree_decision(
    user_id: str,
    project_id: str,
    worktree_id: str,
    action: str,
) -> dict[str, Any]:
    info = load_worktree(user_id, project_id, worktree_id)
    if info is None:
        raise FileNotFoundError(f"worktree 不存在: {worktree_id}")
    if action not in {"merge", "keep", "discard"}:
        raise ValueError(f"无效 action: {action}")
    return finalize_worktree(info, action)  # type: ignore[arg-type]
