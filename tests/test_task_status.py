"""Phase 1: canonical task status semantics and cross-client contract."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from agent.database import TaskStore
from agent.jobs import job_to_dict
from agent.task_status import (
    ACTIVE_STATUSES,
    CANCELABLE_STATUSES,
    STATUS_LABELS_ZH,
    TERMINAL_STATUSES,
    display_job_status,
    enrich_job_dict,
    status_label_zh,
)

CONTRACT_PATH = Path(__file__).resolve().parent / "fixtures" / "task_status_contract.json"


class TaskStatusContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_python_labels_match_contract(self) -> None:
        self.assertEqual(STATUS_LABELS_ZH, self.contract["labels_zh"])

    def test_python_sets_match_contract(self) -> None:
        self.assertEqual(set(ACTIVE_STATUSES), set(self.contract["active_statuses"]))
        self.assertEqual(set(TERMINAL_STATUSES), set(self.contract["terminal_statuses"]))
        self.assertEqual(
            set(CANCELABLE_STATUSES),
            set(self.contract["cancelable_stored_statuses"]),
        )

    def test_display_status_cancel_requested_overrides_active(self) -> None:
        for stored in ("queued", "running", "awaiting_approval", "paused"):
            self.assertEqual(
                display_job_status(stored, cancel_requested=True),
                "cancel_requested",
            )

    def test_display_status_ignores_cancel_flag_on_terminal(self) -> None:
        for stored in TERMINAL_STATUSES:
            self.assertEqual(
                display_job_status(stored, cancel_requested=True),
                stored,
            )

    def test_job_to_dict_includes_display_fields(self) -> None:
        payload = enrich_job_dict(
            {
                "id": "j1",
                "status": "running",
                "cancel_requested": True,
                "user_id": "u",
                "project_id": "p",
            }
        )
        self.assertEqual(payload["display_status"], "cancel_requested")
        self.assertEqual(payload["status_label"], "正在停止")

    def test_status_label_zh_fallback(self) -> None:
        self.assertEqual(status_label_zh("unknown-x"), "unknown-x")


class TaskCancelPauseDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "agent.db")
        conversation = self.store.get_or_create_default_conversation("user", "proj")
        now = time.time()
        for task_id, status in (
            ("task-paused", "paused"),
            ("task-approval", "awaiting_approval"),
        ):
            self.store.create_task(
                {
                    "id": task_id,
                    "user_id": "user",
                    "project_id": "proj",
                    "conversation_id": conversation["id"],
                    "prompt": "x",
                    "status": status,
                    "provider": "openai",
                    "created_at": now,
                }
            )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cancel_applies_to_paused_task(self) -> None:
        self.assertTrue(self.store.request_cancel("task-paused", "user"))
        task = self.store.get_task("task-paused", "user")
        self.assertTrue(task["cancel_requested"])

    def test_cancel_is_idempotent_when_already_requested(self) -> None:
        self.assertTrue(self.store.request_cancel("task-approval", "user"))
        self.assertTrue(self.store.request_cancel("task-approval", "user"))
        task = self.store.get_task("task-approval", "user")
        self.assertTrue(task["cancel_requested"])

    def test_pause_is_idempotent_when_already_paused(self) -> None:
        self.assertTrue(self.store.pause_task("task-paused", "user"))
        task = self.store.get_task("task-paused", "user")
        self.assertEqual(task["status"], "paused")

    def test_pause_is_idempotent_when_already_paused(self) -> None:
        self.assertTrue(self.store.pause_task("task-paused", "user"))
        task = self.store.get_task("task-paused", "user")
        self.assertEqual(task["status"], "paused")
        self.store.request_cancel("task-paused", "user")
        task = self.store.get_task("task-paused", "user")
        dto = job_to_dict(task)
        self.assertEqual(dto["display_status"], "cancel_requested")
        self.assertEqual(dto["status_label"], "正在停止")
        self.assertEqual(dto["status"], "paused")


if __name__ == "__main__":
    unittest.main()
