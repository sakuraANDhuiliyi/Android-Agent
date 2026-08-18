"""Stage 6: checkpoint validation, memory conflicts, and quality metrics."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.conversation_context import (
    build_anthropic_messages,
    build_openai_messages,
    select_context_events,
)
from agent.conversation_events import ConversationEventStore
from agent.conversation_summary import (
    apply_model_structured_state,
    create_semantic_checkpoint,
    parse_model_structured_state,
    repair_invalid_checkpoints,
)
from agent.database import TaskStore
from agent.memory_extract import DeterministicMemoryExtractor, generate_candidates_for_turn
from agent.memory_store import MemoryStore, reset_memory_store
from evals.live import run_live_eval
from evals.quality import (
    anthropic_tool_ids,
    constraint_recall,
    hallucination_rate,
    openai_tool_ids,
    openai_tool_pairing_valid,
    token_savings,
    tool_chain_complete,
    unresolved_retention,
)


class ContextQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.store = TaskStore(self.root / "agent.db")
        self.events = ConversationEventStore(self.store)
        self.conv = self.store.create_conversation("user", "project")
        reset_memory_store()

    def tearDown(self) -> None:
        reset_memory_store()
        self._temp.cleanup()

    def _turn(self, task_id: str) -> dict:
        return self.events.create_turn(
            self.conv["id"],
            "user",
            "project",
            task_id=task_id,
        )

    def _user_assistant(self, index: int, user: str, assistant: str) -> None:
        turn = self._turn(f"t{index}")
        self.events.append_event(
            self.conv["id"],
            turn["id"],
            "user_message",
            {"content": user},
            context_visible=True,
        )
        self.events.append_event(
            self.conv["id"],
            turn["id"],
            "assistant_message",
            {
                "text_blocks": [{"type": "text", "text": assistant}],
                "is_final": True,
            },
            context_visible=True,
        )

    def test_invalid_checkpoint_appends_invalidated_and_keeps_raw_history(self) -> None:
        for index in range(6):
            self._user_assistant(index, f"必须保留约束 {index}", f"决定 {index}")
        rows = self.events.list_events(self.conv["id"], user_id="user")
        last = rows[-1]
        bad = self.events.append_event(
            self.conv["id"],
            last["turn_id"],
            "context_checkpoint",
            {
                "summary": "bad",
                "state": {
                    "goal": [{"text": "invented", "source_seq": 99999}],
                },
                "covers_through_seq": 4,
                "valid": True,
                "checkpoint_version": 2,
            },
            context_visible=True,
        )
        original_state = dict(bad["payload"]["state"])
        repaired = repair_invalid_checkpoints(
            self.events,
            self.conv["id"],
            "user",
        )
        self.assertEqual(repaired, [bad["id"]])
        after = self.events.list_events(self.conv["id"], user_id="user")
        stored = next(event for event in after if event["id"] == bad["id"])
        self.assertEqual(stored["payload"]["state"], original_state)
        self.assertTrue(
            any(
                event.get("event_type") == "context_checkpoint_invalidated"
                for event in after
            )
        )
        selected = select_context_events(after)
        blob = json.dumps(selected, ensure_ascii=False, default=str)
        self.assertIn("必须保留约束 0", blob)
        self.assertNotIn("invented", blob)

    def test_create_checkpoint_rejects_invalid_state_without_overwrite(self) -> None:
        for index in range(6):
            self._user_assistant(index, f"必须 {index}", f"ok {index}")
        with patch(
            "agent.conversation_summary._validate_structured_state",
            return_value=["bad source_seq"],
        ):
            result = create_semantic_checkpoint(
                self.events,
                self.conv["id"],
                "user",
                keep_recent_turns=2,
                force=True,
            )
        self.assertIsNone(result)
        rows = self.events.list_events(self.conv["id"], user_id="user")
        self.assertFalse(
            any(event.get("event_type") == "context_checkpoint" for event in rows)
        )
        self.assertTrue(
            any(
                event.get("event_type") == "context_checkpoint_invalidated"
                for event in rows
            )
        )
        self.assertIn(
            "必须 0",
            str(select_context_events(rows)),
        )

    def test_model_summary_uses_same_schema_and_rejects_bad_json(self) -> None:
        for index in range(5):
            self._user_assistant(index, f"q{index}", f"a{index}")
        rows = self.events.list_events(self.conv["id"], user_id="user")
        summarized = rows[:4]
        covers = int(summarized[-1]["seq"])
        rejected = apply_model_structured_state(
            self.events,
            self.conv["id"],
            "user",
            "{not-json",
            summarized=summarized,
            covers_through_seq=covers,
            turn_ids=["t0"],
        )
        self.assertIsNone(rejected)
        parsed, errors = parse_model_structured_state(
            {
                "goal": [{"text": "q0", "source_seq": summarized[0]["seq"]}],
                "constraints": [],
                "decisions": [],
                "unresolved": [],
                "files": [],
                "tests": [],
                "tool_facts": [],
                "errors": [],
            }
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(parsed)
        accepted = apply_model_structured_state(
            self.events,
            self.conv["id"],
            "user",
            parsed,
            summarized=summarized,
            covers_through_seq=covers,
            turn_ids=["t0"],
        )
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted["payload"]["generator"], "structured-model-v1")
        self.assertTrue(accepted["payload"]["validation"]["valid"])

    def test_openai_anthropic_tool_id_parity(self) -> None:
        turn = self._turn("parity")
        self.events.append_event(
            self.conv["id"],
            turn["id"],
            "user_message",
            {"content": "read"},
            context_visible=True,
        )
        self.events.append_event(
            self.conv["id"],
            turn["id"],
            "assistant_message",
            {
                "message_id": "m1",
                "text_blocks": [{"type": "text", "text": "ok"}],
                "is_final": False,
            },
            context_visible=True,
        )
        self.events.append_event(
            self.conv["id"],
            turn["id"],
            "tool_call",
            {
                "message_id": "m1",
                "tool_call_id": "shared-id",
                "block_index": 0,
                "name": "read_file",
                "input": {"path": "a.kt"},
            },
            context_visible=True,
        )
        self.events.append_event(
            self.conv["id"],
            turn["id"],
            "tool_result",
            {
                "tool_call_id": "shared-id",
                "name": "read_file",
                "ok": True,
                "model_output": "ok",
            },
            context_visible=True,
        )
        rows = self.events.list_events(self.conv["id"], user_id="user")
        openai_msgs = build_openai_messages(rows)
        anthropic_msgs = build_anthropic_messages(rows)
        self.assertEqual(openai_tool_ids(openai_msgs), anthropic_tool_ids(anthropic_msgs))
        self.assertTrue(openai_tool_pairing_valid(openai_msgs))

    def test_memory_confidence_and_conflicts(self) -> None:
        mem = MemoryStore(self.root / "mem.db")
        first = mem.create_memory(
            user_id="user",
            project_id="project",
            scope="project",
            memory_type="decision",
            title="Use ViewBinding in layouts",
            content="决定采用 ViewBinding 作为 UI 绑定方案。",
            status="active",
            source_conversation_id="c1",
            source_event_seq=3,
            confidence=0.9,
        )
        self.assertEqual(first["confidence"], 0.9)
        self.assertEqual(first["source"]["conversation_id"], "c1")
        self.assertEqual(first["conflict_status"], "none")
        second = mem.create_memory(
            user_id="user",
            project_id="project",
            scope="project",
            memory_type="decision",
            title="Use ViewBinding in layouts",
            content="决定改用 DataBinding，不再使用 ViewBinding。",
            status="candidate",
            confidence=0.55,
        )
        self.assertEqual(second["conflict_of"], first["id"])
        self.assertEqual(second["conflict_status"], "open")
        refreshed = mem.get_memory(first["id"], "user")
        self.assertEqual(refreshed["conflict_status"], "open")

        created = generate_candidates_for_turn(
            user_id="user",
            project_id="project",
            conversation_id="c2",
            events=[
                {
                    "event_type": "assistant_message",
                    "seq": 8,
                    "payload": {
                        "text_blocks": [
                            {
                                "type": "text",
                                "text": "MEMORY: [convention] 禁止 findViewById",
                            }
                        ]
                    },
                }
            ],
            final_answer="MEMORY: [convention] 禁止 findViewById",
            store=mem,
            extractor=DeterministicMemoryExtractor(),
        )
        explicit = [item for item in created if "explicit" in (item.get("tags") or [])]
        self.assertTrue(explicit)
        self.assertGreaterEqual(explicit[0]["confidence"], 0.9)

    def test_quality_metrics_defaults_and_scores(self) -> None:
        events = [
            {
                "event_type": "tool_call",
                "seq": 1,
                "payload": {"tool_call_id": "a"},
            },
            {
                "event_type": "tool_result",
                "seq": 2,
                "payload": {"tool_call_id": "a"},
            },
        ]
        state = {
            "constraints": [{"text": "必须使用 ViewBinding", "source_seq": 1}],
            "unresolved": [{"text": "Resolve failed tool write_file", "source_seq": 2}],
        }
        self.assertEqual(tool_chain_complete(events), 1.0)
        self.assertEqual(constraint_recall(["必须使用 ViewBinding"], state["constraints"]), 1.0)
        self.assertEqual(hallucination_rate(state, {1, 2}), 0.0)
        self.assertEqual(
            unresolved_retention(
                ["Resolve failed tool write_file"],
                [item["text"] for item in state["unresolved"]],
            ),
            1.0,
        )
        self.assertGreater(token_savings(1000, 200), 0.5)

    def test_live_eval_is_gated_and_offline_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_LIVE_EVAL", None)
            result = run_live_eval(root=self.root)
        self.assertTrue(result["skipped"])
        self.assertEqual(result["calls_made"], 0)
        self.assertFalse((self.root / ".artifacts" / "live-eval" / "last.json").exists())


if __name__ == "__main__":
    unittest.main()
