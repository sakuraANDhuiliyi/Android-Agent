from __future__ import annotations

from typing import Any

from agent.skills import (
    DEFAULT_SKILL_MAX_CHARS,
    discover_skills_for_context,
    list_skill_resources,
    list_skills,
    load_skill,
)
from agent.tool_registry import ToolSpec, register_tool
from agent.tool_runtime import ToolContext


def _tool_result(ok: bool, payload: dict[str, Any]):
    from agent.tools import ToolResult

    return ToolResult(ok, payload)


def _handle_load_skill(ctx: ToolContext, tool_input: dict[str, Any]):
    """Read-only: load a skill's SKILL.md (and optional resource) into the result.

    Never executes scripts. Execution of any commands still requires Tool Runtime
    and approval via normal tools (run_gradle, etc.).
    """
    name = str(tool_input.get("name") or "").strip()
    if not name:
        return _tool_result(False, {"ok": False, "error": "缺少 name 参数"})
    resource_path = tool_input.get("resource")
    if resource_path is not None:
        resource_path = str(resource_path).strip() or None
    max_chars = int(tool_input.get("max_chars") or DEFAULT_SKILL_MAX_CHARS)
    max_chars = max(200, min(max_chars, DEFAULT_SKILL_MAX_CHARS * 2))

    try:
        content = load_skill(
            ctx.workspace,
            ctx.user_id,
            name,
            max_chars=max_chars,
            resource_path=resource_path,
        )
    except FileNotFoundError as exc:
        return _tool_result(False, {"ok": False, "error": str(exc)})
    except PermissionError as exc:
        return _tool_result(False, {"ok": False, "error": str(exc), "path_escape": True})
    except ValueError as exc:
        return _tool_result(False, {"ok": False, "error": str(exc)})

    if ctx.on_event:
        ctx.on_event(
            "system_note",
            {
                "message": f"Loaded skill {content.meta.name} ({content.chars} chars)",
                "kind": "skill_loaded",
                "skill": content.meta.name,
                "scope": content.meta.scope,
                "chars": content.chars,
                "truncated": content.truncated,
                "resources": list(content.resources),
                "executed": False,
            },
        )

    return _tool_result(
        True,
        {
            "ok": True,
            "executed": False,
            "skill": content.to_dict(),
            "note": (
                "Skill content is instructional only. Scripts inside the skill "
                "directory were not executed; use normal tools with approval to run commands."
            ),
        },
    )


def _handle_list_skills(ctx: ToolContext, tool_input: dict[str, Any]):
    query = tool_input.get("query")
    focus = tool_input.get("focus_paths")
    if isinstance(focus, str):
        focus = [focus]
    if query or focus:
        metas = discover_skills_for_context(
            ctx.workspace,
            ctx.user_id,
            focus_paths=list(focus or []),
            query=str(query) if query else None,
            limit=int(tool_input.get("limit") or 8),
        )
    else:
        metas = list_skills(ctx.workspace, ctx.user_id)
    return _tool_result(
        True,
        {
            "ok": True,
            "skills": [m.to_dict() for m in metas],
            "note": "Metadata only. Call load_skill to inject full skill content.",
        },
    )


register_tool(
    ToolSpec(
        name="load_skill",
        description=(
            "按需加载一个 Skill 的 SKILL.md（及可选资源文件）到上下文。"
            "只读，不会执行 Skill 目录中的脚本；执行命令仍须走普通工具与审批。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill 名称（对应 .android-agent/skills/{name} 或用户 skills 目录）",
                },
                "resource": {
                    "type": "string",
                    "description": "可选，Skill 目录内的相对资源路径（禁止 ..）",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "最大字符数，默认 12000",
                },
            },
            "required": ["name"],
        },
        category="skills",
        read_only=True,
        handler=_handle_load_skill,
    )
)

register_tool(
    ToolSpec(
        name="list_skills",
        description="列出或按描述发现可用 Skills（仅元数据，不注入全文）。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "按名称/描述发现 Skills",
                },
                "focus_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "当前关注的相对路径，用于 glob 匹配",
                },
                "limit": {
                    "type": "integer",
                    "description": "发现结果上限，默认 8",
                },
            },
        },
        category="skills",
        read_only=True,
        handler=_handle_list_skills,
    )
)


def skill_resources_for_api(workspace, user_id: str, name: str) -> list[str]:
    return list_skill_resources(workspace, user_id, name)
