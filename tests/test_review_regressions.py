from __future__ import annotations

import json
import sqlite3
import tarfile
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agent import jobs
from agent.config import Settings, resolve_job_settings, validate_deployment_settings
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.task_settings import model_selection, resolve_task_settings
from agent.worker import TaskWorker


def settings():
    return Settings(
        provider="anthropic", api_key="synthetic-a", model="claude-test",
        model_candidates=["claude-test"], max_turns=2, max_auto_continuations=0,
        max_gradle_retries=0, compact_max_chars=10000, max_output_tokens=1000,
        base_url="https://anthropic.example.invalid", auto_build_after_edit=False,
        server_host="127.0.0.1", server_port=8000, api_token="",
    )


def create_task(store, task_id="first", *, context=None, lock_key=None):
    conv = store.get_or_create_default_conversation("alice", "project")
    store.create_task(dict(
        id=task_id, user_id="alice", project_id="project", conversation_id=conv["id"],
        prompt="review", provider="anthropic", model="claude-test", status="queued",
        created_at=time.time(), context=context or {}, write_lock_key=lock_key,
    ))
    turn = ConversationEventStore(store).create_turn(
        conv["id"], "alice", "project", task_id=task_id, status="queued"
    )
    return store.get_task(task_id), turn


@pytest.mark.parametrize("first_key,second_key", [
    (None, None), ("main:alice:project", "main:alice:project"),
    (None, "main:alice:project"), ("main:alice:project", None),
])
def test_awaiting_approval_holds_project_lock(tmp_path, first_key, second_key):
    store = TaskStore(tmp_path / "tasks.db")
    create_task(store, lock_key=first_key)
    create_task(store, "second", lock_key=second_key)
    first = store.claim_next_task("worker-a")
    store.update_task(first["id"], status="awaiting_approval")
    assert store.claim_next_task("worker-b") is None
    assert store.claim_is_valid(first["id"], "worker-a", first["claim_token"])
    store.release_task(first["id"], "worker-a", "succeeded", claim_token=first["claim_token"])
    assert store.claim_next_task("worker-b")["id"] == "second"


@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_startup_recovery_preserves_live_lease_and_turn(tmp_path, status):
    store = TaskStore(tmp_path / "tasks.db")
    _, turn = create_task(store)
    task = store.claim_next_task("worker-a")
    store.update_task(task["id"], status=status)
    events = ConversationEventStore(store)
    events.update_turn_status(turn["id"], status, user_id="alice")
    assert store.recover_interrupted() == []
    assert store.claim_is_valid(task["id"], "worker-a", task["claim_token"])
    assert events.get_turn(turn["id"], user_id="alice")["status"] == status
    store.update_task(task["id"], lease_expires_at=time.time() - 10)
    assert len(store.recover_interrupted()) == 1
    assert store.get_task(task["id"])["status"] == "failed"
    assert events.get_turn(turn["id"], user_id="alice")["status"] == "interrupted"


def test_worker_switches_complete_provider_configuration():
    base = settings()
    deepseek = replace(base, provider="deepseek", model="deepseek-test",
                       model_candidates=["deepseek-test"], api_key="synthetic-b",
                       base_url="https://deepseek.example.invalid")
    base.provider_fallbacks = [deepseek]
    selected = resolve_job_settings(base, "deepseek", auto_fallback=False)
    result = resolve_task_settings(base, {
        "provider": selected.provider, "model": selected.model,
        "context": {"model_selection": model_selection(selected)},
    })
    assert result.api_key == deepseek.api_key
    assert result.base_url == deepseek.base_url
    assert result.model_candidates == deepseek.model_candidates
    assert result.provider_fallbacks == []
    assert resolve_task_settings(base, {"provider": base.provider}).provider_fallbacks == []
    assert "synthetic" not in json.dumps(model_selection(selected))
    with pytest.raises(ValueError, match="尚未配置"):
        resolve_task_settings(base, {"provider": "missing"})


