from __future__ import annotations

import json
import platform
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.compact import compact_anthropic_messages, compact_openai_messages
from agent.config import Settings, UserAccount
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.mcp_client import McpIndeterminateError, McpTimeoutError
from agent.mcp_config import (
    McpServerConfig,
    is_project_mcp_trusted,
    project_mcp_config_path,
    trust_project_mcp,
)
from agent.mcp_manager import McpManager, McpServerState
from agent.processes import run_command
from agent.safe_paths import resolve_workspace_path
from agent.tools import _resolve_public_addresses
from agent.worker import TaskWorker


def worker_settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider="openai",
        model="fake",
        provider_fallbacks=[],
    )


class LeaseFencingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "tasks.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_task(self, task_id: str = "task") -> None:
        conversation = self.store.get_or_create_default_conversation("u", "p")
        self.store.create_task(
            {
                "id": task_id,
                "user_id": "u",
                "project_id": "p",
                "conversation_id": conversation["id"],
                "prompt": "test",
                "status": "queued",
                "created_at": time.time(),
            }
        )
        ConversationEventStore(self.store).create_turn(
            conversation["id"],
            "u",
            "p",
            task_id=task_id,
            status="queued",
        )

    def test_stale_claim_cannot_heartbeat_or_release(self) -> None:
        self.create_task()
        first = self.store.claim_next_task("worker-1", lease_seconds=1)
        self.assertIsNotNone(first)
        self.store.update_task("task", lease_expires_at=time.time() - 1)
        second = self.store.claim_next_task("worker-2", lease_seconds=30)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["claim_token"], second["claim_token"])
        self.assertFalse(
            self.store.heartbeat_task(
                "task",
                "worker-1",
                claim_token=first["claim_token"],
            )
        )
        self.assertFalse(
            self.store.release_task(
                "task",
                "worker-1",
                "succeeded",
                claim_token=first["claim_token"],
            )
        )
        self.assertEqual(self.store.get_task("task", "u")["claim_owner"], "worker-2")

    def test_heartbeat_prevents_takeover_during_long_call(self) -> None:
        self.create_task()
        started = threading.Event()

        def run_fn(*_args):
            started.set()
            time.sleep(0.2)

        first = TaskWorker(
            self.store,
            run_fn,
            worker_settings(),
            lease_seconds=0.06,
            poll_interval=0.01,
        )
        second = TaskWorker(
            self.store,
            run_fn,
            worker_settings(),
            lease_seconds=0.06,
            poll_interval=0.01,
        )
        thread = threading.Thread(target=first.run_once)
        thread.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.1)
        self.assertIsNone(second.run_once())
        thread.join(1)
        self.assertEqual(self.store.get_task("task", "u")["status"], "succeeded")

    def test_running_pause_must_be_acknowledged_before_resume(self) -> None:
        self.create_task()
        claimed = self.store.claim_next_task("worker", lease_seconds=30)
        self.assertTrue(self.store.pause_task("task", "u"))
        pending = self.store.get_task("task", "u")
        self.assertEqual(pending["status"], "running")
        self.assertEqual(pending["pause_requested"], 1)
        self.assertFalse(self.store.resume_task("task", "u"))
        self.assertTrue(
            self.store.release_task(
                "task",
                "worker",
                "paused",
                claim_token=claimed["claim_token"],
            )
        )
        self.assertTrue(self.store.resume_task("task", "u"))


