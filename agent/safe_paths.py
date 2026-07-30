from __future__ import annotations

from pathlib import Path, PurePosixPath


def resolve_workspace_path(
    workspace: Path,
    rel_path: str,
    *,
    reject_symlinks: bool = True,
) -> Path:
    """Resolve a project-relative path without prefix or symlink escapes."""
    raw = str(rel_path or "").replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise PermissionError(f"路径越界: {rel_path}")

    root = workspace.resolve()
    candidate = root.joinpath(*pure.parts)
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"路径越界: {rel_path}") from exc

    if reject_symlinks:
        current = root
        for part in pure.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if current.is_symlink():
                raise PermissionError(f"禁止访问符号链接路径: {rel_path}")
    return candidate


def is_workspace_file(workspace: Path, path: Path) -> bool:
    root = workspace.resolve()
    try:
        resolved_path = path.resolve()
        rel = resolved_path.relative_to(root)
        resolved = resolve_workspace_path(root, rel.as_posix())
    except (ValueError, PermissionError, OSError):
        return False
    return resolved.is_file()
