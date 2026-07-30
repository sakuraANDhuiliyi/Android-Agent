from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.mcp_config import McpServerConfig, resolve_env_secrets
from agent.processes import build_minimal_env, build_sandboxed_command
from agent.redaction import redact_sensitive_text, redact_sensitive_value


PROTOCOL_VERSION = "2024-11-05"
DEFAULT_CALL_TIMEOUT = 30.0
logger = logging.getLogger(__name__)


class McpError(RuntimeError):
    """Base MCP client error."""


class McpTimeoutError(McpError):
    """Raised when an MCP request times out."""


class McpTransportError(McpError):
    """Raised when the transport fails (crash, broken pipe, etc.)."""


class McpIndeterminateError(McpTransportError):
    """The request may have executed, but no authoritative result was received."""


@dataclass
class McpToolInfo:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class McpCallResult:
    ok: bool
    content: Any
    is_error: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "content": redact_sensitive_value(self.content),
            "is_error": self.is_error,
        }


class McpTransport(ABC):
    """Transport abstraction — stdio first; Streamable HTTP reserved."""

    kind: str = "abstract"

    @abstractmethod
    def start(self) -> dict[str, Any]:
        """Connect and initialize. Returns server capabilities/info."""

    @abstractmethod
    def list_tools(self) -> list[McpToolInfo]:
        ...

    @abstractmethod
    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> McpCallResult:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def healthy(self) -> bool:
        ...


