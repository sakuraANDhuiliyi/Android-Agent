from __future__ import annotations

import json
from typing import Any

from agent.conversation_events import (
    ConversationEventStore,
    ConversationEventType as EventType,
)


DEFAULT_EVENT_THRESHOLD = 200
DEFAULT_CHAR_THRESHOLD = 120_000
DEFAULT_KEEP_RECENT_TURNS = 4
MAX_SUMMARY_CHARS = 20_000


def create_semantic_checkpoint(
    event_store: ConversationEventStore,
    conversation_id: str,
    user_id: str,
    *,
    event_threshold: int = DEFAULT_EVENT_THRESHOLD,
    char_threshold: int = DEFAULT_CHAR_THRESHOLD,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
    force: bool = False,
) -> dict[str, Any] | None:
    """Append an extractive semantic checkpoint when history grows large."""
    events = event_store.list_events(conversation_id, user_id=user_id)
    if not events:
        return None

    turn_ids: list[str] = []
    for event in events:
        turn_id = event.get("turn_id")
        if isinstance(turn_id, str) and turn_id and turn_id not in turn_ids:
            turn_ids.append(turn_id)
    if len(turn_ids) <= max(1, keep_recent_turns):
        return None

    retained_turns = set(turn_ids[-max(1, keep_recent_turns):])
    summarized = [
        event
        for event in events
        if event.get("turn_id") not in retained_turns
        and event.get("event_type") != EventType.CONTEXT_CHECKPOINT
    ]
    if not summarized:
        return None
    covers_through_seq = max(int(event["seq"]) for event in summarized)

    prior_covers = 0
    for event in events:
        if event.get("event_type") != EventType.CONTEXT_CHECKPOINT:
            continue
        covers = (event.get("payload") or {}).get("covers_through_seq")
        if isinstance(covers, int):
            prior_covers = max(prior_covers, covers)
    delta = [
        event
        for event in summarized
        if int(event.get("seq") or 0) > prior_covers
    ]
    delta_chars = sum(
        len(json.dumps(event.get("payload") or {}, ensure_ascii=False))
        for event in delta
    )
    if not force and (
        len(delta) < max(1, event_threshold)
        and delta_chars < max(1, char_threshold)
    ):
        return None

    summary = _summarize_events(summarized)
    if not summary:
        return None
    checkpoint_turn_id = str(events[-1]["turn_id"])
    return event_store.append_event_idempotent(
        conversation_id,
        checkpoint_turn_id,
        EventType.CONTEXT_CHECKPOINT,
        f"checkpoint:{conversation_id}:{covers_through_seq}",
        {
            "summary": summary[:MAX_SUMMARY_CHARS],
            "covers_through_seq": covers_through_seq,
            "source_event_count": len(summarized),
            "turn_ids": [
                turn_id for turn_id in turn_ids if turn_id not in retained_turns
            ],
            "generator": "extractive-semantic-v1",
            "valid": True,
        },
        context_visible=True,
    )


def _summarize_events(events: list[dict[str, Any]]) -> str:
    by_turn: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for event in events:
        turn_id = str(event.get("turn_id") or "")
        if not turn_id:
            continue
        if turn_id not in by_turn:
            by_turn[turn_id] = []
            order.append(turn_id)
        by_turn[turn_id].append(event)

    sections = ["Earlier conversation semantic checkpoint:"]
    for position, turn_id in enumerate(order, start=1):
        turn_events = by_turn[turn_id]
        user_text = ""
        assistant_texts: list[tuple[bool, str]] = []
        tools: list[str] = []
        changed_files: list[str] = []
        for event in turn_events:
            payload = event.get("payload") or {}
            event_type = event.get("event_type")
            if event_type == EventType.USER_MESSAGE:
                user_text = _user_text(payload)
            elif event_type == EventType.ASSISTANT_MESSAGE:
                text = _assistant_text(payload)
                if text:
                    assistant_texts.append(
                        (payload.get("is_final") is True, text)
                    )
            elif event_type == EventType.TOOL_CALL:
                tools.append(
                    f"{payload.get('name') or 'tool'} requested"
                )
            elif event_type == EventType.TOOL_RESULT:
                status = "ok" if payload.get("ok") is True else "failed"
                tools.append(
                    f"{payload.get('name') or 'tool'} {status}: "
                    f"{str(payload.get('model_output') or '')[:240]}"
                )
            elif event_type == EventType.CHANGES:
                raw_files = payload.get("files", payload.get("changed_files", []))
                if isinstance(raw_files, list):
                    for item in raw_files:
                        path = item if isinstance(item, str) else item.get("path")
                        if isinstance(path, str) and path:
                            changed_files.append(path)

        final_answers = [text for final, text in assistant_texts if final]
        assistant = (
            final_answers[-1]
            if final_answers
            else assistant_texts[-1][1] if assistant_texts else ""
        )
        lines = [f"Turn {position}:"]
        if user_text:
            lines.append(f"- User intent: {user_text[:800]}")
        if assistant:
            lines.append(f"- Final outcome: {assistant[:1200]}")
        if tools:
            lines.append(f"- Tool activity: {'; '.join(tools[:12])}")
        if changed_files:
            lines.append(
                f"- Changed files: {', '.join(dict.fromkeys(changed_files))}"
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _user_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
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


def _assistant_text(payload: dict[str, Any]) -> str:
    blocks = payload.get("text_blocks")
    if not isinstance(blocks, list):
        return str(payload.get("text") or "")
    return "".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )
