from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from pathlib import Path


class ToolHandlerMissingError(RuntimeError):
    """Raised when a ToolSpec is registered without a handler."""


class DuplicateToolError(ValueError):
    """Raised when a tool name is registered more than once."""


ToolHandler = Callable[..., Any]


@dataclass
class ToolSpec:
    """Canonical definition of a built-in agent tool.

    The registry is the single source of truth for built-in tool definitions.
    OpenAI and Anthropic schemas are projections from ToolSpec instances.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    category: str = "workspace"
    read_only: bool = False
    workspace_write: bool = False
    network_access: bool = False
    starts_process: bool = False
    destructive: bool = False
    default_timeout_seconds: float = 300.0
    approval_kind: str | None = None
    replay_policy: str = "skip_on_recovery"
    handler: ToolHandler | None = None

    def __post_init__(self) -> None:
        if self.input_schema is None:
            self.input_schema = {"type": "object", "properties": {}, "required": []}
        else:
            self.input_schema = copy.deepcopy(self.input_schema)
        if self.input_schema.get("type") == "object":
            self.input_schema.setdefault("additionalProperties", False)

    def primary_risk(self) -> str:
        """Return the highest risk level declared by the tool."""
        if self.destructive:
            return "destructive"
        if self.network_access:
            return "network"
        if self.starts_process:
            return "process"
        if self.workspace_write:
            return "workspace_write"
        if self.read_only:
            return "read"
        return "read"

    def to_openai(self) -> dict[str, Any]:
        """Project this ToolSpec to the OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def to_anthropic(self) -> dict[str, Any]:
        """Project this ToolSpec to the Anthropic tool definition format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Single source of truth for built-in tool definitions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise DuplicateToolError(f"工具已注册: {spec.name}")
        if not spec.handler:
            raise ToolHandlerMissingError(f"工具缺少 handler: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def _apply_gradle_retry_description(
        self, tools: list[dict[str, Any]], settings: Any
    ) -> list[dict[str, Any]]:
        """Mutate the run_gradle description to include the retry budget."""
        max_retries = max(0, int(getattr(settings, "max_gradle_retries", 3)))
        for index, item in enumerate(tools):
            if item.get("name") == "run_gradle" or (
                item.get("type") == "function"
                and item.get("function", {}).get("name") == "run_gradle"
            ):
                tools[index] = copy.deepcopy(item)
                if item.get("type") == "function":
                    tools[index]["function"]["description"] = (
                        f"在工程目录执行 Gradle。常用 task: assembleDebug。"
                        f"编译失败后最多再尝试 {max_retries} 次修复构建。"
                    )
                else:
                    tools[index]["description"] = (
                        f"在工程目录执行 Gradle。常用 task: assembleDebug。"
                        f"编译失败后最多再尝试 {max_retries} 次修复构建。"
                    )
                break
        return tools

    def to_openai(self, settings: Any = None) -> list[dict[str, Any]]:
        tools = [tool.to_openai() for tool in self._tools.values()]
        if settings is not None:
            tools = self._apply_gradle_retry_description(tools, settings)
        return tools

    def to_anthropic(self, settings: Any = None) -> list[dict[str, Any]]:
        tools = [tool.to_anthropic() for tool in self._tools.values()]
        if settings is not None:
            tools = self._apply_gradle_retry_description(tools, settings)
        return tools


# Dynamic tools belong to one authenticated project/worktree context. A schema
# lookup without a scope intentionally exposes only built-ins.
ToolScope = tuple[str, str, str]
_builtin_registry = ToolRegistry()
_dynamic_registries: dict[ToolScope, ToolRegistry] = {}
_dynamic_lock = threading.RLock()


def tool_scope(user_id: str, project_id: str, workspace: Path) -> ToolScope:
    return (user_id, project_id, str(Path(workspace).resolve()))


def register_tool(spec: ToolSpec) -> ToolSpec:
    """Register a built-in tool in the global registry."""
    return _builtin_registry.register(spec)


def upsert_dynamic_tool(spec: ToolSpec, *, scope: ToolScope) -> ToolSpec:
    """Register a dynamic tool within its owner context only."""
    if _builtin_registry.get(spec.name) is not None:
        raise DuplicateToolError(f"不能覆盖内置工具: {spec.name}")
    if not spec.handler:
        raise ToolHandlerMissingError(f"工具缺少 handler: {spec.name}")
    with _dynamic_lock:
        registry = _dynamic_registries.setdefault(scope, ToolRegistry())
        registry._tools[spec.name] = spec  # noqa: SLF001
    return spec


def unregister_dynamic_tool(name: str, *, scope: ToolScope) -> bool:
    """Remove a tool from its owner context, never another tenant's tool."""
    with _dynamic_lock:
        registry = _dynamic_registries.get(scope)
        if registry is None:
            return False
        removed = registry._tools.pop(name, None) is not None  # noqa: SLF001
        if not registry._tools:  # noqa: SLF001
            _dynamic_registries.pop(scope, None)
        return removed


def clear_dynamic_tools(
    *, prefix: str | None = None, scope: ToolScope | None = None,
) -> list[str]:
    """Clear a scoped registry; omitted scope is for process-wide shutdown/tests."""
    removed: list[str] = []
    with _dynamic_lock:
        scopes = [scope] if scope is not None else list(_dynamic_registries)
        for owner in scopes:
            registry = _dynamic_registries.get(owner)
            if registry is None:
                continue
            for name in list(registry._tools):  # noqa: SLF001
                if prefix is None or name.startswith(prefix):
                    registry._tools.pop(name, None)  # noqa: SLF001
                    removed.append(name)
            if not registry._tools:  # noqa: SLF001
                _dynamic_registries.pop(owner, None)
    return removed


def get_tool_spec(name: str, *, scope: ToolScope | None = None) -> ToolSpec | None:
    builtin = _builtin_registry.get(name)
    if builtin is not None:
        return builtin
    with _dynamic_lock:
        registry = _dynamic_registries.get(scope) if scope is not None else None
        return registry.get(name) if registry is not None else None


def list_tool_specs(*, scope: ToolScope | None = None) -> list[ToolSpec]:
    return _builtin_registry.list_tools() + list_dynamic_tool_specs(scope=scope)


def list_builtin_tool_specs() -> list[ToolSpec]:
    return _builtin_registry.list_tools()


def list_dynamic_tool_specs(*, scope: ToolScope | None = None) -> list[ToolSpec]:
    with _dynamic_lock:
        registry = _dynamic_registries.get(scope) if scope is not None else None
        return registry.list_tools() if registry is not None else []


def get_openai_tool_definitions(
    settings: Any = None, *, scope: ToolScope | None = None,
) -> list[dict[str, Any]]:
    tools = [t.to_openai() for t in list_tool_specs(scope=scope)]
    if settings is not None:
        tools = _builtin_registry._apply_gradle_retry_description(tools, settings)  # noqa: SLF001
    return tools


def get_anthropic_tool_definitions(
    settings: Any = None, *, scope: ToolScope | None = None,
) -> list[dict[str, Any]]:
    tools = [t.to_anthropic() for t in list_tool_specs(scope=scope)]
    if settings is not None:
        tools = _builtin_registry._apply_gradle_retry_description(tools, settings)  # noqa: SLF001
    return tools
