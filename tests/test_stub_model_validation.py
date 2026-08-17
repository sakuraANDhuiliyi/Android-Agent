from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

STUB_PATH = (
    Path(__file__).resolve().parents[1]
    / "desktop"
    / "tests"
    / "smoke"
    / "stub_model_server.py"
)

spec = importlib.util.spec_from_file_location("stub_model_server", STUB_PATH)
stub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)


def assistant_with_calls(*ids: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
            for call_id in ids
        ],
    }


def tool_message(call_id: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": "ok"}


class DanglingToolCallValidationTests(unittest.TestCase):
    def test_valid_tool_round_trip_passes(self) -> None:
        messages = [
            {"role": "user", "content": "读取文件"},
            assistant_with_calls("call-1"),
            tool_message("call-1"),
            {"role": "assistant", "content": "完成"},
        ]

        self.assertEqual(stub.dangling_tool_call_ids(messages), [])

    def test_result_after_user_message_is_dangling(self) -> None:
        messages = [
            assistant_with_calls("call-1"),
            {"role": "user", "content": "催一下"},
            tool_message("call-1"),
        ]

        self.assertEqual(stub.dangling_tool_call_ids(messages), ["call-1"])

    def test_partially_answered_calls_report_missing_ids(self) -> None:
        messages = [
            assistant_with_calls("call-a", "call-b"),
            tool_message("call-a"),
            {"role": "user", "content": "下一问"},
        ]

        self.assertEqual(stub.dangling_tool_call_ids(messages), ["call-b"])

    def test_plain_conversation_has_no_dangling(self) -> None:
        messages = [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
        ]

        self.assertEqual(stub.dangling_tool_call_ids(messages), [])


if __name__ == "__main__":
    unittest.main()
