from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent.config import Settings, UserAccount, validate_deployment_settings
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.governance import QuotaExceededError
from agent.stores import build_runtime_stores
from agent.stores.artifacts import LocalArtifactStore, ObjectArtifactStore
from agent.stores.migrate_pg import compare_summaries, render_postgres_sql, summarize_sqlite
from agent.stores.outbox import SqliteOutboxStore
from agent.stores.rate_limit import SqliteRateLimiter
from agent.stores.tickets import SqliteTicketBackend, WebSocketTicketStore


def _settings(**overrides) -> Settings:
    values = dict(
        provider="openai",
        api_key="fake-key",
        model="fake-model",
        model_candidates=["fake-model"],
        max_turns=3,
        max_auto_continuations=0,
        max_gradle_retries=1,
        compact_max_chars=10_000,
        max_output_tokens=1024,
        base_url=None,
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
        users=[UserAccount(id="local", token="tok")],
    )
    values.update(overrides)
    return Settings(**values)


class DeploymentConfigTests(unittest.TestCase):
    def test_sqlite_is_valid_without_redis_or_postgres(self) -> None:
        validate_deployment_settings(_settings())

    def test_postgres_without_url_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            validate_deployment_settings(_settings(deployment_mode="postgres"))

    def test_hybrid_requires_redis_and_database(self) -> None:
        with self.assertRaises(ValueError):
            validate_deployment_settings(
                _settings(deployment_mode="hybrid", database_url="postgres://db")
            )
        validate_deployment_settings(
            _settings(
                deployment_mode="hybrid",
                database_url="postgres://db/agent",
                redis_url="memory://tickets",
            )
        )

    def test_object_backend_requires_url(self) -> None:
        with self.assertRaises(ValueError):
            validate_deployment_settings(_settings(artifact_backend="object"))


class TicketAtomicityTests(unittest.TestCase):
    def test_two_consumers_only_one_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent.db"
            left = WebSocketTicketStore(backend=SqliteTicketBackend(path))
            right = WebSocketTicketStore(backend=SqliteTicketBackend(path))
            ticket, _ = left.issue("u1", "job", "j1", ttl_seconds=30)
            results: list[str | None] = [None, None]

            def consume(index: int, store: WebSocketTicketStore) -> None:
                results[index] = store.consume(ticket, "job", "j1")

            t1 = threading.Thread(target=consume, args=(0, left))
            t2 = threading.Thread(target=consume, args=(1, right))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            winners = [item for item in results if item == "u1"]
            self.assertEqual(len(winners), 1)
            self.assertIsNone(left.consume(ticket, "job", "j1"))


class LeaseFencingTests(unittest.TestCase):
    def _queued(self, store: TaskStore) -> str:
        conversation = store.get_or_create_default_conversation("user", "proj")
        task_id = "task-lease-1"
        store.create_task(
            {
                "id": task_id,
                "user_id": "user",
                "project_id": "proj",
                "conversation_id": conversation["id"],
                "prompt": "hi",
                "status": "queued",
                "created_at": time.time(),
            }
        )
        return task_id

    def test_two_workers_cannot_claim_the_same_live_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.db"
            store_a = TaskStore(path)
            store_b = TaskStore(path)
            task_id = self._queued(store_a)
            first = store_a.claim_next_task("worker-a", lease_seconds=300)
            self.assertIsNotNone(first)
            self.assertEqual(first["id"], task_id)
            second = store_b.claim_next_task("worker-b", lease_seconds=300)
            self.assertIsNone(second)
            self.assertTrue(
                store_a.claim_is_valid(task_id, "worker-a", first["claim_token"])
            )
            self.assertFalse(
                store_a.heartbeat_task(
                    task_id, "worker-b", claim_token=first["claim_token"]
                )
            )
            self.assertFalse(
                store_a.heartbeat_task(task_id, "worker-a", claim_token="stale")
            )
            self.assertTrue(
                store_a.heartbeat_task(
                    task_id, "worker-a", claim_token=first["claim_token"]
                )
            )

    def test_expired_lease_can_be_taken_over_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "tasks.db")
            task_id = self._queued(store)
            claimed = store.claim_next_task("worker-a", lease_seconds=300)
            self.assertIsNotNone(claimed)
            store.update_task(task_id, lease_expires_at=time.time() - 5)
            takeover = store.claim_next_task("worker-b", lease_seconds=300)
            self.assertIsNotNone(takeover)
            self.assertEqual(takeover["claim_owner"], "worker-b")
            self.assertFalse(
                store.heartbeat_task(
                    task_id, "worker-a", claim_token=claimed["claim_token"]
                )
            )


