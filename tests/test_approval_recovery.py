from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import jobs as jobs_mod
from agent.approvals import (
    ApprovalEventPersistenceError,
    get_pending_approvals,
    request_user_approval,
    resolve_approval,
)
from agent.conversation_context import build_openai_messages
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.tools import dispatch_tool


def fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider="openai",
        model="fake-model",
        auto_build_after_edit=False,
    )


class ApprovalPersistenceTests(unittest.TestCase):
    def test_request_and_all_resolution_decisions_have_complete_linkage(self) -> None:
        for decision in ("approved", "rejected", "timeout", "canceled"):
            with self.subTest(decision=decision):
                emitted: list[tuple[str, dict]] = []

                def on_event(event_type: str, payload: dict) -> None:
                    emitted.append((event_type, payload))
                    if event_type == "approval_required":
                        resolve_approval(
                            payload["approval_id"],
                            "user",
                            approved=decision == "approved",
                            force_decision=decision,
                        )

                actual = request_user_approval(
                    job_id="task-1",
                    user_id="user",
                    kind="download_file",
                    tool_call_id="call-download",
                    payload={
                        "message": "等待用户确认",
                        "url": "https://example.test/file",
                        "path": "downloads/file",
                    },
                    on_event=on_event,
                )

                self.assertEqual(actual, decision)
                self.assertEqual(
                    [event_type for event_type, _payload in emitted],
                    ["approval_required", "approval_resolved"],
                )
                required = emitted[0][1]
                resolved = emitted[1][1]
                self.assertEqual(required["tool_call_id"], "call-download")
                self.assertEqual(required["kind"], "download_file")
                self.assertEqual(
                    required["request"]["path"],
                    "downloads/file",
                )
                self.assertEqual(resolved["tool_call_id"], "call-download")
                self.assertEqual(resolved["decision"], decision)

    def test_job_bridge_persists_approval_events_for_all_decisions(self) -> None:
        for decision in ("approved", "rejected", "timeout", "canceled"):
            with self.subTest(decision=decision):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    store = TaskStore(root / "tasks.db")
                    conversation = store.create_conversation("user", "project")
                    task_id = f"task-{decision}"
                    created_at = time.time()
                    store.create_task(
                        {
                            "id": task_id,
                            "user_id": "user",
                            "project_id": "project",
                            "conversation_id": conversation["id"],
                            "prompt": "下载文件",
                            "status": "queued",
                            "provider": "openai",
                            "model": "fake-model",
                            "created_at": created_at,
                        }
                    )
                    events = ConversationEventStore(store)
                    turn = events.create_turn(
                        conversation["id"],
                        "user",
                        "project",
                        task_id=task_id,
                    )
                    events.append_event(
                        conversation["id"],
                        turn["id"],
                        "user_message",
                        {
                            "message_id": f"user-{decision}",
                            "content": [{"type": "text", "text": "下载文件"}],
                        },
                        task_id=task_id,
                        role="user",
                        context_visible=True,
                    )

                    def fake_agent(*_args, **kwargs):
                        emit = kwargs["on_event"]
                        emit(
                            "assistant_message",
                            {
                                "message_id": f"message-{decision}",
                                "text_blocks": [],
                                "finish_reason": "tool_calls",
                                "is_final": False,
                                "streamed": False,
                                "provider": "openai",
                                "model": "fake-model",
                            },
                        )
                        emit(
                            "tool_call",
                            {
                                "message_id": f"message-{decision}",
                                "tool_call_id": f"call-{decision}",
                                "block_index": 0,
                                "name": "download_file",
                                "input": {"url": "https://example.test/file"},
                            },
                        )
                        emit(
                            "approval_required",
                            {
                                "approval_id": f"approval-{decision}",
                                "kind": "download_file",
                                "tool_call_id": f"call-{decision}",
                                "request": {
                                    "url": "https://example.test/file",
                                    "path": "downloads/file",
                                },
                                "message": "等待用户确认",
                            },
                        )
                        emit(
                            "approval_resolved",
                            {
                                "approval_id": f"approval-{decision}",
                                "kind": "download_file",
                                "tool_call_id": f"call-{decision}",
                                "decision": decision,
                                "message": "用户确认结果",
                            },
                        )
                        emit(
                            "tool_result",
                            {
                                "tool_call_id": f"call-{decision}",
                                "name": "download_file",
                                "ok": decision == "approved",
                                "model_output": f"decision={decision}",
                                "structured_output": None,
                                "duration_ms": 1,
                                "error_type": None,
                                "interrupted": False,
                            },
                        )
                        return "完成"

                    locks = {("user", "project")}
                    with (
                        patch.object(jobs_mod, "_store", store),
                        patch.object(jobs_mod, "_project_locks", locks),
                        patch("agent.jobs.workspace_path", return_value=root),
                        patch("agent.jobs.user_builds_dir", return_value=root / "builds"),
                        patch("agent.jobs.snapshot_workspace", return_value={}),
                        patch("agent.jobs.compare_snapshots", return_value=([], "")),
                        patch("agent.jobs.run_agent", side_effect=fake_agent),
                    ):
                        (root / "builds").mkdir()
                        jobs_mod._run_job(
                            task_id,
                            "user",
                            "project",
                            conversation["id"],
                            turn["id"],
                            "下载文件",
                            fake_settings(),
                            events.list_events(conversation["id"]),
                            0,
                        )

                    canonical = events.list_turn_events(turn["id"])
                    required = next(
                        item
                        for item in canonical
                        if item["event_type"] == "approval_required"
                    )
                    resolved = next(
                        item
                        for item in canonical
                        if item["event_type"] == "approval_resolved"
                    )
                    self.assertFalse(required["context_visible"])
                    self.assertFalse(resolved["context_visible"])
                    self.assertEqual(
                        required["event_key"],
                        f"approval:approval-{decision}:required",
                    )
                    self.assertEqual(resolved["payload"]["decision"], decision)
                    task_event_types = [
                        item["type"] for item in store.get_task(task_id)["events"]
                    ]
                    self.assertIn("approval_required", task_event_types)
                    self.assertIn("approval_resolved", task_event_types)

    def test_approval_events_are_hidden_but_result_enters_context(self) -> None:
        events = [
            {
                "seq": 1,
                "event_type": "assistant_message",
                "context_visible": True,
                "payload": {"message_id": "m1", "text_blocks": []},
            },
            {
                "seq": 2,
                "event_type": "tool_call",
                "context_visible": True,
                "payload": {
                    "message_id": "m1",
                    "tool_call_id": "call-1",
                    "block_index": 0,
                    "name": "download_file",
                    "input": {"url": "https://example.test/file"},
                },
            },
            {
                "seq": 3,
                "event_type": "approval_required",
                "context_visible": False,
                "payload": {"approval_id": "a1", "message": "secret UI state"},
            },
            {
                "seq": 4,
                "event_type": "approval_resolved",
                "context_visible": False,
                "payload": {"approval_id": "a1", "decision": "approved"},
            },
            {
                "seq": 5,
                "event_type": "tool_result",
                "context_visible": True,
                "payload": {
                    "tool_call_id": "call-1",
                    "name": "download_file",
                    "ok": True,
                    "model_output": "download complete",
                },
            },
        ]

        messages = build_openai_messages(events)

        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["content"], "download complete")
        self.assertNotIn("secret UI state", str(messages))

    def test_critical_approval_event_failure_propagates_and_cleans_pending(self) -> None:
        def fail_event(_event_type: str, _payload: dict) -> None:
            raise ValueError("database unavailable")

        with self.assertRaisesRegex(
            ApprovalEventPersistenceError,
            "approval_required 写入失败",
        ):
            request_user_approval(
                job_id="task-failed",
                user_id="user",
                kind="download_file",
                tool_call_id="call-failed",
                payload={"message": "等待用户确认"},
                on_event=fail_event,
            )

        self.assertEqual(
            get_pending_approvals("task-failed", "user"),
            [],
        )

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ApprovalEventPersistenceError):
                dispatch_tool(
                    Path(temp),
                    "user",
                    "project",
                    "download_file",
                    {
                        "url": "https://example.test/file",
                        "path": "downloads/file",
                    },
                    task_id="task-failed",
                    tool_call_id="call-failed",
                    on_event=fail_event,
                )


class InterruptedRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = TaskStore(self.root / "tasks.db")
        self.events = ConversationEventStore(self.store)
        self.conversation = self.store.create_conversation("user", "project")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _active_turn(self, status: str, suffix: str) -> dict:
        task_id = f"task-{suffix}"
        self.store.create_task(
            {
                "id": task_id,
                "user_id": "user",
                "project_id": "project",
                "conversation_id": self.conversation["id"],
                "prompt": "continue",
                "status": status,
                "provider": "openai",
                "model": "fake-model",
                "created_at": time.time(),
            }
        )
        return self.events.create_turn(
            self.conversation["id"],
            "user",
            "project",
            task_id=task_id,
            status=status,
        )

    def _append_call(self, turn: dict, call_id: str = "call-1") -> None:
        self.events.append_event(
            self.conversation["id"],
            turn["id"],
            "assistant_message",
            {"message_id": "message-1", "text_blocks": []},
            task_id=turn["task_id"],
            role="assistant",
            context_visible=True,
        )
        self.events.append_event(
            self.conversation["id"],
            turn["id"],
            "tool_call",
            {
                "message_id": "message-1",
                "tool_call_id": call_id,
                "block_index": 0,
                "name": "download_file",
                "input": {"url": "https://example.test/file"},
            },
            task_id=turn["task_id"],
            context_visible=True,
        )

    def test_running_turn_recovery_repairs_tool_chain_idempotently(self) -> None:
        turn = self._active_turn("running", "running")
        self._append_call(turn)
        dispatch = Mock()

        with patch("agent.tools.dispatch_tool", dispatch):
            self.store.recover_interrupted()
            self.store.recover_interrupted()

        dispatch.assert_not_called()
        recovered = self.events.get_turn(turn["id"], user_id="user")
        self.assertEqual(recovered["status"], "interrupted")
        task = self.store.get_task(turn["task_id"], "user")
        self.assertEqual(task["status"], "failed")
        events = self.events.list_turn_events(turn["id"])
        synthetic = [
            item for item in events if item["event_type"] == "tool_result"
        ]
        interrupted = [
            item for item in events if item["event_type"] == "turn_interrupted"
        ]
        self.assertEqual(len(synthetic), 1)
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(
            synthetic[0]["event_key"],
            f"recovery:{turn['id']}:tool_result:call-1",
        )
        self.assertEqual(
            interrupted[0]["event_key"],
            f"recovery:{turn['id']}:interrupted",
        )
        self.assertFalse(synthetic[0]["payload"]["ok"])
        self.assertTrue(synthetic[0]["payload"]["interrupted"])
        self.assertEqual(
            synthetic[0]["payload"]["error_type"],
            "service_interrupted",
        )
        messages = build_openai_messages(events)
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("服务中断", messages[-1]["content"])

    def test_awaiting_approval_becomes_interrupted_and_request_expires(self) -> None:
        turn = self._active_turn("awaiting_approval", "approval")
        self._append_call(turn, "call-waiting")
        self.events.append_event(
            self.conversation["id"],
            turn["id"],
            "approval_required",
            {
                "approval_id": "approval-waiting",
                "kind": "download_file",
                "tool_call_id": "call-waiting",
                "request": {},
                "message": "等待用户确认",
            },
            task_id=turn["task_id"],
        )

        self.store.recover_interrupted()

        recovered = self.events.get_turn(turn["id"], user_id="user")
        self.assertEqual(recovered["status"], "interrupted")
        self.assertIn("确认已失效", recovered["error_message"])
        result = next(
            item
            for item in self.events.list_turn_events(turn["id"])
            if item["event_type"] == "tool_result"
        )
        self.assertEqual(result["payload"]["tool_call_id"], "call-waiting")

    def test_completed_call_is_not_repaired_again(self) -> None:
        turn = self._active_turn("running", "completed")
        self._append_call(turn, "call-complete")
        self.events.append_event(
            self.conversation["id"],
            turn["id"],
            "tool_result",
            {
                "tool_call_id": "call-complete",
                "name": "download_file",
                "ok": False,
                "model_output": "already failed",
            },
            task_id=turn["task_id"],
            context_visible=True,
        )

        self.store.recover_interrupted()

        results = [
            item
            for item in self.events.list_turn_events(turn["id"])
            if item["event_type"] == "tool_result"
        ]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["payload"]["model_output"], "already failed")

    def test_orphan_result_is_diagnosed_and_excluded_from_context(self) -> None:
        turn = self._active_turn("running", "orphan")
        self.events.append_event(
            self.conversation["id"],
            turn["id"],
            "tool_result",
            {
                "tool_call_id": "missing-call",
                "name": "download_file",
                "ok": False,
                "model_output": "orphan",
            },
            task_id=turn["task_id"],
            context_visible=True,
        )

        with self.assertLogs("agent.database", level="WARNING") as logs:
            self.store.recover_interrupted()

        self.assertIn("orphan tool_result", "\n".join(logs.output))
        messages = build_openai_messages(
            self.events.list_turn_events(turn["id"])
        )
        self.assertFalse(
            any(message.get("role") == "tool" for message in messages)
        )


if __name__ == "__main__":
    unittest.main()
