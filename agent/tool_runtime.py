from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.permissions import PermissionDecision, RunMode, decide_permission
from agent.tool_registry import ToolSpec, get_tool_spec


CancelCheck = Callable[[], None]
EventCallback = Callable[[str, Any], None]
StatusCallback = Callable[[str], None]


@dataclass
class ToolContext:
    """Execution context passed to every ToolSpec handler."""

    workspace: Path
    user_id: str
    project_id: str
    task_id: str | None
    tool_call_id: str | None
    settings: Any
    on_event: EventCallback | None
    set_status: StatusCallback | None
    cancel_check: CancelCheck | None


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> str | None:
    """Validate the JSON Schema subset used by built-in and MCP tools."""
    if not isinstance(schema, dict):
        return f"{path} 的 schema 必须是对象"

    if "const" in schema and value != schema["const"]:
        return f"{path} 必须等于 {schema['const']!r}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} 必须是 {schema['enum']!r} 之一"

    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, list):
            continue
        results = [_validate_schema(value, branch, path) for branch in branches]
        valid_count = sum(error is None for error in results)
        if keyword == "allOf" and valid_count != len(results):
            return next(error for error in results if error is not None)
        if keyword == "anyOf" and valid_count == 0:
            return f"{path} 不符合 anyOf 中的任何 schema"
        if keyword == "oneOf" and valid_count != 1:
            return f"{path} 必须且只能符合 oneOf 中的一个 schema"

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_matches(value, item) for item in expected):
            return f"{path} 类型必须是 {expected!r}"
    elif isinstance(expected, str) and not _type_matches(value, expected):
        return f"{path} 类型必须是 {expected}"

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        if not isinstance(properties, dict) or not isinstance(required, list):
            return f"{path} 的对象 schema 无效"
        for key in required:
            if key not in value:
                return f"{path} 缺少必填参数 {key}"
        additional = schema.get("additionalProperties", False)
        for key, item in value.items():
            child_path = f"{path}.{key}"
            child_schema = properties.get(key)
            if child_schema is not None:
                error = _validate_schema(item, child_schema, child_path)
                if error:
                    return error
            elif additional is False:
                return f"{path} 包含未声明参数 {key}"
            elif isinstance(additional, dict):
                error = _validate_schema(item, additional, child_path)
                if error:
                    return error
        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if min_properties is not None and len(value) < int(min_properties):
            return f"{path} 至少需要 {min_properties} 个参数"
        if max_properties is not None and len(value) > int(max_properties):
            return f"{path} 最多允许 {max_properties} 个参数"

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if min_items is not None and len(value) < int(min_items):
            return f"{path} 至少需要 {min_items} 项"
        if max_items is not None and len(value) > int(max_items):
            return f"{path} 最多允许 {max_items} 项"
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                return f"{path} 不允许重复项"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                error = _validate_schema(item, item_schema, f"{path}[{index}]")
                if error:
                    return error

    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if min_length is not None and len(value) < int(min_length):
            return f"{path} 长度不能小于 {min_length}"
        if max_length is not None and len(value) > int(max_length):
            return f"{path} 长度不能大于 {max_length}"
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                if re.search(str(pattern), value) is None:
                    return f"{path} 不符合格式 {pattern!r}"
            except re.error as exc:
                return f"{path} 的 schema pattern 无效: {exc}"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            return f"{path} 不能小于 {minimum}"
        if maximum is not None and value > maximum:
            return f"{path} 不能大于 {maximum}"

    return None


