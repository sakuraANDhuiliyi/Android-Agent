from __future__ import annotations

from typing import Any

from agent.subagent_roles import DEFAULT_MAX_SUBAGENTS, DEFAULT_WAIT_TIMEOUT_SECONDS, list_roles
from agent.subagents import get_subagent, resolve_worktree_decision, spawn_subagent, wait_subagents
from agent.tool_registry import ToolSpec, register_tool
from agent.tool_runtime import ToolContext


def _require_main_agent(ctx: ToolContext) -> None:
    """Subagents cannot nest."""
    if not ctx.task_id:
        raise PermissionError("spawn_subagent 只能由主 Agent 任务调用")
    from agent import jobs

    task = jobs._store.get_task(ctx.task_id, ctx.user_id)  # noqa: SLF001
    if not task:
        raise PermissionError("找不到当前任务")
    if task.get("parent_task_id") or task.get("role"):
        raise PermissionError("Subagent 不能再创建 Subagent")


def _tool_result(ok: bool, payload: dict[str, Any]):
    from agent.tools import ToolResult

    return ToolResult(ok, payload)


def _handle_spawn_subagent(ctx: ToolContext, tool_input: dict[str, Any]):
    try:
        _require_main_agent(ctx)
    except PermissionError as exc:
        return _tool_result(False, {"ok": False, "error": str(exc), "error_type": "NoNesting"})

    role = str(tool_input.get("role") or "").strip()
    prompt = str(tool_input.get("prompt") or "").strip()
    if not role or not prompt:
        return _tool_result(False, {"ok": False, "error": "role 与 prompt 必填"})
    depends_on = tool_input.get("depends_on") or []
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    try:
        result = spawn_subagent(
            user_id=ctx.user_id,
            project_id=ctx.project_id,
            parent_task_id=ctx.task_id or "",
            role_name=role,
            prompt=prompt,
            depends_on=list(depends_on),
            base_revision=tool_input.get("base_revision"),
            settings=ctx.settings,
            on_event=ctx.on_event,
            max_children=int(tool_input.get("max_children") or DEFAULT_MAX_SUBAGENTS),
        )
    except Exception as exc:
        return _tool_result(False, {"ok": False, "error": str(exc)})
    return _tool_result(True, result)


def _handle_get_subagent(ctx: ToolContext, tool_input: dict[str, Any]):
    try:
        _require_main_agent(ctx)
    except PermissionError as exc:
        return _tool_result(False, {"ok": False, "error": str(exc), "error_type": "NoNesting"})
    child_id = str(tool_input.get("child_task_id") or "").strip()
    if not child_id:
        return _tool_result(False, {"ok": False, "error": "缺少 child_task_id"})
    try:
        info = get_subagent(child_id, ctx.user_id)
    except Exception as exc:
        return _tool_result(False, {"ok": False, "error": str(exc)})
    # Parent only receives summary + artifact refs — never raw tool dumps.
    return _tool_result(True, info)


def _handle_wait_subagents(ctx: ToolContext, tool_input: dict[str, Any]):
    try:
        _require_main_agent(ctx)
    except PermissionError as exc:
        return _tool_result(False, {"ok": False, "error": str(exc), "error_type": "NoNesting"})
    ids = tool_input.get("child_task_ids") or tool_input.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    if not ids:
        return _tool_result(False, {"ok": False, "error": "缺少 child_task_ids"})
    timeout = float(tool_input.get("timeout_seconds") or DEFAULT_WAIT_TIMEOUT_SECONDS)
    try:
        result = wait_subagents(
            list(ids),
            ctx.user_id,
            timeout_seconds=timeout,
        )
    except Exception as exc:
        return _tool_result(False, {"ok": False, "error": str(exc)})
    return _tool_result(True, result)


def _handle_finalize_worktree(ctx: ToolContext, tool_input: dict[str, Any]):
    try:
        _require_main_agent(ctx)
    except PermissionError as exc:
        return _tool_result(False, {"ok": False, "error": str(exc), "error_type": "NoNesting"})
    worktree_id = str(tool_input.get("worktree_id") or "").strip()
    action = str(tool_input.get("action") or "").strip()
    if not worktree_id or action not in {"merge", "keep", "discard"}:
        return _tool_result(
            False,
            {"ok": False, "error": "需要 worktree_id 与 action=merge|keep|discard"},
        )
    try:
        result = resolve_worktree_decision(
            ctx.user_id, ctx.project_id, worktree_id, action
        )
    except Exception as exc:
        return _tool_result(False, {"ok": False, "error": str(exc)})
    return _tool_result(bool(result.get("ok")), result)


register_tool(
    ToolSpec(
        name="spawn_subagent",
        description=(
            "创建受限子代理任务。角色: explore / reviewer / test_runner / implementer。"
            "仅主 Agent 可调用；不可嵌套。implementer 在独立 worktree 中写入。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["explore", "reviewer", "test_runner", "implementer"],
                    "description": "explore | reviewer | test_runner | implementer",
                },
                "prompt": {
                    "type": "string",
                    "minLength": 1,
                    "description": "子任务目标",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "依赖的其它 child_task_id，须先成功",
                },
                "base_revision": {
                    "type": "string",
                    "description": "implementer worktree 的 base revision，可选",
                },
            },
            "required": ["role", "prompt"],
        },
        category="subagent",
        read_only=False,
        starts_process=False,
        approval_kind=None,
        handler=_handle_spawn_subagent,
    )
)

register_tool(
    ToolSpec(
        name="get_subagent",
        description="查询子代理状态与结构化摘要（不含原始大输出）。",
        input_schema={
            "type": "object",
            "properties": {
                "child_task_id": {"type": "string"},
            },
            "required": ["child_task_id"],
        },
        category="subagent",
        read_only=True,
        handler=_handle_get_subagent,
    )
)

register_tool(
    ToolSpec(
        name="wait_subagents",
        description="等待一个或多个子代理完成，按请求顺序返回摘要结果。",
        input_schema={
            "type": "object",
            "properties": {
                "child_task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "timeout_seconds": {"type": "number"},
            },
            "required": ["child_task_ids"],
        },
        category="subagent",
        read_only=True,
        default_timeout_seconds=DEFAULT_WAIT_TIMEOUT_SECONDS,
        handler=_handle_wait_subagents,
    )
)

register_tool(
    ToolSpec(
        name="finalize_worktree",
        description="对 implementer worktree 执行 merge / keep / discard。合并前检测冲突，不自动 push。",
        input_schema={
            "type": "object",
            "properties": {
                "worktree_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["merge", "keep", "discard"],
                    "description": "merge | keep | discard",
                },
            },
            "required": ["worktree_id", "action"],
        },
        category="subagent",
        read_only=False,
        workspace_write=True,
        approval_kind="worktree_finalize",
        handler=_handle_finalize_worktree,
    )
)


def available_roles_for_prompt() -> str:
    lines = ["可用 Subagent 角色:"]
    for role in list_roles():
        lines.append(f"- {role['name']}: {role['description']}")
    return "\n".join(lines)
