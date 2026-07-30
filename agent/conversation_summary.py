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
MAX_SUMMARY_CHARS = 80_000
CHECKPOINT_SCHEMA_VERSION = 2


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

    state = _build_structured_state(summarized)
    validation_errors = _validate_structured_state(
        state,
        summarized,
        covers_through_seq,
    )
    if validation_errors:
        return None
    summary = _format_structured_state(state)
    if not summary or len(summary) > MAX_SUMMARY_CHARS:
        # Correctness wins over compaction: retain raw events instead of
        # activating a checkpoint that silently drops constraints.
        return None
    checkpoint_turn_id = str(events[-1]["turn_id"])
    return event_store.append_event_idempotent(
        conversation_id,
        checkpoint_turn_id,
        EventType.CONTEXT_CHECKPOINT,
        f"checkpoint:{conversation_id}:{covers_through_seq}",
        {
            "summary": summary,
            "state": state,
            "covers_through_seq": covers_through_seq,
            "source_event_count": len(summarized),
            "turn_ids": [
                turn_id for turn_id in turn_ids if turn_id not in retained_turns
            ],
            "generator": "structured-deterministic-v2",
            "checkpoint_version": CHECKPOINT_SCHEMA_VERSION,
            "validation": {
                "valid": True,
                "errors": [],
                "validated_source_count": len(summarized),
            },
            "valid": True,
        },
        context_visible=True,
    )


def _fact(text: str, seq: int, **extra: Any) -> dict[str, Any]:
    return {"text": text, "source_seq": seq, **extra}


def _build_structured_state(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    state: dict[str, list[dict[str, Any]]] = {
        "goal": [],
        "constraints": [],
        "decisions": [],
        "unresolved": [],
        "files": [],
        "tests": [],
        "tool_facts": [],
        "errors": [],
    }
    seen: dict[str, set[str]] = {key: set() for key in state}

    def add(category: str, text: str, seq: int, **extra: Any) -> None:
        cleaned = " ".join(str(text).split()).strip()
        if not cleaned:
            return
        key = json.dumps([cleaned, extra], ensure_ascii=False, sort_keys=True)
        if key in seen[category]:
            return
        seen[category].add(key)
        state[category].append(_fact(cleaned, seq, **extra))

    for event in events:
        payload = event.get("payload") or {}
        event_type = event.get("event_type")
        seq = int(event.get("seq") or 0)
        if seq <= 0:
            continue
        if event_type == EventType.USER_MESSAGE:
            text = _user_text(payload)
            add("goal", text, seq)
            for line in text.splitlines():
                if any(
                    marker in line.lower()
                    for marker in (
                        "必须",
                        "禁止",
                        "不要",
                        "只",
                        "require",
                        "must",
                        "never",
                        "without",
                    )
                ):
                    add("constraints", line, seq)
        elif event_type == EventType.ASSISTANT_MESSAGE:
            text = _assistant_text(payload)
            if text and payload.get("is_final") is True:
                add("decisions", text, seq)
        elif event_type == EventType.TOOL_CALL:
            name = str(payload.get("name") or "tool")
            add(
                "tool_facts",
                f"{name} called with {json.dumps(payload.get('input') or {}, ensure_ascii=False, sort_keys=True)}",
                seq,
                tool_call_id=payload.get("tool_call_id"),
                name=name,
                status="requested",
            )
        elif event_type == EventType.TOOL_RESULT:
            name = str(payload.get("name") or "tool")
            ok = payload.get("ok") is True
            output = str(payload.get("model_output") or "")
            add(
                "tool_facts",
                f"{name} {'succeeded' if ok else 'failed'}: {output}",
                seq,
                tool_call_id=payload.get("tool_call_id"),
                name=name,
                status="succeeded" if ok else "failed",
            )
            if name == "run_gradle":
                add("tests", output, seq, ok=ok, name=name)
            if not ok:
                add(
                    "errors",
                    output or f"{name} failed",
                    seq,
                    error_type=payload.get("error_type"),
                    tool_call_id=payload.get("tool_call_id"),
                )
                add("unresolved", f"Resolve failed tool {name}", seq)
        elif event_type == EventType.CHANGES:
            raw_files = payload.get("files", payload.get("changed_files", []))
            if isinstance(raw_files, list):
                for item in raw_files:
                    path = item if isinstance(item, str) else item.get("path")
                    if isinstance(path, str) and path:
                        add(
                            "files",
                            path,
                            seq,
                            change=(
                                item.get("change")
                                if isinstance(item, dict)
                                else "changed"
                            ),
                        )
        elif event_type in {
            EventType.TURN_FAILED,
            EventType.TURN_INTERRUPTED,
            EventType.TURN_CANCELED,
        }:
            error = str(payload.get("error") or payload.get("message") or event_type)
            add("errors", error, seq, error_type=event_type)
            add("unresolved", error, seq)
    return state


def _validate_structured_state(
    state: dict[str, list[dict[str, Any]]],
    source_events: list[dict[str, Any]],
    covers_through_seq: int,
) -> list[str]:
    errors: list[str] = []
    source_seqs = {int(event.get("seq") or 0) for event in source_events}
    tool_call_ids = {
        str((event.get("payload") or {}).get("tool_call_id"))
        for event in source_events
        if event.get("event_type") == EventType.TOOL_CALL
        and (event.get("payload") or {}).get("tool_call_id")
    }
    for category, facts in state.items():
        if category not in {
            "goal",
            "constraints",
            "decisions",
            "unresolved",
            "files",
            "tests",
            "tool_facts",
            "errors",
        }:
            errors.append(f"unknown category: {category}")
        for fact in facts:
            seq = fact.get("source_seq")
            if not isinstance(seq, int) or seq not in source_seqs:
                errors.append(f"{category} fact has unknown source_seq: {seq}")
            elif seq > covers_through_seq:
                errors.append(f"{category} fact exceeds coverage: {seq}")
            tool_call_id = fact.get("tool_call_id")
            if (
                tool_call_id
                and fact.get("status") == "requested"
                and str(tool_call_id) not in tool_call_ids
            ):
                errors.append(f"unknown tool_call_id: {tool_call_id}")
    return errors


def _format_structured_state(
    state: dict[str, list[dict[str, Any]]],
) -> str:
    sections = ["Earlier conversation structured checkpoint:"]
    labels = {
        "goal": "Goals",
        "constraints": "Constraints",
        "decisions": "Decisions and outcomes",
        "unresolved": "Unresolved items",
        "files": "File state",
        "tests": "Tests",
        "tool_facts": "Tool facts",
        "errors": "Errors",
    }
    for category, label in labels.items():
        facts = state.get(category) or []
        if not facts:
            continue
        sections.append(
            label
            + ":\n"
            + "\n".join(
                f"- [source seq {fact['source_seq']}] {fact['text']}"
                for fact in facts
            )
        )
    return "\n\n".join(sections)


def invalidate_checkpoint(
    event_store: ConversationEventStore,
    conversation_id: str,
    turn_id: str,
    checkpoint_event_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    return event_store.append_event_idempotent(
        conversation_id,
        turn_id,
        EventType.CONTEXT_CHECKPOINT_INVALIDATED,
        f"checkpoint-invalidated:{checkpoint_event_id}",
        {
            "checkpoint_event_id": checkpoint_event_id,
            "reason": reason,
            "valid": False,
        },
        context_visible=False,
    )


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
