# Android Agent Architecture

This document describes the shipping architecture as of Stage 19 (eval / security / release gate).

## Components

- **Python FastAPI server** (`agent/`): jobs, tools, conversation events, approvals, MCP, hooks, subagents, memory, repo index.
- **Desktop** (`desktop/`): Electron + Monaco + xterm; WebSocket task events with cursor reconnect.
- **Android client** (`android-app/`): project/conversation UI, approvals, steer/follow-up, APK install.

## Data layout

| Path | Purpose |
|------|---------|
| `data/users.db` | User registration (token SHA-256 only) |
| `data/agent.db` | Tasks, conversations, events, checkpoints, memories |
| `data/index/{user}/{project}/` | Rebuildable FTS code index |
| `workspaces/{user}/{project}/` | Project workspace |
| `builds/{user}/{project}/` | Build logs and APKs |

Override root with `AGENT_DATA_DIR`.

## Permission modes

| Mode | Behavior |
|------|----------|
| `ask` | Writes / network / process tools require approval |
| `workspace` | Workspace writes allowed; network/download still gated |
| `read_only` | Only read tools |

Hooks cannot weaken a hard permission denial. Destructive tools remain ask/deny according to policy.

## Workspace trust

- Project MCP configs require explicit trust before tools are registered.
- Rules/Skills/Hooks loaded from the workspace are sandboxed to paths inside the workspace (symlink escape blocked).
- Worktree paths are server-allocated under `data/users/{user}/worktrees/{project}/`; models never choose absolute paths.

## Git / checkpoint / restore

- **Git status/diff** is optional metadata when `.git` exists.
- **Checkpoints** store content-addressed blobs + manifests (not Git commits).
- Restore refuses when working-tree conflicts are detected (`error=conflict`).
- Recovery after service interrupt synthesizes failed tool results then queues a recovery turn (max 3).

## Queue and recovery

- Tasks are claimed with SQLite `BEGIN IMMEDIATE` + lease/heartbeat (`TaskWorker`).
- One writable main-agent task per project (`write_lock_key`); explore subagents may run in parallel without the main write lock.
- Queued tasks survive process restart; interrupted running turns are repaired on `configure_task_store`.

## Rules, Skills, MCP, Hooks

- Rules: `AGENTS.md`, `.android-agent/rules`, user global rules — budgeted into the system prompt.
- Skills: discoverable skill packs with optional resources (path-bound).
- MCP: stdio servers; secrets via env refs at spawn time only; crash/reconnect supported.
- Hooks: PreToolUse / PostToolUse / TurnCompleted style actions; cannot elevate privileges.

## Subagents and worktrees

Roles: `explore`, `reviewer`, `test_runner`, `implementer`. Implementer may use an isolated git worktree; finalize merges/discards under server control.

## Memory

Long-term project/user/local memories are **not** conversation checkpoints. Auto-extracted items are `candidate` until approved; only `active` memories enter Context Planner, labeled `project memory`. Offline FTS5 retrieval (no embedding service required).

## Security boundaries

- Workspace path sandbox + symlink resolve checks.
- Process runner: argv only (`shell=False`), cwd inside workspace, filtered env (no API keys).
- Download URL validation blocks `file://`, localhost, private IPs, embedded credentials; redirects re-validated per hop.
- Event/API/log redaction for common secret patterns.
- Strict user isolation on conversation/task/memory APIs (IDOR → 404).

## Known limits

- Free-text secret redaction is best-effort, not a vault.
- Approval timeout floor is 30 seconds in production.
- Index and memory are local SQLite; multi-host shared storage is not provided.
- Desktop Playwright screenshots cover layout smoke, not full E2E against a live model.
- Real `assembleDebug` still needs a local Android SDK; offline evals mock the build step.

## Backup and migration

```bash
./scripts/backup_data.sh [outfile.tar.gz]
python3 scripts/migrate_db.py --backup
```

See README “发布与运维” for desktop packaging and APK steps.
