from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.repo_index import RepoIndex


# Rough char cost estimates for budgeting.
CHARS_PER_TOKEN_ESTIMATE = 4
OVERHEAD_CHARS = 200


def _extract_keywords(prompt: str) -> list[str]:
    """Extract likely meaningful keywords from the user prompt."""
    # Keep camelCase / snake_case identifiers and words longer than 2 chars.
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", prompt)
    # Split camelCase into words too.
    split: list[str] = []
    for word in words:
        split.extend(re.findall(r"[A-Z][a-z]*|[a-z]+", word))
    seen: set[str] = set()
    result: list[str] = []
    for word in words + split:
        low = word.lower()
        if low not in seen and len(low) > 2:
            seen.add(low)
            result.append(low)
    return result


def _estimate_chars(text: str) -> int:
    return len(text)


def _read_file_fragment(workspace: Path, rel_path: str, max_chars: int) -> str:
    path = workspace / rel_path
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if len(text) <= max_chars:
        return text
    # For a fragment, keep the first portion and note truncation.
    return text[:max_chars] + "\n... (已截断) ..."


def _format_selection(item: dict[str, Any]) -> str:
    """Convert a selected item into a string for the model context."""
    if item.get("kind") == "file":
        return f"\n--- 文件: {item['rel_path']} ---\n{item['content']}\n"
    if item.get("kind") == "symbol":
        return (
            f"\n--- 符号: {item['name']} ({item['symbol_type']}) "
            f"在 {item['rel_path']}:{item.get('line', '')} ---\n"
            f"{item.get('content', '')}\n"
        )
    if item.get("kind") == "snippet":
        return (
            f"\n--- 片段: {item['rel_path']}:{item.get('line_start', '')}-{item.get('line_end', '')} ---\n"
            f"{item['content']}\n"
        )
    if item.get("kind") == "memory":
        return item.get("content") or ""
    return ""


