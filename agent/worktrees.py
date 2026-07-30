from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import agent.paths as paths
from agent.paths import validate_id
from agent.redaction import redact_sensitive_value


SECRET_NAME_PATTERNS = (
    re.compile(r"(?i)^\.env(\..+)?$"),
    re.compile(r"(?i).*secret.*"),
    re.compile(r"(?i).*credential.*"),
    re.compile(r"(?i)^local\.properties$"),
    re.compile(r"(?i)^.*\.(pem|key|p12|jks)$"),
    re.compile(r"(?i)^id_rsa(\.pub)?$"),
    re.compile(r"(?i)^.*token.*$"),
)

FinalizeAction = Literal["merge", "keep", "discard"]


@dataclass
class WorktreeInfo:
    id: str
    user_id: str
    project_id: str
    path: Path
    branch: str
    base_revision: str
    main_workspace: Path
    repo_root: Path
    created_at: float = field(default_factory=time.time)
    status: str = "active"  # active|cleaned|kept|merged|discarded
    has_changes: bool | None = None
    diff_artifact: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "path": str(self.path),
            "branch": self.branch,
            "base_revision": self.base_revision,
            "main_workspace": str(self.main_workspace),
            "repo_root": str(self.repo_root),
            "status": self.status,
            "has_changes": self.has_changes,
            "diff_artifact": self.diff_artifact,
            "created_at": self.created_at,
        }


def worktrees_root(user_id: str, project_id: str) -> Path:
    return (
        paths.DATA_DIR
        / "users"
        / validate_id(user_id, kind="user_id")
        / "worktrees"
        / validate_id(project_id, kind="project_id")
    )


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc


def _is_git_repo(path: Path) -> bool:
    git_path = path / ".git"
    return git_path.is_dir() or git_path.is_file()


def _rev_parse(repo: Path, rev: str = "HEAD") -> str:
    proc = _git(repo, "rev-parse", rev)
    return proc.stdout.strip()


def _looks_like_secret(name: str) -> bool:
    base = Path(name).name
    return any(pat.search(base) for pat in SECRET_NAME_PATTERNS)


def create_worktree(
    user_id: str,
    project_id: str,
    main_workspace: Path,
    *,
    base_revision: str | None = None,
    repo_root: Path | None = None,
) -> WorktreeInfo:
    """Create a server-managed git worktree. Path is never model-provided."""
    repo = (repo_root or main_workspace).resolve()
    if not _is_git_repo(repo):
        raise RuntimeError("主仓库不是 Git 仓库，无法创建 worktree")
    base = base_revision or _rev_parse(repo)
    wt_id = uuid.uuid4().hex[:10]
    branch = f"agent-wt-{wt_id}"
    root = worktrees_root(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)
    wt_path = root / wt_id
    if wt_path.exists():
        raise RuntimeError(f"worktree 路径已存在: {wt_path}")

    _git(repo, "worktree", "add", "-b", branch, str(wt_path), base)

    # Strip ignored local secrets if they were checked out (rare) or copied.
    removed = _scrub_secrets(wt_path)
    if removed:
        # Commit scrub so "no agent edits" worktrees appear clean.
        _git(wt_path, "add", "-A", check=False)
        _git(
            wt_path,
            "-c",
            "user.email=agent@localhost",
            "-c",
            "user.name=Android Agent",
            "commit",
            "-m",
            "agent: scrub local secrets from worktree",
            check=False,
        )
        base = _rev_parse(wt_path)

    meta = WorktreeInfo(
        id=wt_id,
        user_id=user_id,
        project_id=project_id,
        path=wt_path,
        branch=branch,
        base_revision=base,
        main_workspace=main_workspace.resolve(),
        repo_root=repo,
    )
    _write_meta(meta)
    return meta


def _meta_path(user_id: str, project_id: str, worktree_id: str) -> Path:
    return worktrees_root(user_id, project_id) / f"{worktree_id}.json"


