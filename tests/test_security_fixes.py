"""Regressions for the September security audit; no real credentials or services."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent import paths
from agent.api import create_app
from agent.database import TaskStore
from agent.git_runner import run_git
from agent.mcp_config import resolve_env_secrets
from agent.processes import ProcessStartError, build_sandboxed_command, run_command
from agent.users import UserStore, UserStoreError
from agent.workspace import WorkspaceRepository
from tests.test_accounts_api import settings


@pytest.fixture
def store(tmp_path):
    return UserStore(tmp_path / "users.db")


def account(store):
    return store.register_account("test@example.com", "secure-password", device={"device_id": "test"})["account"]["user_id"]


def test_otp_failed_attempts_are_durable_and_invalidate_challenge(store):
    uid = account(store)
    code = store.create_code(uid, "login_email")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(UserStoreError, match="验证码"):
            UserStore(store.db_path).verify_code("test@example.com", wrong, "login_email")
    with pytest.raises(UserStoreError, match="验证码"):
        store.verify_code("test@example.com", code, "login_email")


def test_otp_only_one_concurrent_consumer_succeeds(store):
    code = store.create_code(account(store), "login_email")
    gate = threading.Barrier(2)
    def consume():
        gate.wait(timeout=5)
        try:
            store.verify_code("test@example.com", code, "login_email")
            return True
        except UserStoreError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: consume(), range(2))) == [False, True]


def test_otp_send_cooldown_does_not_invalidate_previous_code(store):
    uid = account(store)
    code = store.create_code(uid, "login_email")
    with pytest.raises(UserStoreError) as error:
        store.create_code(uid, "login_email")
    assert error.value.code == "code_rate_limited"
    assert store.verify_code("test@example.com", code, "login_email") == uid


def test_device_id_alone_cannot_take_over_guest(store):
    first = store.guest_session(device={"device_id": "phone"})
    with pytest.raises(UserStoreError) as error:
        store.guest_session(device={"device_id": "phone"})
    assert error.value.code == "guest_auth_required"
    assert store.authenticate_identity(first["token"]) is not None
    second = store.guest_session(device={"device_id": "phone"}, token=first["token"])
    assert first["account"]["user_id"] == second["account"]["user_id"]


def test_new_guest_devices_share_persistent_daily_budget(store):
    one = store.guest_session(device={"device_id": "one"})["account"]["user_id"]
    two = store.guest_session(device={"device_id": "two"})["account"]["user_id"]
    assert store.consume_guest_message(one, daily_limit=1) == 2
    with pytest.raises(UserStoreError) as error:
        UserStore(store.db_path).consume_guest_message(two, daily_limit=1)
    assert error.value.code == "guest_global_quota_exhausted"


def test_guest_endpoint_respects_deployment_policy(tmp_path, store):
    app = create_app(replace(settings(), guest_sessions_enabled=False), user_store=store, task_store=TaskStore(tmp_path / "tasks.db"))
    with TestClient(app) as client:
        assert client.post("/api/auth/guest", json={"device": {"device_id": "phone", "device_name": "Phone"}}).status_code == 404


def test_guest_policy_is_independent_from_account_registration(tmp_path, store):
    app = create_app(
        replace(settings(), registration_enabled=False, email_verification_required=True, guest_sessions_enabled=True),
        user_store=store,
        task_store=TaskStore(tmp_path / "tasks.db"),
    )
    with TestClient(app) as client:
        response = client.post("/api/auth/guest", json={"device": {"device_id": "phone", "device_name": "Phone"}})
    assert response.status_code == 201, response.text


def test_guests_cannot_write_or_enter_terminals(tmp_path, store):
    guest = store.guest_session(device={"device_id": "phone"})
    headers = {"Authorization": f"Bearer {guest['token']}"}
    app = create_app(replace(settings(), terminal_enabled=True), user_store=store, task_store=TaskStore(tmp_path / "tasks.db"))
    with TestClient(app) as client:
        for method, url, payload in [
            ("PUT", "/api/projects/p/files/content", {"path": "a", "content": "b"}),
            ("POST", "/api/projects/p/mcp/trust", {}),
            ("POST", "/api/projects/p/terminals", {}),
            ("POST", "/api/jobs/j/resume", {}),
            ("POST", "/api/ws/tickets", {"resource_type": "terminal", "resource_id": "t"}),
        ]:
            response = client.request(method, url, headers=headers, json=payload)
            assert response.status_code == 403, (url, response.text)
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/api/ws/terminals/t", headers=headers):
                pass
        assert error.value.code == 4403


def test_guest_ask_is_forced_read_only(tmp_path, store):
    guest = store.guest_session(device={"device_id": "phone"})
    headers = {"Authorization": f"Bearer {guest['token']}"}
    app = create_app(settings(), user_store=store, task_store=TaskStore(tmp_path / "tasks.db"))
    with TestClient(app) as client, patch("agent.api.load_project_meta", return_value={}), \
            patch("agent.api.start_ask_job", return_value={}) as start, patch("agent.api.job_to_dict", side_effect=lambda job: job):
        response = client.post("/api/projects/p/ask", headers=headers, json={"prompt": "hello", "run_mode": "workspace", "feedback_requested": True})
        assert response.status_code == 200, response.text
        assert start.call_args.kwargs["run_mode"] == "read_only"
        assert not start.call_args.kwargs["feedback_requested"]
        assert start.call_args.args[3].max_auto_continuations == 0


def test_cors_allows_authenticated_put(tmp_path, store):
    app = create_app(replace(settings(), cors_allowed_origins=["https://client.example"]), user_store=store, task_store=TaskStore(tmp_path / "tasks.db"))
    with TestClient(app) as client:
        response = client.options("/api/projects/p/files/content", headers={"Origin": "https://client.example", "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "authorization,content-type"})
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_mcp_cannot_read_ambient_or_other_tenant_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AGENT_ADMIN_TOKEN", "ambient-only")
    secret = tmp_path / "users/alice/mcp-secrets.json"
    secret.parent.mkdir(parents=True)
    secret.write_text(json.dumps({"servers": {"approved": {"SERVICE_KEY": "tenant-only"}}}))
    assert resolve_env_secrets({"MODE": "normal"}) == {"MODE": "normal"}
    assert resolve_env_secrets({"TOKEN": "${SERVICE_KEY}"}, user_id="alice", server_name="approved") == {"TOKEN": "tenant-only"}
    for user, server, ref in [("alice", "approved", "AGENT_ADMIN_TOKEN"), ("bob", "approved", "SERVICE_KEY"), ("alice", "other", "SERVICE_KEY")]:
        with pytest.raises(PermissionError):
            resolve_env_secrets({"TOKEN": "${" + ref + "}"}, user_id=user, server_name=server)


def test_linux_fails_closed_without_bubblewrap(tmp_path):
    with patch("agent.processes.platform.system", return_value="Linux"), patch("agent.processes.shutil.which", return_value=None), patch.dict(os.environ, {"AGENT_CMD_SANDBOX": "0"}):
        with pytest.raises(ProcessStartError, match="bubblewrap"):
            build_sandboxed_command(["echo", "hello"], tmp_path)


def test_mcp_arguments_cannot_grant_host_file_access(tmp_path):
    with pytest.raises(ProcessStartError):
        build_sandboxed_command(["cat", str(tmp_path.parent / "outside")], tmp_path, extra_read_paths=[tmp_path.parent / "outside"])


def test_project_metadata_cannot_redirect_git_to_another_workspace(tmp_path):
    with patch("agent.workspace.workspace_path", return_value=tmp_path / "own"), \
            patch("agent.workspace.load_project_meta", return_value={"repo_root": str(tmp_path / "other")}):
        with pytest.raises(PermissionError):
            WorkspaceRepository("alice", "project")


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS sandbox regression")
def test_sandbox_blocks_other_workspace_reads_and_writes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "other-account.txt"
    outside.write_text("fake-other-account")
    script = f"""from pathlib import Path
