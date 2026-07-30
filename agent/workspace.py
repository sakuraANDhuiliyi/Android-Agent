from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from agent.database import TaskStore
from agent.paths import DATA_DIR, validate_id, workspace_path
from agent.project import load_project_meta
from agent.safe_paths import resolve_workspace_path

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_PATTERNS = frozenset(
    {
        ".git",
        ".gradle",
        ".idea",
        ".DS_Store",
        "build",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "*.pyc",
        "*.apk",
        "local.properties",
    }
)

MAX_DIFF_CHARS = 200_000


def _user_checkpoints_dir(user_id: str) -> Path:
    return DATA_DIR / "users" / user_id / "checkpoints"


def _content_store_path(user_id: str, sha256: str) -> Path:
    base = _user_checkpoints_dir(user_id) / "content" / sha256[:2]
    base.mkdir(parents=True, exist_ok=True)
    return base / sha256


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _store_blob(user_id: str, data: bytes) -> str:
    sha256 = hashlib.sha256(data).hexdigest()
    dest = _content_store_path(user_id, sha256)
    if not dest.exists():
        dest.write_bytes(data)
    return sha256


def _load_blob(user_id: str, sha256: str) -> bytes:
    path = _content_store_path(user_id, sha256)
    return path.read_bytes()


def _is_inside_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


def _should_ignore(path: Path, workspace: Path) -> bool:
    rel = path.relative_to(workspace).as_posix()
    parts = rel.split("/")
    for part in parts:
        if part in DEFAULT_IGNORE_PATTERNS:
            return True
    for pattern in DEFAULT_IGNORE_PATTERNS:
        if pattern.startswith("*") and rel.endswith(pattern[1:]):
            return True
    if path.is_symlink() and not _is_inside_workspace(path.resolve(), workspace):
        return True
    return False


def _capture_manifest(workspace: Path, user_id: str) -> list[dict[str, Any]]:
    from agent.tools import ALLOWED_WRITE_PREFIXES

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prefix in ALLOWED_WRITE_PREFIXES:
        target = workspace / prefix.rstrip("/")
        if not target.exists():
            continue
        paths = target.rglob("*") if target.is_dir() else [target]
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            if _should_ignore(path, workspace):
                continue
            try:
                resolved = path.resolve()
                if not _is_inside_workspace(resolved, workspace):
                    continue
            except (OSError, RuntimeError):
                continue
            rel = path.relative_to(workspace).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            try:
                data = path.read_bytes()
            except OSError:
                continue
            sha256 = _store_blob(user_id, data)
            entries.append(
                {
                    "path": rel,
                    "sha256": sha256,
                    "size": len(data),
                }
            )
    return sorted(entries, key=lambda e: e["path"])


def _git_cmd(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )


def _parse_git_status(repo_root: Path) -> list[dict[str, str]]:
    proc = _git_cmd(repo_root, "status", "--porcelain=v1", "-uall")
    if proc.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        raw = line[3:]
        if " -> " in raw:
            old, new = raw.split(" -> ", 1)
            entries.append({"path": new, "old_path": old, "index": x, "worktree": y})
        else:
            entries.append({"path": raw, "index": x, "worktree": y})
    return entries


def _classify_git_entry(entry: dict[str, str]) -> str:
    x, y = entry.get("index", " "), entry.get("worktree", " ")
    if x == "R" or y == "R":
        return "renamed"
    if x == "D" or y == "D" or entry.get("old_path"):
        return "deleted"
    if y == "?":
        return "untracked"
    if y != " " or x != " ":
        return "modified"
    return "clean"


def _git_base_revision(repo_root: Path) -> str | None:
    proc = _git_cmd(repo_root, "rev-parse", "HEAD")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


