from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


def estimate_message_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += _content_chars(message.get("content"))
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            total += len(json.dumps(tool_calls, ensure_ascii=False))
    return total


def _content_chars(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        size = 0
        for block in content:
            if isinstance(block, dict):
                size += len(str(block.get("text", "")))
                size += len(str(block.get("content", "")))
                size += len(json.dumps(block.get("input", {}), ensure_ascii=False))
            else:
                # anthropic SDK objects
                text = getattr(block, "text", None)
                if text:
                    size += len(str(text))
                else:
                    size += len(str(block))
        return size
    return len(str(content))


def _shrink_openai_message(message: dict[str, Any]) -> bool:
    """Compress bulky tool payloads in-place. Returns True if modified."""
    changed = False
    role = message.get("role")
    if role == "tool":
        content = message.get("content") or ""
        if isinstance(content, str) and len(content) > 400:
            message["content"] = content[:200] + "\n... (历史工具输出已压缩) ..."
            changed = True
    elif role == "assistant" and message.get("tool_calls"):
        new_calls = []
        for call in message["tool_calls"]:
            call = dict(call)
            fn = dict(call.get("function") or {})
            args = fn.get("arguments") or ""
            if len(args) > 300:
                try:
                    parsed = json.loads(args)
                except (TypeError, ValueError):
                    parsed = None
                keys = sorted(parsed)[:20] if isinstance(parsed, dict) else []
                fn["arguments"] = json.dumps(
                    {
                        "_compressed": True,
                        "original_chars": len(args),
                        "keys": keys,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                changed = True
            call["function"] = fn
            new_calls.append(call)
        message["tool_calls"] = new_calls
    return changed


def _has_compact_marker(message: dict[str, Any] | None) -> bool:
    if not message:
        return False
    return "上下文已压缩" in str(message.get("content") or "")


def _openai_units(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        unit = [message]
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if isinstance(calls, list) and calls:
            call_ids = {str(call.get("id")) for call in calls if call.get("id")}
            cursor = index + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if (
                    candidate.get("role") == "tool"
                    and str(candidate.get("tool_call_id")) in call_ids
                ):
                    unit.append(candidate)
                    cursor += 1
                    continue
                break
            index = cursor
        else:
            index += 1
        units.append(unit)
    return units


def _anthropic_has_tool_use(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return bool(
        message.get("role") == "assistant"
        and isinstance(content, list)
        and any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in content
        )
    )


def _anthropic_is_tool_result_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return bool(
        message.get("role") == "user"
        and isinstance(content, list)
        and content
        and all(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
    )


def _anthropic_units(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        unit = [messages[index]]
        if (
            _anthropic_has_tool_use(messages[index])
            and index + 1 < len(messages)
            and _anthropic_is_tool_result_message(messages[index + 1])
        ):
            unit.append(messages[index + 1])
            index += 2
        else:
            index += 1
        units.append(unit)
    return units


def compact_openai_messages(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = 2_500_000,
    keep_recent: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    """Fold early tool outputs when transcript grows too large.

    Only the oldest messages are trimmed; the newest ``keep_recent`` turns stay intact.
    """
    before = estimate_message_chars(messages)
    if before <= max_chars:
        return messages, False

    result = deepcopy(messages)
    system = result[:1] if result and result[0].get("role") == "system" else []
    units = _openai_units(result[len(system) :])
    protect = max(1, min(keep_recent, len(units)))
    changed = False

    for unit in units[:-protect]:
        for message in unit:
            if _shrink_openai_message(message):
                changed = True

    marker_inserted = False
    result = system + [message for unit in units for message in unit]
    while len(units) > protect and estimate_message_chars(result) > max_chars:
        units.pop(0)
        changed = True
        result = system + [message for unit in units for message in unit]
        insert_at = len(system)
        result.insert(
            insert_at,
            {"role": "user", "content": "[系统] 更早的对话与工具输出已压缩，请基于剩余上下文继续。"},
        )
        marker_inserted = True

    after = estimate_message_chars(result)
    if after >= before and not changed:
        return messages, False
    return result, after < before or marker_inserted


def compact_anthropic_messages(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = 2_500_000,
    keep_recent: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    before = estimate_message_chars(messages)
    if before <= max_chars:
        return messages, False

    result = deepcopy(messages)
    units = _anthropic_units(result)
    protect = max(1, min(keep_recent, len(units)))
    changed = False

    for unit in units[:-protect]:
        for item in unit:
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        text = block.get("content") or ""
                        if isinstance(text, str) and len(text) > 400:
                            block["content"] = text[:200] + "\n... (历史工具输出已压缩) ..."
                            changed = True

    marker_inserted = False
    result = [message for unit in units for message in unit]
    while len(units) > protect and estimate_message_chars(result) > max_chars:
        units.pop(0)
        changed = True
        result = [message for unit in units for message in unit]
        result.insert(0, {"role": "user", "content": "[系统] 更早的对话与工具输出已压缩，请基于剩余上下文继续."})
        marker_inserted = True

    after = estimate_message_chars(result)
    if after >= before and not changed:
        return messages, False
    return result, after < before or marker_inserted


def build_session_prior_messages(
    turns: list[dict[str, Any]],
    *,
    max_turns: int = 6,
) -> list[dict[str, Any]]:
    """Build prior messages for legacy direct callers.

    Canonical Agent jobs rebuild history from conversation_events instead.
    """
    messages: list[dict[str, Any]] = []
    for turn in turns[-max_turns:]:
        user = (turn.get("user") or "").strip()
        assistant = (turn.get("assistant") or "").strip()
        files = turn.get("changed_files") or []
        if user:
            messages.append({"role": "user", "content": user})
        if assistant or files:
            suffix = ""
            if files:
                paths = [
                    (item if isinstance(item, str) else item.get("path", ""))
                    for item in files
                ]
                paths = [p for p in paths if p]
                if paths:
                    suffix = "\n\n(本轮改动文件: " + ", ".join(paths[:30]) + ")"
            messages.append({"role": "assistant", "content": (assistant or "(已完成)") + suffix})
    return messages