p = Path({str(outside)!r})
for operation in [lambda: p.read_text(), lambda: p.write_text('changed')]:
    try: operation(); print('LEAKED')
    except PermissionError: print('DENIED')
Path('allowed.txt').write_text('ok')
"""
    result = run_command([sys.executable, "-c", script], cwd=workspace, workspace=workspace,
                         env={"JAVA_HOME": str(tmp_path)}, timeout_seconds=3)
    assert result.ok, result
    assert result.stdout.count("DENIED") == 2 and "LEAKED" not in result.stdout
    assert outside.read_text() == "fake-other-account"
    assert (workspace / "allowed.txt").read_text() == "ok"


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS sandbox regression")
def test_git_queries_do_not_execute_fsmonitor_or_external_diff(tmp_path):
    def setup(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    setup("init")
    (tmp_path / "file.txt").write_text("before")
    setup("add", "file.txt")
    setup("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "test")
    (tmp_path / "file.txt").write_text("after")
    script = tmp_path / "malicious.sh"
    script.write_text("#!/bin/sh\nprintf invoked > marker\n")
    script.chmod(0o700)
    setup("config", "core.fsmonitor", str(script))
    setup("config", "diff.external", str(script))
    with patch.dict(os.environ, {"AGENT_ADMIN_TOKEN": "fake-ambient"}):
        assert run_git(tmp_path, "status", "--porcelain").returncode == 0
        diff = run_git(tmp_path, "diff")
        assert diff.returncode == 0 and "+after" in diff.stdout
    assert not (tmp_path / "marker").exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="real process sandbox regression")
def test_background_pipe_holder_cannot_bypass_timeout(tmp_path):
    started = time.monotonic()
    result = run_command(["/bin/sh", "-c", "/bin/sleep 2 &"], cwd=tmp_path, workspace=tmp_path, timeout_seconds=0.3)
    assert not result.ok and result.error_type == "Timeout", result
    assert time.monotonic() - started < 1.5
