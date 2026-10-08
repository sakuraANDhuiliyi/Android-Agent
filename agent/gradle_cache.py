"""Copy an operator-prepared cache into a project's isolated Gradle home."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from agent.safe_paths import atomic_workspace_file, open_workspace_file, resolve_workspace_path


def seed_gradle_cache(workspace: Path) -> None:
    configured = os.environ.get("AGENT_GRADLE_CACHE_SEED", "").strip()
    if not configured:
        return
    seed = Path(configured).expanduser().resolve(strict=True)
    version = (seed / ".agent-cache-id").read_text(encoding="utf-8").strip()
    marker = resolve_workspace_path(workspace, ".gradle/.agent-cache-id")
    if marker.is_file():
        with open_workspace_file(workspace, ".gradle/.agent-cache-id") as cached_version:
            if cached_version.read().decode("utf-8").strip() == version:
                return
    # Never share mutable caches between tenants, or copy init scripts/secrets.
    # Only immutable build inputs prepared by the operator are admitted.
    for section in ("wrapper", "caches"):
        source = seed / section
        if not source.is_dir() or source.is_symlink():
            raise ValueError(f"Gradle 缓存种子缺少有效的 {section} 目录")
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError("Gradle 缓存种子不能包含符号链接")
            if not path.is_file() or path.name.endswith((".lock", ".lck")):
                continue
            relative = path.relative_to(seed)
            destination = resolve_workspace_path(workspace, f".gradle/{relative.as_posix()}")
            if destination.exists():
                continue
            with atomic_workspace_file(workspace, f".gradle/{relative.as_posix()}") as target:
                with path.open("rb") as source_file:
                    shutil.copyfileobj(source_file, target)
                os.fchmod(target.fileno(), path.stat().st_mode & 0o777 | 0o600)
    # The marker is an optimization only, written after every copy succeeds.
    with atomic_workspace_file(workspace, ".gradle/.agent-cache-id") as stream:
        stream.write((version + "\n").encode("utf-8"))
