from __future__ import annotations

import json
import unittest
from typing import Any

from agent.conversation_context import (
    build_anthropic_messages,
    build_openai_messages,
    build_provider_messages,
    select_context_events,
)


def event(
    seq: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    context_visible: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"event-{seq}",
        "seq": seq,
        "event_type": event_type,
        "payload": payload or {},
        "context_visible": context_visible,
        "created_at": float(seq),
    }


def user_event(seq: int, text: str) -> dict[str, Any]:
    return event(
        seq,
        "user_message",
        {
            "message_id": f"user-{seq}",
            "content": [{"type": "text", "text": text}],
        },
    )


def assistant_event(
    seq: int,
    text: str,
    *,
    message_id: str | None = None,
    is_final: bool = True,
) -> dict[str, Any]:
    return event(
        seq,
        "assistant_message",
        {
            "message_id": message_id or f"assistant-{seq}",
            "text_blocks": [
                {"block_index": 0, "type": "text", "text": text}
            ],
            "is_final": is_final,
        },
    )


def tool_call_event(
    seq: int,
    *,
    message_id: str,
    tool_call_id: str,
    block_index: int,
    name: str = "read_file",
    tool_input: Any = None,
) -> dict[str, Any]:
    return event(
        seq,
        "tool_call",
        {
            "message_id": message_id,
            "tool_call_id": tool_call_id,
            "block_index": block_index,
            "name": name,
            "input": {} if tool_input is None else tool_input,
        },
    )


def tool_result_event(
    seq: int,
    tool_call_id: str,
    output: str,
    *,
    ok: bool = True,
) -> dict[str, Any]:
    return event(
        seq,
        "tool_result",
        {
            "tool_call_id": tool_call_id,
            "name": "read_file",
            "ok": ok,
            "model_output": output,
            "error_type": None if ok else "ToolExecutionError",
        },
    )


class OpenAIContextTests(unittest.TestCase):
    def test_plain_multiturn_messages(self) -> None:
        events = [
            user_event(1, "第一问"),
            assistant_event(2, "第一答"),
            user_event(3, "第二问"),
            assistant_event(4, "第二答"),
        ]

        self.assertEqual(
            build_openai_messages(events),
            [
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "第一答"},
                {"role": "user", "content": "第二问"},
                {"role": "assistant", "content": "第二答"},
            ],
        )

    def test_single_tool_call_and_result(self) -> None:
        events = [
            user_event(1, "读取文件"),
            assistant_event(2, "我来读取。", message_id="message-1"),
            tool_call_event(
                3,
                message_id="message-1",
                tool_call_id="call-1",
                block_index=1,
                tool_input={"path": "app/Main.kt"},
            ),
            tool_result_event(4, "call-1", "文件内容"),
            assistant_event(5, "读取完成"),
        ]

        messages = build_openai_messages(events)

        self.assertEqual(messages[1]["content"], "我来读取。")
        self.assertEqual(messages[1]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(messages[2]["tool_call_id"], "call-1")
        self.assertEqual(messages[2]["content"], "文件内容")

    def test_multiple_tool_calls_sort_by_block_index(self) -> None:
        events = [
            assistant_event(1, "", message_id="message-1"),
            tool_call_event(
                2,
                message_id="message-1",
                tool_call_id="call-b",
                block_index=2,
                name="glob_files",
                tool_input={"pattern": "*.kt"},
            ),
            tool_call_event(
                3,
                message_id="message-1",
                tool_call_id="call-a",
                block_index=1,
                name="read_file",
                tool_input={"path": "a.kt"},
            ),
            tool_result_event(4, "call-b", "b"),
            tool_result_event(5, "call-a", "a"),
        ]

        messages = build_openai_messages(events)
        calls = messages[0]["tool_calls"]

        self.assertEqual([call["id"] for call in calls], ["call-a", "call-b"])
        self.assertEqual(
            [json.loads(call["function"]["arguments"]) for call in calls],
            [{"path": "a.kt"}, {"pattern": "*.kt"}],
        )

    def test_failed_tool_result_remains_in_context(self) -> None:
        events = [
            assistant_event(1, "", message_id="message-1"),
            tool_call_event(
                2,
                message_id="message-1",
                tool_call_id="call-1",
                block_index=0,
            ),
            tool_result_event(3, "call-1", "permission denied", ok=False),
        ]

        messages = build_openai_messages(events)

        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["content"], "permission denied")

    def test_arguments_are_always_valid_json(self) -> None:
        events = [
            assistant_event(1, "", message_id="message-1"),
            tool_call_event(
                2,
                message_id="message-1",
                tool_call_id="call-1",
                block_index=0,
                tool_input='{"path":"来自 OpenAI.kt"}',
            ),
            tool_result_event(3, "call-1", "ok"),
        ]

        arguments = build_openai_messages(events)[0]["tool_calls"][0][
            "function"
        ]["arguments"]

        self.assertEqual(json.loads(arguments), {"path": "来自 OpenAI.kt"})

    def test_interleaved_tool_result_immediately_follows_assistant(self) -> None:
        events = [
            user_event(1, "第一问"),
            assistant_event(2, "我查一下", message_id="message-1"),
            tool_call_event(
                3,
                message_id="message-1",
                tool_call_id="call-1",
                block_index=1,
            ),
            user_event(4, "怎么还没好"),
            tool_result_event(5, "call-1", "迟到结果"),
            assistant_event(6, "最终回答"),
        ]

        messages = build_openai_messages(events)

        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "tool", "user", "assistant"],
        )
        self.assertEqual(messages[2]["tool_call_id"], "call-1")
        self.assertEqual(messages[2]["content"], "迟到结果")
        self.assertEqual(messages[4]["content"], "最终回答")
        self.assert_no_dangling_tool_calls(messages)

    def test_interleaved_results_follow_call_order_without_duplicates(self) -> None:
        events = [
            assistant_event(1, "", message_id="message-1"),
            tool_call_event(
                2,
                message_id="message-1",
                tool_call_id="call-a",
                block_index=1,
            ),
            tool_call_event(
                3,
                message_id="message-1",
                tool_call_id="call-b",
                block_index=2,
                name="glob_files",
            ),
            user_event(4, "催一下"),
            tool_result_event(5, "call-b", "结果B"),
            tool_result_event(6, "call-a", "结果A"),
            tool_result_event(7, "call-a", "重复结果A"),
        ]

        messages = build_openai_messages(events)

        self.assertEqual(
            [
                (message["tool_call_id"], message["content"])
                for message in messages
                if message["role"] == "tool"
            ],
            [("call-a", "结果A"), ("call-b", "结果B")],
        )
        self.assert_no_dangling_tool_calls(messages)

    def assert_no_dangling_tool_calls(
        self,
        messages: list[dict[str, Any]],
    ) -> None:
        for index, message in enumerate(messages):
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                continue
            followed: set[str] = set()
            cursor = index + 1
            while (
                cursor < len(messages)
                and messages[cursor].get("role") == "tool"
            ):
                followed.add(messages[cursor].get("tool_call_id"))
                cursor += 1
            expected = {call["id"] for call in tool_calls}
            self.assertEqual(
                expected - followed,
                set(),
                f"assistant message at {index} has dangling tool_calls",
            )


