from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agent.tool_registry import ToolSpec

RiskLevel = Literal["read", "workspace_write", "network", "process", "destructive"]
RunMode = Literal["ask", "workspace", "read_only"]
PermissionProfile = Literal["safe", "standard", "full_access"]

# Profiles are the user-facing permission presets. They map each risk level to
# an action, plus tool-name / approval-kind overrides:
# - allow_tools: always allowed regardless of risk (e.g. sandboxed gradle tasks)
# - always_ask_kinds: approval kinds that ask in every profile (external
#   publish / credential-touching integrations)
PROFILE_RULES: dict[str, dict[str, Any]] = {
    "safe": {
        "label": "安全",
        "description": "只读、搜索、构建与测试自动执行；写入文件、联网、运行命令均需确认。",
        "risk_actions": {
            "read": "allow",
            "workspace_write": "ask",
            "process": "ask",
            "network": "ask",
            "destructive": "ask",
        },
        "allow_tools": {"run_gradle"},
        "always_ask_kinds": {"download_file", "mcp_tool"},
    },
    "standard": {
        "label": "标准",
        "description": "工作区读写、Git 与构建测试自动执行；联网、命令与破坏性操作需确认。",
        "risk_actions": {
            "read": "allow",
            "workspace_write": "allow",
            "process": "ask",
            "network": "ask",
            "destructive": "ask",
        },
        "allow_tools": {"run_gradle"},
        "always_ask_kinds": {"download_file", "mcp_tool"},
    },
    "full_access": {
        "label": "完全访问",
        "description": "大部分操作自动执行；破坏性操作、下载与外部服务调用始终需要确认。",
        "risk_actions": {
            "read": "allow",
            "workspace_write": "allow",
            "process": "allow",
            "network": "allow",
            "destructive": "ask",
        },
        "allow_tools": set(),
        "always_ask_kinds": {"download_file", "mcp_tool", "process"},
    },
}

VALID_PROFILES = frozenset(PROFILE_RULES)


def profile_summary() -> list[dict[str, Any]]:
    return [
        {
            "profile": name,
            "label": rules["label"],
            "description": rules["description"],
            "risk_actions": dict(rules["risk_actions"]),
        }
        for name, rules in PROFILE_RULES.items()
    ]


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


def _profile_decision(
    tool_spec: ToolSpec,
    profile: str,
    risk: RiskLevel,
) -> PermissionDecision | None:
    rules = PROFILE_RULES.get(profile)
    if rules is None:
        return None
    approval_kind = tool_spec.approval_kind
    if tool_spec.name in rules["always_ask_kinds"] or (
        approval_kind and approval_kind in rules["always_ask_kinds"]
    ):
        return PermissionDecision(
            action="ask",
            reason="该类操作涉及外部系统或破坏性影响，当前权限档位下始终需要确认",
            matched_rule=f"profile:{profile}:always_ask",
            approval_kind=approval_kind or "tool",
            risk=risk,
        )
    if tool_spec.name in rules["allow_tools"]:
        return PermissionDecision(
            action="allow",
            reason=f"{profile} 档位允许构建/测试类工具",
            matched_rule=f"profile:{profile}:allow_tools",
            approval_kind=approval_kind,
            risk=risk,
        )
    action = rules["risk_actions"].get(risk, "ask")
    if action == "allow":
        return PermissionDecision(
            action="allow",
            reason=f"{rules['label']}档位自动允许该风险级别操作",
            matched_rule=f"profile:{profile}:{risk}:allow",
            approval_kind=approval_kind,
            risk=risk,
        )
    if action == "deny":
        return PermissionDecision(
            action="deny",
            reason=f"{rules['label']}档位不允许该风险级别操作",
            matched_rule=f"profile:{profile}:{risk}:deny",
            approval_kind=approval_kind,
            risk=risk,
        )
    return PermissionDecision(
        action="ask",
        reason=f"{rules['label']}档位下该风险级别操作需要确认",
        matched_rule=f"profile:{profile}:{risk}:ask",
        approval_kind=approval_kind or "tool",
        risk=risk,
    )


def decide_permission(
    tool_spec: ToolSpec,
    run_mode: RunMode = "workspace",
    *,
    recovery_mode: bool = False,
    is_replay: bool = False,
    profile: str | None = None,
) -> PermissionDecision:
    """Return a structured decision for running ``tool_spec`` in ``run_mode``.

    Rules:
    - A permission profile (safe/standard/full_access), when provided, takes
      precedence over the legacy run mode mapping.
    - read_only: only read-only tools are allowed.
    - workspace: read and workspace_write are allowed unless the tool declares
      an explicit approval_kind; build/test tools (sandboxed gradle tasks) are
      allowed; network/process/destructive ask if they have an approval_kind,
      otherwise deny.
    - ask: read allowed; any non-read tool with an approval_kind asks; others
      are denied.
    - Recovery replay of a tool whose replay_policy requires approval is
      always promoted to ask, regardless of run_mode or profile.
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

    if profile and profile in PROFILE_RULES:
        decision = _profile_decision(tool_spec, profile, risk)
        if decision is not None:
            return decision

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
        if tool_spec.category == "build" and not tool_spec.destructive:
            return PermissionDecision(
                action="allow",
                reason="workspace 模式下允许沙箱化的构建/测试任务",
                matched_rule="workspace:build_allow",
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
