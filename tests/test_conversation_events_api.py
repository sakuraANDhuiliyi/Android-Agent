from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.conversation_events import ConversationEventStore
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


class ConversationEventsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.task_store = TaskStore(root / "tasks.db")
        self.user_store = UserStore(root / "users.db")
        self.alice, self.alice_token = self.user_store.register()
        self.bob, self.bob_token = self.user_store.register()
        self.conversation = self.task_store.create_conversation(
            self.alice,
            "project",
            title="API 对话",
        )
        self.event_store = ConversationEventStore(self.task_store)
        self.turn = self.event_store.create_turn(
            self.conversation["id"],
            self.alice,
            "project",
            status="succeeded",
            created_at=10.0,
            finished_at=11.0,
        )
        self._append_fixture_events()
        self.client = TestClient(
            create_app(
                settings=api_settings(),
                user_store=self.user_store,
                task_store=self.task_store,
            )
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _headers(self, token: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token or self.alice_token}",
        }

    def _append(
        self,
        event_type: str,
        payload: dict,
        *,
        context_visible: bool,
        role: str | None = None,
    ) -> None:
        self.event_store.append_event(
            self.conversation["id"],
            self.turn["id"],
            event_type,
            payload,
            role=role,
            context_visible=context_visible,
        )

    def _append_fixture_events(self) -> None:
        self._append(
            "user_message",
            {
                "message_id": "user-1",
                "content": [{"type": "text", "text": "读取文件"}],
            },
            context_visible=True,
            role="user",
        )
        self._append(
            "turn_started",
            {"message": "started"},
            context_visible=False,
        )
        self._append(
            "assistant_message",
            {
                "message_id": "assistant-tool",
                "text_blocks": [],
                "is_final": False,
            },
            context_visible=True,
            role="assistant",
        )
        self._append(
            "tool_call",
            {
                "message_id": "assistant-tool",
                "tool_call_id": "call-1",
                "block_index": 0,
                "name": "read_file",
                "input": {"path": "app/Main.kt"},
            },
            context_visible=True,
        )
        self._append(
            "approval_required",
            {
                "approval_id": "approval-1",
                "tool_call_id": "call-1",
                "message": "waiting",
            },
            context_visible=False,
        )
        self._append(
            "tool_result",
            {
                "tool_call_id": "call-1",
                "name": "read_file",
                "ok": True,
                "model_output": "file content",
            },
            context_visible=True,
        )
        self._append(
            "assistant_message",
            {
                "message_id": "assistant-final",
                "text_blocks": [
                    {"block_index": 0, "type": "text", "text": "完成"}
                ],
                "is_final": True,
            },
            context_visible=True,
            role="assistant",
        )

    @property
    def events_url(self) -> str:
        return f"/api/conversations/{self.conversation['id']}/events"

    def test_first_page_and_cursor_metadata(self) -> None:
        response = self.client.get(
            self.events_url,
            params={"limit": 3},
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["conversation_id"], self.conversation["id"])
        self.assertEqual([item["seq"] for item in body["events"]], [1, 2, 3])
        self.assertEqual(body["next_after_seq"], 3)
        self.assertTrue(body["has_more"])
        self.assertIsInstance(body["events"][0]["payload"], dict)
        self.assertIs(body["events"][0]["context_visible"], True)
        self.assertIs(body["events"][1]["context_visible"], False)
        self.assertNotIn("payload_json", body["events"][0])

    def test_following_pages_have_no_duplicates_or_gaps(self) -> None:
        sequences: list[int] = []
        after_seq = None
        while True:
            params = {"limit": 2}
            if after_seq is not None:
                params["after_seq"] = after_seq
            response = self.client.get(
                self.events_url,
                params=params,
                headers=self._headers(),
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            sequences.extend(item["seq"] for item in body["events"])
            after_seq = body["next_after_seq"]
            if not body["has_more"]:
                break

        self.assertEqual(sequences, list(range(1, 8)))
        self.assertEqual(len(sequences), len(set(sequences)))

    def test_context_only_filters_before_pagination(self) -> None:
        first = self.client.get(
            self.events_url,
            params={"context_only": "true", "limit": 3},
            headers=self._headers(),
        ).json()
        second = self.client.get(
            self.events_url,
            params={
                "context_only": "true",
                "limit": 3,
                "after_seq": first["next_after_seq"],
            },
            headers=self._headers(),
        ).json()

        sequences = [
            item["seq"] for item in first["events"] + second["events"]
        ]
        self.assertEqual(sequences, [1, 3, 4, 6, 7])
        self.assertTrue(
            all(
                item["context_visible"]
                for item in first["events"] + second["events"]
            )
        )
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])

    def test_limit_boundaries_and_strict_page_size(self) -> None:
        for invalid in (0, 501):
            with self.subTest(limit=invalid):
                response = self.client.get(
                    self.events_url,
                    params={"limit": invalid},
                    headers=self._headers(),
                )
                self.assertEqual(response.status_code, 422)

        one = self.client.get(
            self.events_url,
            params={"limit": 1},
            headers=self._headers(),
        )
        maximum = self.client.get(
            self.events_url,
            params={"limit": 500},
            headers=self._headers(),
        )
        self.assertEqual(len(one.json()["events"]), 1)
        self.assertEqual(len(maximum.json()["events"]), 7)

    def test_other_user_and_missing_conversation_both_return_404(self) -> None:
        other = self.client.get(
            self.events_url,
            headers=self._headers(self.bob_token),
        )
        missing = self.client.get(
            "/api/conversations/missing/events",
            headers=self._headers(),
        )

        self.assertEqual(other.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other.json()["detail"].split(":")[0], "对话不存在")
        self.assertEqual(missing.json()["detail"].split(":")[0], "对话不存在")

    def test_response_recursively_removes_credential_fields(self) -> None:
        with self.task_store._connect() as conn:
            conn.execute(
                """UPDATE conversation_events SET payload_json=?
                   WHERE conversation_id=? AND seq=2""",
                (
                    json.dumps(
                        {
                            "message": (
                                "safe Bearer abcdefghijklmnopqrstuvwxyz "
                                "api_key=sk-abcdefghijklmnopqrstuvwxyz"
                            ),
                            "api_key": "must-not-leak",
                            "nested": {
                                "Authorization": "Bearer must-not-leak",
                                "api_token": "must-not-leak",
                                "visible": "yes",
                            },
                        }
                    ),
                    self.conversation["id"],
                ),
            )

        response = self.client.get(
            self.events_url,
            headers=self._headers(),
        )
        serialized = json.dumps(response.json(), ensure_ascii=False).lower()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        payload = response.json()["events"][1]["payload"]
        self.assertIn("[REDACTED]", payload["message"])
        self.assertEqual(payload["nested"], {"visible": "yes"})

    def test_corrupt_payload_returns_safe_error(self) -> None:
        with self.task_store._connect() as conn:
            conn.execute(
                """UPDATE conversation_events SET payload_json=?
                   WHERE conversation_id=? AND seq=2""",
                ("{broken", self.conversation["id"]),
            )

        response = self.client.get(
            self.events_url,
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"detail": "Conversation Event 数据读取失败"},
        )

    def test_conversation_detail_keeps_legacy_projection(self) -> None:
        response = self.client.get(
            f"/api/conversations/{self.conversation['id']}",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        expected_keys = {
            "id",
            "user_id",
            "project_id",
            "title",
            "status",
            "turns",
            "turn_count",
            "created_at",
            "updated_at",
        }
        self.assertEqual(set(body), expected_keys)
        self.assertEqual(body["turn_count"], 1)
        self.assertEqual(
            body["turns"],
            [
                {
                    "user": "读取文件",
                    "assistant": "完成",
                    "changed_files": [],
                    "ts": 10.0,
                }
            ],
        )
        self.assertNotIn("tool_call", json.dumps(body))

    def test_job_and_websocket_interfaces_remain_compatible(self) -> None:
        task_id = "api-job"
        self.task_store.create_task(
            {
                "id": task_id,
                "user_id": self.alice,
                "project_id": "project",
                "conversation_id": self.conversation["id"],
                "prompt": "test",
                "status": "succeeded",
                "provider": "fake",
                "model": "fake",
                "created_at": time.time(),
            }
        )
        self.task_store.update_task(
            task_id,
            finished_at=time.time(),
            final_message="done",
        )
        self.task_store.add_event(task_id, "started", {"message": "started"})

        response = self.client.get(
            f"/api/jobs/{task_id}",
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job"]["id"], task_id)
        self.assertEqual(response.json()["job"]["result"], "done")

        with self.client.websocket_connect(
            f"/api/ws/jobs/{task_id}?token={self.alice_token}"
        ) as websocket:
            event = websocket.receive_json()
            done = websocket.receive_json()
        self.assertEqual(event["type"], "started")
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["status"], "succeeded")

    def test_old_conversation_and_ask_routes_still_dispatch(self) -> None:
        fake_job = {
            "id": "fake-job",
            "user_id": self.alice,
            "project_id": "project",
            "conversation_id": self.conversation["id"],
            "prompt": "hello",
            "status": "queued",
            "provider": "fake",
            "model": "fake",
            "created_at": time.time(),
            "final_message": None,
            "error_message": None,
        }
        with (
            patch("agent.jobs.load_project_meta", return_value={}),
            patch("agent.api.load_project_meta", return_value={}),
            patch("agent.api.start_ask_job", return_value=fake_job) as start,
        ):
            listed = self.client.get(
                "/api/projects/project/conversations",
                headers=self._headers(),
            )
            created = self.client.post(
                "/api/projects/project/conversations",
                json={"title": "新建"},
                headers=self._headers(),
            )
            conversation_ask = self.client.post(
                f"/api/conversations/{self.conversation['id']}/ask",
                json={"prompt": "hello"},
                headers=self._headers(),
            )
            project_ask = self.client.post(
                "/api/projects/project/ask",
                json={
                    "prompt": "hello",
                    "conversation_id": self.conversation["id"],
                },
                headers=self._headers(),
            )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(conversation_ask.status_code, 200)
        self.assertEqual(project_ask.status_code, 200)
        self.assertEqual(start.call_count, 2)

        patched = self.client.patch(
            f"/api/conversations/{created.json()['id']}",
            json={"title": "已修改"},
            headers=self._headers(),
        )
        deleted = self.client.delete(
            f"/api/conversations/{created.json()['id']}",
            headers=self._headers(),
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["title"], "已修改")
        self.assertEqual(deleted.status_code, 204)


if __name__ == "__main__":
    unittest.main()