class AnthropicContextTests(unittest.TestCase):
    def test_plain_multiturn_messages(self) -> None:
        events = [
            user_event(1, "第一问"),
            assistant_event(2, "第一答"),
            user_event(3, "第二问"),
            assistant_event(4, "第二答"),
        ]

        messages = build_anthropic_messages(events)

        self.assertEqual(messages[0], {"role": "user", "content": "第一问"})
        self.assertEqual(
            messages[1],
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "第一答"}],
            },
        )
        self.assertEqual(messages[2], {"role": "user", "content": "第二问"})

    def test_tool_use_and_tool_result_ids_match(self) -> None:
        events = [
            assistant_event(1, "读取", message_id="message-1"),
            tool_call_event(
                2,
                message_id="message-1",
                tool_call_id="tool-use-1",
                block_index=1,
                tool_input={"path": "a.kt"},
            ),
            tool_result_event(3, "tool-use-1", "content"),
        ]

        messages = build_anthropic_messages(events)
        tool_use = messages[0]["content"][1]
        tool_result = messages[1]["content"][0]

        self.assertEqual(tool_use["type"], "tool_use")
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(tool_use["id"], tool_result["tool_use_id"])

    def test_multiple_tools_and_results_keep_stable_order(self) -> None:
        events = [
            assistant_event(1, "", message_id="message-1"),
            tool_call_event(
                2,
                message_id="message-1",
                tool_call_id="call-b",
                block_index=2,
                name="glob_files",
            ),
            tool_call_event(
                3,
                message_id="message-1",
                tool_call_id="call-a",
                block_index=1,
            ),
            tool_result_event(4, "call-b", "result-b"),
            tool_result_event(5, "call-a", "result-a"),
        ]

        messages = build_anthropic_messages(events)
        tool_uses = messages[0]["content"]
        tool_results = messages[1]["content"]

        self.assertEqual(
            [block["id"] for block in tool_uses],
            ["call-a", "call-b"],
        )
        self.assertEqual(
            [block["tool_use_id"] for block in tool_results],
            ["call-a", "call-b"],
        )

    def test_interleaved_tool_result_immediately_follows_assistant(self) -> None:
        events = [
            user_event(1, "第一问"),
            assistant_event(2, "我查一下", message_id="message-1"),
            tool_call_event(
                3,
                message_id="message-1",
                tool_call_id="call-1",
                block_index=1,
            ),
            user_event(4, "怎么还没好"),
            tool_result_event(5, "call-1", "迟到结果"),
            assistant_event(6, "最终回答"),
        ]

        messages = build_anthropic_messages(events)

        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "user", "assistant"],
        )
        self.assertEqual(messages[2]["content"][0]["type"], "tool_result")
        self.assertEqual(messages[2]["content"][0]["tool_use_id"], "call-1")
        self.assertEqual(messages[2]["content"][1], {"type": "text", "text": "怎么还没好"})
        self.assertEqual(messages[3]["content"][0], {"type": "text", "text": "最终回答"})

    def test_failed_result_sets_is_error(self) -> None:
        events = [
            assistant_event(1, "", message_id="message-1"),
            tool_call_event(
                2,
                message_id="message-1",
                tool_call_id="call-1",
                block_index=0,
            ),
            tool_result_event(3, "call-1", "failed", ok=False),
        ]

        result = build_anthropic_messages(events)[1]["content"][0]

        self.assertTrue(result["is_error"])


