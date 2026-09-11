"""Repository history never mutates conversations. Restores require a fresh preview."""
from __future__ import annotations

import hashlib
import json
import tempfile
from agent.git_runner import run_git
from typing import Any

from agent.workspace import WorkspaceRepository, _capture_manifest, _load_blob
from agent.safe_paths import resolve_workspace_path
from agent.tools import is_writable_path


def revision(repo: WorkspaceRepository) -> str:
    files = _capture_manifest(repo.workspace, repo.user_id)
    return _revision(files)


def _revision(files: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps([(f["path"], f["sha256"], f.get("executable", False)) for f in files], sort_keys=True).encode()).hexdigest()


def history(repo: WorkspaceRepository) -> list[dict[str, Any]]:
    tasks = {t["id"]: t for t in repo.store.list_tasks(repo.user_id, repo.project_id)}
    entries = []
    for cp in repo.list_checkpoints():
        if cp["kind"] == "before_turn":
            continue
        task = tasks.get(cp.get("task_id"), {})
        diff = repo.turn_diff(cp["turn_id"]) if cp.get("turn_id") else None
        entries.append({**cp, "title": task.get("prompt") or "Manual recovery point",
                        "changed_files": len(diff.get("files", [])) if diff and diff.get("ok") else None})
    return entries


def restore_preview(repo: WorkspaceRepository, checkpoint_id: str) -> dict[str, Any]:
    cp = repo.get_checkpoint(checkpoint_id)
    if not cp:
        raise FileNotFoundError("Checkpoint not found")
    manifest = _capture_manifest(repo.workspace, repo.user_id)
    current = {f["path"]: f["sha256"] for f in manifest}
    target = {f["path"]: f["sha256"] for f in cp["files"]}
    diff = repo._diff_manifests(current, target, "workspace", "restored snapshot")
    current_modes = {f["path"]: f.get("executable", False) for f in manifest}
    for entry in cp["files"]:
        if entry["path"] in current and current[entry["path"]] == entry["sha256"] and "executable" in entry and current_modes[entry["path"]] != entry["executable"]:
            diff["files"].append({"path": entry["path"], "change": "modified", "patch": "File executable mode changes"})
    return {"checkpoint_id": checkpoint_id, "revision": _revision(manifest),
            "diff": diff,
            "conversation_history_preserved": True}


def restore_snapshot(repo: WorkspaceRepository, checkpoint_id: str, expected_revision: str) -> dict[str, Any]:
    cp = repo.get_checkpoint(checkpoint_id)
    if not cp:
        raise FileNotFoundError("Checkpoint not found")
    if revision(repo) != expected_revision:
        return {"ok": False, "error": "Workspace changed; preview again before restoring"}
    # Validate every blob/path before changing anything. Backup is durable before writes.
    files = []
    for entry in cp["files"]:
        lexical = repo.workspace
        for part in entry["path"].split("/"):
            lexical = lexical / part
            if lexical.is_symlink():
                raise ValueError("Restore refuses symbolic links: " + entry["path"])
        destination = resolve_workspace_path(repo.workspace, entry["path"])
        if destination.exists() and not destination.is_file():
            raise ValueError("Restore target is not a regular file: " + entry["path"])
        for parent in destination.parents:
            if parent == repo.workspace.resolve():
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError("Restore parent is not a directory: " + entry["path"])
        files.append((destination,
                      _load_blob(repo.user_id, entry["sha256"]), entry.get("executable")))
    current = _capture_manifest(repo.workspace, repo.user_id)
    backup = repo.create_checkpoint("manual")
    targets = {f["path"] for f in cp["files"]}
    for path, data, executable in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if executable is not None:
            path.chmod((path.stat().st_mode & ~0o111) | (0o111 if executable else 0))
    removed = []
    for f in current:
        if f["path"] not in targets and not repo._is_protected_path(f["path"]):
            resolve_workspace_path(repo.workspace, f["path"]).unlink()
            removed.append(f["path"])
    return {"ok": True, "backup_checkpoint_id": backup["id"], "restored": sorted(targets),
            "removed": removed, "conversation_history_preserved": True}


def branch_snapshot(repo: WorkspaceRepository, checkpoint_id: str, name: str) -> dict[str, Any]:
    """Create a snapshot commit with a private index; never checkout, stash, or alter HEAD/index."""
    cp = repo.get_checkpoint(checkpoint_id)
    if not cp:
        raise FileNotFoundError("Checkpoint not found")
    if not repo.is_git() or not cp.get("base_revision"):
        raise ValueError("Creating a branch requires a Git-backed checkpoint")
    if not name or name.startswith("-"):
        raise ValueError("Invalid branch name")
    root = repo.repo_root
    def git(*args: str, data: bytes | None = None, env=None) -> bytes:
        result = run_git(root, *args, input_data=data, env=env, text=False)
        if result.returncode:
            raise ValueError(result.stderr.decode(errors="replace")[:1000])
        return result.stdout.strip()
    git("check-ref-format", "refs/heads/" + name)
    with tempfile.TemporaryDirectory(prefix="agent-snapshot-index-", dir=root) as temporary:
        env = {"GIT_INDEX_FILE": temporary + "/index",
               "GIT_AUTHOR_NAME": "Android Agent", "GIT_AUTHOR_EMAIL": "agent@localhost",
               "GIT_COMMITTER_NAME": "Android Agent", "GIT_COMMITTER_EMAIL": "agent@localhost"}
        git("read-tree", cp["base_revision"], env=env)
        prefix = repo.workspace.resolve().relative_to(root.resolve()).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        target = {f["path"]: f for f in cp["files"]}
        for raw in git("ls-files", "-z", env=env).split(b"\0"):
            if not raw:
                continue
            path = raw.decode()
            rel = path[len(prefix):] if path.startswith(prefix) else None
            if rel is not None and is_writable_path(rel) and rel not in target:
                git("update-index", "--force-remove", "--", path, env=env)
        for rel, entry in target.items():
            blob = git("hash-object", "-w", "--stdin", data=_load_blob(repo.user_id, entry["sha256"]), env=env).decode()
            mode = "100755" if entry.get("executable") else "100644"
            git("update-index", "--add", "--cacheinfo", mode, blob, prefix + rel, env=env)
        tree = git("write-tree", env=env).decode()
        commit = git("commit-tree", tree, "-p", cp["base_revision"], "-m", "Checkpoint " + checkpoint_id, env=env).decode()
        # Empty old value means create only: an existing branch can never be overwritten.
        git("update-ref", "refs/heads/" + name, commit, "", env=env)
    return {"ok": True, "branch": name, "commit": commit, "checked_out": False}
