from __future__ import annotations

import json
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
                fn["arguments"] = args[:150] + "...(compressed)"
                changed = True
            call["function"] = fn
            new_calls.append(call)
        message["tool_calls"] = new_calls
    return changed


def _has_compact_marker(message: dict[str, Any] | None) -> bool:
    if not message:
        return False
    return "上下文已压缩" in str(message.get("content") or "")


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

    result = [dict(message) for message in messages]
    protect = max(2, min(keep_recent, max(0, len(result) - 1)))
    changed = False

    # Compress everything except the newest protect messages
    for message in result[:-protect] if protect else result:
        if _shrink_openai_message(message):
            changed = True

    # Still too large: drop oldest non-system messages (keep working under budget)
    marker_inserted = False
    while len(result) > protect + 1 and estimate_message_chars(result) > max_chars:
        drop_at = 1 if result and result[0].get("role") == "system" else 0
        if drop_at >= len(result) - protect:
            break
        result.pop(drop_at)
        changed = True
        if (
            not marker_inserted
            and result
            and result[0].get("role") == "system"
            and not _has_compact_marker(result[1] if len(result) > 1 else None)
        ):
            result.insert(
                1,
                {
                    "role": "user",
                    "content": "[系统] 更早的对话与工具输出已压缩，请基于剩余上下文继续。",
                },
            )
            marker_inserted = True
            changed = True

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

    result = [dict(message) for message in messages]
    protect = max(2, min(keep_recent, max(0, len(result) - 1)))
    changed = False
    cutoff = max(0, len(result) - protect)

    for index, item in enumerate(result):
        if index >= cutoff:
            continue
        content = item.get("content")
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    text = block.get("content") or ""
                    if isinstance(text, str) and len(text) > 400:
                        block = dict(block)
                        block["content"] = text[:200] + "\n... (历史工具输出已压缩) ..."
                        changed = True
                new_blocks.append(block)
            item["content"] = new_blocks

    marker_inserted = False
    while len(result) > protect and estimate_message_chars(result) > max_chars:
        result.pop(0)
        changed = True
        if not marker_inserted and not (
            result
            and isinstance(result[0].get("content"), str)
            and "上下文已压缩" in result[0].get("content", "")
        ):
            result.insert(
                0,
                {
                    "role": "user",
                    "content": "[系统] 更早的对话与工具输出已压缩，请基于剩余上下文继续。",
                },
            )
            marker_inserted = True
            changed = True

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