class StdioMcpTransport(McpTransport):
    """MCP stdio transport speaking JSON-RPC over newline-delimited messages.

    Prefer the official ``mcp`` Python SDK when available (Python >= 3.10).
    This implementation covers initialize / tools/list / tools/call for hosts
    that cannot install the SDK, and for deterministic tests.
    """

    kind = "stdio"

    def __init__(
        self,
        config: McpServerConfig,
        *,
        workspace: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.workspace = workspace
        self.extra_env = extra_env or {}
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._closed = False
        self._capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        self._stderr_tail: list[str] = []

    def start(self) -> dict[str, Any]:
        if not self.config.command:
            raise McpTransportError(f"stdio server {self.config.name} 缺少 command")
        base = build_minimal_env()
        # Secrets from config env_refs are injected only into the child process
        # at spawn time and must never be written to events/logs.
        resolved_secrets = resolve_env_secrets(self.config.env_refs)
        env = {**base, **self.extra_env, **resolved_secrets}
        # Never log resolved secrets — env for process only.
        cwd = self.config.cwd or (str(self.workspace) if self.workspace else None)
        command = [self.config.command, *self.config.args]
        if self.workspace is not None:
            declared_files: list[Path] = []
            command_path = Path(self.config.command)
            resolved_command = (
                command_path
                if command_path.is_absolute()
                else Path(cwd or self.workspace) / command_path
            )
            if resolved_command.is_file():
                declared_files.append(resolved_command)
            for arg in self.config.args:
                candidate = Path(arg)
                if not candidate.is_absolute():
                    candidate = Path(cwd or self.workspace) / candidate
                if candidate.is_file():
                    declared_files.append(candidate)
            command = build_sandboxed_command(
                command,
                self.workspace,
                allow_network=False,
                env=env,
                extra_read_paths=declared_files,
            )
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except Exception as exc:
            raise McpTransportError(f"无法启动 MCP server {self.config.name}: {exc}") from exc

        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name=f"mcp-out-{self.config.name}", daemon=True)
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name=f"mcp-err-{self.config.name}", daemon=True
        )
        self._reader.start()
        self._stderr_thread.start()

        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "android-agent", "version": "1.0.0"},
            },
            timeout=self.config.timeout_seconds,
        )
        self._capabilities = dict(result.get("capabilities") or {})
        self._server_info = dict(result.get("serverInfo") or {})
        self._notify("notifications/initialized", {})
        return {
            "capabilities": self._capabilities,
            "serverInfo": self._server_info,
            "protocolVersion": result.get("protocolVersion") or PROTOCOL_VERSION,
        }

    def list_tools(self) -> list[McpToolInfo]:
        result = self._request("tools/list", {}, timeout=self.config.timeout_seconds)
        tools = result.get("tools") or []
        out: list[McpToolInfo] = []
        for item in tools:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            schema = item.get("inputSchema") or item.get("input_schema") or {
                "type": "object",
                "properties": {},
            }
            out.append(
                McpToolInfo(
                    name=str(item["name"]),
                    description=str(item.get("description") or ""),
                    input_schema=dict(schema) if isinstance(schema, dict) else {
                        "type": "object",
                        "properties": {},
                    },
                )
            )
        return out

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> McpCallResult:
        timeout = timeout if timeout is not None else self.config.timeout_seconds
        try:
            result = self._request(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                timeout=timeout,
            )
        except (McpTimeoutError, McpTransportError):
            raise
        except McpError as exc:
            return McpCallResult(ok=False, content=str(exc), is_error=True, raw={"error": str(exc)})
        is_error = bool(result.get("isError"))
        content = result.get("content", result)
        return McpCallResult(ok=not is_error, content=content, is_error=is_error, raw=result)

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception as exc:
            logger.warning(
                "MCP stdin close failed server=%s: %s",
                self.config.name,
                redact_sensitive_text(str(exc)),
            )
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except Exception as exc:
                logger.warning(
                    "MCP force-kill failed server=%s: %s",
                    self.config.name,
                    redact_sensitive_text(str(exc)),
                )
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception as exc:
                logger.warning(
                    "MCP stream close failed server=%s: %s",
                    self.config.name,
                    redact_sensitive_text(str(exc)),
                )
        for thread in (self._reader, self._stderr_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=1)

    def healthy(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None and not self._closed

    @property
    def capabilities(self) -> dict[str, Any]:
        return dict(self._capabilities)

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    def stderr_tail(self, n: int = 20) -> list[str]:
        return [redact_sensitive_text(line) for line in self._stderr_tail[-n:]]

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._closed:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("id")
                if msg_id is None:
                    continue  # notification from server — ignore for now
                try:
                    key = int(msg_id)
                except Exception:
                    continue
                with self._lock:
                    q = self._pending.get(key)
                if q is not None:
                    q.put(msg)
        except Exception as exc:
            logger.warning(
                "MCP stdout reader failed server=%s: %s",
                self.config.name,
                redact_sensitive_text(str(exc)),
            )
        finally:
            # Unblock waiters on crash.
            with self._lock:
                pending = list(self._pending.items())
            for _, q in pending:
                q.put({"jsonrpc": "2.0", "error": {"message": "MCP server closed"}})

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                text = line.rstrip("\n")
                if text:
                    self._stderr_tail.append(text)
                    if len(self._stderr_tail) > 200:
                        self._stderr_tail = self._stderr_tail[-100:]
        except Exception as exc:
            logger.warning(
                "MCP stderr reader failed server=%s: %s",
                self.config.name,
                redact_sensitive_text(str(exc)),
            )

    def _next_request_id(self) -> int:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            return rid

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpTransportError(f"MCP server {self.config.name} 未运行")
        data = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except Exception as exc:
            raise McpTransportError(f"写入 MCP server 失败: {exc}") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        if not self.healthy() and method != "initialize":
            raise McpTransportError(f"MCP server {self.config.name} 不健康")
        rid = self._next_request_id()
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[rid] = q
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            try:
                msg = q.get(timeout=timeout)
            except queue.Empty as exc:
                raise McpTimeoutError(
                    f"MCP {self.config.name}.{method} 超时 ({timeout}s)"
                ) from exc
        finally:
            with self._lock:
                self._pending.pop(rid, None)

        if "error" in msg and msg.get("error"):
            err = msg["error"]
            if isinstance(err, dict):
                raise McpError(err.get("message") or str(err))
            raise McpError(str(err))
        result = msg.get("result")
        if not isinstance(result, dict):
            return {"value": result}
        return result


def create_transport(
    config: McpServerConfig,
    *,
    workspace: Path | None = None,
) -> McpTransport:
    if config.transport != "stdio":
        raise McpTransportError(f"当前版本不支持 MCP transport: {config.transport}")
    return StdioMcpTransport(config, workspace=workspace)


def try_official_sdk() -> bool:
    """Return True if the official mcp package is importable."""
    try:
        import mcp  # noqa: F401

        return True
    except Exception:
        return False
