from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

from agent.tools import ALLOWED_WRITE_PREFIXES


def snapshot_workspace(workspace: Path) -> dict[str, str]:
    """Capture writable-tree file contents (text) or sha256 (binary)."""
    snapshot: dict[str, str] = {}
    for prefix in ALLOWED_WRITE_PREFIXES:
        target = workspace / prefix.rstrip("/")
        paths = target.rglob("*") if target.is_dir() else [target]
        for path in paths:
            if path.is_file():
                rel = path.relative_to(workspace).as_posix()
                try:
                    snapshot[rel] = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    snapshot[rel] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def compare_snapshots(workspace: Path, before: dict[str, str], after: dict[str, str]) -> tuple[list[dict], str]:
    changes: list[dict] = []
    diff_parts: list[str] = []
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel):
            continue
        kind = "added" if rel not in before else "deleted" if rel not in after else "modified"
        changes.append({"path": rel, "change": kind})

        old = before.get(rel, "")
        new = after.get(rel, "")
        if old.startswith("sha256:") or new.startswith("sha256:"):
            diff_parts.append(f"--- a/{rel}\n+++ b/{rel}\n@@ binary or unreadable @@\n")
            continue

        old_lines = old.splitlines(keepends=True) if kind != "added" else []
        new_lines = new.splitlines(keepends=True) if kind != "deleted" else []
        patch = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        diff_parts.extend(patch)
    return changes, "".join(diff_parts)[:200_000]
