from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent.tool_registry import ToolSpec

RiskLevel = Literal["read", "workspace_write", "network", "process", "destructive"]
RunMode = Literal["ask", "workspace", "read_only"]


@dataclass
class PermissionDecision:
    """Structured permission decision for a tool invocation."""

    action: Literal["allow", "deny", "ask"]
    reason: str
    matched_rule: str
    approval_kind: str | None = None
    risk: RiskLevel = "read"

    @property
    def allow(self) -> bool:
        return self.action == "allow"

    @property
    def deny(self) -> bool:
        return self.action == "deny"

    @property
    def ask(self) -> bool:
        return self.action == "ask"


def classify_risk(tool_spec: ToolSpec) -> RiskLevel:
    """Classify a tool's highest risk level."""
    if tool_spec.destructive:
        return "destructive"
    if tool_spec.network_access:
        return "network"
    if tool_spec.starts_process:
        return "process"
    if tool_spec.workspace_write:
        return "workspace_write"
    if tool_spec.read_only:
        return "read"
    return "read"


def decide_permission(
    tool_spec: ToolSpec,
    run_mode: RunMode = "workspace",
    *,
    recovery_mode: bool = False,
    is_replay: bool = False,
) -> PermissionDecision:
    """Return a structured decision for running ``tool_spec`` in ``run_mode``.

    Rules:
    - read_only: only read-only tools are allowed.
    - workspace: read and workspace_write are allowed unless the tool declares
      an explicit approval_kind; network/process/destructive ask if they have
      an approval_kind, otherwise deny.
    - ask: read allowed; any non-read tool with an approval_kind asks; others
      are denied.
    - Recovery replay of a tool whose replay_policy requires approval is
      always promoted to ask, regardless of run_mode.
    """
    risk = classify_risk(tool_spec)
    approval_kind = tool_spec.approval_kind

    if recovery_mode and is_replay and tool_spec.replay_policy == "requires_approval_on_recovery":
        return PermissionDecision(
            action="ask",
            reason="恢复任务中重放有副作用的工具调用需要重新确认",
            matched_rule="recovery_replay",
            approval_kind="recovery_tool_replay",
            risk=risk,
        )

    if run_mode == "read_only":
        if risk == "read":
            return PermissionDecision(
                action="allow",
                reason="只读工具在 read_only 模式下允许",
                matched_rule="read_only:read",
                risk=risk,
            )
        return PermissionDecision(
            action="deny",
            reason="read_only 模式仅允许只读工具",
            matched_rule="read_only:deny",
            approval_kind=approval_kind,
            risk=risk,
        )

    if run_mode == "workspace":
        if risk in {"read", "workspace_write"}:
            if approval_kind is None:
                return PermissionDecision(
                    action="allow",
                    reason="workspace 模式下允许工作区内普通读写",
                    matched_rule="workspace:workspace_write",
                    risk=risk,
                )
            return PermissionDecision(
                action="ask",
                reason="工具显式声明需要审批",
                matched_rule="workspace:approval_kind",
                approval_kind=approval_kind,
                risk=risk,
            )
        if approval_kind:
            return PermissionDecision(
                action="ask",
                reason="workspace 模式下网络、进程或破坏性操作需要审批",
                matched_rule="workspace:risk_ask",
                approval_kind=approval_kind,
                risk=risk,
            )
        return PermissionDecision(
            action="deny",
            reason="workspace 模式下不允许此类风险操作",
            matched_rule="workspace:risk_deny",
            approval_kind=approval_kind,
            risk=risk,
        )

    # ask mode
    if risk == "read":
        return PermissionDecision(
            action="allow",
            reason="只读工具允许",
            matched_rule="ask:read",
            risk=risk,
        )
    if approval_kind:
        return PermissionDecision(
            action="ask",
            reason="ask 模式下风险操作需要审批",
            matched_rule="ask:risk_ask",
            approval_kind=approval_kind,
            risk=risk,
        )
    return PermissionDecision(
        action="deny",
        reason="ask 模式下未配置审批类别的风险操作不允许",
        matched_rule="ask:risk_deny",
        approval_kind=approval_kind,
        risk=risk,
    )
