from __future__ import annotations

import os
from pathlib import Path

from agent.safe_paths import resolve_workspace_path
from agent.tools import IGNORE_DIR_NAMES, ALLOWED_READ_PREFIXES, _is_allowed


def search_files(workspace: Path, query: str = "", kind: str = "all", modified: set[str] | None = None, limit: int = 1000) -> dict:
    entries = []
    scanned = 0
    for directory, dirs, files in os.walk(workspace, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIR_NAMES and not d.startswith(".") and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            rel = path.relative_to(workspace).as_posix()
            if path.is_symlink() or not _is_allowed(rel, ALLOWED_READ_PREFIXES):
                continue
            scanned += 1
            if query.lower() not in rel.lower() or (modified is not None and rel not in modified):
                continue
            if kind == "code" and path.suffix not in {".kt", ".java", ".kts"}:
                continue
            if kind == "resources" and "/res/" not in rel:
                continue
            resolve_workspace_path(workspace, rel)
            entries.append({"name": name, "path": rel, "type": "file"})
            if len(entries) >= limit:
                return {"entries": entries, "truncated": True}
    return {"entries": entries, "truncated": False}
