from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.compact import build_session_prior_messages, compact_openai_messages, estimate_message_chars
from agent.database import TaskStore
from agent.tools import (
    glob_files,
    grep_files,
    is_writable_path,
    str_replace,
    summarize_build_log,
    write_file,
)
from agent.loop import _total_turns, run_agent
from agent.changes import compare_snapshots, snapshot_workspace
from agent import jobs as jobs_mod


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

    def test_session_turns_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "tasks.db")
            store.append_session_turn("u", "p", user="hi", assistant="ok", changed_files=["a.kt"])
            turns = store.get_session_turns("u", "p")
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["user"], "hi")
            store.clear_session("u", "p")
            self.assertEqual(store.get_session_turns("u", "p"), [])


class ConversationStoreTests(unittest.TestCase):
    @staticmethod
    def _settings(**overrides):
        base = dict(
            provider="openai",
            model="test",
            api_key="k",
            auto_build_after_edit=False,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_conversation_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "tasks.db")
            a = store.create_conversation("u", "p", title="对话 A")
            b = store.create_conversation("u", "p", title="对话 B")
            store.append_conversation_turn(a["id"], user="A 问", assistant="A 答")
            store.append_conversation_turn(b["id"], user="B 问", assistant="B 答")
            self.assertEqual(store.get_conversation_turns(a["id"])[0]["user"], "A 问")
            self.assertEqual(store.get_conversation_turns(b["id"])[0]["user"], "B 问")
            self.assertNotEqual(a["id"], b["id"])

    def test_migrate_legacy_session_to_default_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.db"
            store = TaskStore(path)
            with store._connect() as conn:
                conn.execute(
                    """INSERT INTO project_sessions(user_id, project_id, turns_json, updated_at)
                       VALUES (?,?,?,?)""",
                    (
                        "u",
                        "p",
                        '[{"user":"旧消息","assistant":"旧回复","changed_files":[]}]',
                        100.0,
                    ),
                )
            migrated = TaskStore(path)
            convs = migrated.list_conversations("u", "p")
            self.assertTrue(any(c["title"] == "默认对话" for c in convs))
            default = next(c for c in convs if c["title"] == "默认对话")
            self.assertEqual(default["turns"][0]["user"], "旧消息")

    def test_default_conversation_prefers_titled_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "tasks.db")
            default = store.create_conversation("u", "p", title="默认对话")
            newer = store.create_conversation("u", "p", title="新对话")
            store.append_conversation_turn(newer["id"], user="新", assistant="答")
            got = store.get_or_create_default_conversation("u", "p")
            self.assertEqual(got["id"], default["id"])
            self.assertEqual(got["title"], "默认对话")

    def test_ask_without_gradle_can_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            builds = root / "builds"
            builds.mkdir()
            store = TaskStore(root / "tasks.db")
            locks: set = set()
            with (
                patch.object(jobs_mod, "_store", store),
                patch.object(jobs_mod, "_project_locks", locks),
                patch("agent.jobs.load_project_meta", return_value={}),
                patch("agent.jobs.workspace_path", return_value=root),
                patch("agent.jobs.user_builds_dir", return_value=builds),
                patch("agent.jobs.snapshot_workspace", return_value={}),
                patch("agent.jobs.compare_snapshots", return_value=([], "")),
                patch("agent.jobs.run_agent", return_value="这是纯文本回答，未构建"),
            ):
                job = jobs_mod.start_ask_job(
                    "u",
                    "p",
                    "这个布局是干什么的？",
                    self._settings(),
                )
                task = self._wait_task(store, job["id"], "u", locks=locks)
            self.assertEqual(task["status"], "succeeded")
            self.assertIn("纯文本", task["final_message"] or "")
            self.assertTrue(task.get("conversation_id"))

    def test_failed_assemble_debug_fails_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            builds = root / "builds"
            builds.mkdir()
            store = TaskStore(root / "tasks.db")
            locks: set = set()

            def fake_agent(*_args, **kwargs):
                on_event = kwargs.get("on_event")
                if on_event:
                    on_event(
                        "tool_result",
                        {
                            "name": "run_gradle",
                            "ok": False,
                            "input": {"task": "assembleDebug"},
                            "preview": "BUILD FAILED",
                        },
                    )
                return "构建失败了"

            with (
                patch.object(jobs_mod, "_store", store),
                patch.object(jobs_mod, "_project_locks", locks),
                patch("agent.jobs.load_project_meta", return_value={}),
                patch("agent.jobs.workspace_path", return_value=root),
                patch("agent.jobs.user_builds_dir", return_value=builds),
                patch("agent.jobs.snapshot_workspace", return_value={}),
                patch("agent.jobs.compare_snapshots", return_value=([], "")),
                patch("agent.jobs.run_agent", side_effect=fake_agent),
            ):
                job = jobs_mod.start_ask_job("u", "p", "改一下再构建", self._settings())
                task = self._wait_task(store, job["id"], "u", locks=locks)
            self.assertEqual(task["status"], "failed")

    def test_failed_non_assemble_gradle_does_not_fail_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            builds = root / "builds"
            builds.mkdir()
            store = TaskStore(root / "tasks.db")
            locks: set = set()

            def fake_agent(*_args, **kwargs):
                on_event = kwargs.get("on_event")
                if on_event:
                    on_event(
                        "tool_result",
                        {
                            "name": "run_gradle",
                            "ok": False,
                            "input": {"task": "clean"},
                            "preview": "CLEAN FAILED",
                        },
                    )
                return "清理失败但任务可继续"

            with (
                patch.object(jobs_mod, "_store", store),
                patch.object(jobs_mod, "_project_locks", locks),
                patch("agent.jobs.load_project_meta", return_value={}),
                patch("agent.jobs.workspace_path", return_value=root),
                patch("agent.jobs.user_builds_dir", return_value=builds),
                patch("agent.jobs.snapshot_workspace", return_value={}),
                patch("agent.jobs.compare_snapshots", return_value=([], "")),
                patch("agent.jobs.run_agent", side_effect=fake_agent),
            ):
                job = jobs_mod.start_ask_job("u", "p", "跑一下 clean", self._settings())
                task = self._wait_task(store, job["id"], "u", locks=locks)
            self.assertEqual(task["status"], "succeeded")

    def test_prior_turns_stay_in_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            builds = root / "builds"
            builds.mkdir()
            store = TaskStore(root / "tasks.db")
            a = store.create_conversation("u", "p", title="A")
            b = store.create_conversation("u", "p", title="B")
            store.append_conversation_turn(a["id"], user="只在 A", assistant="A 答")
            captured: list = []

            def fake_agent(*_args, **kwargs):
                captured.append(list(kwargs.get("prior_turns") or []))
                return "ok"

            locks: set = set()
            with (
                patch.object(jobs_mod, "_store", store),
                patch.object(jobs_mod, "_project_locks", locks),
                patch("agent.jobs.load_project_meta", return_value={}),
                patch("agent.jobs.workspace_path", return_value=root),
                patch("agent.jobs.user_builds_dir", return_value=builds),
                patch("agent.jobs.snapshot_workspace", return_value={}),
                patch("agent.jobs.compare_snapshots", return_value=([], "")),
                patch("agent.jobs.run_agent", side_effect=fake_agent),
            ):
                job = jobs_mod.start_ask_job(
                    "u", "p", "继续", self._settings(), conversation_id=b["id"]
                )
                self._wait_task(store, job["id"], "u", locks=locks)
            self.assertEqual(captured[0], [])

            locks2: set = set()
            with (
                patch.object(jobs_mod, "_store", store),
                patch.object(jobs_mod, "_project_locks", locks2),
                patch("agent.jobs.load_project_meta", return_value={}),
                patch("agent.jobs.workspace_path", return_value=root),
                patch("agent.jobs.user_builds_dir", return_value=builds),
                patch("agent.jobs.snapshot_workspace", return_value={}),
                patch("agent.jobs.compare_snapshots", return_value=([], "")),
                patch("agent.jobs.run_agent", side_effect=fake_agent),
            ):
                job2 = jobs_mod.start_ask_job(
                    "u", "p", "继续 A", self._settings(), conversation_id=a["id"]
                )
                self._wait_task(store, job2["id"], "u", locks=locks2)
            self.assertEqual(captured[1][0]["user"], "只在 A")

    @staticmethod
    def _wait_task(
        store: TaskStore,
        task_id: str,
        user_id: str,
        timeout: float = 3.0,
        locks: set | None = None,
        project_id: str = "p",
    ):
        import time

        deadline = time.time() + timeout
        task = None
        while time.time() < deadline:
            task = store.get_task(task_id, user_id)
            if task and task["status"] in {"succeeded", "failed", "canceled"}:
                break
            time.sleep(0.02)
        # Wait for job finally{} to release the project lock before temp DB teardown
        if locks is not None:
            key = (user_id, project_id)
            while key in locks and time.time() < deadline:
                time.sleep(0.02)
        else:
            time.sleep(0.05)
        return task or store.get_task(task_id, user_id)


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

    def test_str_replace_and_grep_glob(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "app/src/main/java/Demo.kt"
            target.parent.mkdir(parents=True)
            target.write_text("fun hello() {\n  println(\"a\")\n}\n", encoding="utf-8")

            bad = str_replace(root, "settings.gradle.kts", "a", "b")
            self.assertFalse(bad.ok)

            missing = str_replace(root, "app/src/main/java/Demo.kt", "nope", "x")
            self.assertFalse(missing.ok)

            ok = str_replace(root, "app/src/main/java/Demo.kt", 'println("a")', 'println("b")')
            self.assertTrue(ok.ok)
            self.assertIn('println("b")', target.read_text(encoding="utf-8"))

            grepped = grep_files(root, r"println", path="app/src")
            self.assertTrue(grepped.ok)
            self.assertIn("Demo.kt", str(grepped.output))

            found = glob_files(root, "*.kt", path="app/src")
            self.assertTrue(found.ok)
            self.assertIn("Demo.kt", str(found.output))

    def test_build_log_summary_keeps_first_error_and_tail(self) -> None:
        log = "\n".join(["start", "e: Main.kt:4: error: unresolved reference", *[f"line {i}" for i in range(150)], "BUILD FAILED"])
        summary = summarize_build_log(log, tail_lines=20)
        self.assertIn("unresolved reference", summary)
        self.assertIn("BUILD FAILED", summary)
        self.assertLess(len(summary.splitlines()), 45)


class CompactTests(unittest.TestCase):
    def test_compact_shrinks_tool_output(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "read_file", "arguments": "x" * 500}}]},
            {"role": "tool", "tool_call_id": "1", "content": "y" * 5000},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "u4"},
            {"role": "assistant", "content": "a4"},
        ]
        before = estimate_message_chars(messages)
        compacted, changed = compact_openai_messages(messages, max_chars=2000)
        self.assertTrue(changed)
        self.assertLess(estimate_message_chars(compacted), before)

    def test_prior_messages_from_turns(self) -> None:
        prior = build_session_prior_messages(
            [{"user": "改标题", "assistant": "已改", "changed_files": ["app/src/main/res/values/strings.xml"]}]
        )
        self.assertEqual(prior[0]["role"], "user")
        self.assertIn("strings.xml", prior[1]["content"])


class ChangesTests(unittest.TestCase):
    def test_modified_file_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "app/src/main/res/values/strings.xml"
            path.parent.mkdir(parents=True)
            path.write_text("<resources><string name=\"a\">one</string></resources>\n", encoding="utf-8")
            before = snapshot_workspace(root)
            path.write_text("<resources><string name=\"a\">two</string></resources>\n", encoding="utf-8")
            after = snapshot_workspace(root)
            changes, diff = compare_snapshots(root, before, after)
            self.assertEqual(changes[0]["change"], "modified")
            self.assertIn("strings.xml", diff)
            self.assertIn("two", diff)


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
        # cancel_check is still the last positional before prior was added — check kwargs/args
        self.assertIs(provider_loop.call_args.args[6], check)


if __name__ == "__main__":
    unittest.main()
