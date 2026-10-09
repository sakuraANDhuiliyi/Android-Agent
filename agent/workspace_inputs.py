"""Bounded evidence for workspace inputs; never a proof of external dependencies.

Unlike review checkpoints this includes root build configuration, wrappers and
all modules. It stores no source contents and does not follow symbolic links.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Any

from agent.safe_paths import open_workspace_file

SCOPE = "workspace_inputs_v1"
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".gradle", ".kotlin", ".idea", ".cxx", ".externalNativeBuild",
    "build", "node_modules", "__pycache__", ".artifacts",
})
EXCLUDED_ENTRIES = frozenset({".git", ".DS_Store"})


class _Incomplete(Exception):
    pass


def _identity(info: os.stat_result) -> tuple[int, ...]:
    # atime changes when we read a file and is not part of its identity.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _scan(root: Path, max_files: int, max_total_bytes: int,
          max_file_bytes: int) -> dict[str, tuple[int, ...]]:
    files: dict[str, tuple[int, ...]] = {}
    folders = [root]
    size = entries_seen = 0
    while folders:
        folder = folders.pop()
        # Fail closed if a directory was swapped for a symlink after listing.
        if folder.is_symlink():
            raise _Incomplete("symlink_input")
        with os.scandir(folder) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > max_files * 4 + 1024:
                    raise _Incomplete("entry_limit")
                if entry.name in EXCLUDED_ENTRIES:
                    continue
                if entry.is_symlink():
                    raise _Incomplete("symlink_input")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    # A package or asset folder named "build" inside a source
                    # set is still an input, not Gradle's generated directory.
                    in_source_set = "src" in Path(entry.path).relative_to(root).parts
                    if entry.name == "build" and not in_source_set and any(
                        (Path(entry.path) / marker).exists() for marker in
                        ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
                    ):
                        # A real module named build cannot safely be treated
                        # as the conventional generated-output directory.
                        raise _Incomplete("excluded_build_module")
                    if entry.name not in EXCLUDED_DIRECTORIES or in_source_set:
                        folders.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise _Incomplete("unsupported_input")
                if info.st_size > max_file_bytes:
                    raise _Incomplete("file_size_limit")
                size += info.st_size
                if size > max_total_bytes:
                    raise _Incomplete("total_size_limit")
                relative = Path(entry.path).relative_to(root).as_posix()
                files[relative] = _identity(info)
                if len(files) > max_files:
                    raise _Incomplete("file_count_limit")
    return files


def capture_workspace_inputs(
    workspace: Path, *, max_files: int = 10_000,
    max_total_bytes: int = 134_217_728, max_file_bytes: int = 33_554_432,
) -> dict[str, Any]:
    """Return a digest only when every in-scope input was read consistently.

    Generated directories and installed dependencies above are outside this
    versioned scope. SDKs, remote dependencies and external included builds
    are never certified by a matching workspace digest.
    """
    if min(max_files, max_total_bytes, max_file_bytes) < 1:
        raise ValueError("workspace input limits must be positive")
    result: dict[str, Any] = {
        "scope": SCOPE, "status": "unknown", "digest": None,
        "file_count": 0, "byte_count": 0, "reason": None,
    }
    try:
        root = workspace.resolve(strict=True)
        if not root.is_dir():
            raise _Incomplete("workspace_unavailable")
        initial = _scan(root, max_files, max_total_bytes, max_file_bytes)
        digest = hashlib.sha256()
        for relative, expected in sorted(initial.items()):
            file_digest = hashlib.sha256()
            read_bytes = 0
            with open_workspace_file(root, relative) as handle:
                if _identity(os.fstat(handle.fileno())) != expected:
                    raise _Incomplete("inputs_changed_during_capture")
                while chunk := handle.read(1024 * 1024):
                    read_bytes += len(chunk)
                    if read_bytes > max_file_bytes or result["byte_count"] + read_bytes > max_total_bytes:
                        raise _Incomplete("inputs_changed_during_capture")
                    file_digest.update(chunk)
                if read_bytes != expected[3] or _identity(os.fstat(handle.fileno())) != expected:
                    raise _Incomplete("inputs_changed_during_capture")
            # NUL cannot occur in a file name; these records are unambiguous.
            digest.update(relative.encode("utf-8", errors="surrogateescape") + b"\0")
            digest.update(file_digest.digest())
            digest.update(str(expected[2] & 0o111).encode() + b"\0")
            result["file_count"] += 1
            result["byte_count"] += read_bytes
        if _scan(root, max_files, max_total_bytes, max_file_bytes) != initial:
            raise _Incomplete("inputs_changed_during_capture")
        result.update(status="complete", digest=digest.hexdigest())
    except _Incomplete as exc:
        result["reason"] = str(exc)
    except (OSError, RuntimeError, ValueError):
        # Never certify a partial inventory after an unreadable file, broken
        # path or a concurrent workspace replacement.
        result["reason"] = "input_unavailable"
    return result


def compare_workspace_inputs(before: Any, after: Any) -> str:
    """Unknown/legacy/incomplete inventories must not compare as a match."""
    for value in (before, after):
        if not isinstance(value, dict) or value.get("scope") != SCOPE or value.get("status") != "complete":
            return "unknown"
        digest = value.get("digest")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            return "unknown"
    return "match" if before["digest"] == after["digest"] else "changed"
