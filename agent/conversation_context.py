from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from agent.conversation_events import (
    CONTEXT_EVENT_TYPES,
    CONTEXT_NOTE_EVENT_TYPES,
    ConversationEventType as EventType,
)


def select_context_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return ordered model-visible events after the last valid checkpoint."""
    ordered = sorted(
        (dict(event) for event in events),
        key=lambda event: (event.get("seq", 0), event.get("created_at", 0)),
    )
    invalidated_ids = {
        str(_payload(event).get("checkpoint_event_id"))
        for event in ordered
        if event.get("event_type")
        == EventType.CONTEXT_CHECKPOINT_INVALIDATED
        and _payload(event).get("checkpoint_event_id")
    }
    checkpoint_index = -1
    for index, event in enumerate(ordered):
        payload = _payload(event)
        if (
            event.get("event_type") == EventType.CONTEXT_CHECKPOINT
            and str(event.get("id") or "") not in invalidated_ids
            and _checkpoint_text(payload)
            and payload.get("valid", True) is not False
        ):
            checkpoint_index = index
    if checkpoint_index >= 0:
        checkpoint = ordered[checkpoint_index]
        covers = _payload(checkpoint).get("covers_through_seq")
        if isinstance(covers, int):
            retained = [
                event
                for index, event in enumerate(ordered)
                if event.get("seq", 0) > covers
                and index != checkpoint_index
                and event.get("event_type") != EventType.CONTEXT_CHECKPOINT
            ]
            ordered = [checkpoint, *retained]
        else:
            ordered = ordered[checkpoint_index:]

    selected: list[dict[str, Any]] = []
    for event in ordered:
        event_type = event.get("event_type")
        if event_type == EventType.CONTEXT_CHECKPOINT:
            payload = _payload(event)
            if (
                not _checkpoint_text(payload)
                or payload.get("valid", True) is False
            ):
                continue
        if event_type in CONTEXT_EVENT_TYPES:
            selected.append(event)
        elif (
            event_type in CONTEXT_NOTE_EVENT_TYPES
            and event.get("context_visible") is True
        ):
            selected.append(event)
    return selected


def build_openai_messages(
    events: Iterable[dict[str, Any]],
    *,
    current_user_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI Chat Completions messages from canonical events."""
    selected = select_context_events(events)
    paired_tool_calls = _paired_tool_call_ids(selected)
    paired_results = _paired_tool_results(selected, paired_tool_calls)
    assistant_groups = _assistant_groups(selected)
    messages: list[dict[str, Any]] = []
    emitted_groups: set[str] = set()
    available_tool_calls: set[str] = set()
    emitted_tool_results: set[str] = set()

    for index, event in enumerate(selected):
        event_type = event.get("event_type")
        payload = _payload(event)
        if event_type == EventType.USER_MESSAGE:
            text = _user_text(payload)
            if text:
                messages.append({"role": "user", "content": text})
        elif event_type in {
            EventType.SYSTEM_NOTE,
            EventType.RECOVERY_NOTE,
            EventType.CONTEXT_CHECKPOINT,
        }:
            text = (
                _checkpoint_text(payload)
                if event_type == EventType.CONTEXT_CHECKPOINT
                else _note_text(payload)
            )
            if text:
                messages.append({"role": "system", "content": text})
        elif event_type in {EventType.ASSISTANT_MESSAGE, EventType.TOOL_CALL}:
            group_key = _assistant_group_key(event, index)
            if group_key in emitted_groups:
                continue
            group = assistant_groups[group_key]
            message, tool_call_ids = _openai_assistant_message(
                group,
                paired_tool_calls,
            )
            if message is not None:
                messages.append(message)
                available_tool_calls.update(tool_call_ids)
                _append_openai_tool_results(
                    messages,
                    tool_call_ids,
                    paired_results,
                    emitted_tool_results,
                )
            emitted_groups.add(group_key)
        elif event_type == EventType.TOOL_RESULT:
            tool_call_id = _tool_result_id(payload)
            if (
                not tool_call_id
                or tool_call_id not in available_tool_calls
                or tool_call_id in emitted_tool_results
            ):
                continue
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": _tool_result_content(payload),
                }
            )
            emitted_tool_results.add(tool_call_id)

    _append_openai_current_prompt(messages, current_user_prompt)
    return messages


