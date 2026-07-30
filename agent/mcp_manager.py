from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.mcp_client import (
    McpCallResult,
    McpError,
    McpIndeterminateError,
    McpTimeoutError,
    McpToolInfo,
    McpTransport,
    McpTransportError,
    create_transport,
)
from agent.mcp_config import (
    McpServerConfig,
    is_project_mcp_trusted,
    load_project_mcp_config,
    load_user_mcp_config,
    merge_mcp_configs,
    public_env_preview,
    trust_project_mcp,
)
from agent.redaction import redact_sensitive_value
from agent.tool_registry import (
    DuplicateToolError,
    ToolSpec,
    clear_dynamic_tools,
    list_dynamic_tool_specs,
    unregister_dynamic_tool,
    upsert_dynamic_tool,
)


EventCallback = Callable[[str, Any], None]

_SERVER_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


def mcp_tool_name(server: str, tool: str) -> str:
    """Stable namespaced tool id: mcp__server__tool."""
    s = _SERVER_NAME_SAFE.sub("_", (server or "").strip()) or "server"
    t = _SERVER_NAME_SAFE.sub("_", (tool or "").strip()) or "tool"
    return f"mcp__{s}__{t}"


def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    parts = name.split("__", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


@dataclass
class McpServerState:
    config: McpServerConfig
    status: str = "stopped"  # stopped|starting|ready|error|disabled|untrusted
    error: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    server_info: dict[str, Any] = field(default_factory=dict)
    tools: list[McpToolInfo] = field(default_factory=list)
    transport: McpTransport | None = None
    last_started_at: float | None = None
    reconnect_attempts: int = 0

    def public_dict(self) -> dict[str, Any]:
        return {
            **self.config.public_dict(),
            "status": self.status,
            "error": self.error,
            "capabilities": self.capabilities,
            "server_info": self.server_info,
            "tools": [t.to_dict() for t in self.tools],
            "tool_names": [mcp_tool_name(self.config.name, t.name) for t in self.tools],
            "last_started_at": self.last_started_at,
            "reconnect_attempts": self.reconnect_attempts,
            "healthy": bool(self.transport and self.transport.healthy()),
            "env_preview": public_env_preview(self.config.env_refs),
        }


class McpManager:
    """Per-user/project MCP lifecycle: start, refresh schemas, call, reconnect."""

    def __init__(
        self,
        user_id: str,
        project_id: str,
        workspace: Path,
        *,
        on_event: EventCallback | None = None,
    ) -> None:
        self.user_id = user_id
        self.project_id = project_id
        self.workspace = workspace
        self.on_event = on_event
        self._lock = threading.RLock()
        self._servers: dict[str, McpServerState] = {}
        self._enabled_overrides: dict[str, bool] = {}

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.on_event:
            safe = redact_sensitive_value(payload)
            self.on_event(event_type, safe if isinstance(safe, dict) else {"message": str(safe)})

    def reload_configs(self) -> list[McpServerConfig]:
        trusted = is_project_mcp_trusted(self.user_id, self.project_id, self.workspace)
        user = load_user_mcp_config(self.user_id)
        project = load_project_mcp_config(self.workspace)
        merged = merge_mcp_configs(user, project, project_trusted=trusted)
        with self._lock:
            # Drop removed servers.
            keep = {c.name for c in merged}
            for name in list(self._servers):
                if name not in keep:
                    self._stop_locked(name)
            for cfg in merged:
                override = self._enabled_overrides.get(cfg.name)
                if override is not None:
                    cfg = McpServerConfig(**{**cfg.__dict__, "enabled": override})
                existing = self._servers.get(cfg.name)
                if existing is None:
                    status = "disabled" if not cfg.enabled else (
                        "untrusted" if cfg.scope == "project" and not trusted else "stopped"
                    )
                    if cfg.scope == "project" and not trusted:
                        status = "untrusted"
                    self._servers[cfg.name] = McpServerState(config=cfg, status=status)
                else:
                    existing.config = cfg
                    if cfg.scope == "project" and not trusted:
                        existing.status = "untrusted"
                    elif not cfg.enabled:
                        existing.status = "disabled"
        self._emit(
            "mcp_status",
            message="MCP configs reloaded",
            trusted=trusted,
            servers=[s.public_dict() for s in self._servers.values()],
        )
        return merged

    def list_servers(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._servers:
                self.reload_configs()
            return [s.public_dict() for s in self._servers.values()]

    def get_server(self, name: str) -> McpServerState | None:
        with self._lock:
            return self._servers.get(name)

    def trust_project(self) -> dict[str, Any]:
        result = trust_project_mcp(self.user_id, self.project_id, self.workspace)
        self.reload_configs()
        self._emit("mcp_status", message="project MCP trusted", **result)
        return result

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._enabled_overrides[name] = enabled
            state = self._servers.get(name)
            if state is None:
                self.reload_configs()
                state = self._servers.get(name)
            if state is None:
                raise KeyError(f"MCP server 不存在: {name}")
            state.config.enabled = enabled
            if not enabled:
                self._stop_locked(name)
                state.status = "disabled"
            else:
                if state.status in {"disabled", "stopped", "error", "untrusted"}:
                    if (
                        state.config.scope == "project"
                        and not is_project_mcp_trusted(
                            self.user_id, self.project_id, self.workspace
                        )
                    ):
                        state.status = "untrusted"
                    else:
                        state.status = "stopped"
            snapshot = state.public_dict()
        self._emit("mcp_status", message=f"server {name} enabled={enabled}", server=snapshot)
        return snapshot

    def start_server(self, name: str) -> dict[str, Any]:
        with self._lock:
            return self._start_locked(name)

    def reconnect(self, name: str) -> dict[str, Any]:
        with self._lock:
            state = self._servers.get(name)
            if state:
                state.reconnect_attempts += 1
            self._stop_locked(name)
            return self._start_locked(name)

    def refresh_tools(self, name: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            names = [name] if name else list(self._servers)
            out: list[dict[str, Any]] = []
            for n in names:
                state = self._servers.get(n)
                if state is None or state.transport is None or not state.transport.healthy():
                    continue
                try:
                    tools = state.transport.list_tools()
                    state.tools = tools
                    self._sync_registry_locked(state)
                    out.append(state.public_dict())
                    self._emit(
                        "mcp_status",
                        message=f"tools refreshed for {n}",
                        server=n,
                        tool_count=len(tools),
                    )
                except McpError as exc:
                    state.status = "error"
                    state.error = str(exc)
            return out

    def start_enabled(self) -> list[dict[str, Any]]:
        self.reload_configs()
        started: list[dict[str, Any]] = []
        with self._lock:
            for name, state in list(self._servers.items()):
                if state.config.enabled and state.status not in {"untrusted", "disabled"}:
                    try:
                        started.append(self._start_locked(name))
                    except Exception as exc:
                        state.status = "error"
                        state.error = str(exc)
                        started.append(state.public_dict())
        return started

    def stop_all(self) -> None:
        with self._lock:
            for name in list(self._servers):
                self._stop_locked(name)

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> McpCallResult:
        with self._lock:
            state = self._servers.get(server)
            if state is None:
                raise McpError(f"未知 MCP server: {server}")
            if state.transport is None or not state.transport.healthy():
                # Crash reconnect once.
                try:
                    self._start_locked(server)
                except Exception as exc:
                    raise McpTransportError(f"MCP server {server} 不可用: {exc}") from exc
                state = self._servers[server]
            transport = state.transport
            assert transport is not None
        try:
            return transport.call_tool(tool, arguments or {}, timeout=timeout)
        except (McpTimeoutError, McpTransportError) as exc:
            # The server may have completed a side-effecting call before the
            # transport failed. Reconnect for future calls, never replay this one.
            with self._lock:
                state = self._servers.get(server)
                if state:
                    state.reconnect_attempts += 1
                    self._emit(
                        "mcp_status",
                        message=f"resetting {server} after indeterminate call",
                        server=server,
                    )
                    self._stop_locked(server)
            raise McpIndeterminateError(
                f"MCP 调用结果未知，未自动重试: {server}/{tool}: {exc}"
            ) from exc

    def _start_locked(self, name: str) -> dict[str, Any]:
        if name not in self._servers:
            self.reload_configs()
        state = self._servers.get(name)
        if state is None:
            raise KeyError(f"MCP server 不存在: {name}")
        if state.config.scope == "project" and not is_project_mcp_trusted(
            self.user_id, self.project_id, self.workspace
        ):
            state.status = "untrusted"
            state.error = "项目 MCP 配置尚未信任"
            self._emit("mcp_status", message="blocked untrusted project MCP", server=state.public_dict())
            return state.public_dict()
        if not state.config.enabled:
            state.status = "disabled"
            return state.public_dict()

        self._stop_locked(name)
        state.status = "starting"
        state.error = None
        transport = create_transport(state.config, workspace=self.workspace)
        try:
            info = transport.start()
            tools = transport.list_tools()
        except Exception as exc:
            transport.close()
            state.status = "error"
            state.error = str(exc)
            state.transport = None
            self._emit("mcp_status", message=f"start failed: {name}", error=str(exc))
            raise

        state.transport = transport
        state.capabilities = dict(info.get("capabilities") or {})
        state.server_info = dict(info.get("serverInfo") or {})
        state.tools = tools
        state.status = "ready"
        state.last_started_at = time.time()
        self._sync_registry_locked(state)
        snapshot = state.public_dict()
        self._emit("mcp_status", message=f"server ready: {name}", server=snapshot)
        return snapshot

    def _stop_locked(self, name: str) -> None:
        state = self._servers.get(name)
        if state is None:
            return
        if state.transport is not None:
            try:
                state.transport.close()
            except Exception as exc:
                self._emit(
                    "mcp_diagnostic",
                    message=f"close failed for {name}",
                    server=name,
                    error=str(exc),
                )
            state.transport = None
        # Unregister this server's tools.
        prefix = f"mcp__{name}__"
        clear_dynamic_tools(prefix=prefix)
        if state.status not in {"disabled", "untrusted"}:
            state.status = "stopped"

    def _sync_registry_locked(self, state: McpServerState) -> None:
        prefix = f"mcp__{state.config.name}__"
        existing = {
            t.name
            for t in list_dynamic_tool_specs()
            if t.name.startswith(prefix)
        }
        seen: set[str] = set()
        for tool in state.tools:
            full_name = mcp_tool_name(state.config.name, tool.name)
            seen.add(full_name)
            # Skip if would collide with builtin.
            from agent.tool_registry import list_builtin_tool_specs

            if any(b.name == full_name for b in list_builtin_tool_specs()):
                self._emit(
                    "mcp_status",
                    message=f"skip tool colliding with builtin: {full_name}",
                )
                continue
            handler = self._make_handler(state.config.name, tool.name)
            # Risk: MCP tools default to network + approval (external process).
            spec = ToolSpec(
                name=full_name,
                description=f"[MCP:{state.config.name}] {tool.description or tool.name}",
                input_schema=tool.input_schema or {"type": "object", "properties": {}},
                category="mcp",
                read_only=False,
                network_access=True,
                starts_process=True,
                approval_kind="mcp_tool",
                default_timeout_seconds=float(state.config.timeout_seconds),
                replay_policy="requires_approval_on_recovery",
                handler=handler,
            )
            try:
                upsert_dynamic_tool(spec)
            except DuplicateToolError:
                pass
        for name in existing - seen:
            unregister_dynamic_tool(name)


    def _make_handler(self, server: str, tool: str):
        manager = self

        def _handler(ctx, tool_input: dict[str, Any]):
            from agent.tools import ToolResult

            if ctx.cancel_check:
                ctx.cancel_check()
            started = time.monotonic()
            try:
                result = manager.call_tool(
                    server,
                    tool,
                    tool_input,
                    timeout=None,
                )
            except Exception as exc:
                return ToolResult(False, f"MCP 调用失败: {exc}", error_type=type(exc).__name__)
            duration_ms = int((time.monotonic() - started) * 1000)
            payload = {
                **result.to_dict(),
                "server": server,
                "tool": tool,
                "duration_ms": duration_ms,
                "namespaced": mcp_tool_name(server, tool),
            }
            return ToolResult(result.ok, payload)

        return _handler


# Process-local managers keyed by user/project.
_managers: dict[tuple[str, str], McpManager] = {}
_managers_lock = threading.Lock()


def get_mcp_manager(
    user_id: str,
    project_id: str,
    workspace: Path,
    *,
    on_event: EventCallback | None = None,
) -> McpManager:
    key = (user_id, project_id)
    with _managers_lock:
        mgr = _managers.get(key)
        if mgr is None:
            mgr = McpManager(user_id, project_id, workspace, on_event=on_event)
            _managers[key] = mgr
        elif on_event is not None:
            mgr.on_event = on_event
        return mgr


def reset_mcp_managers() -> None:
    with _managers_lock:
        for mgr in _managers.values():
            try:
                mgr.stop_all()
            except Exception as exc:
                mgr._emit(
                    "mcp_diagnostic",
                    message="manager shutdown failed",
                    error=str(exc),
                )
        _managers.clear()
    clear_dynamic_tools(prefix="mcp__")
