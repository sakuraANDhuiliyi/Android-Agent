from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import jobs as jobs_mod
from agent.api import create_app
from agent.changes import diff_stats
from agent.config import Settings
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.tools import parse_gradle_summary
from agent.turn_trace import build_turn_trace
from agent.users import UserStore


def settings(provider: str = "openai", **overrides):
    values = {
        "provider": provider,
        "api_key": "fake-key",
        "base_url": None,
        "model": "fake-model",
        "model_candidates": ["fake-model"],
        "provider_fallbacks": [],
        "max_turns": 4,
        "max_auto_continuations": 0,
        "max_gradle_retries": 2,
        "compact_max_chars": 1_000_000,
        "max_output_tokens": 4096,
        "auto_build_after_edit": False,
        "tavily_api_key": "",
        "server_host": "127.0.0.1",
        "server_port": 8000,
        "api_token": "",
        "users": [],
    }
    values.update(overrides)
    return Settings(**values)


GRADLE_FAIL_LOG = """> Task :app:compileDebugKotlin FAILED
e: file:///app/src/main/java/Main.kt:12:5 unresolved reference: foo

FAILURE: Build failed with an exception.
* What went wrong:
Execution failed for task ':app:compileDebugKotlin'.
> Compilation error; see the compiler error output for details.

BUILD FAILED in 3s
"""

GRADLE_TEST_LOG = """> Task :app:testDebugUnitTest
12 tests completed, 2 failed

BUILD SUCCESSFUL in 9s
"""


class GradleSummaryUnitTests(unittest.TestCase):
    def test_build_failure_summary(self) -> None:
        summary = parse_gradle_summary(
            "assembleDebug",
            GRADLE_FAIL_LOG,
            success=False,
            exit_code=1,
            duration_ms=3120,
            build_id="b1",
            log_path=Path("/tmp/build.log"),
        )
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["kind"], "build")
        self.assertFalse(summary["success"])
        self.assertEqual(summary["exit_code"], 1)
        self.assertEqual(summary["duration_ms"], 3120)
        self.assertGreaterEqual(summary["error_count"], 2)
        self.assertTrue(
            any("unresolved reference" in err for err in summary["errors"]),
        )
        self.assertNotIn("tests", summary)

    def test_build_success_summary_with_apk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "app-debug.apk"
            apk.write_bytes(b"x" * 2048)
            summary = parse_gradle_summary(
                "assembleDebug",
                "BUILD SUCCESSFUL in 2s",
                success=True,
                exit_code=0,
                duration_ms=2100,
                build_id="b2",
                log_path=Path("/tmp/build.log"),
                apk_path=apk,
            )
        self.assertTrue(summary["success"])
        self.assertEqual(summary["error_count"], 0)
        self.assertEqual(summary["apk_size_bytes"], 2048)
        self.assertIn("apk_path", summary)

    def test_test_summary_extracts_counts(self) -> None:
        summary = parse_gradle_summary(
            "testDebugUnitTest",
            GRADLE_TEST_LOG,
            success=False,
            exit_code=1,
            duration_ms=9300,
            build_id="b3",
            log_path=Path("/tmp/test.log"),
        )
        self.assertEqual(summary["kind"], "test")
        self.assertEqual(summary["tests"]["passed"], 10)
        self.assertEqual(summary["tests"]["failed"], 2)

    def test_diff_stats(self) -> None:
        diff = "--- a/F.kt\n+++ b/F.kt\n@@ -1,3 +1,4 @@\n-old\n kept\n+new\n+new2\n"
        self.assertEqual(diff_stats(diff), {"additions": 2, "deletions": 1})
        self.assertEqual(diff_stats(""), {"additions": 0, "deletions": 0})


class _JobHarness:
    """Runs start_ask_job with a fake agent that replays canned events."""

    def __init__(self, fake_agent) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = TaskStore(self.root / "tasks.db")
        self.conversation = self.store.create_conversation("user", "project")
        self.locks: set = set()
        builds = self.root / "builds"
        builds.mkdir(exist_ok=True)
        self.patches = (
            patch.object(jobs_mod, "_store", self.store),
            patch.object(jobs_mod, "_project_locks", self.locks),
            patch("agent.jobs.load_project_meta", return_value={}),
            patch("agent.jobs.workspace_path", return_value=self.root),
            patch("agent.jobs.user_builds_dir", return_value=builds),
            patch("agent.jobs.snapshot_workspace", return_value={}),
            patch("agent.jobs.compare_snapshots", return_value=([], "")),
            patch("agent.jobs.run_agent", side_effect=fake_agent),
        )

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
        self.temp_dir.cleanup()

    def run(self, prompt: str) -> dict:
        job = jobs_mod.start_ask_job(
            "user",
            "project",
            prompt,
            settings(),
            conversation_id=self.conversation["id"],
        )
        deadline = time.time() + 5.0
        task = {}
        while time.time() < deadline:
            task = self.store.get_task(job["id"], "user") or {}
            if task.get("status") in {"succeeded", "failed", "canceled"} and (
                "user", "project"
            ) not in self.locks:
                break
            time.sleep(0.01)
        return task


