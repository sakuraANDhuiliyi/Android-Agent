from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.compact import build_session_prior_messages, compact_openai_messages, estimate_message_chars
from agent.database import TaskStore
from agent.honesty import (
    prompt_expects_file_edit,
    sanitize_final_answer,
    text_claims_file_edit,
)
from agent.tools import (
    dispatch_tool,
    get_tool_definitions,
    glob_files,
    grep_files,
    is_writable_path,
    str_replace,
    summarize_build_log,
    web_search,
    write_file,
)
from agent.model_fallback import should_try_next_provider
from agent.stream import _DeltaFlusher
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

    def test_canonical_history_stays_in_conversation(self) -> None:
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
                captured.append(list(kwargs.get("conversation_events") or []))
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
            self.assertFalse(
                any(
                    event.get("payload", {}).get("content")
                    == [{"type": "text", "text": "只在 A"}]
                    for event in captured[0]
                )
            )

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
            self.assertTrue(
                any(
                    event.get("payload", {}).get("content")
                    == [{"type": "text", "text": "只在 A"}]
                    for event in captured[1]
                )
            )

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


class HonestyTests(unittest.TestCase):
    def test_prompt_expects_edit(self) -> None:
        self.assertTrue(prompt_expects_file_edit("请创建一个跑酷游戏"))
        self.assertTrue(prompt_expects_file_edit("帮我修改 MainActivity"))
        self.assertFalse(prompt_expects_file_edit("什么样的提示词比较好"))

    def test_claims_edit(self) -> None:
        self.assertTrue(text_claims_file_edit("改造完成！游戏已经改好了"))
        self.assertFalse(text_claims_file_edit("本轮未改任何文件，仅说明方案"))
        self.assertFalse(text_claims_file_edit("已请求下载，请在对话中确认是否允许"))

    def test_sanitize_blocks_fake_success(self) -> None:
        out = sanitize_final_answer(
            "✅ 改造完成，已更新 GameView.kt",
            changed_files=[],
            successful_edits=0,
            user_prompt="参考神庙逃亡重新设计",
        )
        self.assertIn("系统校验", out)
        self.assertIn("未检测到任何文件改动", out)

    def test_sanitize_appends_real_changes(self) -> None:
        out = sanitize_final_answer(
            "已写入布局",
            changed_files=[{"path": "app/src/main/res/layout/activity_main.xml", "change": "modified"}],
            successful_edits=1,
            user_prompt="改一下布局",
        )
        self.assertIn("本轮实际改动", out)
        self.assertIn("activity_main.xml", out)

    def test_sanitize_user_rejected_download_is_not_deception(self) -> None:
        out = sanitize_final_answer(
            "用户拒绝了下载，本轮未改任何文件",
            changed_files=[],
            successful_edits=0,
            user_prompt="下载素材并改造游戏",
            approval_decisions=["rejected"],
        )
        self.assertIn("系统说明", out)
        self.assertIn("未批准下载", out)
        self.assertNotIn("系统校验", out)

    def test_sanitize_waiting_for_permission(self) -> None:
        out = sanitize_final_answer(
            "请确认是否允许下载 icon.png",
            changed_files=[],
            successful_edits=0,
            user_prompt="下载一个图标",
        )
        self.assertIn("系统说明", out)
        self.assertNotIn("请勿当作已落地的代码改动", out)


class WebSearchTests(unittest.TestCase):
    def test_web_search_requires_key(self) -> None:
        result = web_search("android viewbinding", api_key="")
        self.assertFalse(result.ok)
        self.assertIn("Tavily", result.output)

    def test_web_search_tool_registered_when_configured(self) -> None:
        without = get_tool_definitions(SimpleNamespace(max_gradle_retries=3, tavily_api_key=""))
        self.assertFalse(any(t["name"] == "web_search" for t in without))
        self.assertTrue(any(t["name"] == "download_file" for t in without))
        with_key = get_tool_definitions(SimpleNamespace(max_gradle_retries=3, tavily_api_key="tvly-test"))
        self.assertTrue(any(t["name"] == "web_search" for t in with_key))

    def test_web_search_formats_results(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "query": "viewbinding",
                    "answer": "Use ActivityMainBinding",
                    "results": [
                        {
                            "title": "View Binding",
                            "url": "https://developer.android.com/topic/libraries/view-binding",
                            "content": "View binding generates a binding class",
                            "score": 0.95,
                        }
                    ],
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None):
                self.url = url
                self.json = json
                return FakeResponse()

        with patch("httpx.Client", FakeClient):
            result = web_search("viewbinding", api_key="tvly-test", max_results=3)
        self.assertTrue(result.ok)
        self.assertIn("View Binding", result.output)
        self.assertIn("developer.android.com", result.output)
        self.assertIn("摘要:", result.output)


