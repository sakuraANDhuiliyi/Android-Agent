from __future__ import annotations

from typing import Any

from agent.context_planner import ContextPlanner
from agent.paths import workspace_path
from agent.repo_index import get_repo_index
from agent.tool_registry import ToolSpec, register_tool
from agent.tool_runtime import ToolContext


def _handle_repo_map(ctx: ToolContext, tool_input: dict[str, Any]) -> dict[str, Any]:
    index = get_repo_index(ctx.user_id, ctx.project_id)
    status = index.status()
    if status["status"] != "ready":
        index.rebuild()
    max_files = int(tool_input.get("max_files", 100))
    return index.repo_map(max_files=max_files)


def _handle_search_code(ctx: ToolContext, tool_input: dict[str, Any]) -> dict[str, Any]:
    index = get_repo_index(ctx.user_id, ctx.project_id)
    status = index.status()
    if status["status"] != "ready":
        index.rebuild()
    query = str(tool_input.get("query", "")).strip()
    limit = int(tool_input.get("limit", 20))
    if not query:
        return {"ok": False, "error": "缺少 query 参数"}
    hits = index.search(query, limit=limit)
    return {
        "ok": True,
        "query": query,
        "hits": hits,
    }


def _handle_find_symbol(ctx: ToolContext, tool_input: dict[str, Any]) -> dict[str, Any]:
    index = get_repo_index(ctx.user_id, ctx.project_id)
    status = index.status()
    if status["status"] != "ready":
        index.rebuild()
    name = tool_input.get("name")
    symbol_type = tool_input.get("symbol_type")
    rel_path = tool_input.get("rel_path")
    limit = int(tool_input.get("limit", 50))
    if not name and not symbol_type and not rel_path:
        return {"ok": False, "error": "至少提供 name、symbol_type 或 rel_path"}
    results = index.find_symbol(
        name=name,
        symbol_type=symbol_type,
        rel_path=rel_path,
        limit=limit,
    )
    return {
        "ok": True,
        "symbols": results,
    }


def _handle_find_references(ctx: ToolContext, tool_input: dict[str, Any]) -> dict[str, Any]:
    index = get_repo_index(ctx.user_id, ctx.project_id)
    status = index.status()
    if status["status"] != "ready":
        index.rebuild()
    symbol_name = str(tool_input.get("symbol_name", "")).strip()
    if not symbol_name:
        return {"ok": False, "error": "缺少 symbol_name 参数"}
    rel_path = tool_input.get("rel_path")
    limit = int(tool_input.get("limit", 50))
    results = index.find_references(
        symbol_name=symbol_name,
        rel_path=rel_path,
        limit=limit,
    )
    return {
        "ok": True,
        "symbol_name": symbol_name,
        "references": results,
    }


def _handle_related_files(ctx: ToolContext, tool_input: dict[str, Any]) -> dict[str, Any]:
    index = get_repo_index(ctx.user_id, ctx.project_id)
    status = index.status()
    if status["status"] != "ready":
        index.rebuild()
    rel_path = str(tool_input.get("rel_path", "")).strip()
    if not rel_path:
        return {"ok": False, "error": "缺少 rel_path 参数"}
    limit = int(tool_input.get("limit", 20))
    results = index.related_files(rel_path, limit=limit)
    return {
        "ok": True,
        "rel_path": rel_path,
        "related_files": results,
    }


def _handle_plan_context(ctx: ToolContext, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Internal tool for the agent to ask the Context Planner."""
    index = get_repo_index(ctx.user_id, ctx.project_id)
    status = index.status()
    if status["status"] != "ready":
        index.rebuild()
    planner = ContextPlanner(index, user_id=ctx.user_id, project_id=ctx.project_id)
    prompt = str(tool_input.get("prompt", "")).strip()
    if not prompt:
        return {"ok": False, "error": "缺少 prompt 参数"}
    current_file = tool_input.get("current_file") or None
    selection = tool_input.get("selection") or None
    history = tool_input.get("history") or None
    budget = int(tool_input.get("budget_chars", 100_000))
    plan = planner.plan(
        prompt=prompt,
        current_file=current_file,
        selection=selection,
        history=history,
        budget_chars=budget,
        task_id=ctx.task_id,
    )
    return {"ok": True, "plan": plan}


register_tool(
    ToolSpec(
        name="repo_map",
        description="返回项目代码索引的 Repo Map：文件清单、语言、大小和符号汇总。",
        input_schema={
            "type": "object",
            "properties": {
                "max_files": {
                    "type": "integer",
                    "description": "最多返回文件数，默认 100",
                }
            },
        },
        category="repo",
        read_only=True,
        handler=_handle_repo_map,
    )
)

register_tool(
    ToolSpec(
        name="search_code",
        description="使用 SQLite FTS5 搜索项目代码内容，返回匹配文件路径和排序分数。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "FTS 查询字符串，支持 OR/AND 和词组",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认 20",
                },
            },
            "required": ["query"],
        },
        category="repo",
        read_only=True,
        handler=_handle_search_code,
    )
)

register_tool(
    ToolSpec(
        name="find_symbol",
        description="按名称、类型或文件路径查找代码符号。",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "符号名"},
                "symbol_type": {"type": "string", "description": "符号类型，如 class/function/resource_id"},
                "rel_path": {"type": "string", "description": "限制在某个文件路径"},
                "limit": {"type": "integer", "description": "最多返回条数，默认 50"},
            },
        },
        category="repo",
        read_only=True,
        handler=_handle_find_symbol,
    )
)

register_tool(
    ToolSpec(
        name="find_references",
        description="查找符号在项目中的引用位置。",
        input_schema={
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "要查找引用的符号名",
                },
                "rel_path": {"type": "string", "description": "限制文件路径"},
                "limit": {"type": "integer", "description": "最多返回条数，默认 50"},
            },
            "required": ["symbol_name"],
        },
        category="repo",
        read_only=True,
        handler=_handle_find_references,
    )
)

register_tool(
    ToolSpec(
        name="related_files",
        description="返回与指定文件共享符号引用的相关文件列表。",
        input_schema={
            "type": "object",
            "properties": {
                "rel_path": {
                    "type": "string",
                    "description": "相对项目根的文件路径",
                },
                "limit": {"type": "integer", "description": "最多返回条数，默认 20"},
            },
            "required": ["rel_path"],
        },
        category="repo",
        read_only=True,
        handler=_handle_related_files,
    )
)