class ContextAndPathHardeningTests(unittest.TestCase):
    def test_compaction_preserves_tool_pairs_and_valid_arguments(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old" * 500},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "a" * 1000}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "x" * 2000},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "done"},
        ]
        compacted, changed = compact_openai_messages(
            messages,
            max_chars=700,
            keep_recent=1,
        )
        self.assertTrue(changed)
        calls = [
            call
            for message in compacted
            for call in message.get("tool_calls", [])
        ]
        for call in calls:
            json.loads(call["function"]["arguments"])
            call_id = call["id"]
            self.assertTrue(
                any(
                    item.get("role") == "tool"
                    and item.get("tool_call_id") == call_id
                    for item in compacted
                )
            )

        anthropic = [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "read", "input": {}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 2000}],
            },
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "done"},
        ]
        compacted_anthropic, _ = compact_anthropic_messages(
            anthropic,
            max_chars=500,
            keep_recent=1,
        )
        tool_use_ids = {
            block["id"]
            for message in compacted_anthropic
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        }
        result_ids = {
            block["tool_use_id"]
            for message in compacted_anthropic
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_result"
        }
        self.assertEqual(tool_use_ids, result_ids)

    def test_prefix_and_symlink_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            sibling = Path(temp) / "project-secret"
            root.mkdir()
            sibling.mkdir()
            with self.assertRaises(PermissionError):
                resolve_workspace_path(root, "../project-secret/value.txt")
            outside = sibling / "value.txt"
            outside.write_text("secret", encoding="utf-8")
            (root / "linked").symlink_to(sibling, target_is_directory=True)
            with self.assertRaises(PermissionError):
                resolve_workspace_path(root, "linked/value.txt")

    def test_dns_answer_with_private_address_is_rejected(self) -> None:
        records = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=records):
            with self.assertRaises(ValueError):
                _resolve_public_addresses("https://example.com/file")

    @unittest.skipUnless(platform.system() == "Darwin", "requires sandbox-exec")
    def test_process_cannot_read_other_home_files(self) -> None:
        marker = Path.home() / f".android-agent-sandbox-{uuid.uuid4().hex}"
        marker.write_text("secret", encoding="utf-8")
        try:
            with tempfile.TemporaryDirectory() as temp:
                result = run_command(
                    [
                        sys.executable,
                        "-c",
                        (
                            "from pathlib import Path\n"
                            f"p=Path({str(marker)!r})\n"
                            "try:\n p.read_text(); print('LEAKED')\n"
                            "except PermissionError:\n print('DENIED')\n"
                        ),
                    ],
                    cwd=".",
                    workspace=Path(temp),
                )
            self.assertTrue(result.ok)
            self.assertIn("DENIED", result.stdout)
            self.assertNotIn("LEAKED", result.stdout)
        finally:
            marker.unlink(missing_ok=True)


class McpHardeningTests(unittest.TestCase):
    def test_indeterminate_call_is_not_retried(self) -> None:
        class TimeoutTransport:
            calls = 0
            closed = False

            def healthy(self):
                return True

            def call_tool(self, *_args, **_kwargs):
                self.calls += 1
                raise McpTimeoutError("timeout")

            def close(self):
                self.closed = True

        manager = McpManager("u", "p", Path("/tmp"))
        transport = TimeoutTransport()
        manager._servers["s"] = McpServerState(  # noqa: SLF001
            config=McpServerConfig(name="s"),
            status="ready",
            transport=transport,
        )
        with self.assertRaises(McpIndeterminateError):
            manager.call_tool("s", "side_effect", {})
        self.assertEqual(transport.calls, 1)
        self.assertTrue(transport.closed)

    def test_trust_changes_when_declared_script_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "server.py"
            script.write_text("print('v1')", encoding="utf-8")
            config_path = project_mcp_config_path(root)
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "local": {
                                "command": sys.executable,
                                "args": [str(script)],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            data = root / "data"
            with patch("agent.mcp_config.paths.DATA_DIR", data):
                trust_project_mcp("u", "p", root)
                self.assertTrue(is_project_mcp_trusted("u", "p", root))
                script.write_text("print('v2')", encoding="utf-8")
                self.assertFalse(is_project_mcp_trusted("u", "p", root))


class ProjectDeletionTests(unittest.TestCase):
    def settings(self) -> Settings:
        return Settings(
            provider="openai",
            api_key="fake",
            model="fake",
            model_candidates=["fake"],
            max_turns=3,
            max_auto_continuations=0,
            max_gradle_retries=1,
            compact_max_chars=50_000,
            max_output_tokens=1024,
            base_url=None,
            auto_build_after_edit=False,
            server_host="127.0.0.1",
            server_port=8000,
            api_token="",
            users=[UserAccount(id="u", token="token")],
        )

    def test_active_approval_task_blocks_project_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(
                settings=self.settings(),
                task_store=TaskStore(Path(temp) / "db.sqlite"),
            )
            with (
                patch("agent.api.load_project_meta", return_value={}),
                patch(
                    "agent.api.list_jobs",
                    return_value=[{"id": "task", "status": "awaiting_approval"}],
                ),
                patch("agent.api.list_terminals", return_value=[]),
                patch("agent.api.list_worktrees", return_value=[]),
                patch("agent.api.get_mcp_manager") as manager,
                patch("agent.api.delete_project") as delete_project,
            ):
                response = TestClient(app).delete(
                    "/api/projects/p",
                    headers={"Authorization": "Bearer token"},
                )
        self.assertEqual(response.status_code, 409)
        self.assertIn("task", response.json()["detail"]["task_ids"])
        manager.return_value.stop_all.assert_called_once()
        delete_project.assert_not_called()
