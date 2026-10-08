from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path, PurePosixPath
import secrets
import stat


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


@contextmanager
def _workspace_parent(workspace: Path, rel_path: str, *, create: bool = False):
    """Pin each directory while traversing, so a concurrent symlink swap fails closed."""
    root = workspace.resolve()
    path = resolve_workspace_path(root, rel_path)
    parts = path.relative_to(root).parts
    if not parts:
        raise PermissionError("必须指定工作区内的文件")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise PermissionError(f"禁止访问符号链接或非目录路径: {rel_path}") from exc
        raise
    finally:
        os.close(fd)


@contextmanager
def open_workspace_file(workspace: Path, rel_path: str):
    """Open a regular file without following links, including links swapped after validation."""
    with _workspace_parent(workspace, rel_path) as (parent_fd, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PermissionError(f"不是普通文件: {rel_path}")
            handle = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            yield handle


@contextmanager
def atomic_workspace_file(workspace: Path, rel_path: str):
    """Write and atomically replace a file relative to a pinned, symlink-free parent."""
    with _workspace_parent(workspace, rel_path, create=True) as (parent_fd, name):
        temp_name = f".agent-write-{secrets.token_hex(16)}.tmp"
        fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                yield handle
                handle.flush()
                os.fsync(handle.fileno())
            # The destination entry itself is replaced, never followed. If its
            # parent was renamed concurrently, dir_fd still pins the original.
            os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
