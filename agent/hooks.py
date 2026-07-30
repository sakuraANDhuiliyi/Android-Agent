from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

from agent.mcp_config import project_hooks_config_path, user_hooks_config_path
from agent.permissions import PermissionDecision, decide_permission
from agent.redaction import redact_sensitive_value
from agent.tool_registry import ToolSpec


HookEvent = Literal[
    "BeforeModel",
    "AfterModel",
    "PreToolUse",
    "PostToolUse",
    "ToolFailure",
    "ApprovalRequired",
    "TurnCompleted",
    "TaskStopped",
]

HookActionType = Literal["allow", "deny", "ask"]
EventCallback = Callable[[str, Any], None]


@dataclass
class HookAction:
    type: Literal["log", "command", "http"]
    message: str | None = None
    command: list[str] | None = None
    url: str | None = None
    method: str = "POST"
    body: dict[str, Any] | None = None


@dataclass
class HookRule:
    event: HookEvent
    matcher: str = ".*"  # tool name regex or "*"
    decision: HookActionType | None = None
    modify_input: dict[str, Any] | None = None
    append_context: str | None = None
    actions: list[HookAction] = field(default_factory=list)
    source: str = "user"
    id: str = ""

    def matches_tool(self, tool_name: str) -> bool:
        if self.event not in {"PreToolUse", "PostToolUse", "ToolFailure", "ApprovalRequired"}:
            return True
        pattern = self.matcher or ".*"
        if pattern in {"*", ".*"}:
            return True
        try:
            return bool(re.search(pattern, tool_name or ""))
        except re.error:
            return pattern == tool_name


@dataclass
class HookDecision:
    action: HookActionType = "allow"
    reason: str = ""
    modified_input: dict[str, Any] | None = None
    append_context: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "modified_input": self.modified_input,
            "append_context": list(self.append_context),
            "matched_rules": list(self.matched_rules),
        }


_PRIORITY = {"deny": 3, "ask": 2, "allow": 1}


def load_hooks_file(path: Path, *, source: str) -> list[HookRule]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_hooks = data.get("hooks") if isinstance(data, dict) else data
    if not isinstance(raw_hooks, list):
        return []
    rules: list[HookRule] = []
    for index, item in enumerate(raw_hooks):
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "")
        if event not in {
            "BeforeModel",
            "AfterModel",
            "PreToolUse",
            "PostToolUse",
            "ToolFailure",
            "ApprovalRequired",
            "TurnCompleted",
            "TaskStopped",
        }:
            continue
        decision = item.get("decision")
        if decision is not None:
            decision = str(decision).lower()
            if decision not in {"allow", "deny", "ask"}:
                decision = None
        actions: list[HookAction] = []
        for act in item.get("actions") or []:
            if not isinstance(act, dict):
                continue
            atype = str(act.get("type") or "log")
            if atype not in {"log", "command", "http"}:
                continue
            actions.append(
                HookAction(
                    type=atype,  # type: ignore[arg-type]
                    message=str(act["message"]) if act.get("message") else None,
                    command=[str(c) for c in act.get("command") or []],
                    url=str(act["url"]) if act.get("url") else None,
                    method=str(act.get("method") or "POST").upper(),
                    body=act.get("body") if isinstance(act.get("body"), dict) else None,
                )
            )
        rules.append(
            HookRule(
                event=event,  # type: ignore[arg-type]
                matcher=str(item.get("matcher") or ".*"),
                decision=decision,  # type: ignore[arg-type]
                modify_input=dict(item["modify_input"])
                if isinstance(item.get("modify_input"), dict)
                else None,
                append_context=str(item["append_context"])
                if item.get("append_context")
                else None,
                actions=actions,
                source=source,
                id=str(item.get("id") or f"{source}:{event}:{index}"),
            )
        )
    return rules


def load_hooks(user_id: str, workspace: Path | None = None) -> list[HookRule]:
    rules = load_hooks_file(user_hooks_config_path(user_id), source="user")
    if workspace is not None:
        rules.extend(load_hooks_file(project_hooks_config_path(workspace), source="project"))
    return rules


