"""Stage 19 fault-injection tests (offline)."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent.database import TaskStore
from agent.processes import ProcessStartError, run_command
from agent.tools import ToolResult, write_file


class SqliteBusyFaultTests(unittest.TestCase):
    def test_claim_survives_busy_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent.db"
            store = TaskStore(db)
            task_id = uuid.uuid4().hex[:12]
            store.create_task(
                {
                    "id": task_id,
                    "user_id": "u",
                    "project_id": "p",
                    "prompt": "x",
                    "status": "queued",
                    "created_at": time.time(),
                }
            )

            # Hold a write lock briefly in another connection to induce busy.
            blocker = sqlite3.connect(str(db), timeout=0.1)
            blocker.execute("BEGIN EXCLUSIVE")
            errors: list[Exception] = []

            def claim() -> None:
                try:
                    time.sleep(0.05)
                    store.claim_next_task("worker-a", lease_seconds=30)
                except Exception as exc:  # noqa: BLE001 — capture for assertion
                    errors.append(exc)

            t = threading.Thread(target=claim)
            t.start()
            time.sleep(0.15)
            blocker.rollback()
            blocker.close()
            t.join(timeout=5)
            claimed = store.get_task(task_id)
            # Either claimed after unlock or still queued; must not corrupt DB.
            self.assertIsNotNone(claimed)
            self.assertIn(claimed["status"], {"queued", "running"})
            self.assertEqual(errors, [])


class DiskWriteFaultTests(unittest.TestCase):
    def test_write_file_reports_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            rel = "app/src/main/java/com/example/app/X.kt"
            (ws / rel).parent.mkdir(parents=True)

            def boom(self, *a, **k):  # noqa: ANN001
                raise OSError("disk full")

            with patch.object(Path, "write_text", boom):
                result = write_file(ws, rel, "class X")
            self.assertFalse(result.ok)
            self.assertIn("disk full", str(result.output))


class ProcessStartFaultTests(unittest.TestCase):
    def test_process_start_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with self.assertRaises(ProcessStartError):
                run_command(
                    ["/nonexistent/binary/for/fault/injection"],
                    cwd=ws,
                    workspace=ws,
                    timeout_seconds=2,
                )


class OversizedOutputFaultTests(unittest.TestCase):
    def test_model_output_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            # Generate large stdout via python -c
            result = run_command(
                [
                    "python3",
                    "-c",
                    "print('x' * 200000)",
                ],
                cwd=ws,
                workspace=ws,
                timeout_seconds=10,
                max_model_output_chars=1000,
            )
            self.assertEqual(result.returncode, 0)
            self.assertLessEqual(len(result.stdout), 1200)
            self.assertTrue(
                result.truncated
                or len(result.stdout) <= 1000
                or len(result.stdout) < 200000
            )


class ModelTimeoutFaultTests(unittest.TestCase):
    def test_fallback_treats_timeout_as_retryable(self) -> None:
        from agent.model_fallback import should_try_next_model

        self.assertTrue(should_try_next_model(TimeoutError("model timeout")))


class ServiceRestartFaultTests(unittest.TestCase):
    def test_queued_task_survives_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent.db"
            store = TaskStore(db)
            task_id = uuid.uuid4().hex[:12]
            store.create_task(
                {
                    "id": task_id,
                    "user_id": "u",
                    "project_id": "p",
                    "prompt": "persist",
                    "status": "queued",
                    "created_at": time.time(),
                }
            )
            reopened = TaskStore(db)
            task = reopened.get_task(task_id, "u")
            self.assertIsNotNone(task)
            self.assertEqual(task["status"], "queued")
            claimed = reopened.claim_next_task("worker-restart", lease_seconds=60)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["id"], task_id)


class WebsocketDisconnectCursorTests(unittest.TestCase):
    def test_event_cursor_resume_without_dup(self) -> None:
        from agent.conversation_events import ConversationEventStore

        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "agent.db")
            events = ConversationEventStore(store)
            conv = store.create_conversation("u", "p")
            turn = events.create_turn(conv["id"], "u", "p")
            for i in range(10):
                events.append_event(
                    conv["id"], turn["id"], "system_note", {"text": str(i)}, context_visible=True
                )
            first = events.list_events(conv["id"], user_id="u", after_seq=0, limit=4)
            cursor = first[-1]["seq"]
            second = events.list_events(conv["id"], user_id="u", after_seq=cursor, limit=20)
            overlap = {e["seq"] for e in first} & {e["seq"] for e in second}
            self.assertEqual(overlap, set())
            self.assertEqual(len(first) + len(second), 10)


if __name__ == "__main__":
    unittest.main()
