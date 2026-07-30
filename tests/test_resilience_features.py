from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import jobs as jobs_mod
from agent.approvals import resolve_approval
from agent.conversation_context import build_openai_messages
from agent.conversation_events import ConversationEventStore
from agent.conversation_summary import create_semantic_checkpoint
from agent.database import TaskStore
from agent.loop import dispatch_agent_tool
from agent.redaction import REDACTED, redact_sensitive_text
from agent.tools import ToolResult
from agent.worker import TaskWorker


def recovery_settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider="openai",
        model="fake-model",
        auto_build_after_edit=False,
    )


class SemanticCheckpointTests(unittest.TestCase):
    def test_checkpoint_replaces_early_raw_history_and_keeps_recent_turns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "events.db")
            conversation = store.create_conversation("user", "project")
            for index in range(1, 7):
                store.append_conversation_turn(
                    conversation["id"],
                    user=f"旧问题 {index}",
                    assistant=f"旧回答 {index}",
                    auto_title=False,
                )
            events = ConversationEventStore(store)

            checkpoint = create_semantic_checkpoint(
                events,
                conversation["id"],
                "user",
                keep_recent_turns=2,
                force=True,
            )
            duplicate = create_semantic_checkpoint(
                events,
                conversation["id"],
                "user",
                keep_recent_turns=2,
                force=True,
            )

            self.assertIsNotNone(checkpoint)
            self.assertEqual(checkpoint["id"], duplicate["id"])
            self.assertEqual(
                checkpoint["payload"]["generator"],
                "extractive-semantic-v1",
            )
            messages = build_openai_messages(
                events.list_events(conversation["id"], user_id="user")
            )
            serialized = str(messages)
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("旧问题 1", messages[0]["content"])
            self.assertNotIn(
                {"role": "user", "content": "旧问题 1"},
                messages,
            )
            self.assertIn(
                {"role": "user", "content": "旧问题 5"},
                messages,
            )
            self.assertIn(
                {"role": "assistant", "content": "旧回答 6"},
                messages,
            )
            self.assertIn("旧回答 4", serialized)