def merge_decisions(decisions: list[HookDecision]) -> HookDecision:
    """Priority: deny > ask > allow."""
    if not decisions:
        return HookDecision(action="allow", reason="no hooks")
    best = decisions[0]
    for item in decisions[1:]:
        if _PRIORITY[item.action] > _PRIORITY[best.action]:
            best = HookDecision(
                action=item.action,
                reason=item.reason or best.reason,
                modified_input=item.modified_input or best.modified_input,
                append_context=best.append_context + item.append_context,
                matched_rules=best.matched_rules + item.matched_rules,
            )
        else:
            best.append_context.extend(item.append_context)
            best.matched_rules.extend(item.matched_rules)
            if best.modified_input is None and item.modified_input is not None:
                best.modified_input = item.modified_input
            elif best.modified_input is not None and item.modified_input is not None:
                merged = dict(best.modified_input)
                merged.update(item.modified_input)
                best.modified_input = merged
    return best


def _is_safe_input_modification(
    original: dict[str, Any],
    modified: dict[str, Any],
    *,
    workspace: Path | None,
) -> dict[str, Any]:
    """Apply modifications that cannot escalate path access outside workspace."""
    result = dict(original)
    for key, value in modified.items():
        if key in {"path", "cwd", "target", "file", "filepath", "rel_path"}:
            if not isinstance(value, str):
                continue
            normalized = value.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                # Refuse path escape — keep original.
                continue
            if workspace is not None:
                try:
                    target = (workspace / normalized).resolve()
                    target.relative_to(workspace.resolve())
                except Exception:
                    continue
            result[key] = value
        elif key in {"url", "command", "argv", "shell"}:
            # Do not allow hooks to inject network/shell via input rewrite alone.
            continue
        else:
            result[key] = value
    return result


def run_hooks(
    event: HookEvent,
    *,
    rules: list[HookRule] | None = None,
    user_id: str | None = None,
    workspace: Path | None = None,
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
    tool_result: Any = None,
    on_event: EventCallback | None = None,
    execute_actions: bool = True,
    ctx: Any = None,
) -> HookDecision:
    if rules is None:
        if user_id is None:
            rules = []
        else:
            rules = load_hooks(user_id, workspace)

    matched: list[HookRule] = []
    for rule in rules:
        if rule.event != event:
            continue
        if tool_name is not None and not rule.matches_tool(tool_name):
            continue
        matched.append(rule)

    decisions: list[HookDecision] = []
    for rule in matched:
        action: HookActionType = rule.decision or "allow"
        modified = None
        if rule.modify_input and tool_input is not None:
            modified = _is_safe_input_modification(
                tool_input, rule.modify_input, workspace=workspace
            )
        append = [rule.append_context] if rule.append_context else []
        decisions.append(
            HookDecision(
                action=action,
                reason=f"hook:{rule.id}",
                modified_input=modified,
                append_context=append,
                matched_rules=[rule.id],
            )
        )
        if execute_actions and rule.actions:
            _dispatch_actions(rule.actions, on_event=on_event, ctx=ctx, workspace=workspace)

    result = merge_decisions(decisions)
    if on_event and matched:
        on_event(
            "hook_decision",
            redact_sensitive_value(
                {
                    "message": f"hook {event} -> {result.action}",
                    "event": event,
                    "tool_name": tool_name,
                    "decision": result.to_dict(),
                }
            ),
        )
    return result


def combine_with_permission(
    permission: PermissionDecision,
    hook: HookDecision,
) -> PermissionDecision:
    """Hooks cannot weaken hard permission denials or lower security level."""
    if permission.deny:
        return permission
    if hook.action == "deny":
        return PermissionDecision(
            action="deny",
            reason=hook.reason or "hook denied",
            matched_rule="hook:deny",
            approval_kind=permission.approval_kind,
            risk=permission.risk,
        )
    if permission.ask or hook.action == "ask":
        return PermissionDecision(
            action="ask",
            reason=hook.reason if hook.action == "ask" else permission.reason,
            matched_rule="hook:ask" if hook.action == "ask" else permission.matched_rule,
            approval_kind=permission.approval_kind or "hook",
            risk=permission.risk,
        )
    return permission


