"""Shared context-quality scorers for offline evals and unit tests."""

from __future__ import annotations

from typing import Any, Iterable


def iter_facts(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    if not isinstance(state, dict):
        return facts
    for value in state.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                facts.append(item)
    return facts


def fact_blob(facts: Iterable[dict[str, Any]] | str) -> str:
    if isinstance(facts, str):
        return facts
    return "\n".join(str(item.get("text") or "") for item in facts)


def constraint_recall(
    expected: Iterable[str],
    observed: Iterable[dict[str, Any]] | str,
) -> float:
    needles = [item for item in expected if item]
    if not needles:
        return 1.0
    blob = fact_blob(observed)
    hits = sum(1 for item in needles if item in blob)
    return hits / len(needles)


def tool_chain_complete(events: Iterable[dict[str, Any]]) -> float:
    calls: set[str] = set()
    results: set[str] = set()
    for event in events:
        payload = event.get("payload") or {}
        event_type = event.get("event_type") or event.get("type")
        tool_id = payload.get("tool_call_id")
        if not tool_id:
            continue
        if event_type == "tool_call":
            calls.add(str(tool_id))
        elif event_type == "tool_result":
            results.add(str(tool_id))
    if not calls:
        return 1.0
    return len(calls & results) / len(calls)


def hallucination_rate(
    state: dict[str, Any] | None,
    source_seqs: Iterable[int],
) -> float:
    allowed = {int(seq) for seq in source_seqs}
    facts = iter_facts(state)
    if not facts:
        return 0.0
    bad = 0
    for fact in facts:
        seq = fact.get("source_seq")
        if not isinstance(seq, int) or seq not in allowed:
            bad += 1
    return bad / len(facts)


def unresolved_retention(
    before: Iterable[str],
    after: Iterable[str],
) -> float:
    prior = [item for item in before if item]
    if not prior:
        return 1.0
    later = list(after)
    hits = 0
    for item in prior:
        if any(item in other or other in item for other in later):
            hits += 1
    return hits / len(prior)


def token_savings(original_chars: int, compressed_chars: int) -> float:
    if original_chars <= 0:
        return 0.0
    return max(0.0, 1.0 - (max(0, compressed_chars) / original_chars))


def openai_tool_ids(messages: Iterable[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            tool_id = call.get("id") or call.get("tool_call_id")
            if tool_id:
                ids.append(str(tool_id))
        if message.get("role") == "tool" and message.get("tool_call_id"):
            ids.append(str(message["tool_call_id"]))
    return ids


def anthropic_tool_ids(messages: Iterable[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id"):
                ids.append(str(block["id"]))
            elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                ids.append(str(block["tool_use_id"]))
    return ids


def openai_tool_pairing_valid(messages: list[dict[str, Any]]) -> bool:
    """assistant.tool_calls must be followed immediately by matching tool messages."""
    for index, message in enumerate(messages):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        expected = [
            str(call.get("id") or "")
            for call in message["tool_calls"]
            if call.get("id")
        ]
        seen: list[str] = []
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool":
            seen.append(str(messages[cursor].get("tool_call_id") or ""))
            cursor += 1
        if seen != expected:
            return False
    return True


def chars_of(payload: Any) -> int:
    import json

    return len(json.dumps(payload, ensure_ascii=False, default=str))
