from __future__ import annotations

from dataclasses import replace
import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.api_contract import (
    dump_openapi,
    openapi_path_index,
    public_job_ws_done,
    public_job_ws_event,
    public_terminal_ws_chunk,
    public_terminal_ws_done,
)
from agent.api_errors import ERROR_SCHEMA_VERSION, build_error_body
from agent.config import Settings
from agent.conversation_events import EVENT_SCHEMA_VERSION, ConversationEventStore
from agent.database import TaskStore
from agent.jobs import job_to_dict
from agent.users import UserStore

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "api_contract"


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


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ApiContractTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _headers(self, token: str | None = None) -> dict[str, str]:
        value = token if token is not None else self.token
        return {"Authorization": f"Bearer {value}"}

    def test_manifest_lists_client_contract_tests(self) -> None:
        manifest = load_fixture("manifest.json")
        self.assertEqual(manifest["schema_version"], 1)
        client_tests = manifest["client_tests"]
        root = Path(__file__).resolve().parents[1]
        for relative in client_tests:
            self.assertTrue((root / relative).is_file(), relative)

    def test_error_envelope_shape_matches_fixture(self) -> None:
        expected = load_fixture("errors/unauthorized_401.json")
        body = build_error_body(401, "未提供 API Token", code="unauthorized")
        self.assertEqual(body["detail"], expected["detail"])
        self.assertEqual(body["error"]["schema_version"], ERROR_SCHEMA_VERSION)
        self.assertEqual(body["error"]["code"], expected["error"]["code"])
        self.assertEqual(body["error"]["retryable"], expected["error"]["retryable"])
        self.assertEqual(body["error"]["user_message"], expected["error"]["user_message"])

    def test_health_requires_auth_and_matches_contract(self) -> None:
        unauthorized = self.client.get("/api/health")
        self.assertEqual(unauthorized.status_code, 401)
        payload = unauthorized.json()
        self.assertEqual(payload["error"]["code"], "unauthorized")
        self.assertFalse(payload["error"]["retryable"])

        response = self.client.get("/api/health", headers=self._headers())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in ("status", "user_id", "provider", "model", "api_key_configured", "port"):
            self.assertIn(key, body)
        self.assertEqual(body["user_id"], self.user_id)

    def test_job_get_not_found_returns_contract_envelope(self) -> None:
        response = self.client.get(
            "/api/jobs/job-missing",
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "not_found")
        self.assertFalse(payload["error"]["retryable"])
        self.assertIn("任务不存在", payload["error"]["user_message"])

    def test_job_message_rejects_unknown_fields(self) -> None:
        conversation = self.task_store.create_conversation(
            self.user_id,
            "demo",
        )
        self.task_store.create_task(
            {
                "id": "job-001",
                "user_id": self.user_id,
                "project_id": "demo",
                "conversation_id": conversation["id"],
                "prompt": "hello",
                "status": "running",
                "created_at": time.time(),
            }
        )
        response = self.client.post(
            "/api/jobs/job-001/messages",
            headers=self._headers(),
            json={
                "message_key": "client-msg-001",
                "type": "steer",
                "payload": {"text": "focus"},
                "unknown_field": True,
            },
        )
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "validation_error")
        self.assertFalse(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["user_message"], "请求参数无效")
        self.assertIsInstance(payload["detail"], list)

    def test_job_message_conflict_when_task_finished(self) -> None:
        conversation = self.task_store.create_conversation(
            self.user_id,
            "demo",
        )
        self.task_store.create_task(
            {
                "id": "job-done",
                "user_id": self.user_id,
                "project_id": "demo",
                "conversation_id": conversation["id"],
                "prompt": "hello",
                "status": "succeeded",
                "created_at": time.time(),
            }
        )
        response = self.client.post(
            "/api/jobs/job-done/messages",
            headers=self._headers(),
            json={
                "message_key": "client-msg-002",
                "type": "steer",
                "payload": {"text": "too late"},
            },
        )
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "conflict")
        self.assertEqual(payload["detail"], "任务已结束")

    def test_job_message_success_includes_message_key(self) -> None:
        conversation = self.task_store.create_conversation(
            self.user_id,
            "demo",
        )
        self.task_store.create_task(
            {
                "id": "job-live",
                "user_id": self.user_id,
                "project_id": "demo",
                "conversation_id": conversation["id"],
                "prompt": "hello",
                "status": "running",
                "created_at": time.time(),
            }
        )
        response = self.client.post(
            "/api/jobs/job-live/messages",
            headers=self._headers(),
            json={
                "message_key": "client-msg-003",
                "type": "steer",
                "payload": {"text": "Focus on settings screen only"},
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["job_id"], "job-live")
        self.assertEqual(payload["message"]["message_key"], "client-msg-003")
        self.assertEqual(payload["message"]["type"], "steer")

    def test_conversation_events_include_schema_version(self) -> None:
        conversation = self.task_store.create_conversation(
            self.user_id,
            "demo",
            title="Contract",
        )
        event_store = ConversationEventStore(self.task_store)
        turn = event_store.create_turn(
            conversation["id"],
            self.user_id,
            "demo",
            status="running",
        )
        event_store.append_event(
            conversation["id"],
            turn["id"],
            "user_message",
            {
                "message_id": "msg-001",
                "content": [{"type": "text", "text": "Add dark mode toggle"}],
            },
            role="user",
            context_visible=True,
        )
        response = self.client.get(
            f"/api/conversations/{conversation['id']}/events",
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema_version"], 1)
        self.assertGreaterEqual(len(payload["events"]), 1)
        self.assertIn("schema_version", payload["events"][0])
        self.assertEqual(payload["events"][0]["schema_version"], 1)

    def test_job_dto_matches_contract_keys(self) -> None:
        public = job_to_dict(
            {
                "id": "job-001",
                "user_id": self.user_id,
                "project_id": "demo",
                "conversation_id": "conv-001",
                "status": "running",
                "prompt": "Add dark mode toggle",
                "provider": "deepseek",
                "model": "fake-model",
                "created_at": 1000.0,
                "started_at": 1001.0,
                "apk_path": "/Users/private/build.apk",
                "build_log_path": "/Users/private/build.log",
                "claim_token": "secret-claim",
            }
        )
        expected = load_fixture("job_get_200.json")["job"]
        for key in expected:
            self.assertIn(key, public, key)
        serialized = json.dumps(public)
        self.assertNotIn("/Users/private", serialized)
        self.assertNotIn("secret-claim", serialized)

    def test_error_status_fixtures_cover_contract_matrix(self) -> None:
        manifest = load_fixture("manifest.json")
        covered = {item["status"] for item in manifest["endpoints"]}
        for status in manifest["error_statuses"]:
            self.assertIn(status, covered, status)

    def test_payload_too_large_and_internal_error_fixtures(self) -> None:
        too_large = load_fixture("errors/payload_too_large_413.json")
        self.assertEqual(
            build_error_body(413, "请求体超过服务端大小限制"),
            too_large,
        )
        internal = load_fixture("errors/internal_error_500.json")
        self.assertEqual(
            build_error_body(500, "Conversation Event 数据读取失败"),
            internal,
        )

    def test_request_body_limit_returns_error_envelope(self) -> None:
        settings = replace(api_settings(), max_request_bytes=64)
        tiny = TestClient(
            create_app(
                settings=settings,
                user_store=self.user_store,
                task_store=self.task_store,
            )
        )
        try:
            response = tiny.post(
                "/api/projects",
                headers=self._headers(),
                content=b"x" * 200,
            )
        finally:
            tiny.close()
        self.assertEqual(response.status_code, 413)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "payload_too_large")
        self.assertFalse(payload["error"]["retryable"])

    def test_authenticated_get_paths_return_unauthorized_envelope(self) -> None:
        spec = dump_openapi(self.client.app)
        skipped = {
            "/openapi.json",
            "/docs",
            "/docs/oauth2-redirect",
            "/redoc",
            "/api/pair",
            "/api/register",
        }
        checked = 0
        for row in openapi_path_index(spec):
            method, path = row.split(" ", 1)
            if method != "GET" or path in skipped:
                continue
            concrete = path.replace("{", "").replace("}", "")
            response = self.client.get(concrete)
            self.assertEqual(response.status_code, 401, row)
            body = response.json()
            self.assertEqual(body["error"]["code"], "unauthorized")
            self.assertFalse(body["error"]["retryable"])
            self.assertEqual(body["error"]["schema_version"], ERROR_SCHEMA_VERSION)
            checked += 1
        self.assertGreaterEqual(checked, 8)

    def test_websocket_helpers_match_shared_fixtures(self) -> None:
        event = public_job_ws_event(
            {"id": 12, "type": "started", "ts": 1001.0, "message": "started"}
        )
        self.assertEqual(event, load_fixture("ws/job_event.json"))
        done = public_job_ws_done(
            {
                "status": "succeeded",
                "finished_at": 1010.0,
                "final_message": "Dark mode toggle added.",
                "error_message": None,
                "cancel_requested": False,
            }
        )
        self.assertEqual(done, load_fixture("ws/job_done.json"))
        chunk = public_terminal_ws_chunk(
            {"seq": 3, "data": "hello\n", "is_stderr": False, "created_at": 1003.0}
        )
        self.assertEqual(chunk, load_fixture("ws/terminal_chunk.json"))
        terminal_done = public_terminal_ws_done({"status": "exited", "exit_code": 0})
        self.assertEqual(terminal_done, load_fixture("ws/terminal_done.json"))

    def test_job_websocket_done_includes_schema_version(self) -> None:
        conversation = self.task_store.create_conversation(self.user_id, "demo")
        self.task_store.create_task(
            {
                "id": "ws-job",
                "user_id": self.user_id,
                "project_id": "demo",
                "conversation_id": conversation["id"],
                "prompt": "done",
                "status": "succeeded",
                "created_at": time.time(),
                "finished_at": time.time(),
                "final_message": "ok",
            }
        )
        self.task_store.add_event("ws-job", "started", {"message": "started"})
        ticket = self.client.post(
            "/api/ws/tickets",
            headers=self._headers(),
            json={"resource_type": "job", "resource_id": "ws-job"},
        )
        self.assertEqual(ticket.status_code, 201)
        with self.client.websocket_connect(
            f"/api/ws/jobs/ws-job?ticket={ticket.json()['ticket']}"
        ) as websocket:
            event = websocket.receive_json()
            done = websocket.receive_json()
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["type"], "started")
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["schema_version"], 1)
        self.assertEqual(done["display_status"], "succeeded")
        self.assertEqual(done["status_label"], "已完成")

    def test_old_event_schema_version_is_projected_idempotently(self) -> None:
        conversation = self.task_store.create_conversation(self.user_id, "demo")
        event_store = ConversationEventStore(self.task_store)
        turn = event_store.create_turn(
            conversation["id"],
            self.user_id,
            "demo",
            status="succeeded",
        )
        written = event_store.append_event(
            conversation["id"],
            turn["id"],
            "user_message",
            {"content": [{"type": "text", "text": "old"}]},
            role="user",
            context_visible=True,
        )
        with self.task_store._connect() as conn:
            conn.execute(
                "UPDATE conversation_events SET schema_version=0 WHERE id=?",
                (written["id"],),
            )
        first = event_store.list_events(conversation["id"], user_id=self.user_id)
        second = event_store.list_events(conversation["id"], user_id=self.user_id)
        self.assertEqual(first[0]["schema_version"], EVENT_SCHEMA_VERSION)
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual(first[0]["payload"], second[0]["payload"])

    def test_live_openapi_matches_snapshot(self) -> None:
        snapshot_path = FIXTURES / "openapi.json"
        self.assertTrue(snapshot_path.is_file(), "missing OpenAPI snapshot")
        live = dump_openapi(self.client.app)
        live["x-android-agent-paths"] = openapi_path_index(live)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(
            live.get("x-android-agent-paths"),
            snapshot.get("x-android-agent-paths"),
        )


if __name__ == "__main__":
    unittest.main()
