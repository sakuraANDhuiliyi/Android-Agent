"""性能相关 API：构建日志分页（P16）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.database import TaskStore
from agent.users import UserStore


def api_settings() -> Settings:
    return Settings(
        provider="deepseek",
        api_key="fake-model-key",
        model="fake-model",
        model_candidates=["fake-model"],
        max_turns=4,
        max_auto_continuations=0,
        max_gradle_retries=2,
        compact_max_chars=100_000,
        max_output_tokens=4096,
        base_url="https://example.test",
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
        tavily_api_key="",
        users=[],
        provider_fallbacks=[],
    )


class BuildLogPaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.task_store = TaskStore(root / "tasks.db")
        self.user_store = UserStore(root / "users.db")
        self.user_id, self.token = self.user_store.register()
        self.client = TestClient(
            create_app(
                settings=api_settings(),
                user_store=self.user_store,
                task_store=self.task_store,
            )
        )
        self.auth = {"Authorization": f"Bearer {self.token}"}
        self.log_path = root / "build-001.log"

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _create_job_with_log(self, content: str) -> str:
        self.log_path.write_text(content, encoding="utf-8")
        task_id = "task-log-1"
        self.task_store.create_task(
            {
                "id": task_id,
                "user_id": self.user_id,
                "project_id": "proj-1",
                "conversation_id": "conv-1",
                "prompt": "构建",
                "status": "succeeded",
                "provider": "openai",
                "created_at": 1_700_000_000.0,
            }
        )
        self.task_store.update_task(task_id, build_log_path=str(self.log_path))
        return task_id

    def test_full_log_without_pagination_params(self) -> None:
        task_id = self._create_job_with_log("line1\nline2\nline3\n")
        resp = self.client.get(f"/api/jobs/{task_id}/log", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["content"], "line1\nline2\nline3\n")
        self.assertEqual(body["total_size"], 18)
        self.assertFalse(body["has_more"])

    def test_paginated_log_slices_and_reports_more(self) -> None:
        task_id = self._create_job_with_log("abcdefghij")  # 10 chars
        resp = self.client.get(
            f"/api/jobs/{task_id}/log",
            params={"offset": 2, "limit": 4},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["content"], "cdef")
        self.assertEqual(body["offset"], 2)
        self.assertEqual(body["total_size"], 10)
        self.assertTrue(body["has_more"])

        # 最后一页：has_more 归 false
        resp = self.client.get(
            f"/api/jobs/{task_id}/log",
            params={"offset": 6, "limit": 10},
            headers=self.auth,
        )
        body = resp.json()
        self.assertEqual(body["content"], "ghij")
        self.assertFalse(body["has_more"])

    def test_page_limit_is_capped(self) -> None:
        task_id = self._create_job_with_log("x" * 100)
        resp = self.client.get(
            f"/api/jobs/{task_id}/log",
            params={"offset": 0, "limit": 10_000_000},
            headers=self.auth,
        )
        body = resp.json()
        self.assertEqual(body["limit"], 524_288)
        self.assertEqual(body["content"], "x" * 100)
        self.assertFalse(body["has_more"])

    def test_missing_log_returns_404(self) -> None:
        task_id = "task-no-log"
        self.task_store.create_task(
            {
                "id": task_id,
                "user_id": self.user_id,
                "project_id": "proj-1",
                "conversation_id": "conv-1",
                "prompt": "构建",
                "status": "succeeded",
                "provider": "openai",
                "created_at": 1_700_000_000.0,
            }
        )
        resp = self.client.get(f"/api/jobs/{task_id}/log", headers=self.auth)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
