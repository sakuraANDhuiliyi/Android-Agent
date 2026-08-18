from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from agent.conversation_events import (
    ConversationEventStore,
    ConversationNotFoundError,
    CorruptEventPayloadError,
    InvalidTurnStatusError,
    PayloadSerializationError,
    PayloadValidationError,
)
from agent.database import TaskStore


class ConversationEventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "events.db"
        self.task_store = TaskStore(self.db_path)
        self.events = ConversationEventStore(self.task_store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _conversation(
        self,
        user_id: str = "alice",
        project_id: str = "project-a",
    ) -> dict:
        return self.task_store.create_conversation(user_id, project_id)

    def _turn(
        self,
        conversation: dict,
        *,
        task_id: str | None = None,
        status: str = "queued",
    ) -> dict:
        return self.events.create_turn(
            conversation["id"],
            conversation["user_id"],
            conversation["project_id"],
            task_id=task_id,
            status=status,
            provider="fake",
            model="fake-model",
        )

    def test_create_turn_lookup_and_status_update(self) -> None:
        conversation = self._conversation()
        turn = self._turn(conversation, task_id="task-1")

        self.assertEqual(turn["status"], "queued")
        self.assertEqual(
            self.events.get_turn_by_task("task-1", user_id="alice")["id"],
            turn["id"],
        )
        updated = self.events.update_turn_status(
            turn["id"],
            "running",
            user_id="alice",
            started_at=10.0,
        )
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["started_at"], 10.0)
        with self.assertRaises(InvalidTurnStatusError):
            self.events.update_turn_status(turn["id"], "unknown")

    def test_event_sequence_is_strictly_increasing(self) -> None:
        conversation = self._conversation()
        first_turn = self._turn(conversation, task_id="task-1")
        second_turn = self._turn(conversation, task_id="task-2")

        first = self.events.append_event(
            conversation["id"],
            first_turn["id"],
            "user_message",
            {"content": "hello"},
            context_visible=True,
        )
        second = self.events.append_event(
            conversation["id"],
            first_turn["id"],
            "assistant_message",
            {"content": "hi"},
        )
        third = self.events.append_event(
            conversation["id"],
            second_turn["id"],
            "user_message",
            {"content": "again"},
        )

        self.assertEqual([first["seq"], second["seq"], third["seq"]], [1, 2, 3])
        self.assertIs(first["context_visible"], True)
        self.assertEqual(first["payload"], {"content": "hello"})
        self.assertEqual(
            [event["seq"] for event in self.events.list_turn_events(first_turn["id"])],
            [1, 2],
        )

    def test_event_key_is_idempotent(self) -> None:
        conversation = self._conversation()
        turn = self._turn(conversation)

        first = self.events.append_event_idempotent(
            conversation["id"],
            turn["id"],
            "turn_started",
            "turn:started",
            {"attempt": 1},
        )
        duplicate = self.events.append_event_idempotent(
            conversation["id"],
            turn["id"],
            "turn_started",
            "turn:started",
            {"attempt": 2},
        )

        self.assertEqual(duplicate["id"], first["id"])
        self.assertEqual(duplicate["payload"], {"attempt": 1})
        self.assertEqual(len(self.events.list_events(conversation["id"])), 1)

    def test_events_survive_database_reopen(self) -> None:
        conversation = self._conversation()
        turn = self._turn(conversation)
        event = self.events.append_event(
            conversation["id"],
            turn["id"],
            "system_note",
            {"message": "durable"},
        )

        reopened = ConversationEventStore(self.db_path)
        loaded = reopened.list_events(conversation["id"], user_id="alice")

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], event["id"])
        self.assertEqual(loaded[0]["payload"]["message"], "durable")

    def test_conversation_user_and_project_isolation(self) -> None:
        alice = self._conversation("alice", "project-a")
        bob = self._conversation("bob", "project-b")
        alice_turn = self._turn(alice, task_id="alice-task")
        bob_turn = self._turn(bob, task_id="bob-task")
        self.events.append_event(
            alice["id"], alice_turn["id"], "note", {"owner": "alice"}
        )
        self.events.append_event(
            bob["id"], bob_turn["id"], "note", {"owner": "bob"}
        )

        self.assertEqual(
            [item["payload"]["owner"] for item in self.events.list_events(
                alice["id"], user_id="alice"
            )],
            ["alice"],
        )
        self.assertEqual(self.events.list_events(alice["id"], user_id="bob"), [])
        self.assertIsNone(
            self.events.get_turn_by_task("alice-task", user_id="bob")
        )
        with self.assertRaises(ConversationNotFoundError):
            self.events.create_turn(
                alice["id"],
                "bob",
                "project-a",
            )

    def test_concurrent_appends_have_unique_contiguous_sequences(self) -> None:
        conversation = self._conversation()
        turn = self._turn(conversation)
        start = threading.Barrier(3)
        failures: list[BaseException] = []

        def append_batch(worker: int) -> None:
            try:
                local_store = ConversationEventStore(self.db_path)
                start.wait()
                for index in range(20):
                    local_store.append_event(
                        conversation["id"],
                        turn["id"],
                        "worker_event",
                        {"worker": worker, "index": index},
                    )
            except BaseException as exc:
                failures.append(exc)

        threads = [
            threading.Thread(target=append_batch, args=(worker,))
            for worker in range(2)
        ]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        sequence = [
            event["seq"] for event in self.events.list_events(conversation["id"])
        ]
        self.assertEqual(sequence, list(range(1, 41)))
        self.assertEqual(len(sequence), len(set(sequence)))

    def test_foreign_keys_uniqueness_and_cascades(self) -> None:
        conversation = self._conversation()
        first_turn = self._turn(conversation, task_id="unique-task")
        self.events.append_event(
            conversation["id"], first_turn["id"], "note", {"value": 1}
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self._turn(conversation, task_id="unique-task")
        with self.task_store._connect() as conn:
            conn.execute(
                "DELETE FROM conversation_turns WHERE id=?",
                (first_turn["id"],),
            )
            event_count = conn.execute(
                "SELECT COUNT(*) FROM conversation_events"
            ).fetchone()[0]
        self.assertEqual(event_count, 0)

        second_turn = self._turn(conversation)
        self.events.append_event(
            conversation["id"], second_turn["id"], "note", {"value": 2}
        )
        with self.task_store._connect() as conn:
            conn.execute(
                "DELETE FROM conversations WHERE id=?",
                (conversation["id"],),
            )
            turn_count = conn.execute(
                "SELECT COUNT(*) FROM conversation_turns"
            ).fetchone()[0]
            event_count = conn.execute(
                "SELECT COUNT(*) FROM conversation_events"
            ).fetchone()[0]
        self.assertEqual(turn_count, 0)
        self.assertEqual(event_count, 0)

    def test_payload_errors_are_diagnostic(self) -> None:
        conversation = self._conversation()
        turn = self._turn(conversation)

        with self.assertRaisesRegex(
            PayloadSerializationError,
            "not JSON serializable",
        ):
            self.events.append_event(
                conversation["id"],
                turn["id"],
                "bad_payload",
                {"bad": object()},
            )
        with self.assertRaisesRegex(
            PayloadValidationError,
            "forbidden credential field",
        ):
            self.events.append_event(
                conversation["id"],
                turn["id"],
                "secret",
                {"Authorization": "Bearer secret"},
            )

        valid = self.events.append_event(
            conversation["id"],
            turn["id"],
            "will_corrupt",
            {"ok": True},
        )
        with self.task_store._connect() as conn:
            conn.execute(
                "UPDATE conversation_events SET payload_json=? WHERE id=?",
                ("{broken", valid["id"]),
            )
        with self.assertRaisesRegex(
            CorruptEventPayloadError,
            "invalid payload_json",
        ):
            self.events.list_events(conversation["id"])


class LegacyConversationMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "legacy.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _set_legacy_turns(self, conversation_id: str, turns: list[dict]) -> None:
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE conversations SET turns_json=? WHERE id=?",
                (json.dumps(turns, ensure_ascii=False), conversation_id),
            )

    def test_turns_json_migrates_with_content_and_timestamp(self) -> None:
        conversation = self.store.create_conversation("alice", "project-a")
        changed_files = [{"path": "app/Main.kt", "change": "modified"}]
        self._set_legacy_turns(
            conversation["id"],
            [
                {
                    "user": "原用户输入",
                    "assistant": "原回复",
                    "changed_files": changed_files,
                    "ts": 123.5,
                }
            ],
        )

        migrated = TaskStore(self.db_path)
        event_store = ConversationEventStore(migrated)
        loaded = migrated.get_conversation(conversation["id"], "alice")
        events = event_store.list_events(conversation["id"], user_id="alice")

        self.assertEqual(loaded["turn_count"], 1)
        self.assertEqual(
            loaded["turns"],
            [
                {
                    "user": "原用户输入",
                    "assistant": "原回复",
                    "changed_files": changed_files,
                    "ts": 123.5,
                }
            ],
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            [
                "user_message",
                "assistant_message",
                "changes",
                "turn_completed",
            ],
        )
        self.assertEqual(
            events[0]["payload"],
            {
                "message_id": events[0]["payload"]["message_id"],
                "content": [{"type": "text", "text": "原用户输入"}],
                "source": "legacy",
                "legacy_imported": True,
            },
        )
        self.assertEqual(
            events[1]["payload"]["text_blocks"],
            [{"block_index": 0, "type": "text", "text": "原回复"}],
        )
        self.assertTrue(events[1]["payload"]["is_final"])
        self.assertTrue(all(event["event_key"] for event in events))

    def test_migration_is_idempotent_across_reopens(self) -> None:
        conversation = self.store.create_conversation("alice", "project-a")
        self._set_legacy_turns(
            conversation["id"],
            [{"user": "u", "assistant": "a", "ts": 10.0}],
        )

        first = TaskStore(self.db_path)
        first_events = ConversationEventStore(first).list_events(
            conversation["id"]
        )
        second = TaskStore(self.db_path)
        second_events = ConversationEventStore(second).list_events(
            conversation["id"]
        )

        self.assertEqual(
            [event["id"] for event in second_events],
            [event["id"] for event in first_events],
        )
        self.assertEqual(len(second_events), 3)
        self.assertTrue(
            all(event["schema_version"] == 1 for event in first_events)
        )
        self.assertEqual(
            [event["schema_version"] for event in first_events],
            [event["schema_version"] for event in second_events],
        )
        with second._connect() as conn:
            turn_count = conn.execute(
                """SELECT COUNT(*) FROM conversation_turns
                   WHERE conversation_id=?""",
                (conversation["id"],),
            ).fetchone()[0]
        self.assertEqual(turn_count, 1)

    def test_existing_canonical_events_prevent_legacy_import(self) -> None:
        conversation = self.store.create_conversation("alice", "project-a")
        self._set_legacy_turns(
            conversation["id"],
            [{"user": "不应导入", "assistant": "旧回复", "ts": 10.0}],
        )
        event_store = ConversationEventStore(self.store)
        turn = event_store.create_turn(
            conversation["id"], "alice", "project-a", status="succeeded"
        )
        canonical = event_store.append_event(
            conversation["id"],
            turn["id"],
            "user_message",
            {"content": [{"type": "text", "text": "规范消息"}]},
        )

        reopened = TaskStore(self.db_path)
        events = ConversationEventStore(reopened).list_events(
            conversation["id"]
        )

        self.assertEqual([event["id"] for event in events], [canonical["id"]])
        self.assertFalse(any(
            event["payload"].get("legacy_imported") for event in events
        ))

    def test_projection_prefers_final_assistant_and_ignores_intermediate_events(
        self,
    ) -> None:
        conversation = self.store.create_conversation("alice", "project-a")
        event_store = ConversationEventStore(self.store)
        turn = event_store.create_turn(
            conversation["id"],
            "alice",
            "project-a",
            status="succeeded",
            created_at=50.0,
        )
        event_store.append_event(
            conversation["id"],
            turn["id"],
            "user_message",
            {"content": [{"type": "text", "text": "问题"}]},
        )
        event_store.append_event(
            conversation["id"],
            turn["id"],
            "assistant_message",
            {"text_blocks": [{"type": "text", "text": "中间回复"}]},
        )
        event_store.append_event(
            conversation["id"],
            turn["id"],
            "tool_call",
            {"name": "fake_tool"},
        )
        event_store.append_event(
            conversation["id"],
            turn["id"],
            "assistant_message",
            {
                "text_blocks": [{"type": "text", "text": "最终回复"}],
                "is_final": True,
            },
        )
        event_store.append_event(
            conversation["id"],
            turn["id"],
            "changes",
            {"files": ["app/Main.kt"]},
        )

        loaded = self.store.get_conversation(conversation["id"], "alice")

        self.assertEqual(loaded["turn_count"], 1)
        self.assertEqual(
            loaded["turns"][0],
            {
                "user": "问题",
                "assistant": "最终回复",
                "changed_files": ["app/Main.kt"],
                "ts": 50.0,
            },
        )

    def test_compatibility_append_writes_canonical_events(self) -> None:
        conversation = self.store.create_conversation("alice", "project-a")

        turns = self.store.append_conversation_turn(
            conversation["id"],
            user="兼容问题",
            assistant="兼容回复",
            changed_files=["app/Main.kt"],
        )

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user"], "兼容问题")
        events = ConversationEventStore(self.store).list_events(
            conversation["id"]
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            [
                "user_message",
                "assistant_message",
                "changes",
                "turn_completed",
            ],
        )
        with self.store._connect() as conn:
            turns_json = conn.execute(
                "SELECT turns_json FROM conversations WHERE id=?",
                (conversation["id"],),
            ).fetchone()[0]
        self.assertEqual(json.loads(turns_json), [])

    def test_malformed_conversation_does_not_block_other_migrations(self) -> None:
        broken = self.store.create_conversation("alice", "project-a")
        valid = self.store.create_conversation("alice", "project-a")
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE conversations SET turns_json=? WHERE id=?",
                ("{broken", broken["id"]),
            )
        self._set_legacy_turns(
            valid["id"],
            [{"user": "有效", "assistant": "已迁移", "ts": 20.0}],
        )

        with self.assertLogs("agent.database", level="WARNING") as logs:
            reopened = TaskStore(self.db_path)

        self.assertTrue(any(broken["id"] in message for message in logs.output))
        loaded = reopened.get_conversation(valid["id"], "alice")
        self.assertEqual(loaded["turn_count"], 1)
        self.assertEqual(loaded["turns"][0]["assistant"], "已迁移")


if __name__ == "__main__":
    unittest.main()