def build_anthropic_messages(
    events: Iterable[dict[str, Any]],
    *,
    current_user_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Build Anthropic Messages API messages from canonical events."""
    selected = select_context_events(events)
    paired_tool_calls = _paired_tool_call_ids(selected)
    paired_results = _paired_tool_results(selected, paired_tool_calls)
    assistant_groups = _assistant_groups(selected)
    messages: list[dict[str, Any]] = []
    emitted_groups: set[str] = set()
    available_tool_calls: set[str] = set()
    emitted_tool_results: set[str] = set()

    for index, event in enumerate(selected):
        event_type = event.get("event_type")
        payload = _payload(event)
        if event_type == EventType.USER_MESSAGE:
            text = _user_text(payload)
            if text:
                _append_anthropic_user_text(messages, text)
        elif event_type in {
            EventType.SYSTEM_NOTE,
            EventType.RECOVERY_NOTE,
            EventType.CONTEXT_CHECKPOINT,
        }:
            text = (
                _checkpoint_text(payload)
                if event_type == EventType.CONTEXT_CHECKPOINT
                else _note_text(payload)
            )
            if text:
                _append_anthropic_user_text(messages, f"[System context]\n{text}")
        elif event_type in {EventType.ASSISTANT_MESSAGE, EventType.TOOL_CALL}:
            group_key = _assistant_group_key(event, index)
            if group_key in emitted_groups:
                continue
            group = assistant_groups[group_key]
            message, tool_call_ids = _anthropic_assistant_message(
                group,
                paired_tool_calls,
            )
            if message is not None:
                messages.append(message)
                available_tool_calls.update(tool_call_ids)
                _append_anthropic_tool_results(
                    messages,
                    tool_call_ids,
                    paired_results,
                    emitted_tool_results,
                )
            emitted_groups.add(group_key)
        elif event_type == EventType.TOOL_RESULT:
            tool_call_id = _tool_result_id(payload)
            if (
                not tool_call_id
                or tool_call_id not in available_tool_calls
                or tool_call_id in emitted_tool_results
            ):
                continue
            block = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": _tool_result_content(payload),
                "is_error": payload.get("ok") is False,
            }
            if (
                messages
                and messages[-1].get("role") == "user"
                and isinstance(messages[-1].get("content"), list)
                and all(
                    isinstance(item, dict)
                    and item.get("type") == "tool_result"
                    for item in messages[-1]["content"]
                )
            ):
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
            emitted_tool_results.add(tool_call_id)

    _append_anthropic_current_prompt(messages, current_user_prompt)
    return messages


def build_provider_messages(
    events: Iterable[dict[str, Any]],
    provider: str,
    *,
    current_user_prompt: str | None = None,
) -> list[dict[str, Any]]:
    normalized = provider.strip().lower()
    if normalized == "anthropic":
        return build_anthropic_messages(
            events,
            current_user_prompt=current_user_prompt,
        )
    return build_openai_messages(
        events,
        current_user_prompt=current_user_prompt,
    )


def _assistant_groups(
    events: list[dict[str, Any]],
) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, event in enumerate(events):
        if event.get("event_type") not in {
            EventType.ASSISTANT_MESSAGE,
            EventType.TOOL_CALL,
        }:
            continue
        key = _assistant_group_key(event, index)
        groups.setdefault(key, []).append((index, event))
    return groups


def _assistant_group_key(event: dict[str, Any], index: int) -> str:
    payload = _payload(event)
    message_id = payload.get("message_id")
    if isinstance(message_id, str) and message_id:
        return f"message:{message_id}"
    return f"event:{event.get('id') or event.get('seq') or index}"


def _openai_assistant_message(
    group: list[tuple[int, dict[str, Any]]],
    paired_tool_calls: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    text_blocks = _group_text_blocks(group)
    tool_calls = _group_tool_calls(group, paired_tool_calls)
    content = "".join(block["text"] for block in text_blocks)
    if not content and not tool_calls:
        return None, []
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content or None,
    }
    call_ids: list[str] = []
    if tool_calls:
        message["tool_calls"] = []
        for call in tool_calls:
            message["tool_calls"].append(
                {
                    "id": call["tool_call_id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": _json_arguments(call["input"]),
                    },
                }
            )
            call_ids.append(call["tool_call_id"])
    return message, call_ids


def _anthropic_assistant_message(
    group: list[tuple[int, dict[str, Any]]],
    paired_tool_calls: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    ordered_blocks: list[tuple[int, int, dict[str, Any]]] = []
    for order, block in enumerate(_group_text_blocks(group)):
        ordered_blocks.append(
            (
                block["block_index"],
                order,
                {"type": "text", "text": block["text"]},
            )
        )
    call_ids: list[str] = []
    offset = len(ordered_blocks)
    for order, call in enumerate(
        _group_tool_calls(group, paired_tool_calls),
        start=offset,
    ):
        ordered_blocks.append(
            (
                call["block_index"],
                order,
                {
                    "type": "tool_use",
                    "id": call["tool_call_id"],
                    "name": call["name"],
                    "input": call["input"],
                },
            )
        )
        call_ids.append(call["tool_call_id"])
    ordered_blocks.sort(key=lambda item: (item[0], item[1]))
    content = [item[2] for item in ordered_blocks]
    if not content:
        return None, []
    return {"role": "assistant", "content": content}, call_ids


def _group_text_blocks(
    group: list[tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    blocks: list[tuple[int, int, dict[str, Any]]] = []
    fallback_index = 0
    for event_order, event in group:
        if event.get("event_type") != EventType.ASSISTANT_MESSAGE:
            continue
        payload = _payload(event)
        raw_blocks = payload.get("text_blocks")
        if isinstance(raw_blocks, list):
            for position, raw_block in enumerate(raw_blocks):
                if not isinstance(raw_block, dict):
                    continue
                text = raw_block.get("text")
                if not isinstance(text, str) or not text:
                    continue
                block_index = _integer_index(
                    raw_block.get("block_index"),
                    fallback_index,
                )
                blocks.append(
                    (
                        block_index,
                        event_order * 1000 + position,
                        {"block_index": block_index, "text": text},
                    )
                )
                fallback_index = max(fallback_index, block_index + 1)
        else:
            text = payload.get("text", payload.get("content", ""))
            if isinstance(text, str) and text:
                blocks.append(
                    (
                        fallback_index,
                        event_order * 1000,
                        {"block_index": fallback_index, "text": text},
                    )
                )
                fallback_index += 1
    blocks.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in blocks]


def _group_tool_calls(
    group: list[tuple[int, dict[str, Any]]],
    paired_tool_calls: set[str],
) -> list[dict[str, Any]]:
    calls: list[tuple[int, int, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for event_order, event in group:
        if event.get("event_type") != EventType.TOOL_CALL:
            continue
        payload = _payload(event)
        tool_call_id = _tool_call_id(payload)
        name = payload.get("name")
        if (
            not tool_call_id
            or tool_call_id not in paired_tool_calls
            or tool_call_id in seen_ids
            or not isinstance(name, str)
            or not name
        ):
            continue
        block_index = _integer_index(payload.get("block_index"), event_order)
        tool_input = payload.get("input", payload.get("arguments", {}))
        calls.append(
            (
                block_index,
                event_order,
                {
                    "block_index": block_index,
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "input": _tool_input(tool_input),
                },
            )
        )
        seen_ids.add(tool_call_id)
    calls.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in calls]


def _paired_tool_call_ids(events: list[dict[str, Any]]) -> set[str]:
    call_positions: dict[str, int] = {}
    paired: set[str] = set()
    for index, event in enumerate(events):
        payload = _payload(event)
        if event.get("event_type") == EventType.TOOL_CALL:
            tool_call_id = _tool_call_id(payload)
            if tool_call_id and tool_call_id not in call_positions:
                call_positions[tool_call_id] = index
        elif event.get("event_type") == EventType.TOOL_RESULT:
            tool_call_id = _tool_result_id(payload)
            if (
                tool_call_id
                and tool_call_id in call_positions
                and call_positions[tool_call_id] < index
            ):
                paired.add(tool_call_id)
    return paired


def _paired_tool_results(
    events: list[dict[str, Any]],
    paired_tool_calls: set[str],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") != EventType.TOOL_RESULT:
            continue
        payload = _payload(event)
        tool_call_id = _tool_result_id(payload)
        if tool_call_id in paired_tool_calls and tool_call_id not in results:
            results[tool_call_id] = payload
    return results


def _append_openai_tool_results(
    messages: list[dict[str, Any]],
    tool_call_ids: list[str],
    paired_results: dict[str, dict[str, Any]],
    emitted_tool_results: set[str],
) -> None:
    for tool_call_id in tool_call_ids:
        payload = paired_results.get(tool_call_id)
        if payload is None or tool_call_id in emitted_tool_results:
            continue
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": _tool_result_content(payload),
            }
        )
        emitted_tool_results.add(tool_call_id)


def _append_anthropic_tool_results(
    messages: list[dict[str, Any]],
    tool_call_ids: list[str],
    paired_results: dict[str, dict[str, Any]],
    emitted_tool_results: set[str],
) -> None:
    blocks: list[dict[str, Any]] = []
    for tool_call_id in tool_call_ids:
        payload = paired_results.get(tool_call_id)
        if payload is None or tool_call_id in emitted_tool_results:
            continue
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": _tool_result_content(payload),
                "is_error": payload.get("ok") is False,
            }
        )
        emitted_tool_results.add(tool_call_id)
    if blocks:
        messages.append({"role": "user", "content": blocks})


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _user_text(payload: dict[str, Any]) -> str:
    content = payload.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def _checkpoint_text(payload: dict[str, Any]) -> str:
    for key in ("summary", "content", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _note_text(payload: dict[str, Any]) -> str:
    for key in ("content", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tool_call_id(payload: dict[str, Any]) -> str:
    value = payload.get("tool_call_id", payload.get("id"))
    return value if isinstance(value, str) else ""


def _tool_result_id(payload: dict[str, Any]) -> str:
    value = payload.get("tool_call_id", payload.get("tool_use_id"))
    return value if isinstance(value, str) else ""


def _tool_input(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value if value is not None else {}


def _json_arguments(value: Any) -> str:
    return json.dumps(
        _tool_input(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _tool_result_content(payload: dict[str, Any]) -> str:
    for key in ("model_output", "content", "output"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if value is not None:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    structured = payload.get("structured_output")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    error_type = payload.get("error_type")
    return str(error_type) if error_type else ""


def _integer_index(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _append_openai_current_prompt(
    messages: list[dict[str, Any]],
    prompt: str | None,
) -> None:
    if not prompt:
        return
    if (
        messages
        and messages[-1].get("role") == "user"
        and messages[-1].get("content") == prompt
    ):
        return
    messages.append({"role": "user", "content": prompt})


def _append_anthropic_user_text(
    messages: list[dict[str, Any]],
    text: str,
) -> None:
    if not messages or messages[-1].get("role") != "user":
        messages.append({"role": "user", "content": text})
        return
    content = messages[-1].get("content")
    text_block = {"type": "text", "text": text}
    if isinstance(content, str):
        messages[-1]["content"] = [
            {"type": "text", "text": content},
            text_block,
        ]
    elif isinstance(content, list):
        content.append(text_block)
    else:
        messages[-1]["content"] = [text_block]


def _append_anthropic_current_prompt(
    messages: list[dict[str, Any]],
    prompt: str | None,
) -> None:
    if not prompt:
        return
    if _anthropic_last_user_text(messages) == prompt:
        return
    _append_anthropic_user_text(messages, prompt)


def _anthropic_last_user_text(messages: list[dict[str, Any]]) -> str | None:
    if not messages or messages[-1].get("role") != "user":
        return None
    content = messages[-1].get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in reversed(content):
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                return block["text"]
    return None
