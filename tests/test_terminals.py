import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import paths as paths_mod
from agent.api import create_app
from agent.config import Settings, UserAccount
from agent.database import TaskStore
from agent.terminal import (
    TerminalStore,
    create_terminal,
    get_terminal,
    list_terminals,
    mark_interrupted_terminals,
    terminate_terminal,
    terminal_outputs,
    write_terminal_input,
)


def _make_workspace(temp: Path, user_id: str, project_id: str) -> Path:
    ws = paths_mod.workspace_path(user_id, project_id)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".agent-project.json").write_text(
        '{"id": "' + project_id + '", "name": "' + project_id + '"}',
        encoding="utf-8",
    )
    return ws


def _api_settings(*, terminal_enabled: bool) -> Settings:
    return Settings(
        provider="openai",
        api_key="fake",
        model="fake",
        model_candidates=["fake"],
        max_turns=2,
        max_auto_continuations=0,
        max_gradle_retries=1,
        compact_max_chars=50_000,
        max_output_tokens=1024,
        base_url="https://example.test",
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
        users=[UserAccount(id="local", token="test-token")],
        terminal_enabled=terminal_enabled,
    )


class TerminalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._workspaces = temp / "workspaces"
        self._data = temp / "data"
        self._workspaces.mkdir()
        self._data.mkdir()
        self._patches = [
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
        ]
        for p in self._patches:
            p.start()
        self._workspace = _make_workspace(temp, "user1", "project1")
        self._store = TerminalStore()

    def tearDown(self) -> None:
        # Terminate any running terminals.
        from agent.terminal import _manager
        with _manager._lock:
            sessions = list(_manager._sessions.values())
        for session in sessions:
            if session.status in {"starting", "running"}:
                session.terminate()
        with _manager._lock:
            for session in sessions:
                _manager._sessions.pop(session.session_id, None)
        for p in reversed(self._patches):
            p.stop()
        self._temp.cleanup()

    def test_create_terminal_and_exit_code(self) -> None:
        info = create_terminal(
            "user1",
            "project1",
            argv=["python3", "-c", "print('hello')"],
        )
        self.assertEqual(info["user_id"], "user1")
        self.assertEqual(info["project_id"], "project1")
        self.assertEqual(info["status"], "running")
        # Wait for the process to exit.
        for _ in range(50):
            fresh = get_terminal(info["id"], "user1")
            if fresh and fresh["status"] in {"exited", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(fresh["status"], "exited")
        self.assertEqual(fresh["exit_code"], 0)
        outputs = terminal_outputs(info["id"])
        self.assertTrue(any("hello" in o["data"] for o in outputs))

    def test_input_and_output(self) -> None:
        info = create_terminal(
            "user1",
            "project1",
            shell="/bin/bash",
        )
        time.sleep(0.2)
        write_terminal_input(info["id"], "user1", "echo from-input\n")
        for _ in range(50):
            outputs = terminal_outputs(info["id"])
            if any("from-input" in o["data"] for o in outputs):
                break
            time.sleep(0.05)
        self.assertTrue(any("from-input" in o["data"] for o in terminal_outputs(info["id"])))
        terminate_terminal(info["id"], "user1")

    def test_resize(self) -> None:
        info = create_terminal(
            "user1",
            "project1",
            shell="/bin/bash",
        )
        time.sleep(0.2)
        from agent.terminal import _manager
        session = _manager.get(info["id"], "user1")
        self.assertIsNotNone(session)
        session.resize(120, 40)
        self.assertEqual(session.cols, 120)
        self.assertEqual(session.rows, 40)
        terminate_terminal(info["id"], "user1")

    def test_timeout_terminate(self) -> None:
        info = create_terminal(
            "user1",
            "project1",
            argv=["python3", "-c", "import time; time.sleep(30)"],
        )
        self.assertEqual(info["status"], "running")
        terminate_terminal(info["id"], "user1")
        fresh = get_terminal(info["id"], "user1")
        self.assertIn(fresh["status"], {"terminated", "exited", "failed"})

    def test_cursor_resume(self) -> None:
        info = create_terminal(
            "user1",
            "project1",
            argv=["python3", "-c", "for i in range(3): print(i)"],
        )
        # Wait for all output.
        for _ in range(50):
            fresh = get_terminal(info["id"], "user1")
            if fresh and fresh["status"] in {"exited", "failed"}:
                break
            time.sleep(0.05)
        all_out = terminal_outputs(info["id"])
        seqs = [o["seq"] for o in all_out]
        if seqs:
            after = max(seqs) - 1
            resumed = terminal_outputs(info["id"], after_seq=after)
            self.assertTrue(all(o["seq"] > after for o in resumed))

    def test_buffer_does_not_grow_unbounded(self) -> None:
        # Generate many output lines.
        info = create_terminal(
            "user1",
            "project1",
            argv=["python3", "-c", "for i in range(100): print('line', i)"],
        )
        for _ in range(50):
            fresh = get_terminal(info["id"], "user1")
            if fresh and fresh["status"] in {"exited", "failed"}:
                break
            time.sleep(0.05)
        outputs = terminal_outputs(info["id"])
        self.assertLessEqual(len(outputs), 2000)

    def test_restart_marks_running_as_interrupted(self) -> None:
        info = create_terminal(
            "user1",
            "project1",
            argv=["python3", "-c", "import time; time.sleep(30)"],
        )
        mark_interrupted_terminals()
        fresh = get_terminal(info["id"], "user1")
        self.assertEqual(fresh["status"], "interrupted")
        self.assertEqual(fresh["exit_code"], -1)

    def test_user_isolation(self) -> None:
        _make_workspace(self._data, "user2", "project2")
        info1 = create_terminal(
            "user1",
            "project1",
            argv=["python3", "-c", "print('a')"],
        )
        info2 = create_terminal(
            "user2",
            "project2",
            argv=["python3", "-c", "print('b')"],
        )
        self.assertIsNotNone(get_terminal(info1["id"], "user1"))
        self.assertIsNone(get_terminal(info1["id"], "user2"))
        self.assertIsNotNone(get_terminal(info2["id"], "user2"))
        self.assertIsNone(get_terminal(info2["id"], "user1"))

    def test_cwd_escape_is_blocked(self) -> None:
        with self.assertRaises(PermissionError):
            create_terminal(
                "user1",
                "project1",
                cwd="/tmp",
            )

    def test_env_redaction(self) -> None:
        # Start with a fake API key in env; output should not leak it.
        info = create_terminal(
            "user1",
            "project1",
            argv=["python3", "-c", "import os; print(os.environ.get('SENSITIVE', ''))"],
            env={"SENSITIVE": "sk-12345678901234567890"},
        )
        for _ in range(50):
            fresh = get_terminal(info["id"], "user1")
            if fresh and fresh["status"] in {"exited", "failed"}:
                break
            time.sleep(0.05)
        outputs = terminal_outputs(info["id"])
        joined = "".join(o["data"] for o in outputs)
        self.assertNotIn("sk-1234567890", joined)

    def test_resource_limit_sessions(self) -> None:
        sessions = []
        for _ in range(5):
            sessions.append(
                create_terminal(
                    "user1",
                    "project1",
                    argv=["python3", "-c", "import time; time.sleep(30)"],
                )
            )
        with self.assertRaises(RuntimeError):
            create_terminal(
                "user1",
                "project1",
                argv=["python3", "-c", "print('x')"],
            )
        for s in sessions:
            terminate_terminal(s["id"], "user1")

    def test_dangerous_input_blocked(self) -> None:
        info = create_terminal(
            "user1",
            "project1",
            shell="/bin/bash",
        )
        time.sleep(0.2)
        result = write_terminal_input(info["id"], "user1", "rm -rf /\n")
        self.assertFalse(result["ok"])
        terminate_terminal(info["id"], "user1")


class TerminalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._workspaces = temp / "workspaces"
        self._data = temp / "data"
        self._workspaces.mkdir()
        self._data.mkdir()
        self._patches = [
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
        ]
        for p in self._patches:
            p.start()
        _make_workspace(temp, "local", "demo")
        self._client = TestClient(
            create_app(
                settings=_api_settings(terminal_enabled=True),
                task_store=TaskStore(self._data / "agent.db"),
            ),
            headers={"Authorization": "Bearer test-token"},
        )

    def tearDown(self) -> None:
        from agent.terminal import _manager
        with _manager._lock:
            sessions = list(_manager._sessions.values())
        for session in sessions:
            if session.status in {"starting", "running"}:
                session.terminate()
        with _manager._lock:
            for session in sessions:
                _manager._sessions.pop(session.session_id, None)
        for p in reversed(self._patches):
            p.stop()
        self._temp.cleanup()

    def test_api_create_and_list(self) -> None:
        resp = self._client.post(
            "/api/projects/demo/terminals",
            json={"argv": ["python3", "-c", "print('api')"]},
        )
        self.assertEqual(resp.status_code, 201)
        terminal_id = resp.json()["id"]
        resp = self._client.get("/api/projects/demo/terminals")
        self.assertEqual(resp.status_code, 200)
        ids = [t["id"] for t in resp.json()["terminals"]]
        self.assertIn(terminal_id, ids)
        resp = self._client.get(f"/api/terminals/{terminal_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], terminal_id)

    def test_api_websocket_cursor(self) -> None:
        resp = self._client.post(
            "/api/projects/demo/terminals",
            json={"argv": ["python3", "-c", "print('ws')"]},
        )
        terminal_id = resp.json()["id"]
        for _ in range(50):
            info = get_terminal(terminal_id, "local")
            if info and info["status"] in {"exited", "failed"}:
                break
            time.sleep(0.05)
        with self._client.websocket_connect(
            f"/api/ws/terminals/{terminal_id}"
        ) as ws:
            messages = []
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "done":
                    break
                messages.append(msg)
        if messages:
            last_seq = max(m["seq"] for m in messages)
            with self._client.websocket_connect(
                f"/api/ws/terminals/{terminal_id}?after_seq={last_seq}"
            ) as ws:
                resumed = []
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "done":
                        break
                    resumed.append(msg)
            self.assertEqual(resumed, [])