class AutomaticRecoveryTests(unittest.TestCase):
    def test_startup_creates_new_recovery_task_and_continues_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            builds = root / "builds"
            builds.mkdir()
            store = TaskStore(root / "tasks.db")
            conversation = store.create_conversation("user", "project")
            store.create_task(
                {
                    "id": "interrupted-task",
                    "user_id": "user",
                    "project_id": "project",
                    "conversation_id": conversation["id"],
                    "prompt": "写入文件",
                    "status": "running",
                    "provider": "openai",
                    "model": "fake-model",
                    "created_at": time.time(),
                }
            )
            events = ConversationEventStore(store)
            turn = events.create_turn(
                conversation["id"],
                "user",
                "project",
                task_id="interrupted-task",
                status="running",
            )
            events.append_event(
                conversation["id"],
                turn["id"],
                "assistant_message",
                {"message_id": "m1", "text_blocks": []},
                task_id="interrupted-task",
                context_visible=True,
            )
            events.append_event(
                conversation["id"],
                turn["id"],
                "tool_call",
                {
                    "message_id": "m1",
                    "tool_call_id": "old-write",
                    "block_index": 0,
                    "name": "write_file",
                    "input": {
                        "path": "app/Main.kt",
                        "content": "content",
                    },
                },
                task_id="interrupted-task",
                context_visible=True,
            )
            captured: dict = {}

            def fake_agent(*_args, **kwargs):
                captured["events"] = kwargs["conversation_events"]
                captured["replays"] = kwargs["recovery_replays"]
                return "恢复完成"

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
                recovered = jobs_mod.configure_task_store(
                    store,
                    recovery_settings(),
                )
                self.assertEqual(len(recovered), 1)
                original = store.get_task("interrupted-task", "user")
                self.assertEqual(original["status"], "failed")
                recovery_tasks = [
                    task
                    for task in store.list_tasks("user", "project")
                    if task.get("recovery_of_task_id") == "interrupted-task"
                ]
                self.assertEqual(len(recovery_tasks), 1)
                worker = TaskWorker(store, jobs_mod._run_job, recovery_settings())
                worker.run_once()

            recovery_task = store.get_task(recovery_tasks[0]["id"], "user")
            self.assertIsNotNone(recovery_task)
            self.assertEqual(recovery_task["status"], "succeeded")
            self.assertEqual(recovery_task["recovery_attempt"], 1)
            self.assertEqual(
                captured["replays"],
                [
                    {
                        "tool_call_id": "old-write",
                        "name": "write_file",
                        "input": {
                            "path": "app/Main.kt",
                            "content": "content",
                        },
                    }
                ],
            )
            self.assertTrue(
                any(
                    event["event_type"] == "recovery_note"
                    for event in captured["events"]
                )
            )

    def test_repeated_side_effect_needs_approval_but_read_only_does_not(self) -> None:
        replay = [
            {
                "tool_call_id": "old-write",
                "name": "write_file",
                "input": {"path": "app/Main.kt", "content": "x"},
            }
        ]
        dispatched = Mock(return_value=ToolResult(True, "written"))
        emitted: list[tuple[str, dict]] = []

        def reject_event(event_type: str, payload: dict) -> None:
            emitted.append((event_type, payload))
            if event_type == "approval_required":
                resolve_approval(
                    payload["approval_id"],
                    "user",
                    approved=False,
                )

        with patch("agent.loop.dispatch_tool", dispatched):
            rejected = dispatch_agent_tool(
                Path("."),
                "user",
                "project",
                "write_file",
                {"path": "app/Main.kt", "content": "changed-after-restart"},
                task_id="recovery-task",
                tool_call_id="new-write",
                on_event=reject_event,
                recovery_replays=replay,
                recovery_mode=True,
            )
        self.assertFalse(rejected.ok)
        dispatched.assert_not_called()
        self.assertEqual(emitted[0][1]["kind"], "recovery_tool_replay")

        def approve_event(event_type: str, payload: dict) -> None:
            if event_type == "approval_required":
                resolve_approval(
                    payload["approval_id"],
                    "user",
                    approved=True,
                )

        with patch("agent.loop.dispatch_tool", dispatched):
            approved = dispatch_agent_tool(
                Path("."),
                "user",
                "project",
                "write_file",
                {"path": "app/Main.kt", "content": "x"},
                task_id="recovery-task",
                tool_call_id="new-write-2",
                on_event=approve_event,
                recovery_replays=replay,
                recovery_mode=True,
            )
        self.assertTrue(approved.ok)
        dispatched.assert_called_once()
        self.assertEqual(replay, [])

        read_dispatch = Mock(return_value=ToolResult(True, "content"))
        with patch("agent.loop.dispatch_tool", read_dispatch):
            read = dispatch_agent_tool(
                Path("."),
                "user",
                "project",
                "read_file",
                {"path": "app/Main.kt"},
                task_id="recovery-task",
                tool_call_id="new-read",
                recovery_replays=[
                    {
                        "tool_call_id": "old-read",
                        "name": "read_file",
                        "input": {"path": "app/Main.kt"},
                    }
                ],
                recovery_mode=True,
            )
        self.assertTrue(read.ok)
        read_dispatch.assert_called_once()


class FreeTextRedactionTests(unittest.TestCase):
    def test_common_secret_shapes_are_redacted(self) -> None:
        raw = (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
            "api_key=sk-abcdefghijklmnopqrstuvwxyz "
            "jwt=eyJabcdefghi.abcdefghijkl.abcdefghijkl "
            "url=https://alice:password123@example.com/file "
            "tavily tvly-abcdefghijklmnopqrstuvwxyz"
        )

        redacted = redact_sensitive_text(raw)

        self.assertGreaterEqual(redacted.count(REDACTED), 4)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("password123", redacted)

    def test_event_task_and_legacy_reads_redact_free_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "redaction.db")
            conversation = store.create_conversation("user", "project")
            events = ConversationEventStore(store)
            turn = events.create_turn(
                conversation["id"],
                "user",
                "project",
            )
            event = events.append_event(
                conversation["id"],
                turn["id"],
                "system_note",
                {
                    "message": (
                        "Bearer abcdefghijklmnopqrstuvwxyz "
                        "secret=supersecretvalue"
                    )
                },
            )
            store.create_task(
                {
                    "id": "secret-task",
                    "user_id": "user",
                    "project_id": "project",
                    "conversation_id": conversation["id"],
                    "prompt": "api_key=sk-abcdefghijklmnopqrstuvwxyz",
                    "status": "queued",
                    "provider": "fake",
                    "model": "fake",
                    "created_at": time.time(),
                }
            )
            store.add_event(
                "secret-task",
                "log",
                {"message": "Bearer abcdefghijklmnopqrstuvwxyz"},
            )

            self.assertNotIn(
                "abcdefghijklmnopqrstuvwxyz",
                event["payload"]["message"],
            )
            task = store.get_task("secret-task", "user")
            self.assertIn(REDACTED, task["prompt"])
            self.assertIn(REDACTED, task["events"][0]["message"])


if __name__ == "__main__":
    unittest.main()
