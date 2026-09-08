from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.context_planner import ContextPlanner
from agent.memory_store import get_memory_store
from agent.paths import DATA_DIR, build_log_path, workspace_path
from agent.repo_index import get_repo_index
from agent.safe_paths import resolve_workspace_path
from agent.workspace import WorkspaceRepository

MAX_ITEMS = 20
MAX_ITEM_CHARS = 24_000


def _read_text(path: Path, limit: int = MAX_ITEM_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    return text[:limit] + ("\n…（已截断）" if len(text) > limit else "")


def _safe_file(user_id: str, project_id: str, rel_path: str) -> Path:
    return resolve_workspace_path(workspace_path(user_id, project_id), rel_path)


def _resolve_one(
    user_id: str,
    project_id: str,
    item: dict[str, Any],
    store: Any,
) -> dict[str, Any]:
    kind = str(item.get("kind") or "file")
    label = str(item.get("label") or item.get("path") or kind)[:240]
    rel_path = str(item.get("path") or "").strip()
    text = str(item.get("text") or "")[:MAX_ITEM_CHARS]
    content = ""

    if kind == "file" and rel_path:
        content = _read_text(_safe_file(user_id, project_id, rel_path))
    elif kind == "folder" and rel_path:
        folder = _safe_file(user_id, project_id, rel_path)
        root = workspace_path(user_id, project_id)
        if folder.is_dir():
            names = [
                child.relative_to(root).as_posix()
                for child in folder.rglob("*")
                if child.is_file()
            ][:200]
            content = "\n".join(names)
    elif kind == "symbol" and rel_path:
        raw = _read_text(_safe_file(user_id, project_id, rel_path), 200_000)
        lines = raw.splitlines()
        line = max(1, int(item.get("line_start") or 1))
        start = max(0, line - 20)
        end = min(len(lines), line + 80)
        content = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))
    elif kind == "diff":
        diff = WorkspaceRepository(user_id, project_id, task_store=store).git_diff()
        if not diff.get("ok"):
            checkpoints = WorkspaceRepository(user_id, project_id, task_store=store).list_checkpoints()
            baseline = next((row for row in checkpoints if row.get("kind") == "before_turn"), None)
            diff = (
                WorkspaceRepository(user_id, project_id, task_store=store).checkpoint_diff(baseline["id"])
                if baseline else {"diff": ""}
            )
        content = str(diff.get("diff") or "")[:MAX_ITEM_CHARS]
    elif kind == "build_log" and item.get("ref_id"):
        job = store.get_task(str(item["ref_id"]), user_id)
        if job and job.get("project_id") == project_id and job.get("build_log_path"):
            from agent.paths import user_builds_dir
            log_path = Path(job["build_log_path"])
            try:
                log_path.resolve().relative_to((user_builds_dir(user_id) / project_id).resolve())
                content = _read_text(log_path)
            except ValueError:
                content = ""
        elif job is None:
            content = _read_text(build_log_path(user_id, project_id, str(item["ref_id"])))
    elif kind == "conversation" and item.get("ref_id"):
        conversation = store.get_conversation(str(item["ref_id"]), user_id)
        if conversation and conversation.get("project_id") == project_id:
            content = "\n".join(
                value for value in [conversation.get("title"), conversation.get("summary")] if value
            )
    else:
        content = text

    return {
        "kind": kind,
        "label": label,
        "path": rel_path or None,
        "line_start": item.get("line_start"),
        "line_end": item.get("line_end"),
        "content": content,
        "chars": len(content),
        "tokens_estimate": max(1, len(content) // 4) if content else 0,
        "source": "explicit",
    }


def build_context_bundle(
    user_id: str,
    project_id: str,
    prompt: str,
    attachments: list[dict[str, Any]] | None,
    store: Any,
    *,
    include_automatic: bool = True,
    task_id: str | None = None,
    budget_chars: int = 100_000,
) -> dict[str, Any]:
    explicit: list[dict[str, Any]] = []
    remaining = max(0, budget_chars - 1_000)
    for raw_item in (attachments or [])[:MAX_ITEMS]:
        item = _resolve_one(user_id, project_id, raw_item, store)
        if not item["content"] and item["kind"] != "screenshot":
            continue
        if remaining <= 0:
            break
        if item["chars"] > remaining:
            item["content"] = item["content"][:remaining] + "\n…（上下文预算已截断）"
            item["chars"] = len(item["content"])
            item["tokens_estimate"] = max(1, item["chars"] // 4)
        explicit.append(item)
        remaining -= item["chars"]
    used_chars = sum(item["chars"] for item in explicit)

    index = get_repo_index(user_id, project_id)
    if index.status().get("status") != "ready":
        index.rebuild()
    automatic: list[dict[str, Any]] = []
    auto_plan: dict[str, Any] = {"tokens_estimate": 0}
    if include_automatic and budget_chars - used_chars > 2_000:
        first_file = next((item.get("path") for item in explicit if item["kind"] == "file"), None)
        auto_plan = ContextPlanner(index, user_id=user_id, project_id=project_id).plan(
            prompt,
            current_file=first_file,
            budget_chars=max(2_000, budget_chars - used_chars),
            include_memories=task_id is not None,
            task_id=task_id,
        )
        explicit_paths = {item.get("path") for item in explicit}
        for item in auto_plan.get("selected") or []:
            if item.get("rel_path") in explicit_paths:
                continue
            automatic.append(
                {
                    "kind": item.get("kind", "file"),
                    "label": item.get("name") or item.get("rel_path") or item.get("reason") or "Context",
                    "path": item.get("rel_path"),
                    "content": item.get("content") or "",
                    "chars": len(item.get("content") or ""),
                    "tokens_estimate": max(1, len(item.get("content") or "") // 4),
                    "source": "memory" if item.get("kind") == "memory" else "automatic",
                }
            )

    def render_item(item: dict[str, Any]) -> str:
        heading = f"### {item['kind']}: {item['label']}"
        if item.get("path"):
            heading += f"\nFile: {item['path']}"
        if item.get("line_start"):
            heading += f"\nLines: {item['line_start']}-{item.get('line_end') or item['line_start']}"
        return f"{heading}\n{item['content']}"

    sections: list[str] = []
    if explicit:
        sections.append("## 用户显式选择的上下文\n" + "\n\n".join(
            render_item(item) for item in explicit
        ))
    if automatic:
        sections.append("## 自动检索的仓库上下文\n" + "\n\n".join(
            render_item(item) for item in automatic
        ))
    repo_map = index.repo_map(max_files=1)
    symbol_count = sum(int(row.get("count") or 0) for row in repo_map.get("symbol_summary") or [])
    memory_count = get_memory_store(DATA_DIR / "agent.db").count_memories(user_id, project_id=project_id)
    all_items = explicit + automatic
    total_tokens = sum(item["tokens_estimate"] for item in all_items)
    return {
        "explicit": [{key: value for key, value in item.items() if key != "content"} for item in explicit],
        "automatic": [{key: value for key, value in item.items() if key != "content"} for item in automatic if item["source"] != "memory"],
        "memory": [{key: value for key, value in item.items() if key != "content"} for item in automatic if item["source"] == "memory"],
        "memory_count": memory_count,
        "symbol_count": symbol_count,
        "total_tokens": total_tokens,
        "budget_tokens": max(1, budget_chars // 4),
        "model_context": "\n\n".join(sections),
    }