class ContextBoundaryAndCompatibilityTests(unittest.TestCase):
    def test_non_context_events_are_excluded(self) -> None:
        events = [
            event(1, "text_delta", {"content": "delta"}),
            event(2, "usage", {"input_tokens": 1}),
            event(3, "turn_started"),
            event(4, "changes", {"files": ["a.kt"]}),
            event(5, "provider_switch"),
            event(6, "approval_required"),
            event(7, "ui_preview", {"content": "preview"}),
            event(
                8,
                "system_note",
                {"message": "hidden"},
                context_visible=False,
            ),
            event(
                9,
                "recovery_note",
                {"message": "visible"},
                context_visible=True,
            ),
        ]

        selected = select_context_events(events)
        messages = build_openai_messages(events)

        self.assertEqual([item["event_type"] for item in selected], ["recovery_note"])
        self.assertEqual(messages, [{"role": "system", "content": "visible"}])

    def test_eight_turns_keep_the_first_turn(self) -> None:
        events: list[dict[str, Any]] = []
        seq = 1
        for turn in range(1, 9):
            events.append(user_event(seq, f"问题 {turn}"))
            seq += 1
            events.append(assistant_event(seq, f"回答 {turn}"))
            seq += 1

        openai_messages = build_openai_messages(events)
        anthropic_messages = build_anthropic_messages(events)

        self.assertEqual(openai_messages[0]["content"], "问题 1")
        self.assertEqual(openai_messages[-1]["content"], "回答 8")
        self.assertEqual(anthropic_messages[0]["content"], "问题 1")
        self.assertEqual(len(openai_messages), 16)

    def test_anthropic_shaped_history_converts_to_openai(self) -> None:
        events = [
            assistant_event(1, "", message_id="anthropic-message"),
            tool_call_event(
                2,
                message_id="anthropic-message",
                tool_call_id="anthropic-tool",
                block_index=0,
                tool_input={"path": "a.kt"},
            ),
            event(
                3,
                "tool_result",
                {
                    "tool_use_id": "anthropic-tool",
                    "content": "anthropic result",
                    "ok": True,
                },
            ),
        ]

        messages = build_provider_messages(events, "openai")

        self.assertEqual(messages[0]["tool_calls"][0]["id"], "anthropic-tool")
        self.assertEqual(messages[1]["tool_call_id"], "anthropic-tool")

    def test_openai_shaped_history_converts_to_anthropic(self) -> None:
        events = [
            assistant_event(1, "", message_id="openai-message"),
            event(
                2,
                "tool_call",
                {
                    "message_id": "openai-message",
                    "id": "openai-call",
                    "block_index": 0,
                    "name": "read_file",
                    "arguments": '{"path":"a.kt"}',
                },
            ),
            tool_result_event(3, "openai-call", "openai result"),
        ]

        messages = build_provider_messages(events, "anthropic")

        self.assertEqual(messages[0]["content"][0]["id"], "openai-call")
        self.assertEqual(
            messages[0]["content"][0]["input"],
            {"path": "a.kt"},
        )
        self.assertEqual(
            messages[1]["content"][0]["tool_use_id"],
            "openai-call",
        )

    def test_last_valid_checkpoint_is_history_start(self) -> None:
        events = [
            user_event(1, "已被覆盖"),
            assistant_event(2, "旧回答"),
            event(3, "context_checkpoint", {"summary": "此前摘要"}),
            user_event(4, "摘要之后"),
            event(
                5,
                "context_checkpoint",
                {"summary": "无效摘要", "valid": False},
            ),
            assistant_event(6, "新回答"),
        ]

        messages = build_openai_messages(events)

        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "此前摘要"},
                {"role": "user", "content": "摘要之后"},
                {"role": "assistant", "content": "新回答"},
            ],
        )

    def test_current_user_prompt_is_not_duplicated(self) -> None:
        events = [user_event(1, "当前问题")]

        openai_messages = build_openai_messages(
            events,
            current_user_prompt="当前问题",
        )
        anthropic_messages = build_anthropic_messages(
            events,
            current_user_prompt="当前问题",
        )

        self.assertEqual(openai_messages, [{"role": "user", "content": "当前问题"}])
        self.assertEqual(
            anthropic_messages,
            [{"role": "user", "content": "当前问题"}],
        )

    def test_orphan_tool_events_do_not_create_invalid_messages(self) -> None:
        events = [
            tool_result_event(1, "missing-call", "orphan result"),
            tool_call_event(
                2,
                message_id="message-without-result",
                tool_call_id="missing-result",
                block_index=0,
            ),
        ]

        self.assertEqual(build_openai_messages(events), [])
        self.assertEqual(build_anthropic_messages(events), [])


if __name__ == "__main__":
    unittest.main()
