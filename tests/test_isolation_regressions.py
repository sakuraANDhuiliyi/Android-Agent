"""Tenant, path and runtime regressions; only synthetic files and model stubs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import pytest

from agent import paths
from agent.api import create_app
from agent.changes import snapshot_workspace
from agent.database import TaskStore
from agent.loop import _openai_tools
from agent.mcp_client import McpCallResult, McpToolInfo
from agent.mcp_config import McpServerConfig
from agent.mcp_manager import McpManager, McpServerState, get_mcp_manager, reset_mcp_managers
from agent.project_settings import load_project_settings, save_project_settings
from agent.safe_paths import atomic_workspace_file, open_workspace_file
from agent.subagent_roles import ROLES
from agent.subagents import _execute_subagent_agent, spawn_subagent
from agent.task_settings import model_selection
from agent.tool_registry import clear_dynamic_tools, get_tool_spec, list_dynamic_tool_specs
from agent.tool_runtime import ToolContext
from agent.tools import dispatch_tool, get_tool_definitions, run_gradle
from agent.users import UserStore
from tests.test_accounts_api import settings
from tests.test_creative_catalog import content


@pytest.fixture
def managers(tmp_path):
    owners = [("alice", "one", "main"), ("bob", "one", "main"),
              ("alice", "two", "main"), ("alice", "one", "worktree")]
    result = []
    for user, project, directory in owners:
        workspace = tmp_path / user / project / directory
        workspace.mkdir(parents=True)
        manager = McpManager(user, project, workspace)
        marker = f"{user}/{project}/{directory}"
        manager.call_tool = Mock(return_value=McpCallResult(True, marker))
        state = McpServerState(
            config=McpServerConfig(name="shared", command="unused"),
            status="ready",
            tools=[McpToolInfo("read", description=marker,
                               input_schema={"type": "object", "properties": {}})],
        )
        manager._servers["shared"] = state
        result.append(manager)
    # Same-named registrations from different tenants/projects/worktrees may race.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda m: m._sync_registry_locked(m._servers["shared"]), result))
    yield result
    clear_dynamic_tools(prefix="mcp__")


def test_mcp_schemas_dispatch_and_unload_are_owner_scoped(managers):
    name = "mcp__shared__read"
    assert get_tool_spec(name) is None
    assert not list_dynamic_tool_specs()
    assert all(t["name"] != name for t in get_tool_definitions())
    with patch("agent.approvals.request_user_approval", return_value="approved"):
        for manager in managers:
            expected = manager.call_tool.return_value.content
            schema = next(t for t in get_tool_definitions(scope=manager.registry_scope) if t["name"] == name)
            assert expected in schema["description"]
            schema = next(t["function"] for t in _openai_tools(settings(), scope=manager.registry_scope)
                          if t["function"]["name"] == name)
            assert expected in schema["description"]
            result = dispatch_tool(manager.workspace, manager.user_id, manager.project_id,
                                   name, {}, task_id="test", tool_call_id="call")
            assert result.ok
            assert result.output["content"] == expected
            manager.call_tool.assert_called_once()
    managers[0].stop_all()
    assert get_tool_spec(name, scope=managers[0].registry_scope) is None
    for manager in managers[1:]:
        assert get_tool_spec(name, scope=manager.registry_scope) is not None


def test_captured_mcp_handler_rechecks_owner_context(managers):
    owner, other = managers[:2]
    spec = get_tool_spec("mcp__shared__read", scope=owner.registry_scope)
    ctx = ToolContext(other.workspace, other.user_id, other.project_id,
                      "task", "call", settings(), None, None, None)
    result = spec.handler(ctx, {})
    assert not result.ok and result.error_type == "PermissionDenied"
    owner.call_tool.assert_not_called()


def test_manager_cache_distinguishes_worktrees(tmp_path):
    try:
        one = get_mcp_manager("alice", "project", tmp_path / "main")
        two = get_mcp_manager("alice", "project", tmp_path / "worktree")
        assert one is not two
        assert get_mcp_manager("alice", "project", tmp_path / "main") is one
    finally:
        reset_mcp_managers()


@pytest.mark.parametrize("role_name", sorted(ROLES))
def test_real_subagent_entry_resolves_and_enforces_role(tmp_path, role_name):
    role = ROLES[role_name]
    with patch("agent.loop.run_agent", return_value="done") as run:
        result = _execute_subagent_agent(settings(), tmp_path, "u", "p", "inspect",
                                        role_name=role_name, max_turns=2, on_event=None,
                                        cancel_check=None, task_id="child", turn_id="turn")
    assert result["role"] == role_name and result["text"] == "done"
    assert run.call_args.kwargs["allowed_tools"] == frozenset(role.allowed_tools)
    assert run.call_args.kwargs["run_mode"] == role.permission_mode
    assert role.system_prompt in run.call_args.kwargs["extra_system_prompt"]
    assert run.call_args.args[0].max_turns == 2
    assert run.call_args.args[0].max_auto_continuations == 0


def test_real_subagent_rejects_unknown_role_before_model(tmp_path):
    with patch("agent.loop.run_agent") as run, pytest.raises(ValueError, match="角色"):
        _execute_subagent_agent(settings(), tmp_path, "u", "p", "inspect",
                                role_name="unrestricted", max_turns=2, on_event=None,
                                cancel_check=None, task_id="child", turn_id="turn")
    run.assert_not_called()


@pytest.fixture
def parent_task(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.db")
    monkeypatch.setattr("agent.subagents._task_store", lambda: store)
    monkeypatch.setattr("agent.subagents.workspace_path", lambda *_: tmp_path)
    store.create_task({"id": "parent", "user_id": "u", "project_id": "p", "status": "running", "prompt": "parent", "created_at": 1.0})
    return store


@pytest.mark.parametrize("role_name", ["implementer", "test_runner"])
def test_readonly_parent_cannot_spawn_writing_or_process_role(parent_task, role_name):
    parent_task.update_task("parent", context={"run_mode": "read_only", "permission_profile": None})
    with patch("agent.subagents.create_worktree") as create, pytest.raises(PermissionError, match="只读"):
        spawn_subagent(user_id="u", project_id="p", parent_task_id="parent",
                       role_name=role_name, prompt="inspect", settings=settings())
    create.assert_not_called()
    assert parent_task.count_active_children("parent") == 0


@pytest.mark.parametrize("parent_mode,profile,child_mode,child_profile", [
    ("read_only", "safe", "workspace", "safe"),
    ("ask", None, "ask", None),
    ("workspace", "full_access", "workspace", "full_access"),
])
def test_subagent_inherits_parent_restrictions_and_budget(parent_task, parent_mode, profile,
                                                         child_mode, child_profile):
    constrained = replace(settings(), max_turns=1, max_auto_continuations=0, provider_fallbacks=[])
    parent_task.update_task("parent", context={"run_mode": parent_mode, "permission_profile": profile,
                                                "model_selection": model_selection(constrained)})
    child = spawn_subagent(user_id="u", project_id="p", parent_task_id="parent",
                           role_name="test_runner", prompt="inspect", settings=settings())
    context = parent_task.get_task(child["child_task_id"], "u")["context"]
    assert context["run_mode"] == child_mode
    assert context["permission_profile"] == child_profile
    assert context["model_selection"]["limits"]["max_turns"] == 1
    assert context["model_selection"]["fallback_providers"] == []


@pytest.mark.parametrize("role_name,mode,profile", [
    ("reviewer", "workspace", "full_access"),
    ("explore", "workspace", "full_access"),
    ("implementer", "workspace", "safe"),
    ("test_runner", "ask", None),
])
def test_subagent_loop_never_expands_role_or_parent_permission(tmp_path, role_name, mode, profile):
    with patch("agent.loop.run_agent", return_value="done") as run:
        _execute_subagent_agent(replace(settings(), max_turns=1), tmp_path, "u", "p", "inspect",
                                role_name=role_name, max_turns=8, on_event=None, cancel_check=None,
                                task_id="child", turn_id="turn", run_mode=mode, permission_profile=profile)
    readonly = ROLES[role_name].permission_mode == "read_only"
    assert run.call_args.kwargs["run_mode"] == ("read_only" if readonly else mode)
    assert run.call_args.kwargs["permission_profile"] == (None if readonly else profile)
    assert run.call_args.args[0].max_turns == 1


def test_snapshot_omits_symlink_files_and_prefix_directories(tmp_path):
    workspace = tmp_path / "project"
    source = workspace / "app/src/main/java"
    source.mkdir(parents=True)
    external = tmp_path / "private.txt"
    external.write_text("outside-marker")
    (source / "normal.kt").write_text("class Normal")
    (source / "binary.kt").write_bytes(b"\xff\x00")
    (source / "linked.kt").symlink_to(external)
    (source / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    (workspace / "app/src/test").symlink_to(tmp_path, target_is_directory=True)
    snapshot = snapshot_workspace(workspace)
    assert snapshot == {
        "app/src/main/java/normal.kt": "class Normal",
        "app/src/main/java/binary.kt": "sha256:" + hashlib.sha256(b"\xff\x00").hexdigest(),
    }


@pytest.mark.parametrize("link_directory", [False, True])
def test_settings_reject_external_symlink_for_read_and_write(tmp_path, link_directory):
    workspace = tmp_path / "project"
    workspace.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    target = private / "settings.json"
    original = '{"permission_profile":"safe"}'
    target.write_text(original)
    config_dir = workspace / ".android-agent"
    if link_directory:
        config_dir.symlink_to(private, target_is_directory=True)
    else:
        config_dir.mkdir()
        (config_dir / "settings.json").symlink_to(target)
    with pytest.raises(PermissionError):
        load_project_settings(workspace)
    with pytest.raises(PermissionError):
        save_project_settings(workspace, {"permission_profile": "full_access"})
    assert target.read_text() == original


def test_settings_save_does_not_follow_predictable_temp_symlink(tmp_path):
    directory = tmp_path / "project/.android-agent"
    directory.mkdir(parents=True)
    private = tmp_path / "private"
    private.write_text("unchanged")
    (directory / "settings.json.tmp").symlink_to(private)
    save_project_settings(directory.parent, {"permission_profile": "safe"})
    assert load_project_settings(directory.parent)["permission_profile"] == "safe"
    assert private.read_text() == "unchanged"


@pytest.mark.parametrize("swap_parent", [False, True])
def test_secure_open_rejects_symlink_swapped_after_validation(tmp_path, monkeypatch, swap_parent):
    from agent import safe_paths

    workspace = tmp_path / "project"
    directory = workspace / "source"
    directory.mkdir(parents=True)
    (directory / "file.txt").write_text("safe")
    private = tmp_path / "private"
    private.mkdir()
    (private / "file.txt").write_text("outside-marker")
    resolve = safe_paths.resolve_workspace_path

    def swap(*args, **kwargs):
        candidate = resolve(*args, **kwargs)
        if swap_parent:
            directory.rename(workspace / "original")
            directory.symlink_to(private, target_is_directory=True)
        else:
            candidate.unlink()
            candidate.symlink_to(private / "file.txt")
        return candidate

    monkeypatch.setattr(safe_paths, "resolve_workspace_path", swap)
    with pytest.raises(PermissionError):
        with open_workspace_file(workspace, "source/file.txt") as handle:
            handle.read()


def test_atomic_writer_pins_parent_when_directory_is_swapped(tmp_path):
    workspace = tmp_path / "project"
    directory = workspace / "config"
    directory.mkdir(parents=True)
    private = tmp_path / "private"
    private.mkdir()
    (private / "settings.json").write_text("unchanged")
    with atomic_workspace_file(workspace, "config/settings.json") as handle:
        directory.rename(workspace / "original")
        directory.symlink_to(private, target_is_directory=True)
        handle.write(b"new-data")
    assert (private / "settings.json").read_text() == "unchanged"
    assert (workspace / "original/settings.json").read_bytes() == b"new-data"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(paths, "BUILDS_DIR", tmp_path / "builds")
    users = UserStore(tmp_path / "users.db")
    user = users.register_account("test@example.test", "isolated-test-password")
    user_id = user["account"]["user_id"]
    headers = {"Authorization": f"Bearer {user['token']}"}
    workspace = paths.workspace_path(user_id, "project")
    workspace.mkdir(parents=True)
    monkeypatch.setattr("agent.api.load_project_meta", lambda *_: {})
    app = create_app(replace(settings(), creative_submissions_enabled=True),
                     user_store=users, task_store=TaskStore(tmp_path / "tasks.db"))
    with TestClient(app) as http:
        yield http, headers, workspace, user_id


def test_apk_download_rejects_external_symlink_and_serves_valid_file(client, tmp_path):
    http, headers, workspace, _ = client
    external = tmp_path / "private.txt"
    external.write_text("outside-marker")
    apk = workspace / "app/build/outputs/apk/debug/app-debug.apk"
    apk.parent.mkdir(parents=True)
    apk.symlink_to(external)
    response = http.get("/api/projects/project/apk", headers=headers)
    assert response.status_code == 404
    assert "outside-marker" not in response.text
    apk.unlink()
    apk.write_bytes(b"synthetic-apk")
    response = http.get("/api/projects/project/apk", headers=headers)
    assert response.status_code == 200 and response.content == b"synthetic-apk"
    assert response.headers["X-APK-SHA256"] == hashlib.sha256(response.content).hexdigest()


def test_apk_response_pins_content_and_cleans_snapshot_after_range_download(client, tmp_path, monkeypatch):
    from agent import api

    http, headers, workspace, _ = client
    apk = workspace / "app/build/outputs/apk/debug/app-debug.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"synthetic-apk")
    external = tmp_path / "private"
    external.write_text("outside-marker")
    snapshots = []
    original = api._apk_file_response

    def swap_after_response(*args, **kwargs):
        response = original(*args, **kwargs)
        snapshots.append(Path(response.path))
        apk.unlink()
        apk.symlink_to(external)
        return response

    monkeypatch.setattr(api, "_apk_file_response", swap_after_response)
    response = http.get("/api/projects/project/apk", headers=headers | {"Range": "bytes=0-8"})
    assert response.status_code == 206 and response.content == b"synthetic"
    assert snapshots and all(not path.exists() for path in snapshots)


def test_apk_snapshot_is_removed_when_response_send_fails(tmp_path):
    from agent.api import _apk_file_response

    apk = tmp_path / "test.apk"
    apk.write_bytes(b"synthetic-apk")
    response = _apk_file_response(apk, "test.apk", root=tmp_path)
    snapshot = Path(response.path)
    assert snapshot.is_file()

    async def fail_send(_message):
        raise RuntimeError("synthetic disconnected client")

    with pytest.raises(RuntimeError, match="disconnected"):
        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, None, fail_send))
    assert not snapshot.exists()


def test_settings_api_rejects_symlink_without_modifying_external_file(client, tmp_path):
    http, headers, workspace, _ = client
    private = tmp_path / "private"
    private.mkdir()
    settings_file = private / "settings.json"
    settings_file.write_text('{"permission_profile":"safe"}')
    (workspace / ".android-agent").symlink_to(private, target_is_directory=True)
    for method, kwargs in [("GET", {}), ("PATCH", {"json": {"permission_profile": "full_access"}})]:
        response = http.request(method, "/api/projects/project/settings", headers=headers, **kwargs)
        assert response.status_code == 400
    assert json.loads(settings_file.read_text())["permission_profile"] == "safe"


def test_custom_validator_valueerror_has_serializable_422_envelope(client):
    http, headers, *_ = client
    invalid = content().model_dump() | {"title": "  "}
    response = http.post("/api/me/creative/items", headers=headers,
                         json={"client_id": "validation-test", "content": invalid})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any(error["type"] == "value_error" for error in body["detail"])
    assert all(isinstance(error.get("ctx", {}).get("error", ""), str) for error in body["detail"])


def test_successful_gradle_does_not_publish_symlinked_apk(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    apk = workspace / "app/build/outputs/apk/debug/app-debug.apk"
    apk.parent.mkdir(parents=True)
    (workspace / "gradlew").write_text("synthetic")
    external = tmp_path / "private.txt"
    external.write_text("outside-marker")
    apk.symlink_to(external)
    monkeypatch.setattr(paths, "BUILDS_DIR", tmp_path / "builds")
    monkeypatch.setattr("agent.tools.ensure_local_properties", lambda _: tmp_path / "sdk")
    monkeypatch.setattr("agent.tools._run_command", lambda *a, **kw: SimpleNamespace(ok=True))
    result = run_gradle(workspace, "u", "p")
    assert not result.ok and result.error_type == "PermissionDenied"
    assert not paths.latest_apk_path("u", "p").exists()
