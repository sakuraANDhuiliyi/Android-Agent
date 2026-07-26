from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import jobs as jobs_mod
from agent.approvals import ApprovalEventPersistenceError
from agent.conversation_context import build_openai_messages
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.loop import (
    CancellationRequested,
    _run_anthropic,
    _run_openai_compatible,
)
from agent.tools import ToolResult


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
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def openai_response(
    *,
    text: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    response_id: str = "response-1",
):
    return SimpleNamespace(
        id=response_id,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=text,
                    tool_calls=tool_calls or [],
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def openai_tool_call(
    tool_call_id: str,
    name: str = "read_file",
    arguments: str = '{"path":"app/Main.kt"}',
):
    return SimpleNamespace(
        id=tool_call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def anthropic_response(
    content: list,
    *,
    stop_reason: str,
    response_id: str,
):
    return SimpleNamespace(
        id=response_id,
        content=content,
        stop_reason=stop_reason,
        usage=None,
    )


class ProviderLoopCanonicalEventTests(unittest.TestCase):
    def test_openai_records_multiple_responses_and_complete_tool_pair(self) -> None:
        responses = [
            openai_response(
                text="先读取。",
                tool_calls=[openai_tool_call("call-1")],
                finish_reason="tool_calls",
                response_id="response-tool",
            ),
            openai_response(
                text="读取完成。",
                response_id="response-final",
            ),
        ]
        emitted: list[tuple[str, dict]] = []
        fallback = Mock(side_effect=[(responses[0], "fake-model"), (responses[1], "fake-model")])

        with (
            patch.dict(
                sys.modules,
                {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())},
            ),
            patch("agent.loop._chat_completion_with_fallback", fallback),
            patch("agent.loop._openai_tools", return_value=[]),
            patch(
                "agent.loop.dispatch_tool",
                return_value=ToolResult(True, {"content": "文件内容"}),
            ),
        ):
            answer = _run_openai_compatible(
                settings(),
                Path("."),
                "user",
                "project",
                "读取文件内容",
                "system",
                lambda kind, payload: emitted.append((kind, payload)),
                None,
                [{"role": "user", "content": "读取文件内容"}],
                append_user_prompt=False,
                task_id="task-1",
                turn_id="turn-1",
            )

        canonical = [
            (kind, payload)
            for kind, payload in emitted
            if kind in {"assistant_message", "tool_call", "tool_result"}
        ]
        self.assertEqual(
            [kind for kind, _payload in canonical],
            [
                "assistant_message",
                "tool_call",
                "tool_result",
                "assistant_message",
            ],
        )
        self.assertEqual(canonical[0][1]["message_id"], canonical[1][1]["message_id"])
        self.assertEqual(canonical[1][1]["tool_call_id"], "call-1")
        self.assertEqual(canonical[2][1]["tool_call_id"], "call-1")
        self.assertEqual(canonical[2][1]["model_output"], '{"content": "文件内容"}')
        self.assertTrue(canonical[-1][1]["is_final"])
        self.assertIn("读取完成", answer)
        second_request = fallback.call_args_list[1].kwargs["messages"]
        self.assertTrue(any(item.get("role") == "tool" for item in second_request))

    def test_openai_failed_tool_is_recorded_and_returned_to_model(self) -> None:
        responses = [
            openai_response(
                tool_calls=[openai_tool_call("call-failed")],
                finish_reason="tool_calls",
            ),
            openai_response(text="已看到失败。", response_id="response-2"),
        ]
        emitted: list[tuple[str, dict]] = []
        fallback = Mock(side_effect=[(responses[0], "fake-model"), (responses[1], "fake-model")])

        with (
            patch.dict(
                sys.modules,
                {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())},
            ),
            patch("agent.loop._chat_completion_with_fallback", fallback),
            patch("agent.loop._openai_tools", return_value=[]),
            patch(
                "agent.loop.dispatch_tool",
                return_value=ToolResult(False, "permission denied"),
            ),
        ):
            _run_openai_compatible(
                settings(),
                Path("."),
                "user",
                "project",
                "读取",
                "system",
                lambda kind, payload: emitted.append((kind, payload)),
                None,
                [],
                task_id="task-1",
                turn_id="turn-1",
            )

        result = next(payload for kind, payload in emitted if kind == "tool_result")
        self.assertFalse(result["ok"])
        self.assertEqual(result["model_output"], "permission denied")
        second_request = fallback.call_args_list[1].kwargs["messages"]
        self.assertEqual(second_request[-1]["content"], "permission denied")

    def test_tool_call_write_failure_prevents_execution(self) -> None:
        response = openai_response(
            tool_calls=[openai_tool_call("call-1")],
            finish_reason="tool_calls",
        )
        dispatch = Mock(return_value=ToolResult(True, "should not run"))

        def fail_on_tool_call(kind: str, _payload: dict) -> None:
            if kind == "tool_call":
                raise ValueError("canonical tool_call write failed")

        with (
            patch.dict(
                sys.modules,
                {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())},
            ),
            patch(
                "agent.loop._chat_completion_with_fallback",
                return_value=(response, "fake-model"),
            ),
            patch("agent.loop._openai_tools", return_value=[]),
            patch("agent.loop.dispatch_tool", dispatch),
        ):
            with self.assertRaisesRegex(ValueError, "canonical"):
                _run_openai_compatible(
                    settings(),
                    Path("."),
                    "user",
                    "project",
                    "读取",
                    "system",
                    fail_on_tool_call,
                    None,
                    [],
                    task_id="task-1",
                    turn_id="turn-1",
                )

        dispatch.assert_not_called()

    def test_tool_result_write_failure_stops_before_next_model_response(self) -> None:
        response = openai_response(
            tool_calls=[openai_tool_call("call-1")],
            finish_reason="tool_calls",
        )
        fallback = Mock(return_value=(response, "fake-model"))

        def fail_on_result(kind: str, _payload: dict) -> None:
            if kind == "tool_result":
                raise ValueError("canonical tool_result write failed")

        with (
            patch.dict(
                sys.modules,
                {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())},
            ),
            patch("agent.loop._chat_completion_with_fallback", fallback),
            patch("agent.loop._openai_tools", return_value=[]),
            patch(
                "agent.loop.dispatch_tool",
                return_value=ToolResult(True, "result"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "canonical"):
                _run_openai_compatible(
                    settings(),
                    Path("."),
                    "user",
                    "project",
                    "读取",
                    "system",
                    fail_on_result,
                    None,
                    [],
                    task_id="task-1",
                    turn_id="turn-1",
                )

        self.assertEqual(fallback.call_count, 1)

    def test_approval_persistence_failure_is_not_downgraded_to_tool_failure(
        self,
    ) -> None:
        response = openai_response(
            tool_calls=[
                openai_tool_call(
                    "call-write",
                    name="write_file",
                    arguments='{"path":"app/Main.kt","content":"x"}',
                )
            ],
            finish_reason="tool_calls",
        )
        with (
            patch.dict(
                sys.modules,
                {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())},
            ),
            patch(
                "agent.loop._chat_completion_with_fallback",
                return_value=(response, "fake-model"),
            ),
            patch("agent.loop._openai_tools", return_value=[]),
            patch(
                "agent.loop.dispatch_agent_tool",
                side_effect=ApprovalEventPersistenceError("event write failed"),
            ),
        ):
            with self.assertRaises(ApprovalEventPersistenceError):
                _run_openai_compatible(
                    settings(),
                    Path("."),
                    "user",
                    "project",
                    "写文件",
                    "system",
                    lambda _kind, _payload: None,
                    None,
                    [],
                    task_id="task-1",
                    turn_id="turn-1",
                    recovery_mode=True,
                )

    def test_anthropic_records_tool_use_and_matching_result(self) -> None:
        responses = [
            anthropic_response(
                [
                    SimpleNamespace(type="text", text="读取"),
                    SimpleNamespace(
                        type="tool_use",
                        id="anthropic-call",
                        name="read_file",
                        input={"path": "app/Main.kt"},
                    ),
                ],
                stop_reason="tool_use",
                response_id="anthropic-1",
            ),
            anthropic_response(
                [SimpleNamespace(type="text", text="完成")],
                stop_reason="end_turn",
                response_id="anthropic-2",
            ),
        ]
        emitted: list[tuple[str, dict]] = []

        with (
            patch.dict(
                sys.modules,
                {"anthropic": SimpleNamespace(Anthropic=lambda **_kwargs: object())},
            ),
            patch(
                "agent.loop._anthropic_message_with_fallback",
                side_effect=[
                    (responses[0], "fake-model"),
                    (responses[1], "fake-model"),
                ],
            ),
            patch("agent.loop.get_tool_definitions", return_value=[]),
            patch(
                "agent.loop.dispatch_tool",
                return_value=ToolResult(True, "anthropic result"),
            ),
        ):
            _run_anthropic(
                settings("anthropic"),
                Path("."),
                "user",
                "project",
                "读取",
                "system",
                lambda kind, payload: emitted.append((kind, payload)),
                None,
                [{"role": "user", "content": "读取"}],
                append_user_prompt=False,
                task_id="task-1",
                turn_id="turn-1",
            )

        tool_call = next(payload for kind, payload in emitted if kind == "tool_call")
        tool_result = next(payload for kind, payload in emitted if kind == "tool_result")
        assistants = [payload for kind, payload in emitted if kind == "assistant_message"]
        self.assertEqual(tool_call["tool_call_id"], "anthropic-call")
        self.assertEqual(tool_result["tool_call_id"], "anthropic-call")
        self.assertEqual(len(assistants), 2)
        self.assertTrue(assistants[-1]["is_final"])


