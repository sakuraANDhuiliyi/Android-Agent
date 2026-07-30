from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from pydantic import ValidationError

from agent.api import AskRequest, RequestBodyLimitMiddleware
from agent.conversation_context import build_openai_messages
from agent.conversation_events import ConversationEventStore, ConversationEventType
from agent.conversation_summary import create_semantic_checkpoint
from agent.database import TaskStore
from agent.diagnostics import DiagnosticStore
from agent.jobs import job_to_dict
from agent.loop import parse_tool_arguments
from agent.memory_store import MemoryStore
from agent.ws_tickets import WebSocketTicketStore


class P2HardeningTests(unittest.TestCase):
    def test_malformed_tool_arguments_are_rejected(self) -> None:
        value, error = parse_tool_arguments('{"path":')
        self.assertEqual(value, {})
        self.assertIn("Expecting", error or "")

        value, error = parse_tool_arguments('["not", "an", "object"]')
        self.assertEqual(value, {})
        self.assertIn("JSON object", error or "")

        value, error = parse_tool_arguments('{"path":"ok"}')
        self.assertEqual(value, {"path": "ok"})
        self.assertIsNone(error)

    def test_websocket_ticket_is_scoped_short_lived_and_one_time(self) -> None:
        store = WebSocketTicketStore()
        ticket, expires_at = store.issue("u", "job", "j", ttl_seconds=30)
        self.assertGreater(expires_at, time.time())
        self.assertIsNone(store.consume(ticket, "terminal", "j"))

        second, _ = store.issue("u", "job", "j", ttl_seconds=30)
        self.assertEqual(store.consume(second, "job", "j"), "u")
        self.assertIsNone(store.consume(second, "job", "j"))

    def test_local_memory_is_project_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = MemoryStore(Path(temp) / "memory.db")
            store.create_memory(
                user_id="user",
                project_id="project_a",
                scope="local",
                memory_type="decision",
                title="Only A",
                content="Use the architecture chosen for project A only.",
                status="active",
            )
            self.assertEqual(
                len(
                    store.search(
                        "user",
                        "architecture",
                        project_id="project_a",
                    )
                ),
                1,
            )
            self.assertEqual(
                store.search(
                    "user",
                    "architecture",
                    project_id="project_b",
                ),
                [],
            )
            with self.assertRaisesRegex(ValueError, "local scope"):
                store.create_memory(
                    user_id="user",
                    scope="local",
                    memory_type="decision",
                    title="Invalid",
                    content="A local memory without a project is invalid.",
                )

    def test_structured_checkpoint_has_source_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_store = TaskStore(Path(temp) / "events.db")
            conversation = task_store.create_conversation("user", "project")
            for index in range(6):
                task_store.append_conversation_turn(
                    conversation["id"],
                    user=f"必须保留约束 {index}",
                    assistant=f"决定 {index}",
                    auto_title=False,
                )
            events = ConversationEventStore(task_store)
            checkpoint = create_semantic_checkpoint(
                events,
                conversation["id"],
                "user",
                keep_recent_turns=2,
                force=True,
            )
            self.assertIsNotNone(checkpoint)
            payload = checkpoint["payload"]
            self.assertEqual(payload["checkpoint_version"], 2)
            self.assertEqual(payload["generator"], "structured-deterministic-v2")
            self.assertTrue(payload["validation"]["valid"])
            self.assertTrue(payload["state"]["constraints"])
            for facts in payload["state"].values():
                for fact in facts:
                    self.assertIsInstance(fact["source_seq"], int)
                    self.assertLessEqual(
                        fact["source_seq"],
                        payload["covers_through_seq"],
                    )
            messages = build_openai_messages(
                events.list_events(conversation["id"], user_id="user")
            )
            self.assertIn("必须保留约束 0", messages[0]["content"])

    def test_terminal_lifecycle_is_atomic_and_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.db"
            store = TaskStore(path)
            conversation = store.create_conversation("user", "project")
            store.create_task(
                {
                    "id": "task",
                    "user_id": "user",
                    "project_id": "project",
                    "conversation_id": conversation["id"],
                    "prompt": "hello",
                    "status": "queued",
                    "created_at": time.time(),
                }
            )
            events = ConversationEventStore(store)
            turn = events.create_turn(
                conversation["id"],
                "user",
                "project",
                task_id="task",
            )
            started = time.time()
            events.start_lifecycle(
                conversation_id=conversation["id"],
                turn_id=turn["id"],
                task_id="task",
                user_id="user",
                project_id="project",
                provider="fake",
                model="fake",
                started_at=started,
            )
            events.finalize_lifecycle(
                conversation_id=conversation["id"],
                turn_id=turn["id"],
                task_id="task",
                user_id="user",
                event_type=ConversationEventType.TURN_COMPLETED,
                event_key=f"turn:{turn['id']}:completed",
                event_payload={"status": "succeeded", "result": "done"},
                status="succeeded",
                finished_at=time.time(),
                final_message="done",
                task_event_type="completed",
                task_event_payload={"message": "done"},
            )
            self.assertEqual(store.get_task("task")["status"], "succeeded")
            self.assertEqual(events.get_turn(turn["id"])["status"], "succeeded")

            with store._connect() as conn:
                conn.execute("UPDATE tasks SET status='running' WHERE id='task'")
                conn.execute(
                    "UPDATE conversation_turns SET status='running' WHERE id=?",
                    (turn["id"],),
                )
            reopened = TaskStore(path)
            self.assertEqual(reopened.get_task("task")["status"], "succeeded")
            event_types = [
                item["event_type"]
                for item in ConversationEventStore(reopened).list_events(
                    conversation["id"]
                )
            ]
            self.assertIn("lifecycle_reconciled", event_types)

    def test_public_job_dto_has_resource_urls_not_host_paths(self) -> None:
        public = job_to_dict(
            {
                "id": "task",
                "user_id": "user",
                "project_id": "project",
                "status": "succeeded",
                "apk_path": "/Users/private/build.apk",
                "build_log_path": "/Users/private/build.log",
                "claim_token": "secret-claim",
                "context_json": {"private": True},
            }
        )
        serialized = json.dumps(public)
        self.assertNotIn("/Users/private", serialized)
        self.assertNotIn("secret-claim", serialized)
        self.assertEqual(public["apk_url"], "/api/jobs/task/apk")
        self.assertEqual(public["build_log_url"], "/api/jobs/task/log")

    def test_diagnostics_are_redacted_and_user_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DiagnosticStore(Path(temp) / "diag.db")
            store.record(
                "hooks",
                "AfterModel",
                "Bearer abcdefghijklmnopqrstuvwxyz",
                user_id="alice",
                details={"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
            )
            self.assertEqual(store.list("bob"), [])
            payload = json.dumps(store.list("alice"))
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz", payload)
            self.assertIn("[REDACTED]", payload)

    def test_request_models_reject_unknown_and_oversized_fields(self) -> None:
        with self.assertRaises(ValidationError):
            AskRequest(prompt="ok", unknown=True)
        with self.assertRaises(ValidationError):
            AskRequest(prompt="x" * 100_001)

    def test_chunked_request_body_limit_does_not_trust_content_length(self) -> None:
        sent: list[dict] = []
        messages = iter(
            [
                {"type": "http.request", "body": b"1234", "more_body": True},
                {"type": "http.request", "body": b"5678", "more_body": False},
            ]
        )

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        async def downstream(_scope, receive_body, _send):
            while True:
                message = await receive_body()
                if not message.get("more_body"):
                    break

        middleware = RequestBodyLimitMiddleware(downstream, max_bytes=6)
        asyncio.run(
            middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/test",
                    "headers": [],
                },
                receive,
                send,
            )
        )
        response_start = next(
            message for message in sent if message["type"] == "http.response.start"
        )
        self.assertEqual(response_start["status"], 413)

    def test_supply_chain_locks_and_release_guards_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        lock = (root / "requirements.lock").read_text(encoding="utf-8")
        self.assertIn("--hash=sha256:", lock)
        wrapper = (
            root
            / "android-app/gradle/wrapper/gradle-wrapper.properties"
        ).read_text(encoding="utf-8")
        self.assertIn("gradle-8.11.1-bin.zip", wrapper)
        self.assertIn("distributionSha256Sum=", wrapper)
        gradle = (
            root / "android-app/app/build.gradle.kts"
        ).read_text(encoding="utf-8")
        self.assertIn("isMinifyEnabled = true", gradle)
        self.assertIn("verifyReleaseSigning", gradle)
        desktop = json.loads(
            (root / "desktop/package.json").read_text(encoding="utf-8")
        )
        self.assertIn("electron-updater", desktop["dependencies"])
        self.assertIn("@electron/osx-sign", desktop["devDependencies"])
        package_script = (
            root / "desktop/scripts/package-macos.js"
        ).read_text(encoding="utf-8")
        self.assertIn('required("CSC_NAME")', package_script)
        self.assertIn("notarize(", package_script)
        self.assertIn("latest-mac.yml", package_script)

    def test_release_manifest_uses_names_and_digests_not_host_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "release.apk"
            artifact.write_bytes(b"signed release bytes")
            output = Path(temp) / "manifest.json"
            subprocess.run(
                [
                    "python3",
                    str(root / "scripts/generate_release_manifest.py"),
                    "--artifact",
                    str(artifact),
                    "--output",
                    str(output),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(manifest["subjects"][0]["name"], "release.apk")
            self.assertEqual(len(manifest["subjects"][0]["digest"]["sha256"]), 64)
            self.assertNotIn(temp, json.dumps(manifest))


if __name__ == "__main__":
    unittest.main()