class ContextPlanner:
    """Select repository context for a model request within a token budget."""

    def __init__(self, index: RepoIndex, *, user_id: str | None = None, project_id: str | None = None):
        self.index = index
        self.user_id = user_id
        self.project_id = project_id

    def plan(
        self,
        prompt: str,
        current_file: str | None = None,
        selection: dict[str, Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        budget_chars: int = 100_000,
        *,
        include_memories: bool = True,
        memory_budget_chars: int = 6_000,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a plan of selected context items with cost estimates."""
        budget_chars = max(budget_chars, 2_000)
        reserved = OVERHEAD_CHARS
        available = budget_chars - reserved

        selected: list[dict[str, Any]] = []
        used = 0
        reasons: list[str] = []
        excluded: list[dict[str, Any]] = []

        workspace = Path(self.index._workspace)

        # 0. Approved project memories (never candidates).
        if include_memories and self.user_id and self.project_id:
            try:
                from agent.memory_retrieve import retrieve_memories_for_task

                mem_budget = min(memory_budget_chars, max(0, available // 5))
                mem_plan = retrieve_memories_for_task(
                    user_id=self.user_id,
                    project_id=self.project_id,
                    prompt=prompt,
                    budget_chars=mem_budget,
                    task_id=task_id,
                    record_usage=True,
                )
                for memory in mem_plan.get("selected") or []:
                    from agent.memory_retrieve import format_memory_for_context

                    text = format_memory_for_context(memory)
                    item = {
                        "kind": "memory",
                        "memory_id": memory["id"],
                        "content": text,
                        "reason": f"project memory: {memory.get('title')}",
                        "source": "project_memory",
                    }
                    cost = _estimate_chars(text)
                    if cost <= available - used:
                        selected.append(item)
                        used += cost
                        reasons.append(f"project memory: {memory['id']} ({memory.get('memory_type')})")
            except Exception as exc:
                # Memory retrieval must not abort context planning; surface for diagnostics.
                import logging

                logging.getLogger(__name__).warning(
                    "project memory retrieval skipped: %s", exc
                )

        # 1. Current file (highest priority).
        if current_file:
            content = _read_file_fragment(workspace, current_file, available - used)
            if content:
                item = {
                    "kind": "file",
                    "rel_path": current_file,
                    "content": content,
                    "reason": "当前编辑文件",
                }
                cost = _estimate_chars(_format_selection(item))
                if cost <= available - used:
                    selected.append(item)
                    used += cost
                    reasons.append(f"当前文件: {current_file}")

        # 2. Selection snippet if provided.
        if selection and current_file:
            snippet_text = selection.get("text", "")
            if snippet_text:
                item = {
                    "kind": "snippet",
                    "rel_path": current_file,
                    "line_start": selection.get("line_start"),
                    "line_end": selection.get("line_end"),
                    "content": snippet_text,
                    "reason": "当前选区",
                }
                cost = _estimate_chars(_format_selection(item))
                if cost <= available - used:
                    selected.append(item)
                    used += cost

        # 3. Symbols matching prompt keywords.
        keywords = _extract_keywords(prompt)
        symbol_names: set[str] = set()
        for keyword in keywords:
            symbols = self.index.find_symbol(name=keyword, limit=10)
            for sym in symbols:
                symbol_names.add(sym["name"])
                if available - used <= 0:
                    break
                # Read the symbol's surrounding lines if possible.
                content = _read_file_fragment(
                    workspace, sym["rel_path"], min(2000, available - used)
                )
                item = {
                    "kind": "symbol",
                    "rel_path": sym["rel_path"],
                    "symbol_type": sym["symbol_type"],
                    "name": sym["name"],
                    "line": sym.get("line"),
                    "content": content,
                    "reason": f"符号 {sym['name']} 匹配关键词 '{keyword}'",
                }
                cost = _estimate_chars(_format_selection(item))
                if cost <= available - used:
                    selected.append(item)
                    used += cost
                    reasons.append(f"符号 {sym['name']} ({sym['symbol_type']}) 匹配关键词 {keyword}")

        # 4. References for matched symbols.
        for name in list(symbol_names)[:10]:
            refs = self.index.find_references(symbol_name=name, limit=10)
            for ref in refs:
                if available - used <= 0:
                    break
                rel = ref["rel_path"]
                content = _read_file_fragment(workspace, rel, min(1500, available - used))
                item = {
                    "kind": "file",
                    "rel_path": rel,
                    "content": content,
                    "reason": f"引用符号 {name}",
                }
                cost = _estimate_chars(_format_selection(item))
                if cost <= available - used and not any(
                    s["rel_path"] == rel and s["kind"] == "file" for s in selected
                ):
                    selected.append(item)
                    used += cost

        # 5. Related files for current file.
        if current_file:
            related = self.index.related_files(current_file, limit=10)
            for row in related:
                if available - used <= 0:
                    break
                rel = row["rel_path"]
                if any(s["rel_path"] == rel and s["kind"] == "file" for s in selected):
                    continue
                content = _read_file_fragment(workspace, rel, min(1500, available - used))
                item = {
                    "kind": "file",
                    "rel_path": rel,
                    "content": content,
                    "reason": f"与 {current_file} 共享符号",
                }
                cost = _estimate_chars(_format_selection(item))
                if cost <= available - used:
                    selected.append(item)
                    used += cost

        # 6. FTS search results.
        if keywords:
            query = " OR ".join(keywords)
            hits = self.index.search(query, limit=10)
            for hit in hits:
                if available - used <= 0:
                    break
                rel = hit["rel_path"]
                if any(s["rel_path"] == rel and s["kind"] == "file" for s in selected):
                    continue
                content = _read_file_fragment(workspace, rel, min(1500, available - used))
                item = {
                    "kind": "file",
                    "rel_path": rel,
                    "content": content,
                    "reason": f"全文检索匹配 '{query}'",
                }
                cost = _estimate_chars(_format_selection(item))
                if cost <= available - used:
                    selected.append(item)
                    used += cost

        # 7. History summary (compact, only references to files / symbols).
        if history:
            history_text = json.dumps(history[:8], ensure_ascii=False)
            if len(history_text) > available - used:
                history_text = history_text[: available - used] + "\n... (历史已截断) ..."
            if history_text:
                item = {
                    "kind": "history_summary",
                    "content": history_text,
                    "reason": "历史对话摘要",
                }
                cost = _estimate_chars(_format_selection(item))
                if cost <= available - used:
                    selected.append(item)
                    used += cost

        # Compile plan output.
        total_chars = used + reserved
        tokens_estimate = total_chars // CHARS_PER_TOKEN_ESTIMATE
        excluded = []
        if available - used <= 0:
            excluded.append({
                "reason": "预算不足",
                "note": "更多相关文件未纳入，建议提高预算或缩小范围",
            })

        return {
            "selected": selected,
            "total_chars": total_chars,
            "tokens_estimate": tokens_estimate,
            "budget_chars": budget_chars,
            "reasons": reasons,
            "excluded": excluded,
            "truncated": bool(excluded),
        }
