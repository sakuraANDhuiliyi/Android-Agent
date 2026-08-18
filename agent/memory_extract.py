from __future__ import annotations

import re
from typing import Any, Protocol

from agent.memory_store import MemoryStore, get_memory_store
from agent.redaction import redact_sensitive_text


class MemoryExtractor(Protocol):
    """Pluggable extractor — real model adapters implement this later."""

    def extract(
        self,
        events: list[dict[str, Any]],
        *,
        user_prompt: str = "",
        final_answer: str = "",
        changed_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        ...


_TYPE_HINTS: list[tuple[str, re.Pattern[str], list[str]]] = [
    (
        "architecture",
        re.compile(r"(?i)\b(architecture|架构|分层|module structure|包结构)\b"),
        ["architecture"],
    ),
    (
        "convention",
        re.compile(r"(?i)\b(convention|约定|规范|naming|ViewBinding|禁止 findViewById)\b"),
        ["convention"],
    ),
    (
        "decision",
        re.compile(r"(?i)\b(decision|决定|采用|选择了|we (decided|chose)|结论)\b"),
        ["decision"],
    ),
    (
        "workflow",
        re.compile(r"(?i)\b(workflow|流程|步骤|assembleDebug|构建流程)\b"),
        ["workflow"],
    ),
    (
        "known_issue",
        re.compile(r"(?i)\b(known.?issue|已知问题|坑|workaround|失败原因|bug)\b"),
        ["known_issue"],
    ),
    (
        "preference",
        re.compile(r"(?i)\b(preference|偏好|喜欢|prefer|always use)\b"),
        ["preference"],
    ),
]

_MEMORY_LINE = re.compile(
    r"(?im)^\s*(?:[-*]|\d+\.)\s*(?:\[?(architecture|convention|decision|workflow|known_issue|preference)\]?\s*[:：-]?\s*)?(.+)$"
)


class DeterministicMemoryExtractor:
    """Offline, deterministic extractor for tests and default production fallback."""

    def extract(
        self,
        events: list[dict[str, Any]],
        *,
        user_prompt: str = "",
        final_answer: str = "",
        changed_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        texts: list[str] = []
        if user_prompt:
            texts.append(user_prompt)
        if final_answer:
            texts.append(final_answer)
        source_seq: int | None = None
        for event in events:
            et = event.get("event_type") or event.get("type")
            payload = event.get("payload") or {}
            if isinstance(event.get("seq"), int):
                source_seq = event["seq"]
            if et in {"assistant_message", "user_message"}:
                text = _payload_text(payload)
                if text:
                    texts.append(text)
            if et == "changes":
                files = payload.get("files") or []
                if files:
                    texts.append("changed files: " + ", ".join(str(f) for f in files[:20]))

        blob = "\n".join(texts)
        blob = redact_sensitive_text(blob)
        candidates: list[dict[str, Any]] = []

        # Explicit MEMORY: lines in final answer.
        for line in (final_answer or "").splitlines():
            m = re.match(
                r"(?i)^\s*MEMORY\s*[:：]\s*\[?(\w+)\]?\s*[:：-]?\s*(.+)$",
                line.strip(),
            )
            if m:
                mtype = m.group(1).lower()
                if mtype not in {
                    "architecture",
                    "convention",
                    "decision",
                    "workflow",
                    "known_issue",
                    "preference",
                }:
                    mtype = "decision"
                body = m.group(2).strip()
                if len(body) >= 8:
                    candidates.append(
                        _candidate(
                            mtype,
                            body,
                            tags=[mtype, "explicit"],
                            source_seq=source_seq,
                            confidence=0.9,
                        )
                    )

        # Heuristic type hits from combined text.
        for mtype, pattern, tags in _TYPE_HINTS:
            if not pattern.search(blob):
                continue
            # Take a short supporting sentence.
            snippet = _best_sentence(blob, pattern)
            if not snippet or len(snippet) < 12:
                continue
            # Skip huge tool dumps.
            if len(snippet) > 800 or snippet.count("\n") > 12:
                continue
            candidates.append(
                _candidate(
                    mtype,
                    snippet,
                    tags=tags,
                    source_seq=source_seq,
                    confidence=0.55,
                )
            )

        # From changed files → weak workflow/architecture hint.
        if changed_files:
            paths = [str(p) for p in changed_files[:8]]
            if any(p.endswith(".xml") for p in paths):
                candidates.append(
                    _candidate(
                        "convention",
                        "本轮主要修改了 Android XML/资源文件：" + ", ".join(paths),
                        tags=["convention", "ui"],
                        source_seq=source_seq,
                        confidence=0.4,
                    )
                )

        # Dedupe within this batch by normalized content.
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in candidates:
            key = re.sub(r"\s+", " ", item["content"].lower())[:240]
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique[:8]


def _candidate(
    memory_type: str,
    content: str,
    *,
    tags: list[str],
    source_seq: int | None,
    confidence: float = 0.6,
) -> dict[str, Any]:
    content = redact_sensitive_text(content.strip())
    title = content.split("\n", 1)[0][:80]
    return {
        "memory_type": memory_type,
        "title": title,
        "content": content[:2000],
        "tags": tags,
        "source_event_seq": source_seq,
        "scope": "project",
        "confidence": confidence,
    }


def _payload_text(payload: dict[str, Any]) -> str:
    if payload.get("text"):
        return str(payload["text"])
    blocks = payload.get("text_blocks") or payload.get("content") or []
    parts: list[str] = []
    if isinstance(blocks, str):
        return blocks
    for block in blocks:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
            elif block.get("text"):
                parts.append(str(block["text"]))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _best_sentence(blob: str, pattern: re.Pattern[str]) -> str:
    for line in blob.splitlines():
        line = line.strip()
        if pattern.search(line) and 12 <= len(line) <= 400:
            return line
    match = pattern.search(blob)
    if not match:
        return ""
    start = max(0, match.start() - 40)
    end = min(len(blob), match.end() + 120)
    return re.sub(r"\s+", " ", blob[start:end]).strip()


_default_extractor: MemoryExtractor = DeterministicMemoryExtractor()


def set_memory_extractor(extractor: MemoryExtractor) -> None:
    global _default_extractor
    _default_extractor = extractor


def get_memory_extractor() -> MemoryExtractor:
    return _default_extractor


def generate_candidates_for_turn(
    *,
    user_id: str,
    project_id: str,
    conversation_id: str,
    events: list[dict[str, Any]],
    user_prompt: str = "",
    final_answer: str = "",
    changed_files: list[str] | None = None,
    store: MemoryStore | None = None,
    extractor: MemoryExtractor | None = None,
    scope: str = "project",
) -> list[dict[str, Any]]:
    """Create candidate memories from a completed turn. Never auto-activates."""
    store = store or get_memory_store()
    extractor = extractor or get_memory_extractor()
    raw = extractor.extract(
        events,
        user_prompt=user_prompt,
        final_answer=final_answer,
        changed_files=changed_files,
    )
    created: list[dict[str, Any]] = []
    max_seq = None
    for event in events:
        if isinstance(event.get("seq"), int):
            max_seq = event["seq"] if max_seq is None else max(max_seq, event["seq"])

    for item in raw:
        try:
            mem = store.create_memory(
                user_id=user_id,
                project_id=project_id,
                scope=item.get("scope") or scope,
                memory_type=item["memory_type"],
                title=item["title"],
                content=item["content"],
                tags=item.get("tags") or [],
                status="candidate",
                source_conversation_id=conversation_id,
                source_event_seq=item.get("source_event_seq") or max_seq,
                confidence=item.get("confidence"),
            )
            created.append(mem)
        except ValueError:
            # secret / empty — skip
            continue
    return created