_LOCALHOST = {"127.0.0.1", "localhost", "::1"}


def _dispatch_actions(
    actions: list[HookAction],
    *,
    on_event: EventCallback | None,
    ctx: Any,
    workspace: Path | None,
) -> None:
    """Run hook side-effects via permission-aware paths (never bypass Tool Runtime)."""

    def _run() -> None:
        for action in actions:
            try:
                if action.type == "log":
                    if on_event:
                        on_event(
                            "hook_log",
                            {
                                "message": action.message or "hook log",
                            },
                        )
                elif action.type == "command":
                    _run_command_action(action, ctx=ctx, workspace=workspace, on_event=on_event)
                elif action.type == "http":
                    _run_http_action(action, ctx=ctx, on_event=on_event)
            except Exception as exc:
                if on_event:
                    on_event("hook_log", {"message": f"hook action failed: {exc}"})

    # Async log-style: fire in background so hooks don't block the turn heavily.
    thread = threading.Thread(target=_run, name="hook-action", daemon=True)
    thread.start()


def _synthetic_spec(name: str, *, network: bool = False, process: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="hook action",
        input_schema={"type": "object", "properties": {}},
        network_access=network,
        starts_process=process,
        approval_kind="hook_action",
        handler=lambda _ctx, _inp: None,
    )


def _run_command_action(
    action: HookAction,
    *,
    ctx: Any,
    workspace: Path | None,
    on_event: EventCallback | None,
) -> None:
    argv = list(action.command or [])
    if not argv:
        return
    # Restricted: only allow simple echo/true/false style or explicitly listed binaries.
    allowed_bins = {"echo", "true", "false", "/bin/echo", "/usr/bin/true", "/usr/bin/false"}
    if argv[0] not in allowed_bins:
        if on_event:
            on_event("hook_log", {"message": f"hook command denied (not allowlisted): {argv[0]}"})
        return
    decision = decide_permission(_synthetic_spec("hook_command", process=True), "workspace")
    if decision.deny:
        if on_event:
            on_event("hook_log", {"message": f"hook command permission denied: {decision.reason}"})
        return
    # Route through Tool Runtime when context is available.
    if ctx is not None and workspace is not None:
        from agent.tools import dispatch_tool

        dispatch_tool(
            workspace,
            getattr(ctx, "user_id", "local"),
            getattr(ctx, "project_id", "local"),
            "run_command",
            {"argv": argv, "cwd": "."},
            settings=getattr(ctx, "settings", None),
            on_event=on_event,
            task_id=getattr(ctx, "task_id", None),
            tool_call_id=getattr(ctx, "tool_call_id", None),
            run_mode="workspace",
        )
    elif on_event:
        on_event("hook_log", {"message": f"hook command queued: {argv}"})


def _run_http_action(
    action: HookAction,
    *,
    ctx: Any,
    on_event: EventCallback | None,
) -> None:
    url = action.url or ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        if on_event:
            on_event("hook_log", {"message": f"hook http invalid url: {url}"})
        return
    host = (parsed.hostname or "").lower()
    if host not in _LOCALHOST:
        if on_event:
            on_event(
                "hook_log",
                {"message": f"hook http denied (host not allowlisted): {host}"},
            )
        return
    decision = decide_permission(_synthetic_spec("hook_http", network=True), "workspace")
    if decision.deny:
        # In workspace mode network without approval_kind would deny — we set approval_kind
        # so it becomes ask. For async hooks without UI, treat ask as skip unless approved context.
        if on_event:
            on_event("hook_log", {"message": f"hook http requires approval: {decision.reason}"})
        return
    if decision.ask:
        if on_event:
            on_event(
                "hook_log",
                {
                    "message": "hook http deferred (requires approval; not auto-sent)",
                    "url": url,
                },
            )
        return
    try:
        import httpx

        method = action.method or "POST"
        with httpx.Client(timeout=5.0) as client:
            client.request(method, url, json=action.body or {})
        if on_event:
            on_event("hook_log", {"message": f"hook http ok: {method} {host}"})
    except Exception as exc:
        if on_event:
            on_event("hook_log", {"message": f"hook http failed: {exc}"})