class OutboxAndLimiterTests(unittest.TestCase):
    def test_outbox_claim_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent.db"
            left = SqliteOutboxStore(path)
            right = SqliteOutboxStore(path)
            left.enqueue("conversation.event", {"n": 1})
            batch_a = left.claim_batch("api-1", limit=8)
            batch_b = right.claim_batch("api-2", limit=8)
            self.assertEqual(len(batch_a), 1)
            self.assertEqual(batch_b, [])
            self.assertTrue(left.mark_delivered(batch_a[0]["id"], "api-1"))

    def test_sqlite_limiter_is_shared(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent.db"
            a = SqliteRateLimiter(path)
            b = SqliteRateLimiter(path)
            a.check("k", limit=2, window_seconds=60)
            b.check("k", limit=2, window_seconds=60)
            with self.assertRaises(QuotaExceededError):
                a.check("k", limit=2, window_seconds=60)

    def test_conversation_event_enqueues_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "agent.db")
            events = ConversationEventStore(store)
            conv = store.get_or_create_default_conversation("user", "proj")
            turn = events.create_turn(conv["id"], "user", "proj")
            events.append_event(
                conv["id"],
                turn["id"],
                "user_message",
                {"content": "hello"},
            )
            outbox = SqliteOutboxStore(store.db_path)
            pending = outbox.claim_batch("n1")
            self.assertGreaterEqual(len(pending), 1)
            self.assertEqual(pending[0]["topic"], "conversation.event")


class ArtifactAndMigrateTests(unittest.TestCase):
    def test_local_and_memory_object_stores(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            local = LocalArtifactStore(Path(temp) / "art")
            local.put("u/p/app.apk", b"apk-bytes", content_type="application/vnd.android.package-archive")
            self.assertTrue(local.exists("u/p/app.apk"))
            self.assertEqual(local.get("u/p/app.apk"), b"apk-bytes")
            self.assertEqual(len(local.digest("u/p/app.apk") or ""), 64)
        mem = ObjectArtifactStore.from_url("memory://bucket", prefix="android-agent")
        mem.put("u/p/log.txt", b"log")
        self.assertEqual(mem.get("u/p/log.txt"), b"log")

    def test_sqlite_summary_roundtrip_and_sql_dump(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent.db"
            store = TaskStore(path)
            store.create_task(
                {
                    "id": "t1",
                    "user_id": "user",
                    "project_id": "p",
                    "prompt": "x",
                    "status": "queued",
                    "created_at": time.time(),
                }
            )
            first = summarize_sqlite(path)
            second = summarize_sqlite(path)
            self.assertEqual(compare_summaries(first, second), [])
            sql = render_postgres_sql(path)
            self.assertIn("BEGIN;", sql)
            self.assertIn('INSERT INTO public."tasks"', sql)

    def test_build_runtime_sqlite_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "agent.db"
            TaskStore(db)
            runtime = build_runtime_stores(
                _settings(),
                db_path=db,
                data_dir=root,
            )
            self.assertEqual(runtime.mode, "sqlite")
            ticket, _ = runtime.tickets.issue("u", "job", "j")
            self.assertEqual(runtime.tickets.consume(ticket, "job", "j"), "u")


class CreateTurnHelperTests(unittest.TestCase):
    def test_create_conversation_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "agent.db")
            conv = store.get_or_create_default_conversation("user", "proj")
            self.assertIn("id", conv)


if __name__ == "__main__":
    unittest.main()