def test_task_specific_limits_survive_worker_configuration():
    base = replace(settings(), max_turns=20, max_auto_continuations=5, auto_build_after_edit=True)
    guest = replace(base, max_turns=3, max_auto_continuations=0, auto_build_after_edit=False)
    result = resolve_task_settings(base, {"context": {"model_selection": model_selection(guest)}})
    assert result.max_turns == 3
    assert result.max_auto_continuations == 0
    assert result.auto_build_after_edit is False


@pytest.mark.parametrize("profile,mode", [(None, "read_only"), ("safe", "read_only")])
def test_follow_up_and_recovery_inherit_execution_choices(tmp_path, profile, mode):
    store = TaskStore(tmp_path / "tasks.db")
    ctx = {"run_mode": mode, "permission_profile": profile,
           "attachments": [{"kind": "selection", "text": "keep this"}],
           "model_selection": model_selection(settings())}
    original, turn = create_task(store, context=ctx)
    store.add_task_message(original["id"], message_key="follow", type="follow_up", payload={"prompt": "continue"})
    store.update_task(original["id"], status="succeeded")
    ConversationEventStore(store).update_turn_status(turn["id"], "succeeded", user_id="alice")
    with patch.object(jobs, "_store", store), patch.object(jobs, "load_project_meta", return_value={}), \
         patch.object(jobs, "start_worker"):
        TaskWorker(store, Mock(), settings())._create_follow_ups(original)
        follow = next(t for t in store.list_tasks("alice", "project") if t["prompt"] == "continue")
        for key in ctx:
            assert follow["context"][key] == ctx[key]
        assert store.get_pending_messages(original["id"], types=["follow_up"]) == []
        # Remove the follow-up from recovery's duplicate/active-task checks.
        store.update_task(follow["id"], status="failed")
        store.update_task(original["id"], status="failed")
        ConversationEventStore(store).update_turn_status(turn["id"], "interrupted", user_id="alice")
        recovered = jobs.recover_job_explicitly(original["id"], "alice", settings())
        for key in ctx:
            assert recovered["context"][key] == ctx[key]
        assert recovered["write_lock_key"] == "main:alice:project"