class WorkspaceRepository:
    """Unified workspace access: Git state, checkpoints, diffs, and recovery."""

    def __init__(self, user_id: str, project_id: str, task_store: TaskStore | None = None):
        self.user_id = validate_id(user_id, kind="user_id")
        self.project_id = validate_id(project_id, kind="project_id")
        self.workspace = workspace_path(self.user_id, self.project_id)
        meta = load_project_meta(self.user_id, self.project_id)
        repo_root = meta.get("repo_root")
        self.repo_root = Path(repo_root) if repo_root else self.workspace
        self.store = task_store or TaskStore()
        self._content_dir = _user_checkpoints_dir(self.user_id)

    def is_git(self) -> bool:
        git_path = self.repo_root / ".git"
        return git_path.is_dir() or git_path.is_file()

    def git_status(self) -> dict[str, Any]:
        if not self.is_git():
            return {"ok": False, "error": "not_a_git_repo", "files": []}
        raw = _parse_git_status(self.repo_root)
        files = []
        for entry in raw:
            kind = _classify_git_entry(entry)
            files.append(
                {
                    "path": entry["path"],
                    "old_path": entry.get("old_path"),
                    "status": kind,
                    "index": entry.get("index"),
                    "worktree": entry.get("worktree"),
                }
            )
        return {
            "ok": True,
            "branch": self._git_branch(),
            "base_revision": _git_base_revision(self.repo_root),
            "files": files,
            "dirty": bool(files),
        }

    def _git_branch(self) -> str | None:
        if not self.is_git():
            return None
        proc = _git_cmd(self.repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def git_diff(
        self,
        *,
        path: str | None = None,
        staged: bool = False,
        from_revision: str | None = None,
        to_revision: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_git():
            return {"ok": False, "error": "not_a_git_repo", "diff": "", "files": []}
        args = ["git", "diff"]
        if staged:
            args.append("--cached")
        if from_revision and to_revision:
            args.extend([from_revision, to_revision])
        elif from_revision:
            args.append(f"{from_revision}..HEAD")
        if path:
            args.append("--")
            args.append(path)
        args.extend(["--", "."])
        proc = subprocess.run(
            args,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "git_diff_failed",
                "message": proc.stderr.strip(),
                "diff": "",
                "files": [],
            }
        diff_text = proc.stdout
        files = self._extract_diff_files(diff_text)
        truncated = len(diff_text) > MAX_DIFF_CHARS
        return {
            "ok": True,
            "diff": diff_text[:MAX_DIFF_CHARS],
            "truncated": truncated,
            "total_chars": len(diff_text),
            "files": files,
        }

    def _extract_diff_files(self, diff_text: str) -> list[str]:
        files: set[str] = set()
        for line in diff_text.splitlines():
            if line.startswith("--- a/"):
                files.add(line[6:].split("\t")[0])
            elif line.startswith("+++ b/"):
                files.add(line[6:].split("\t")[0])
        return sorted(files)

    def git_log(self, max_count: int = 20) -> dict[str, Any]:
        if not self.is_git():
            return {"ok": False, "error": "not_a_git_repo", "commits": []}
        proc = _git_cmd(
            self.repo_root,
            "log",
            f"--max-count={max_count}",
            "--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s",
            "--date=iso",
        )
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "git_log_failed",
                "message": proc.stderr.strip(),
                "commits": [],
            }
        commits = []
        for line in proc.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) < 6:
                continue
            commits.append(
                {
                    "sha": parts[0],
                    "short_sha": parts[1],
                    "author_name": parts[2],
                    "author_email": parts[3],
                    "date": parts[4],
                    "subject": parts[5],
                }
            )
        return {"ok": True, "commits": commits}

    def create_checkpoint(
        self,
        kind: str,
        *,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"before_turn", "after_turn", "manual"}:
            raise ValueError(f"Invalid checkpoint kind: {kind}")
        base = _git_base_revision(self.repo_root) if self.is_git() else None
        manifest = _capture_manifest(self.workspace, self.user_id)
        checkpoint_id = idempotency_key or uuid.uuid4().hex[:16]
        created_at = time.time()
        manifest_json = json.dumps(
            {"files": manifest, "workspace": str(self.workspace)},
            ensure_ascii=False,
        )
        with self.store._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO checkpoints
                   (id, user_id, project_id, conversation_id, turn_id, task_id,
                    kind, base_revision, manifest_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    checkpoint_id,
                    self.user_id,
                    self.project_id,
                    conversation_id,
                    turn_id,
                    task_id,
                    kind,
                    base,
                    manifest_json,
                    created_at,
                ),
            )
        return {
            "id": checkpoint_id,
            "kind": kind,
            "base_revision": base,
            "file_count": len(manifest),
            "created_at": created_at,
        }

    def list_checkpoints(self) -> list[dict[str, Any]]:
        with self.store._connect() as conn:
            rows = conn.execute(
                """SELECT id, conversation_id, turn_id, task_id, kind,
                          base_revision, manifest_json, created_at
                   FROM checkpoints
                   WHERE user_id=? AND project_id=?
                   ORDER BY created_at DESC""",
                (self.user_id, self.project_id),
            ).fetchall()
        result = []
        for row in rows:
            try:
                manifest = json.loads(row["manifest_json"] or "{}")
                file_count = len(manifest.get("files", []))
            except json.JSONDecodeError:
                file_count = 0
            result.append(
                {
                    "id": row["id"],
                    "conversation_id": row["conversation_id"],
                    "turn_id": row["turn_id"],
                    "task_id": row["task_id"],
                    "kind": row["kind"],
                    "base_revision": row["base_revision"],
                    "file_count": file_count,
                    "created_at": row["created_at"],
                }
            )
        return result

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self.store._connect() as conn:
            row = conn.execute(
                """SELECT id, conversation_id, turn_id, task_id, kind,
                          base_revision, manifest_json, created_at
                   FROM checkpoints
                   WHERE id=? AND user_id=? AND project_id=?""",
                (checkpoint_id, self.user_id, self.project_id),
            ).fetchone()
        if not row:
            return None
        manifest = json.loads(row["manifest_json"] or "{}")
        return {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "turn_id": row["turn_id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "base_revision": row["base_revision"],
            "files": manifest.get("files", []),
            "created_at": row["created_at"],
        }

    def checkpoint_diff(self, checkpoint_id: str) -> dict[str, Any]:
        checkpoint = self.get_checkpoint(checkpoint_id)
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_not_found", "files": [], "diff": ""}
        current = {e["path"]: e["sha256"] for e in _capture_manifest(self.workspace, self.user_id)}
        target = {e["path"]: e["sha256"] for e in checkpoint["files"]}
        return self._diff_manifests(target, current, label_a=f"checkpoint:{checkpoint_id}", label_b="workspace")

    def turn_diff(self, turn_id: str) -> dict[str, Any]:
        with self.store._connect() as conn:
            rows = conn.execute(
                """SELECT id, kind, manifest_json FROM checkpoints
                   WHERE user_id=? AND project_id=? AND turn_id=?
                   ORDER BY created_at ASC""",
                (self.user_id, self.project_id, turn_id),
            ).fetchall()
        if len(rows) < 2:
            return {"ok": False, "error": "insufficient_checkpoints", "files": [], "diff": ""}
        before = json.loads(rows[0]["manifest_json"] or "{}")
        after = json.loads(rows[-1]["manifest_json"] or "{}")
        before_files = {e["path"]: e["sha256"] for e in before.get("files", [])}
        after_files = {e["path"]: e["sha256"] for e in after.get("files", [])}
        return self._diff_manifests(before_files, after_files, label_a="before_turn", label_b="after_turn")

    def _diff_manifests(
        self,
        a_hashes: dict[str, str],
        b_hashes: dict[str, str],
        label_a: str = "a",
        label_b: str = "b",
    ) -> dict[str, Any]:
        import difflib

        files: list[dict[str, Any]] = []
        diff_parts: list[str] = []
        for rel in sorted(set(a_hashes) | set(b_hashes)):
            a_hash = a_hashes.get(rel)
            b_hash = b_hashes.get(rel)
            if a_hash == b_hash:
                continue
            kind = "added" if rel not in a_hashes else "deleted" if rel not in b_hashes else "modified"
            files.append({"path": rel, "change": kind})
            if kind == "added":
                old_lines: list[str] = []
                new_lines = _blob_lines(self.user_id, b_hash)
            elif kind == "deleted":
                old_lines = _blob_lines(self.user_id, a_hash)
                new_lines = []
            else:
                old_lines = _blob_lines(self.user_id, a_hash)
                new_lines = _blob_lines(self.user_id, b_hash)
            diff_parts.extend(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"{label_a}/{rel}",
                    tofile=f"{label_b}/{rel}",
                )
            )
        diff_text = "".join(diff_parts)
        truncated = len(diff_text) > MAX_DIFF_CHARS
        return {
            "ok": True,
            "files": files,
            "diff": diff_text[:MAX_DIFF_CHARS],
            "truncated": truncated,
            "total_chars": len(diff_text),
        }

    def _reference_checkpoint_for(self, checkpoint_id: str) -> dict[str, Any] | None:
        """Return the after_turn checkpoint of the same turn, or the checkpoint itself."""
        checkpoint = self.get_checkpoint(checkpoint_id)
        if not checkpoint or not checkpoint.get("turn_id"):
            return checkpoint
        with self.store._connect() as conn:
            row = conn.execute(
                """SELECT id, manifest_json FROM checkpoints
                   WHERE user_id=? AND project_id=? AND turn_id=? AND kind='after_turn'
                   ORDER BY created_at DESC LIMIT 1""",
                (self.user_id, self.project_id, checkpoint["turn_id"]),
            ).fetchone()
        if not row:
            return checkpoint
        return self.get_checkpoint(row["id"])

    def detect_conflicts(self, checkpoint_id: str) -> dict[str, Any]:
        checkpoint = self.get_checkpoint(checkpoint_id)
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_not_found", "conflicts": []}
        reference = self._reference_checkpoint_for(checkpoint_id)
        target_hashes = {e["path"]: e["sha256"] for e in checkpoint["files"]}
        reference_hashes = {e["path"]: e["sha256"] for e in reference["files"]}
        current = {e["path"]: e["sha256"] for e in _capture_manifest(self.workspace, self.user_id)}
        conflicts = []
        for rel in sorted(set(target_hashes) | set(current)):
            current_hash = current.get(rel)
            target_hash = target_hashes.get(rel)
            ref_hash = reference_hashes.get(rel)
            if current_hash == target_hash:
                continue
            if current_hash == ref_hash:
                continue
            conflicts.append(
                {
                    "path": rel,
                    "current_sha256": current_hash,
                    "expected_sha256": ref_hash,
                }
            )
        return {"ok": True, "conflicts": conflicts, "has_conflicts": bool(conflicts)}

    def restore_file(self, checkpoint_id: str, rel_path: str) -> dict[str, Any]:
        conflicts = self.detect_conflicts(checkpoint_id)
        if not conflicts["ok"]:
            return conflicts
        if conflicts["has_conflicts"]:
            return {"ok": False, "error": "conflict", "conflicts": conflicts["conflicts"]}
        checkpoint = self.get_checkpoint(checkpoint_id)
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_not_found"}
        target = {e["path"]: e for e in checkpoint["files"]}
        entry = target.get(rel_path)
        if not entry:
            return {"ok": False, "error": "file_not_in_checkpoint", "path": rel_path}
        try:
            dest = resolve_workspace_path(self.workspace, rel_path)
        except PermissionError:
            return {"ok": False, "error": "path_escape", "path": rel_path}
        data = _load_blob(self.user_id, entry["sha256"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return {"ok": True, "restored": [rel_path]}

    def restore_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        conflicts = self.detect_conflicts(checkpoint_id)
        if not conflicts["ok"]:
            return conflicts
        if conflicts["has_conflicts"]:
            return {"ok": False, "error": "conflict", "conflicts": conflicts["conflicts"]}
        checkpoint = self.get_checkpoint(checkpoint_id)
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_not_found"}
        restored: list[str] = []
        for entry in checkpoint["files"]:
            rel = entry["path"]
            try:
                dest = resolve_workspace_path(self.workspace, rel)
            except PermissionError:
                continue
            data = _load_blob(self.user_id, entry["sha256"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            restored.append(rel)
        # Remove files that exist in workspace but not in checkpoint for whole-turn restore
        current = {e["path"] for e in _capture_manifest(self.workspace, self.user_id)}
        target_paths = {e["path"] for e in checkpoint["files"]}
        for rel in current - target_paths:
            if not self._is_protected_path(rel):
                try:
                    resolve_workspace_path(self.workspace, rel).unlink()
                except PermissionError:
                    continue
        return {"ok": True, "restored": restored}

    def _is_protected_path(self, rel: str) -> bool:
        parts = rel.split("/")
        if parts[0] in {".git", ".gradle", "build"}:
            return True
        if rel in {"local.properties", ".agent-project.json"}:
            return True
        return False


def _blob_lines(user_id: str, sha256: str | None) -> list[str]:
    if not sha256:
        return []
    try:
        data = _load_blob(user_id, sha256)
    except FileNotFoundError:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["<binary>\n"]
    return text.splitlines(keepends=True)
