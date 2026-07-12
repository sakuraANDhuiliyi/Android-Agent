from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.database import TaskStore
from agent.tools import is_writable_path, summarize_build_log, write_file
from agent.loop import _total_turns, run_agent


class TaskStoreTests(unittest.TestCase):
    def test_task_events_and_cancel_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.db"
            store = TaskStore(path)
            store.create_task({
                "id": "task1", "user_id": "user1", "project_id": "project1",
                "prompt": "做一个计数器", "status": "queued", "provider": "openai",
                "model": "model", "created_at": 1.0,
            })
            store.add_event("task1", "plan", {"message": "先修改布局"})
            self.assertTrue(store.request_cancel("task1", "user1"))

            reopened = TaskStore(path)
            task = reopened.get_task("task1", "user1")
            self.assertIsNotNone(task)
            self.assertTrue(task["cancel_requested"])
            self.assertEqual(task["events"][0]["message"], "先修改布局")

    def test_user_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "tasks.db")
            store.create_task({
                "id": "task1", "user_id": "alice", "project_id": "p",
                "prompt": "x", "status": "queued", "provider": None,
                "model": None, "created_at": 1.0,
            })
            self.assertIsNone(store.get_task("task1", "bob"))


class ToolSafetyTests(unittest.TestCase):
    def test_write_whitelist(self) -> None:
        self.assertTrue(is_writable_path("app/src/main/res/layout/main.xml"))
        self.assertTrue(is_writable_path("app/build.gradle.kts"))
        self.assertFalse(is_writable_path("settings.gradle.kts"))
        self.assertFalse(is_writable_path("../agent/api.py"))

    def test_write_cannot_escape_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = write_file(Path(temp), "../agent/api.py", "bad")
            self.assertFalse(result.ok)

    def test_build_log_summary_keeps_first_error_and_tail(self) -> None:
        log = "\n".join(["start", "e: Main.kt:4: error: unresolved reference", *[f"line {i}" for i in range(150)], "BUILD FAILED"])
        summary = summarize_build_log(log, tail_lines=20)
        self.assertIn("unresolved reference", summary)
        self.assertIn("BUILD FAILED", summary)
        self.assertLess(len(summary.splitlines()), 45)


class AgentLoopTests(unittest.TestCase):
    def test_auto_continuation_expands_turn_budget(self) -> None:
        settings = SimpleNamespace(max_turns=15, max_auto_continuations=2)
        self.assertEqual(_total_turns(settings), 45)

    def test_cancel_check_is_forwarded_to_provider_loop(self) -> None:
        check = lambda: None
        settings = SimpleNamespace(api_key="key", provider="deepseek", provider_fallbacks=[])
        with patch("agent.loop._run_agent_with_provider", return_value="ok") as provider_loop:
            result = run_agent(settings, Path("."), "user", "project", "prompt", cancel_check=check)
        self.assertEqual(result, "ok")
        self.assertIs(provider_loop.call_args.args[-1], check)


if __name__ == "__main__":
    unittest.main()