class DownloadFileTests(unittest.TestCase):
    def test_rejects_non_http_url(self) -> None:
        from agent.tools import download_file

        with tempfile.TemporaryDirectory() as temp:
            result = download_file(
                Path(temp),
                "file:///etc/passwd",
                "downloads/x.txt",
                user_id="local",
                task_id="task1",
            )
        self.assertFalse(result.ok)
        self.assertIn("http", result.output.lower())

    def test_requires_user_approval(self) -> None:
        from agent.approvals import resolve_approval
        from agent.tools import download_file

        events: list[tuple[str, dict]] = []

        def on_event(event_type: str, payload: dict) -> None:
            events.append((event_type, payload))
            if event_type == "approval_required":
                resolve_approval(payload["approval_id"], "local", approved=False)

        with tempfile.TemporaryDirectory() as temp:
            result = download_file(
                Path(temp),
                "https://example.com/a.png",
                "downloads/a.png",
                user_id="local",
                task_id="task1",
                on_event=on_event,
                timeout_sec=5,
            )
        self.assertFalse(result.ok)
        self.assertIn("拒绝", result.output)
        self.assertTrue(any(t == "approval_required" for t, _ in events))
        self.assertTrue(any(t == "approval_resolved" for t, _ in events))

    def test_downloads_after_approval(self) -> None:
        from agent.approvals import resolve_approval
        from agent.tools import download_file

        class FakeStreamResponse:
            status_code = 200
            headers = {"content-length": "4"}

            def iter_bytes(self):
                yield b"data"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_stream(*args, **kwargs):
            return FakeStreamResponse()

        def on_event(event_type: str, payload: dict) -> None:
            if event_type == "approval_required":
                resolve_approval(payload["approval_id"], "local", approved=True)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch("agent.tools._pinned_http_stream", fake_stream),
                patch(
                    "agent.tools._resolve_public_addresses",
                    return_value={"93.184.216.34"},
                ),
            ):
                result = download_file(
                    root,
                    "https://example.com/a.bin",
                    "downloads/a.bin",
                    user_id="local",
                    task_id="task1",
                    on_event=on_event,
                    timeout_sec=5,
                )
            self.assertTrue(result.ok, result.output)
            self.assertTrue((root / "downloads" / "a.bin").is_file())
            self.assertEqual((root / "downloads" / "a.bin").read_bytes(), b"data")


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

    def test_missing_path_does_not_raise_keyerror(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = dispatch_tool(
                Path(temp),
                "local",
                "project",
                "write_file",
                {"content": "package demo"},
            )
            self.assertFalse(result.ok)
            self.assertIn("path", str(result.output).lower())

            result2 = dispatch_tool(
                Path(temp),
                "local",
                "project",
                "read_file",
                {},
            )
            self.assertFalse(result2.ok)
            self.assertIn("path", str(result2.output).lower())

    def test_keyerror_path_is_not_provider_outage(self) -> None:
        self.assertFalse(should_try_next_provider(KeyError("path")))
        self.assertTrue(should_try_next_provider(ConnectionError("connection reset")))

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
        compacted, changed = compact_openai_messages(messages, max_chars=2000, keep_recent=2)
        self.assertTrue(changed)
        self.assertLess(estimate_message_chars(compacted), before)

    def test_compact_skips_when_under_budget(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        compacted, changed = compact_openai_messages(messages, max_chars=2_500_000)
        self.assertFalse(changed)
        self.assertEqual(compacted, messages)

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

    def test_delta_flusher_coalesces(self) -> None:
        events: list[tuple[str, dict]] = []
        flusher = _DeltaFlusher(lambda t, p: events.append((t, p)), min_chars=5, max_interval=10.0)
        flusher.push("ab")
        self.assertEqual(events, [])
        flusher.push("cdef")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "text_delta")
        self.assertEqual(events[0][1]["content"], "abcdef")


if __name__ == "__main__":
    unittest.main()