def _write_meta(info: WorktreeInfo) -> None:
    path = _meta_path(info.user_id, info.project_id, info.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(info.public_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_worktree(user_id: str, project_id: str, worktree_id: str) -> WorktreeInfo | None:
    path = _meta_path(user_id, project_id, worktree_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorktreeInfo(
        id=data["id"],
        user_id=data["user_id"],
        project_id=data["project_id"],
        path=Path(data["path"]),
        branch=data["branch"],
        base_revision=data["base_revision"],
        main_workspace=Path(data.get("main_workspace") or ""),
        repo_root=Path(data.get("repo_root") or data.get("main_workspace") or ""),
        created_at=float(data.get("created_at") or time.time()),
        status=str(data.get("status") or "active"),
        has_changes=data.get("has_changes"),
        diff_artifact=data.get("diff_artifact"),
    )


def _scrub_secrets(root: Path) -> list[str]:
    removed: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except Exception:
            continue
        if _looks_like_secret(rel):
            try:
                path.unlink()
                removed.append(rel)
            except Exception:
                pass
    return removed


def worktree_has_changes(info: WorktreeInfo) -> bool:
    if not info.path.is_dir():
        return False
    proc = _git(info.path, "status", "--porcelain=v1", check=False)
    return bool(proc.stdout.strip())


def build_diff_artifact(info: WorktreeInfo) -> str:
    proc = _git(
        info.path,
        "diff",
        f"{info.base_revision}...HEAD",
        check=False,
    )
    # Also include unstaged/uncommitted
    dirty = _git(info.path, "diff", check=False)
    staged = _git(info.path, "diff", "--cached", check=False)
    parts = [proc.stdout or "", staged.stdout or "", dirty.stdout or ""]
    text = "\n".join(p for p in parts if p.strip())
    artifact_dir = worktrees_root(info.user_id, info.project_id) / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{info.id}.diff"
    artifact_path.write_text(text, encoding="utf-8")
    info.diff_artifact = str(artifact_path)
    info.has_changes = bool(text.strip()) or worktree_has_changes(info)
    _write_meta(info)
    return text


def detect_main_conflicts(info: WorktreeInfo) -> dict[str, Any]:
    """Detect whether merging the worktree branch into main would conflict."""
    main = info.main_workspace if info.main_workspace.is_dir() else info.repo_root
    if not _is_git_repo(main):
        return {"ok": False, "error": "main_not_git", "conflicts": []}
    # Compare changed files in worktree vs dirty/changed files on main since base.
    wt_files = _changed_files(info.path, info.base_revision)
    main_dirty = _porcelain_files(main)
    main_since = _changed_files(main, info.base_revision)
    overlap = sorted(set(wt_files) & (set(main_dirty) | set(main_since)))
    return {
        "ok": True,
        "conflicts": overlap,
        "has_conflicts": bool(overlap),
        "worktree_files": wt_files,
        "main_files": sorted(set(main_dirty) | set(main_since)),
    }


def _changed_files(repo: Path, base: str) -> list[str]:
    proc = _git(repo, "diff", "--name-only", f"{base}...HEAD", check=False)
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    files.extend(_porcelain_files(repo))
    return sorted(set(files))


def _porcelain_files(repo: Path) -> list[str]:
    proc = _git(repo, "status", "--porcelain=v1", "-uall", check=False)
    files: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path)
    return files


def finalize_worktree(
    info: WorktreeInfo,
    action: FinalizeAction,
) -> dict[str, Any]:
    """merge | keep | discard. Never auto-push."""
    info.has_changes = worktree_has_changes(info) or bool(
        (info.diff_artifact and Path(info.diff_artifact).is_file() and Path(info.diff_artifact).stat().st_size > 0)
    )
    if not info.has_changes and action in {"merge", "keep", "discard"}:
        # No changes → auto cleanup regardless of action preference.
        return cleanup_worktree(info, reason="no_changes")

    if action == "discard":
        return cleanup_worktree(info, reason="discarded")

    if action == "keep":
        build_diff_artifact(info)
        info.status = "kept"
        _write_meta(info)
        return {"ok": True, "action": "keep", "worktree": info.public_dict()}

    # merge
    conflicts = detect_main_conflicts(info)
    if conflicts.get("has_conflicts"):
        return {
            "ok": False,
            "action": "merge",
            "error": "conflicts_detected",
            "conflicts": conflicts.get("conflicts") or [],
            "worktree": info.public_dict(),
        }
    # Apply worktree tree into main via checkout from worktree branch paths.
    # Commit any dirty changes in worktree first.
    if worktree_has_changes(info):
        _git(info.path, "add", "-A", check=False)
        _git(
            info.path,
            "-c",
            "user.email=agent@localhost",
            "-c",
            "user.name=Android Agent",
            "commit",
            "-m",
            f"agent worktree {info.id}",
            check=False,
        )
    build_diff_artifact(info)
    main = info.main_workspace if info.main_workspace.is_dir() else info.repo_root
    # Merge branch into main working tree without push.
    proc = _git(main, "merge", "--no-ff", "--no-edit", info.branch, check=False)
    if proc.returncode != 0:
        _git(main, "merge", "--abort", check=False)
        return {
            "ok": False,
            "action": "merge",
            "error": "merge_failed",
            "detail": proc.stderr.strip() or proc.stdout.strip(),
            "worktree": info.public_dict(),
        }
    info.status = "merged"
    _write_meta(info)
    cleanup_worktree(info, reason="merged", remove_branch=True)
    return {"ok": True, "action": "merge", "worktree": info.public_dict()}


def cleanup_worktree(
    info: WorktreeInfo,
    *,
    reason: str = "cleaned",
    remove_branch: bool = True,
) -> dict[str, Any]:
    repo = info.repo_root if info.repo_root.is_dir() else info.main_workspace
    if info.path.is_dir():
        _git(repo, "worktree", "remove", "--force", str(info.path), check=False)
        if info.path.exists():
            shutil.rmtree(info.path, ignore_errors=True)
    if remove_branch:
        _git(repo, "branch", "-D", info.branch, check=False)
    info.status = reason if reason in {"cleaned", "discarded", "merged", "kept"} else "cleaned"
    info.has_changes = False
    _write_meta(info)
    return {"ok": True, "action": "cleanup", "reason": reason, "worktree": info.public_dict()}


def secrets_would_copy(main_workspace: Path) -> list[str]:
    """List secret-like files present in main workspace (for tests/diagnostics)."""
    found: list[str] = []
    if not main_workspace.is_dir():
        return found
    for path in main_workspace.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(main_workspace).as_posix()
        except Exception:
            continue
        if _looks_like_secret(rel):
            found.append(rel)
    return found