def _validate_input(spec: ToolSpec, tool_input: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Validate model-controlled input before hooks, approval, or execution."""
    if not isinstance(tool_input, dict):
        return {}, "工具输入必须是对象"

    return tool_input, _validate_schema(tool_input, spec.input_schema or {})


def _find_replay(
    tool_input: dict[str, Any],
    name: str,
    recovery_replays: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if not recovery_replays:
        return None
    return next(
        (
            item
            for item in recovery_replays
            if item.get("name") == name
            and (item.get("input") or {}) == (tool_input or {})
        ),
        None,
    )


def _build_approval_payload(
    decision: PermissionDecision,
    tool_input: dict[str, Any],
    spec: ToolSpec,
    *,
    recovery_tool_call_id: str | None = None,
) -> dict[str, Any]:
    """Build the approval payload shown in the UI card."""
    kind = decision.approval_kind

    if kind == "download_file":
        path = tool_input.get("path", "")
        return {
            "message": f"请求下载文件到 {path}（请在对话确认卡片中选择允许或拒绝）",
            "url": tool_input.get("url", ""),
            "path": path,
            "max_bytes": int(tool_input.get("max_bytes", 50 * 1024 * 1024)),
        }

    if kind == "recovery_tool_replay":
        return {
            "message": (
                f"恢复任务准备重新执行中断前未完成的工具 {spec.name}，"
                "需要重新确认。"
            ),
            "name": spec.name,
            "input": tool_input,
            "interrupted_tool_call_id": recovery_tool_call_id,
        }

    return {
        "message": f"请求执行 {spec.name}（请在对话确认卡片中选择允许或拒绝）",
        "name": spec.name,
        **tool_input,
    }


def _approval_denied_result(decision: str, decision_obj: PermissionDecision) -> Any:
    """Return a ToolResult for a denied/timeout/canceled approval."""
    from agent.tools import ToolResult

    reason = {
        "rejected": "用户拒绝了此次操作",
        "timeout": "等待用户确认超时",
        "canceled": "任务已取消，操作中止",
    }.get(decision, f"未获批准: {decision}")
    return ToolResult(
        False,
        reason,
        error_type="ApprovalCanceled" if decision == "canceled" else "ApprovalDenied",
    )


def _permission_denied_result(decision: PermissionDecision) -> Any:
    """Return a ToolResult for a permission denial."""
    from agent.tools import ToolResult

    return ToolResult(
        False,
        decision.reason,
        error_type="PermissionDenied",
    )


def execute_tool(
    workspace: Path,
    user_id: str,
    project_id: str,
    name: str,
    tool_input: dict[str, Any],
    *,
    cancel_check: CancelCheck | None = None,
    settings: Any = None,
    on_event: EventCallback | None = None,
    task_id: str | None = None,
    tool_call_id: str | None = None,
    set_status: StatusCallback | None = None,
    run_mode: RunMode = "workspace",
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
) -> Any:
    """Execute a built-in tool through the unified runtime.

    The pipeline is:
    1. Tool definition lookup and input validation.
    2. PreToolUse hooks (may deny/ask/modify input within safety bounds).
    3. Risk classification and permission decision (hooks cannot weaken denials).
    4. Approval (when required).
    5. Handler execution.
    6. PostToolUse / ToolFailure hooks.
    """
    from agent.approvals import request_user_approval
    from agent.hooks import combine_with_permission, run_hooks
    from agent.tools import ToolResult

    started = time.monotonic()

    spec = get_tool_spec(name)
    if spec is None:
        return ToolResult(False, f"未知工具: {name}")

    candidate_input = tool_input if tool_input is not None else {}
    validated, error = _validate_input(spec, candidate_input)
    if error:
        return ToolResult(False, error, error_type="InvalidToolInput")

    # PreToolUse hooks — may modify input but cannot escalate path access.
    pre = run_hooks(
        "PreToolUse",
        user_id=user_id,
        workspace=workspace,
        tool_name=name,
        tool_input=validated,
        on_event=on_event,
        execute_actions=True,
    )
    if pre.modified_input is not None:
        validated = pre.modified_input
        revalidated, re_error = _validate_input(spec, validated)
        if re_error:
            return ToolResult(False, re_error, error_type="InvalidToolInput")
        validated = revalidated

    if pre.action == "deny":
        return ToolResult(False, pre.reason or "hook denied", error_type="HookDenied")

    replay = _find_replay(validated, name, recovery_replays)
    recovery_tool_call_id = replay.get("tool_call_id") if replay else None

    decision = decide_permission(
        spec,
        run_mode,
        recovery_mode=recovery_mode,
        is_replay=replay is not None,
    )
    decision = combine_with_permission(decision, pre)

    if decision.deny:
        return _permission_denied_result(decision)

    if decision.ask:
        if not task_id or not tool_call_id:
            return _permission_denied_result(
                PermissionDecision(
                    action="deny",
                    reason="需要审批的工具缺少 task_id 或 tool_call_id",
                    matched_rule="runtime:missing_context",
                    approval_kind=decision.approval_kind,
                    risk=decision.risk,
                )
            )
        run_hooks(
            "ApprovalRequired",
            user_id=user_id,
            workspace=workspace,
            tool_name=name,
            tool_input=validated,
            on_event=on_event,
            execute_actions=True,
        )
        approval_payload = _build_approval_payload(
            decision,
            validated,
            spec,
            recovery_tool_call_id=recovery_tool_call_id,
        )
        approval_result = request_user_approval(
            job_id=task_id,
            user_id=user_id,
            kind=decision.approval_kind or "tool",
            tool_call_id=tool_call_id,
            payload=approval_payload,
            on_event=on_event,
            set_status=set_status,
            timeout_sec=max(spec.default_timeout_seconds, 600.0),
            cancel_check=cancel_check,
        )
        if approval_result != "approved":
            return _approval_denied_result(approval_result, decision)
        if recovery_mode and recovery_replays is not None and replay is not None:
            recovery_replays.remove(replay)

    ctx = ToolContext(
        workspace=workspace,
        user_id=user_id,
        project_id=project_id,
        task_id=task_id,
        tool_call_id=tool_call_id,
        settings=settings,
        on_event=on_event,
        set_status=set_status,
        cancel_check=cancel_check,
    )

    try:
        if spec.handler is None:
            return ToolResult(False, f"工具 {name} 未配置 handler")
        result = spec.handler(ctx, validated)
        if not isinstance(result, ToolResult):
            result = ToolResult(bool(result), result)
        post = run_hooks(
            "PostToolUse" if result.ok else "ToolFailure",
            user_id=user_id,
            workspace=workspace,
            tool_name=name,
            tool_input=validated,
            tool_result=result,
            on_event=on_event,
            execute_actions=True,
            ctx=ctx,
        )
        if post.append_context and isinstance(result.output, dict):
            result.output = {
                **result.output,
                "hook_context": list(post.append_context),
            }
        elif post.append_context and isinstance(result.output, str):
            result.output = result.output + "\n" + "\n".join(post.append_context)
        return result
    except Exception as exc:
        # Cancellation and approval persistence must propagate.
        if exc.__class__.__name__ in {
            "CancellationRequested",
            "ApprovalEventPersistenceError",
        }:
            raise
        run_hooks(
            "ToolFailure",
            user_id=user_id,
            workspace=workspace,
            tool_name=name,
            tool_input=validated,
            on_event=on_event,
            execute_actions=True,
            ctx=ctx,
        )
        return ToolResult(
            False,
            f"工具 {name} 执行异常: {exc}",
            error_type=exc.__class__.__name__,
        )
