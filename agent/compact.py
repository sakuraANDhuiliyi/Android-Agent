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


def compact_openai_messages(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = 80_000,
) -> tuple[list[dict[str, Any]], bool]:
    """Fold early tool outputs when transcript grows too large."""
    if estimate_message_chars(messages) <= max_chars:
        return messages, False

    compacted = [dict(message) for message in messages]
    # Keep system + newest half; compress older tool results.
    keep_tail = max(6, len(compacted) // 2)
    head = compacted[:-keep_tail]
    tail = compacted[-keep_tail:]

    changed = False
    for message in head:
        role = message.get("role")
        if role == "tool":
            content = message.get("content") or ""
            if isinstance(content, str) and len(content) > 400:
                message["content"] = content[:200] + "\n... (历史工具输出已压缩) ..."
                changed = True
        elif role == "assistant" and message.get("tool_calls"):
            # Drop bulky arguments from old tool calls
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

    result = head + tail
    # If still too large, drop oldest non-system messages until under budget.
    while len(result) > 3 and estimate_message_chars(result) > max_chars:
        # Never drop index 0 if system
        drop_at = 1 if result and result[0].get("role") == "system" else 0
        if drop_at >= len(result) - 2:
            break
        result.pop(drop_at)
        changed = True
        if result and result[0].get("role") == "system" and len(result) > 1:
            # Insert a boundary marker once
            if result[1].get("role") != "user" or "上下文已压缩" not in str(result[1].get("content", "")):
                result.insert(
                    1,
                    {
                        "role": "user",
                        "content": "[系统] 更早的对话与工具输出已压缩，请基于剩余上下文继续。",
                    },
                )
                changed = True
                break

    return result, changed


def compact_anthropic_messages(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = 80_000,
) -> tuple[list[dict[str, Any]], bool]:
    if estimate_message_chars(messages) <= max_chars:
        return messages, False

    compacted = []
    changed = False
    for index, message in enumerate(messages):
        item = dict(message)
        content = item.get("content")
        # Keep the last few messages intact
        near_end = index >= max(0, len(messages) - 4)
        if isinstance(content, list) and not near_end:
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
        compacted.append(item)

    while len(compacted) > 2 and estimate_message_chars(compacted) > max_chars:
        compacted.pop(0)
        changed = True
        if compacted and not (
            isinstance(compacted[0].get("content"), str)
            and "上下文已压缩" in compacted[0].get("content", "")
        ):
            compacted.insert(
                0,
                {
                    "role": "user",
                    "content": "[系统] 更早的对话与工具输出已压缩，请基于剩余上下文继续。",
                },
            )
            break

    return compacted, changed


def build_session_prior_messages(
    turns: list[dict[str, Any]],
    *,
    max_turns: int = 6,
) -> list[dict[str, Any]]:
    """Build provider-agnostic prior user/assistant messages from saved turns."""
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
