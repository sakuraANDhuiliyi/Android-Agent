from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import agent.paths as paths
from agent.paths import validate_id
from agent.redaction import REDACTED, redact_sensitive_value


TransportKind = Literal["stdio", "streamable_http"]
ConfigScope = Literal["user", "project"]

_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


@dataclass
class McpServerConfig:
    name: str
    transport: TransportKind = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    enabled: bool = True
    timeout_seconds: float = 30.0
    scope: ConfigScope = "user"
    # Raw env before secret resolution — never expose resolved secrets.
    env_refs: dict[str, str] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        """Safe representation for API / events (no secrets)."""
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "url": self.url,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "scope": self.scope,
            "env_keys": sorted(self.env_refs.keys()),
            "has_env": bool(self.env_refs),
        }


def user_mcp_config_path(user_id: str) -> Path:
    return paths.DATA_DIR / "users" / validate_id(user_id, kind="user_id") / "mcp.json"


def project_mcp_config_path(workspace: Path) -> Path:
    return workspace / ".android-agent" / "mcp.json"


def project_mcp_trust_path(user_id: str, project_id: str) -> Path:
    return (
        paths.DATA_DIR
        / "users"
        / validate_id(user_id, kind="user_id")
        / "mcp_trust"
        / f"{validate_id(project_id, kind='project_id')}.json"
    )


def user_hooks_config_path(user_id: str) -> Path:
    return paths.DATA_DIR / "users" / validate_id(user_id, kind="user_id") / "hooks.json"


def project_hooks_config_path(workspace: Path) -> Path:
    return workspace / ".android-agent" / "hooks.json"


def _validate_server_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not _SERVER_NAME_RE.fullmatch(cleaned):
        raise ValueError(f"无效的 MCP server 名称: {name!r}")
    return cleaned


def _parse_servers(raw: Any, *, scope: ConfigScope) -> list[McpServerConfig]:
    if raw is None:
        return []
    if isinstance(raw, dict) and "mcpServers" in raw:
        raw = raw["mcpServers"]
    if isinstance(raw, dict):
        items = []
        for name, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            entry = dict(cfg)
            entry.setdefault("name", name)
            items.append(entry)
        raw = items
    if not isinstance(raw, list):
        return []

    results: list[McpServerConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            name = _validate_server_name(str(item.get("name") or ""))
        except ValueError:
            continue
        transport = str(item.get("transport") or item.get("type") or "stdio").strip()
        if transport in {"http", "sse", "streamable-http", "streamable_http"}:
            transport = "streamable_http"
        if transport not in {"stdio", "streamable_http"}:
            transport = "stdio"
        command = item.get("command")
        args = item.get("args") or []
        if isinstance(args, str):
            args = [args]
        env_raw = item.get("env") or {}
        if not isinstance(env_raw, dict):
            env_raw = {}
        env_refs = {str(k): str(v) for k, v in env_raw.items()}
        results.append(
            McpServerConfig(
                name=name,
                transport=transport,  # type: ignore[arg-type]
                command=str(command) if command else None,
                args=[str(a) for a in args],
                env={},
                cwd=str(item["cwd"]) if item.get("cwd") else None,
                url=str(item["url"]) if item.get("url") else None,
                enabled=bool(item.get("enabled", True)),
                timeout_seconds=float(item.get("timeout_seconds") or item.get("timeout") or 30),
                scope=scope,
                env_refs=env_refs,
            )
        )
    return results


def load_mcp_json(path: Path, *, scope: ConfigScope) -> list[McpServerConfig]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return _parse_servers(data, scope=scope)


def load_user_mcp_config(user_id: str) -> list[McpServerConfig]:
    return load_mcp_json(user_mcp_config_path(user_id), scope="user")


def load_project_mcp_config(workspace: Path) -> list[McpServerConfig]:
    return load_mcp_json(project_mcp_config_path(workspace), scope="project")


def resolve_env_secrets(env_refs: dict[str, str]) -> dict[str, str]:
    """Resolve ${VAR} / $VAR references from the process environment at spawn time."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        return os.environ.get(key, "")

    resolved: dict[str, str] = {}
    for key, value in env_refs.items():
        resolved[key] = _ENV_REF_RE.sub(repl, value)
    return resolved


def public_env_preview(env_refs: dict[str, str]) -> dict[str, str]:
    """Return env keys with redacted values for diagnostics."""
    preview = {k: _ENV_REF_RE.sub(REDACTED, v) for k, v in env_refs.items()}
    return redact_sensitive_value(preview)


def project_config_fingerprint(workspace: Path) -> str:
    path = project_mcp_config_path(workspace)
    if not path.is_file():
        return ""
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_project_mcp_trusted(user_id: str, project_id: str, workspace: Path) -> bool:
    trust_path = project_mcp_trust_path(user_id, project_id)
    if not trust_path.is_file():
        return False
    try:
        data = json.loads(trust_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return data.get("fingerprint") == project_config_fingerprint(workspace)


def trust_project_mcp(user_id: str, project_id: str, workspace: Path) -> dict[str, Any]:
    fingerprint = project_config_fingerprint(workspace)
    trust_path = project_mcp_trust_path(user_id, project_id)
    trust_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project_id": project_id,
        "fingerprint": fingerprint,
        "trusted": True,
    }
    trust_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def revoke_project_mcp_trust(user_id: str, project_id: str) -> None:
    path = project_mcp_trust_path(user_id, project_id)
    if path.is_file():
        path.unlink()


def merge_mcp_configs(
    user_servers: list[McpServerConfig],
    project_servers: list[McpServerConfig],
    *,
    project_trusted: bool,
) -> list[McpServerConfig]:
    """Merge configs. Untrusted project servers are kept but marked via scope.

    Same name: project overrides user only when trusted; otherwise user wins
    and the project entry is still returned with a distinct name collision
    resolved by keeping both only when names differ.
    """
    by_name: dict[str, McpServerConfig] = {s.name: s for s in user_servers}
    for server in project_servers:
        if project_trusted:
            by_name[server.name] = server
        elif server.name not in by_name:
            # Visible but not startable until trusted.
            by_name[server.name] = server
    return list(by_name.values())
