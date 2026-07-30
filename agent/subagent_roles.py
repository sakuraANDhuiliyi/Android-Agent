from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PermissionMode = Literal["read_only", "workspace", "ask"]
IsolationMode = Literal["shared", "worktree", "shared_test"]


@dataclass(frozen=True)
class SubagentRole:
    name: str
    description: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    permission_mode: PermissionMode
    isolation: IsolationMode
    max_turns: int = 8
    context_budget_chars: int = 80_000
    # None = no exclusive write lock (parallel OK).
    write_lock_template: str | None = None
    model: str | None = None
    provider: str | None = None

    def write_lock_key(self, user_id: str, project_id: str, worktree_id: str | None = None) -> str | None:
        if self.write_lock_template is None:
            return None
        return self.write_lock_template.format(
            user_id=user_id,
            project_id=project_id,
            worktree_id=worktree_id or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "permission_mode": self.permission_mode,
            "isolation": self.isolation,
            "max_turns": self.max_turns,
            "context_budget_chars": self.context_budget_chars,
        }


_READ_TOOLS = (
    "list_dir",
    "glob",
    "grep",
    "read_file",
    "git_status",
    "git_diff",
    "git_log",
    "repo_map",
    "search_code",
    "find_symbol",
    "find_references",
    "related_files",
    "list_skills",
    "load_skill",
)

_WRITE_TOOLS = _READ_TOOLS + (
    "str_replace",
    "write_file",
)

_TEST_TOOLS = _READ_TOOLS + (
    "run_command",
    "run_gradle",
)


ROLES: dict[str, SubagentRole] = {
    "explore": SubagentRole(
        name="explore",
        description="只读代码探索，定位文件与符号。",
        system_prompt=(
            "你是只读探索子代理。只使用只读工具了解代码库。"
            "不要修改文件。完成后给出简洁结构化摘要："
            "找到的路径、关键符号、建议的下一步。"
        ),
        allowed_tools=_READ_TOOLS,
        permission_mode="read_only",
        isolation="shared",
        write_lock_template=None,
        max_turns=6,
    ),
    "reviewer": SubagentRole(
        name="reviewer",
        description="只读审阅 diff 与变更风险。",
        system_prompt=(
            "你是只读审阅子代理。审阅 diff/变更，指出风险与测试建议。"
            "不要修改文件。返回结构化审阅摘要。"
        ),
        allowed_tools=_READ_TOOLS,
        permission_mode="read_only",
        isolation="shared",
        write_lock_template=None,
        max_turns=6,
    ),
    "test_runner": SubagentRole(
        name="test_runner",
        description="只读源码，可运行测试/构建（可写构建缓存）。",
        system_prompt=(
            "你是测试子代理。可以运行测试或构建命令，不要修改源码文件。"
            "返回测试结果摘要、失败用例与日志引用。"
        ),
        allowed_tools=_TEST_TOOLS,
        permission_mode="workspace",
        isolation="shared_test",
        write_lock_template="test:{user_id}:{project_id}",
        max_turns=8,
    ),
    "implementer": SubagentRole(
        name="implementer",
        description="在独立 Git worktree 中实现改动。",
        system_prompt=(
            "你是实现子代理，工作在隔离的 Git worktree 中。"
            "完成改动后给出变更摘要与涉及文件列表。不要 push。不要创建子代理。"
        ),
        allowed_tools=_WRITE_TOOLS,
        permission_mode="workspace",
        isolation="worktree",
        write_lock_template="wt:{worktree_id}",
        max_turns=12,
    ),
}


DEFAULT_MAX_SUBAGENTS = 3
DEFAULT_WAIT_TIMEOUT_SECONDS = 120.0


def get_role(name: str) -> SubagentRole:
    role = ROLES.get((name or "").strip())
    if role is None:
        raise ValueError(f"未知 subagent 角色: {name!r}；可选: {', '.join(sorted(ROLES))}")
    return role


def list_roles() -> list[dict[str, Any]]:
    return [r.to_dict() for r in ROLES.values()]
