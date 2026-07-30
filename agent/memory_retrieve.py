from __future__ import annotations

from typing import Any

from agent.memory_store import MemoryStore, get_memory_store


DEFAULT_MEMORY_BUDGET_CHARS = 6_000
DEFAULT_MEMORY_LIMIT = 6


def retrieve_memories_for_task(
    *,
    user_id: str,
    project_id: str,
    prompt: str,
    tags: list[str] | None = None,
    limit: int = DEFAULT_MEMORY_LIMIT,
    budget_chars: int = DEFAULT_MEMORY_BUDGET_CHARS,
    store: MemoryStore | None = None,
    task_id: str | None = None,
    record_usage: bool = True,
) -> dict[str, Any]:
    """Select active memories for context injection (candidates excluded)."""
    store = store or get_memory_store()
    hits = store.search(
        user_id,
        prompt,
        project_id=project_id,
        status="active",
        tags=tags,
        limit=max(limit * 2, limit),
    )
    selected: list[dict[str, Any]] = []
    used = 0
    reasons: list[str] = []
    for hit in hits:
        block = format_memory_for_context(hit)
        cost = len(block)
        if used + cost > budget_chars:
            reasons.append(f"预算不足，跳过 memory {hit['id']}")
            continue
        selected.append(hit)
        used += cost
        reason = (
            f"project memory [{hit['memory_type']}/{hit['scope']}] "
            f"score={hit.get('score')} id={hit['id']}"
        )
        reasons.append(reason)
        if record_usage:
            store.record_usage(
                hit["id"],
                user_id,
                project_id=project_id,
                task_id=task_id,
                reason=reason,
            )
        if len(selected) >= limit:
            break

    return {
        "selected": selected,
        "total_chars": used,
        "budget_chars": budget_chars,
        "reasons": reasons,
        "context_text": "\n".join(format_memory_for_context(m) for m in selected),
    }


def format_memory_for_context(memory: dict[str, Any]) -> str:
    """Explicitly mark source as project memory (not conversation checkpoint)."""
    tags = ", ".join(memory.get("tags") or [])
    return (
        f"\n--- project memory ({memory.get('memory_type')}, scope={memory.get('scope')}, "
        f"id={memory.get('id')}) ---\n"
        f"title: {memory.get('title')}\n"
        f"tags: {tags}\n"
        f"{memory.get('content')}\n"
        f"--- end project memory ---\n"
    )