class JobCanonicalIntegrationTests(unittest.TestCase):
    def _patch_job_environment(
        self,
        store: TaskStore,
        root: Path,
        locks: set,
        fake_agent,
    ):
        builds = root / "builds"
        builds.mkdir(exist_ok=True)
        return (
            patch.object(jobs_mod, "_store", store),
            patch.object(jobs_mod, "_project_locks", locks),
            patch("agent.jobs.load_project_meta", return_value={}),
            patch("agent.jobs.workspace_path", return_value=root),
            patch("agent.jobs.user_builds_dir", return_value=builds),
            patch("agent.jobs.snapshot_workspace", return_value={}),
            patch("agent.jobs.compare_snapshots", return_value=([], "")),
            patch("agent.jobs.run_agent", side_effect=fake_agent),
        )

    @staticmethod
    def _wait(
        store: TaskStore,
        task_id: str,
        locks: set,
        timeout: float = 3.0,
    ) -> dict:
        deadline = time.time() + timeout
        task = {}
        while time.time() < deadline:
            task = store.get_task(task_id, "user") or {}
            if task.get("status") in {"succeeded", "failed", "canceled"} and (
                "user",
                "project",
            ) not in locks:
                break
            time.sleep(0.01)
        return task

    def test_user_message_exists_before_thread_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = TaskStore(root / "tasks.db")
            store.create_conversation("user", "project", conversation_id="conv")
            locks: set = set()

            class DeferredThread:
                def __init__(self, *args, **kwargs):
                    self.args = args
                    self.kwargs = kwargs

                def start(self):
                    return None

            patches = self._patch_job_environment(
                store,
                root,
                locks,
                lambda *_args, **_kwargs: "unused",
            )
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
                patches[6],
                patches[7],
                patch("agent.jobs.threading.Thread", DeferredThread),
            ):
                job = jobs_mod.start_ask_job(
                    "user",
                    "project",
                    "线程前消息",
                    settings(),
                    conversation_id="conv",
                )

            event_store = ConversationEventStore(store)
            turn = event_store.get_turn_by_task(job["id"], user_id="user")
            events = event_store.list_turn_events(turn["id"], user_id="user")
            self.assertEqual([item["event_type"] for item in events], ["user_message"])
            self.assertEqual(
                events[0]["payload"]["content"][0]["text"],
                "线程前消息",
            )

    def test_complete_chain_task_events_and_success_status(self) -> None:
        def fake_agent(*_args, **kwargs):
            on_event = kwargs["on_event"]
            on_event(
                "usage",
                {
                    "provider": "openai",
                    "model": "actual-model",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )
            on_event(
                "assistant_message",
                {
                    "message_id": "message-tool",
                    "text_blocks": [],
                    "finish_reason": "tool_calls",
                    "is_final": False,
                    "streamed": True,
                    "provider": "openai",
                    "model": "actual-model",
                    "response_id": "response-tool",
                },
            )
            on_event(
                "tool_call",
                {
                    "message_id": "message-tool",
                    "tool_call_id": "call-1",
                    "block_index": 0,
                    "name": "read_file",
                    "input": {"path": "app/Main.kt"},
                },
            )
            on_event(
                "tool_result",
                {
                    "tool_call_id": "call-1",
                    "name": "read_file",
                    "ok": True,
                    "model_output": "full file content",
                    "structured_output": None,
                    "duration_ms": 3,
                    "error_type": None,
                    "interrupted": False,
                },
            )
            on_event(
                "assistant_message",
                {
                    "message_id": "message-final",
                    "text_blocks": [
                        {"block_index": 0, "type": "text", "text": "完成"}
                    ],
                    "finish_reason": "stop",
                    "is_final": True,
                    "streamed": False,
                    "provider": "openai",
                    "model": "actual-model",
                    "response_id": "response-final",
                },
            )
            return "完成"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = TaskStore(root / "tasks.db")
            conversation = store.create_conversation("user", "project")
            locks: set = set()
            patches = self._patch_job_environment(store, root, locks, fake_agent)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                job = jobs_mod.start_ask_job(
                    "user",
                    "project",
                    "读取后回答",
                    settings(),
                    conversation_id=conversation["id"],
                )
                task = self._wait(store, job["id"], locks)

            event_store = ConversationEventStore(store)
            turn = event_store.get_turn_by_task(job["id"], user_id="user")
            canonical = event_store.list_turn_events(turn["id"], user_id="user")
            canonical_types = [item["event_type"] for item in canonical]
            self.assertEqual(task["status"], "succeeded")
            self.assertEqual(turn["status"], "succeeded")
            self.assertIn("turn_started", canonical_types)
            self.assertIn("usage", canonical_types)
            self.assertIn("tool_call", canonical_types)
            self.assertIn("tool_result", canonical_types)
            self.assertIn("changes", canonical_types)
            self.assertEqual(canonical_types[-1], "turn_completed")
            task_event_types = [item["type"] for item in task["events"]]
            self.assertIn("tool_call", task_event_types)
            self.assertIn("tool_result", task_event_types)
            task_tool_result = next(
                item for item in task["events"] if item["type"] == "tool_result"
            )
            self.assertNotIn("model_output", task_tool_result)

    def test_second_turn_restores_first_turn_tool_chain_without_prompt_duplication(
        self,
    ) -> None:
        captured_second_messages: list[dict] = []
        calls = {"count": 0}

        def fake_agent(*args, **kwargs):
            calls["count"] += 1
            on_event = kwargs["on_event"]
            if calls["count"] == 1:
                on_event(
                    "assistant_message",
                    {
                        "message_id": "first-tool-message",
                        "text_blocks": [],
                        "finish_reason": "tool_calls",
                        "is_final": False,
                        "streamed": False,
                        "provider": "openai",
                        "model": "fake-model",
                        "response_id": "r1",
                    },
                )
                on_event(
                    "tool_call",
                    {
                        "message_id": "first-tool-message",
                        "tool_call_id": "first-call",
                        "block_index": 0,
                        "name": "read_file",
                        "input": {"path": "app/Main.kt"},
                    },
                )
                on_event(
                    "tool_result",
                    {
                        "tool_call_id": "first-call",
                        "name": "read_file",
                        "ok": True,
                        "model_output": "first result",
                        "structured_output": None,
                        "duration_ms": 1,
                        "error_type": None,
                        "interrupted": False,
                    },
                )
                on_event(
                    "assistant_message",
                    {
                        "message_id": "first-final",
                        "text_blocks": [
                            {"block_index": 0, "type": "text", "text": "第一轮完成"}
                        ],
                        "finish_reason": "stop",
                        "is_final": True,
                        "streamed": False,
                        "provider": "openai",
                        "model": "fake-model",
                        "response_id": "r2",
                    },
                )
                return "第一轮完成"

            captured_second_messages.extend(
                build_openai_messages(
                    kwargs["conversation_events"],
                    current_user_prompt=args[4],
                )
            )
            return "第二轮完成"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = TaskStore(root / "tasks.db")
            conversation = store.create_conversation("user", "project")
            locks: set = set()
            patches = self._patch_job_environment(store, root, locks, fake_agent)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                first = jobs_mod.start_ask_job(
                    "user",
                    "project",
                    "第一轮",
                    settings(),
                    conversation_id=conversation["id"],
                )
                self._wait(store, first["id"], locks)
                second = jobs_mod.start_ask_job(
                    "user",
                    "project",
                    "第二轮",
                    settings(),
                    conversation_id=conversation["id"],
                )
                second_task = self._wait(store, second["id"], locks)

            self.assertEqual(second_task["status"], "succeeded")
            self.assertTrue(
                any(message.get("tool_calls") for message in captured_second_messages)
            )
            self.assertTrue(
                any(
                    message.get("role") == "tool"
                    and message.get("content") == "first result"
                    for message in captured_second_messages
                )
            )
            second_prompts = [
                message
                for message in captured_second_messages
                if message.get("role") == "user"
                and message.get("content") == "第二轮"
            ]
            self.assertEqual(len(second_prompts), 1)

    def test_sqlite_reopen_restores_complete_tool_chain_into_second_model_request(
        self,
    ) -> None:
        first_responses = [
            openai_response(
                text="先读取文件。",
                tool_calls=[
                    openai_tool_call(
                        "call-reopen",
                        arguments='{"path":"app/Main.kt"}',
                    )
                ],
                finish_reason="tool_calls",
                response_id="response-reopen-tool",
            ),
            openai_response(
                text="第一轮最终回答",
                response_id="response-reopen-final",
            ),
        ]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            builds = root / "builds"
            builds.mkdir()
            db_path = root / "tasks.db"
            first_store = TaskStore(db_path)
            conversation = first_store.create_conversation("user", "project")
            first_locks: set = set()

            with (
                patch.object(jobs_mod, "_store", first_store),
                patch.object(jobs_mod, "_project_locks", first_locks),
                patch("agent.jobs.load_project_meta", return_value={}),
                patch("agent.jobs.workspace_path", return_value=root),
                patch("agent.jobs.user_builds_dir", return_value=builds),
                patch("agent.jobs.snapshot_workspace", return_value={}),
                patch("agent.jobs.compare_snapshots", return_value=([], "")),
                patch.dict(
                    sys.modules,
                    {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())},
                ),
                patch(
                    "agent.loop._chat_completion_with_fallback",
                    side_effect=[
                        (first_responses[0], "fake-model"),
                        (first_responses[1], "fake-model"),
                    ],
                ),
                patch("agent.loop._openai_tools", return_value=[]),
                patch(
                    "agent.loop.dispatch_tool",
                    return_value=ToolResult(True, "reopened file content"),
                ),
            ):
                first_job = jobs_mod.start_ask_job(
                    "user",
                    "project",
                    "第一轮读取",
                    settings(),
                    conversation_id=conversation["id"],
                )
                first_task = self._wait(
                    first_store,
                    first_job["id"],
                    first_locks,
                )

            self.assertEqual(first_task["status"], "succeeded")
            first_turn = ConversationEventStore(first_store).get_turn_by_task(
                first_job["id"],
                user_id="user",
            )
            first_events = ConversationEventStore(first_store).list_turn_events(
                first_turn["id"],
                user_id="user",
            )
            core_types = [
                event["event_type"]
                for event in first_events
                if event["event_type"]
                in {
                    "user_message",
                    "assistant_message",
                    "tool_call",
                    "tool_result",
                }
            ]
            self.assertEqual(
                core_types,
                [
                    "user_message",
                    "assistant_message",
                    "tool_call",
                    "tool_result",
                    "assistant_message",
                ],
            )

            reopened_store = TaskStore(db_path)
            reopened_events = ConversationEventStore(reopened_store).list_events(
                conversation["id"],
                user_id="user",
            )
            self.assertTrue(
                any(
                    event["event_type"] == "tool_result"
                    and event["payload"]["model_output"]
                    == "reopened file content"
                    for event in reopened_events
                )
            )

            second_response = openai_response(
                text="第二轮最终回答",
                response_id="response-second-final",
            )
            second_completion = Mock(
                return_value=(second_response, "fake-model")
            )
            second_locks: set = set()
            with (
                patch.object(jobs_mod, "_store", reopened_store),
                patch.object(jobs_mod, "_project_locks", second_locks),
                patch("agent.jobs.load_project_meta", return_value={}),
                patch("agent.jobs.workspace_path", return_value=root),
                patch("agent.jobs.user_builds_dir", return_value=builds),
                patch("agent.jobs.snapshot_workspace", return_value={}),
                patch("agent.jobs.compare_snapshots", return_value=([], "")),
                patch.dict(
                    sys.modules,
                    {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())},
                ),
                patch(
                    "agent.loop._chat_completion_with_fallback",
                    second_completion,
                ),
                patch("agent.loop._openai_tools", return_value=[]),
                patch("agent.loop.dispatch_tool") as second_dispatch,
            ):
                second_job = jobs_mod.start_ask_job(
                    "user",
                    "project",
                    "第二轮继续",
                    settings(),
                    conversation_id=conversation["id"],
                )
                second_task = self._wait(
                    reopened_store,
                    second_job["id"],
                    second_locks,
                )

            self.assertEqual(second_task["status"], "succeeded")
            second_dispatch.assert_not_called()
            model_messages = second_completion.call_args.kwargs["messages"]
            self.assertTrue(
                any(
                    message.get("role") == "user"
                    and message.get("content") == "第一轮读取"
                    for message in model_messages
                )
            )
            self.assertTrue(
                any(
                    message.get("role") == "assistant"
                    and message.get("tool_calls", [])[0]["id"] == "call-reopen"
                    for message in model_messages
                    if message.get("tool_calls")
                )
            )
            self.assertTrue(
                any(
                    message.get("role") == "tool"
                    and message.get("tool_call_id") == "call-reopen"
                    and message.get("content") == "reopened file content"
                    for message in model_messages
                )
            )
            self.assertTrue(
                any(
                    message.get("role") == "assistant"
                    and message.get("content") == "第一轮最终回答"
                    for message in model_messages
                )
            )
            self.assertEqual(
                sum(
                    1
                    for message in model_messages
                    if message.get("role") == "user"
                    and message.get("content") == "第二轮继续"
                ),
                1,
            )

    def test_provider_model_switch_and_terminal_statuses(self) -> None:
        def switched_agent(*_args, **kwargs):
            on_event = kwargs["on_event"]
            on_event(
                "provider_switch",
                {
                    "from_provider": "openai",
                    "to_provider": "anthropic",
                    "model": "claude-fake",
                },
            )
            on_event(
                "model_switch",
                {"from_model": "old", "to_model": "claude-fake"},
            )
            on_event(
                "assistant_message",
                {
                    "message_id": "switched-final",
                    "text_blocks": [
                        {"block_index": 0, "type": "text", "text": "切换完成"}
                    ],
                    "finish_reason": "end_turn",
                    "is_final": True,
                    "streamed": False,
                    "provider": "anthropic",
                    "model": "claude-fake",
                    "response_id": "anthropic-response",
                },
            )
            return "切换完成"

        scenarios = [
            ("succeeded", switched_agent),
            ("failed", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fake failure"))),
            ("canceled", lambda *_args, **_kwargs: (_ for _ in ()).throw(CancellationRequested("fake cancel"))),
        ]
        for expected_status, fake_agent in scenarios:
            with self.subTest(expected_status=expected_status):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    store = TaskStore(root / "tasks.db")
                    conversation = store.create_conversation("user", "project")
                    locks: set = set()
                    patches = self._patch_job_environment(
                        store, root, locks, fake_agent
                    )
                    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                        job = jobs_mod.start_ask_job(
                            "user",
                            "project",
                            expected_status,
                            settings(),
                            conversation_id=conversation["id"],
                        )
                        task = self._wait(store, job["id"], locks)

                    turn = ConversationEventStore(store).get_turn_by_task(
                        job["id"], user_id="user"
                    )
                    self.assertEqual(task["status"], expected_status)
                    self.assertEqual(turn["status"], expected_status)
                    if expected_status == "succeeded":
                        events = ConversationEventStore(store).list_turn_events(
                            turn["id"], user_id="user"
                        )
                        assistant = next(
                            item
                            for item in events
                            if item["event_type"] == "assistant_message"
                        )
                        self.assertEqual(assistant["provider"], "anthropic")
                        self.assertEqual(assistant["model"], "claude-fake")
                        self.assertTrue(any(
                            item["event_type"] == "provider_switch"
                            for item in events
                        ))
                        self.assertTrue(any(
                            item["event_type"] == "model_switch"
                            for item in events
                        ))

    def test_canonical_write_failure_fails_task_and_deltas_stay_ui_only(self) -> None:
        original = ConversationEventStore.append_event_idempotent

        def flaky_append(self, conversation_id, turn_id, event_type, event_key, payload=None, **kwargs):
            if event_type == "tool_call":
                raise ValueError("forced canonical failure")
            return original(
                self,
                conversation_id,
                turn_id,
                event_type,
                event_key,
                payload,
                **kwargs,
            )

        def fake_agent(*_args, **kwargs):
            on_event = kwargs["on_event"]
            for index in range(30):
                on_event("text_delta", {"content": str(index)})
            on_event(
                "tool_call",
                {
                    "message_id": "message",
                    "tool_call_id": "call",
                    "block_index": 0,
                    "name": "read_file",
                    "input": {"path": "a.kt"},
                },
            )
            return "unreachable"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = TaskStore(root / "tasks.db")
            conversation = store.create_conversation("user", "project")
            locks: set = set()
            patches = self._patch_job_environment(store, root, locks, fake_agent)
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
                patches[6],
                patches[7],
                patch.object(
                    ConversationEventStore,
                    "append_event_idempotent",
                    new=flaky_append,
                ),
            ):
                job = jobs_mod.start_ask_job(
                    "user",
                    "project",
                    "触发失败",
                    settings(),
                    conversation_id=conversation["id"],
                )
                task = self._wait(store, job["id"], locks)

            event_store = ConversationEventStore(store)
            turn = event_store.get_turn_by_task(job["id"], user_id="user")
            canonical = event_store.list_turn_events(turn["id"], user_id="user")
            self.assertEqual(task["status"], "failed")
            self.assertEqual(turn["status"], "failed")
            self.assertFalse(any(
                item["event_type"] == "text_delta" for item in canonical
            ))
            self.assertEqual(
                len([item for item in task["events"] if item["type"] == "text_delta"]),
                30,
            )


if __name__ == "__main__":
    unittest.main()
