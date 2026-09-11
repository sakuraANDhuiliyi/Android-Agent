"""Git operations on untrusted repositories use the same OS boundary as tools."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from agent import paths
from agent.safe_paths import resolve_workspace_path
from agent.processes import build_minimal_env, build_sandboxed_command, prepare_workspace_env, _kill_process_group


def run_git(repo: Path, *args: str, input_data: bytes | None = None,
            env: dict[str, str] | None = None, text: bool = True,
            writable_paths: tuple[Path, ...] = ()) -> subprocess.CompletedProcess:
    repo = repo.resolve()
    extra = list(writable_paths)
    dotgit = repo / ".git"
    if dotgit.is_symlink():
        raise PermissionError("Git directory must not be a symlink")
    if dotgit.is_file():
        # Only server-created linked worktrees may point outside their checkout.
        rel = repo.relative_to((paths.DATA_DIR / "users").resolve())
        if len(rel.parts) != 4 or rel.parts[1] != "worktrees":
            raise PermissionError("Untrusted Git worktree metadata")
        main_git = resolve_workspace_path(paths.workspace_path(rel.parts[0], rel.parts[2]), ".git")
        with dotgit.open() as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise PermissionError("Git worktree metadata is too large")
        raw = raw.strip()
        if not raw.startswith("gitdir: "):
            raise PermissionError("Invalid Git worktree metadata")
        gitdir = (repo / raw[8:]).resolve()
        gitdir.relative_to(main_git)
        common = resolve_workspace_path(main_git, (gitdir.relative_to(main_git) / "commondir").as_posix())
        if common.is_file():
            with common.open() as handle:
                common_dir = handle.read(4097)
            if len(common_dir) > 4096 or (gitdir / common_dir.strip()).resolve() != main_git:
                raise PermissionError("Untrusted Git common directory")
        extra.append(main_git)

    child_env = build_minimal_env()
    prepare_workspace_env(repo, child_env)
    excludes = resolve_workspace_path(repo, ".agent-home/git-excludes")
    excludes.write_text("/.agent-home/\n", encoding="utf-8")
    child_env.update({"GIT_CONFIG_NOSYSTEM": "1",
                      "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    # Only internal identity/index settings can be passed by server callers.
    for key in ("GIT_INDEX_FILE", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        if env and key in env:
            child_env[key] = env[key]
    command = ["git", "--no-pager", "-c", "core.fsmonitor=false",
               "-c", f"core.excludesFile={excludes}",
               "-c", f"core.hooksPath={os.devnull}", "-c", "core.untrackedCache=false",
               "-c", "protocol.allow=never", "-c", "credential.helper=",
               "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
               "-c", "maintenance.auto=false", "-c", "gc.auto=0"]
    if args and args[0] == "diff":
        args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
    if args and args[0] in {"status", "diff"}:
        # Runtime caches must never turn a clean worktree into a project edit.
        args = (*args, *(() if "--" in args else ("--", ".")), ":(top,exclude).agent-home")
    command += list(args)
    wrapped = build_sandboxed_command(command, repo, env=child_env, extra_write_paths=extra)
    proc = subprocess.Popen(wrapped, cwd=repo, env=child_env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(input_data, timeout=30)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        proc.wait(timeout=5)
        raise
    finally:
        _kill_process_group(proc)
    if text:
        stdout, stderr = stdout.decode(errors="replace"), stderr.decode(errors="replace")
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