def test_failed_follow_up_creation_keeps_message_pending(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task, _ = create_task(store)
    store.add_task_message(task["id"], message_key="follow", type="follow_up", payload={"prompt": "continue"})
    store.update_task(task["id"], status="succeeded")
    with patch.object(jobs, "load_project_meta", side_effect=RuntimeError("unavailable")):
        TaskWorker(store, Mock(), settings())._create_follow_ups(task)
    assert len(store.get_pending_messages(task["id"], types=["follow_up"])) == 1


def test_profile_reaches_actual_agent_boundary(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task, turn = create_task(store, context={"run_mode": "read_only", "permission_profile": "safe"})
    workspace = tmp_path / "workspace"
    (workspace / "app/src").mkdir(parents=True)
    with patch.object(jobs, "_store", store), patch.object(jobs, "workspace_path", return_value=workspace), \
         patch.object(jobs, "user_builds_dir", return_value=tmp_path / "builds"), \
         patch.object(jobs, "run_agent", return_value="done") as run:
        jobs._run_job(task["id"], "alice", "project", task["conversation_id"], turn["id"],
                      "review", settings(), [], 0, run_mode="read_only")
    assert run.call_args.kwargs["permission_profile"] == "safe"


def test_explicit_read_only_is_not_relaxed_on_recovery():
    from agent.permissions import decide_permission
    from agent.tool_registry import get_tool_spec
    result = decide_permission(get_tool_spec("write_file"), run_mode="read_only",
                               recovery_mode=True, is_replay=True)
    assert result.deny


def test_memory_and_fts_context_can_coexist(tmp_path):
    from agent.context_planner import ContextPlanner
    (tmp_path / "sample.py").write_text("def sample(): return 1")
    index = Mock(_workspace=tmp_path)
    index.symbols.return_value = []
    index.find_symbol.return_value = []
    index.references.return_value = []
    index.search.return_value = [{"rel_path": "sample.py"}]
    index.repo_map.return_value = {"files": [], "symbol_summary": []}
    index.related_files.return_value = []
    with patch("agent.memory_retrieve.retrieve_memories_for_task", return_value={
        "selected": [{"id": "m1", "title": "rule", "memory_type": "rule"}]
    }), patch("agent.memory_retrieve.format_memory_for_context", return_value="keep this rule"):
        plan = ContextPlanner(index, user_id="alice", project_id="project").plan("sample code")
    assert {item["kind"] for item in plan["selected"]} >= {"memory", "file"}


def test_automatic_context_failure_keeps_explicit_attachment(tmp_path):
    from agent.explicit_context import build_context_bundle
    with patch("agent.explicit_context.get_repo_index", side_effect=RuntimeError("index unavailable")), \
         patch("agent.explicit_context.get_memory_store") as memories:
        memories.return_value.count_memories.return_value = 0
        bundle = build_context_bundle("alice", "project", "review", [
            {"kind": "selection", "text": "explicit requirement"}
        ], Mock(), task_id="t")
    assert "explicit requirement" in bundle["model_context"]
    assert len(bundle["explicit"]) == 1


@pytest.mark.parametrize("mode", ["postgres", "hybrid"])
def test_unimplemented_store_modes_fail_even_with_urls(mode):
    with pytest.raises(ValueError, match="尚未实现"):
        validate_deployment_settings(replace(settings(), deployment_mode=mode,
            database_url="postgresql://example.invalid/db", redis_url="memory://tickets"))


def test_backup_restores_committed_wal_and_configured_roots(tmp_path, monkeypatch):
    from scripts.backup_data import main
    roots = {name: tmp_path / f"external-{name}" for name in ("data", "workspaces", "builds")}
    for name, path in roots.items():
        path.mkdir()
        monkeypatch.setenv(f"AGENT_{name.upper()}_DIR", str(path))
    source = sqlite3.connect(roots["data"] / "agent.db")
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE items (value TEXT)")
        source.commit()
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        source.execute("INSERT INTO items VALUES ('committed-in-wal')")
        source.commit()
        (roots["workspaces"] / "source.txt").write_text("workspace-content")
        output = tmp_path / "backup.tar.gz"
        monkeypatch.setattr("sys.argv", ["backup_data.py", str(output)])
        main()
        with tarfile.open(output) as archive:
            restored = tmp_path / "restored.db"
            restored.write_bytes(archive.extractfile("data/agent.db").read())
            assert archive.extractfile("workspaces/source.txt").read() == b"workspace-content"
            assert "data/agent.db-wal" not in archive.getnames()
        with sqlite3.connect(restored) as db:
            assert db.execute("SELECT value FROM items").fetchone()[0] == "committed-in-wal"
    finally:
        source.close()


def test_gradle_cache_seed_is_copied_without_cross_project_writes(tmp_path, monkeypatch):
    from agent.gradle_cache import seed_gradle_cache
    seed = tmp_path / "seed"
    (seed / "wrapper/dists").mkdir(parents=True)
    (seed / "wrapper/dists/gradle.zip").write_bytes(b"distribution")
    (seed / "caches").mkdir()
    (seed / "caches/dependency.jar").write_bytes(b"original")
    (seed / ".agent-cache-id").write_text("version-1")
    (seed / "gradle.properties").write_text("secret=must-not-copy")
    (seed / "init.d").mkdir()
    (seed / "init.d/unsafe.gradle").write_text("must-not-copy")
    monkeypatch.setenv("AGENT_GRADLE_CACHE_SEED", str(seed))
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    seed_gradle_cache(left)
    (left / ".gradle/caches/dependency.jar").write_bytes(b"left change")
    seed_gradle_cache(right)
    assert (right / ".gradle/caches/dependency.jar").read_bytes() == b"original"
    assert (seed / "caches/dependency.jar").read_bytes() == b"original"
    assert not (right / ".gradle/gradle.properties").exists()
    assert not (right / ".gradle/init.d").exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    third = tmp_path / "third"
    third.mkdir()
    (third / ".gradle").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError):
        seed_gradle_cache(third)
    assert list(outside.iterdir()) == []