def _summary_payload(kind: str = "build", success: bool = True) -> dict:
    payload: dict = {
        "schema_version": 1,
        "kind": kind,
        "task": "testDebugUnitTest" if kind == "test" else "assembleDebug",
        "success": success,
        "exit_code": 0 if success else 1,
        "duration_ms": 4500,
        "build_id": "b9",
        "log_path": "/tmp/x.log",
        "error_count": 0 if success else 1,
        "errors": [] if success else ["e: boom"],
    }
    if kind == "test":
        payload["tests"] = {"passed": 10, "failed": 2, "skipped": None}
    return payload


class TurnTraceIntegrationTests(unittest.TestCase):
    def test_trace_id_and_summary_events_flow(self) -> None:
        def fake_agent(*_args, **kwargs):
            on_event = kwargs["on_event"]
            on_event(
                "tool_call",
                {
                    "message_id": "m1",
                    "tool_call_id": "call-gradle",
                    "block_index": 0,
                    "name": "run_gradle",
                    "input": {"task": "testDebugUnitTest"},
                },
            )
            on_event(
                "tool_result",
                {
                    "tool_call_id": "call-gradle",
                    "name": "run_gradle",
                    "ok": False,
                    "model_output": "gradle output",
                    "duration_ms": 4500,
                    "error_type": "ToolExecutionError",
                    "interrupted": False,
                    "input": {"task": "testDebugUnitTest"},
                    "summary": _summary_payload("test", success=False),
                },
            )
            on_event(
                "assistant_message",
                {
                    "message_id": "m-final",
                    "text_blocks": [
                        {"block_index": 0, "type": "text", "text": "完成"}
                    ],
                    "finish_reason": "stop",
                    "is_final": True,
                    "streamed": False,
                    "provider": "openai",
                    "model": "fake-model",
                },
            )
            return "完成"

        with _JobHarness(fake_agent) as harness:
            task = harness.run("跑一次测试")
            self.assertEqual(task["status"], "succeeded")
            event_store = ConversationEventStore(harness.store)
            turn = event_store.get_turn_by_task(task["id"], user_id="user")
            self.assertIsNotNone(turn)
            self.assertTrue(turn["trace_id"])

            events = event_store.list_turn_events(turn["id"], user_id="user")
            types = [item["event_type"] for item in events]
            self.assertIn("test_summary", types)
            self.assertIn("changes", types)
            self.assertEqual(types[-1], "turn_completed")

            # trace_id stamped on every canonical event of this turn
            for item in events:
                if item["event_type"] != "user_message":
                    self.assertEqual(
                        item["payload"].get("trace_id"),
                        turn["trace_id"],
                        item["event_type"],
                    )

            test_summary = next(
                item
                for item in events
                if item["event_type"] == "test_summary"
            )
            self.assertEqual(
                test_summary["payload"]["tool_call_id"], "call-gradle"
            )
            self.assertEqual(test_summary["payload"]["tests"]["failed"], 2)

            changes = next(
                item for item in events if item["event_type"] == "changes"
            )
            self.assertIn("additions", changes["payload"])
            self.assertIn("deletions", changes["payload"])

            # task_events carry the summary too (job events API surface)
            task_row = harness.store.get_task(task["id"], "user") or {}
            task_events = task_row.get("events") or []
            self.assertIn("test_summary", {e["type"] for e in task_events})

            trace = build_turn_trace(
                event_store,
                user_id="user",
                conversation_id=turn["conversation_id"],
                turn_id=turn["id"],
            )
            self.assertIsNotNone(trace)
            self.assertEqual(trace["trace_id"], turn["trace_id"])
            self.assertEqual(trace["status"], "succeeded")
            self.assertIsNotNone(trace["queue_ms"])
            self.assertIsNotNone(trace["total_ms"])
            labels = [step["label"] for step in trace["steps"]]
            self.assertIn("用户消息", labels)
            self.assertIn("工具调用", labels)
            self.assertIn("测试摘要", labels)
            self.assertIn("Turn 完成", labels)
            seqs = [step["seq"] for step in trace["steps"]]
            self.assertEqual(seqs, sorted(seqs))
            # durations are non-negative and monotonic timestamps
            for step in trace["steps"]:
                self.assertGreaterEqual(step["duration_ms"], 0)


class TurnTraceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.task_store = TaskStore(root / "tasks.db")
        self.user_store = UserStore(root / "users.db")
        self.alice, self.alice_token = self.user_store.register()
        self.bob, self.bob_token = self.user_store.register()
        self.conversation = self.task_store.create_conversation(
            self.alice, "project", title="trace"
        )
        self.event_store = ConversationEventStore(self.task_store)
        self.turn = self.event_store.create_turn(
            self.conversation["id"],
            self.alice,
            "project",
            task_id="task-1",
            status="succeeded",
            created_at=10.0,
            started_at=10.5,
            finished_at=12.0,
        )
        self.event_store.append_event(
            self.conversation["id"],
            self.turn["id"],
            "user_message",
            {"content": [{"type": "text", "text": "hi"}]},
            task_id="task-1",
            role="user",
            context_visible=True,
            created_at=10.0,
        )
        self.event_store.append_event(
            self.conversation["id"],
            self.turn["id"],
            "build_summary",
            {
                "schema_version": 1,
                "kind": "build",
                "task": "assembleDebug",
                "success": True,
                "exit_code": 0,
                "duration_ms": 900,
                "build_id": "b1",
                "log_path": "/tmp/b.log",
                "error_count": 0,
                "errors": [],
            },
            task_id="task-1",
            created_at=11.0,
        )
        self.client = TestClient(
            create_app(
                settings=settings(),
                user_store=self.user_store,
                task_store=self.task_store,
            )
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _headers(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.alice_token}"}

    def test_turn_trace_endpoint(self) -> None:
        r = self.client.get(
            f"/api/conversations/{self.conversation['id']}/turns/{self.turn['id']}/trace",
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(body["turn_id"], self.turn["id"])
        self.assertEqual(body["job_id"], "task-1")
        self.assertTrue(body["trace_id"])
        self.assertEqual(body["queue_ms"], 500)
        self.assertEqual(body["total_ms"], 2000)
        self.assertEqual(len(body["steps"]), 2)
        self.assertEqual(body["steps"][0]["label"], "用户消息")
        self.assertEqual(body["steps"][1]["label"], "构建摘要")
        self.assertIn("成功", body["steps"][1]["detail"])
        self.assertEqual(body["steps"][1]["duration_ms"], 1000)

    def test_turn_trace_access_control(self) -> None:
        other = self.client.get(
            f"/api/conversations/{self.conversation['id']}/turns/{self.turn['id']}/trace",
            headers=self._headers(self.bob_token),
        )
        self.assertEqual(other.status_code, 404)

        missing = self.client.get(
            f"/api/conversations/{self.conversation['id']}/turns/does-not-exist/trace",
            headers=self._headers(),
        )
        self.assertEqual(missing.status_code, 404)

    def test_conversation_turns_endpoint(self) -> None:
        r = self.client.get(
            f"/api/conversations/{self.conversation['id']}/turns",
            headers=self._headers(),
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["conversation_id"], self.conversation["id"])
        self.assertEqual(body["schema_version"], 1)
        turns = body["turns"]
        self.assertEqual(len(turns), 1)
        turn = turns[0]
        self.assertEqual(turn["id"], self.turn["id"])
        self.assertEqual(turn["task_id"], "task-1")
        self.assertEqual(turn["status"], "succeeded")
        self.assertTrue(turn["trace_id"])
        self.assertEqual(turn["user_preview"], "hi")
        self.assertEqual(turn["event_counts"].get("user_message"), 1)
        self.assertEqual(turn["event_counts"].get("build_summary"), 1)

    def test_conversation_turns_access_control(self) -> None:
        other = self.client.get(
            f"/api/conversations/{self.conversation['id']}/turns",
            headers=self._headers(self.bob_token),
        )
        self.assertEqual(other.status_code, 404)

        missing = self.client.get(
            "/api/conversations/does-not-exist/turns",
            headers=self._headers(),
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
