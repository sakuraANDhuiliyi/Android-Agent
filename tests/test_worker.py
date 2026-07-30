from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import jobs as jobs_mod
from agent.api import create_app
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.worker import TaskWorker


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider="openai",
        api_key="fake-key",
        model="fake-model",
        model_candidates=["fake-model"],
        max_turns=3,
        max_auto_continuations=0,
        max_gradle_retries=3,
        compact_max_chars=2_500_000,
        max_output_tokens=65_536,
        base_url=None,
        auto_build_after_edit=False,
        provider_fallbacks=[],
    )


def _wait_for_task(
    store: TaskStore,
    task_id: str,
    user_id: str = "user",
    timeout: float = 3.0,
) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = store.get_task(task_id, user_id) or {}
        if task.get("status") in {"succeeded", "failed", "canceled", "paused"}:
            return task
        time.sleep(0.01)
    return store.get_task(task_id, user_id) or {}


def _make_minimal_template(root: Path) -> None:
    java_dir = root / "app" / "src" / "main" / "java" / "com" / "example" / "template"
    java_dir.mkdir(parents=True)
    (java_dir / "MainActivity.kt").write_text(
        "package com.example.template\nclass MainActivity {}", encoding="utf-8"
    )
    res_dir = root / "app" / "src" / "main" / "res" / "layout"
    res_dir.mkdir(parents=True)
    (res_dir / "activity_main.xml").write_text("<LinearLayout/>", encoding="utf-8")
    (root / "app" / "build.gradle.kts").write_text("plugins {}", encoding="utf-8")
    (root / "build.gradle.kts").write_text("// root", encoding="utf-8")
    (root / "settings.gradle.kts").write_text("// settings", encoding="utf-8")
    gradle_dir = root / "gradle" / "wrapper"
    gradle_dir.mkdir(parents=True)
    (gradle_dir / "gradle-wrapper.properties").write_text(
        "distributionBase=GRADLE_USER_HOME", encoding="utf-8"
    )


class WorkerUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.store = TaskStore(self.root / "tasks.db")
        jobs_mod.stop_worker(wait=True, timeout=2.0)
        self._store_patch = patch.object(jobs_mod, "_store", self.store)
        self._store_patch.start()

    def tearDown(self) -> None:
        jobs_mod.stop_worker(wait=True, timeout=2.0)
        self._store_patch.stop()
        self._temp.cleanup()

    def _create_queued_task(self, prompt: str = "test") -> tuple[str, str, str]:
        conversation = self.store.get_or_create_default_conversation("user", "project")
        task_id = f"task-{int(time.time()*1000000)}"
        self.store.create_task(
            {
                "id": task_id,
                "user_id": "user",
                "project_id": "project",
                "conversation_id": conversation["id"],
                "prompt": prompt,
                "status": "queued",
                "provider": "openai",
                "model": "fake-model",
                "created_at": time.time(),
            }
        )
        event_store = ConversationEventStore(self.store)
        turn = event_store.create_turn(
            conversation["id"],
            "user",
            "project",
            task_id=task_id,
            status="queued",
            provider="openai",
            model="fake-model",
        )
        return task_id, conversation["id"], turn["id"]

    def _create_queued_task_for_project(
        self, project_id: str, prompt: str
    ) -> tuple[str, str, str]:
        conversation = self.store.get_or_create_default_conversation("user", project_id)
        task_id = f"task-{project_id}-{int(time.time()*1000000)}"
        self.store.create_task(
            {
                "id": task_id,
                "user_id": "user",
                "project_id": project_id,
                "conversation_id": conversation["id"],
                "prompt": prompt,
                "status": "queued",
                "provider": "openai",
                "model": "fake-model",
                "created_at": time.time(),
            }
        )
        event_store = ConversationEventStore(self.store)
        turn = event_store.create_turn(
            conversation["id"],
            "user",
            project_id,
            task_id=task_id,
            status="queued",
            provider="openai",
            model="fake-model",
        )
        return task_id, conversation["id"], turn["id"]

    def _run_with_worker(
        self,
        fake_agent,
        task_id: str | None = None,
        settings: SimpleNamespace | None = None,
    ) -> dict[str, object]:
        settings = settings or _settings()
        with (
            patch("agent.jobs.load_project_meta", return_value={}),
            patch("agent.jobs.workspace_path", return_value=self.root),
            patch("agent.jobs.user_builds_dir", return_value=self.root / "builds"),
            patch("agent.jobs.snapshot_workspace", return_value={}),
            patch("agent.jobs.compare_snapshots", return_value=([], "")),
            patch("agent.jobs.run_agent", side_effect=fake_agent),
        ):
            worker = TaskWorker(
                self.store, jobs_mod._run_job, settings, poll_interval=0.05
            )
            if task_id is None:
                worker.start()
                result = _wait_for_task(self.store, task_id or "")
            else:
                worker.run_once()
                result = self.store.get_task(task_id, "user") or {}
            jobs_mod.stop_worker(wait=True, timeout=2.0)
            return result

    def test_worker_atomic_claim(self) -> None:
        task_id, _, _ = self._create_queued_task()
        self.assertEqual(self.store.get_task(task_id, "user")["status"], "queued")

        def fake_agent(*_args, **_kwargs):
            return "ok"

        task = self._run_with_worker(fake_agent, task_id)
        self.assertEqual(task["status"], "succeeded")
        self.assertIsNone(task.get("claim_owner"))
        self.assertIsNone(task.get("lease_expires_at"))

    def test_two_workers_no_duplicate_execution(self) -> None:
        task_id, _, _ = self._create_queued_task()
        executed = {"count": 0}

        def fake_agent(*_args, **_kwargs):
            executed["count"] += 1
            time.sleep(0.05)
            return "ok"

        with (
            patch("agent.jobs.load_project_meta", return_value={}),
            patch("agent.jobs.workspace_path", return_value=self.root),
            patch("agent.jobs.user_builds_dir", return_value=self.root / "builds"),
            patch("agent.jobs.snapshot_workspace", return_value={}),
            patch("agent.jobs.compare_snapshots", return_value=([], "")),
            patch("agent.jobs.run_agent", side_effect=fake_agent),
        ):
            worker1 = TaskWorker(
                self.store, jobs_mod._run_job, _settings(), poll_interval=0.05
            )
            worker2 = TaskWorker(
                self.store, jobs_mod._run_job, _settings(), poll_interval=0.05
            )
            worker1.start()
            worker2.start()
            task = _wait_for_task(self.store, task_id, "user")
            worker1.stop(wait=True, timeout=2.0)
            worker2.stop(wait=True, timeout=2.0)

        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(executed["count"], 1)

    def test_queued_task_survives_service_restart(self) -> None:
        task_id, _, _ = self._create_queued_task()
        turn = ConversationEventStore(self.store).get_turn_by_task(
            task_id, user_id="user"
        )
        self.assertEqual(turn["status"], "queued")
        recovered = self.store.recover_interrupted()
        self.assertEqual(recovered, [])
        task = self.store.get_task(task_id, "user")
        self.assertEqual(task["status"], "queued")
        turn = ConversationEventStore(self.store).get_turn_by_task(
            task_id, user_id="user"
        )
        self.assertEqual(turn["status"], "queued")

        def fake_agent(*_args, **_kwargs):
            return "ok"

        task = self._run_with_worker(fake_agent, task_id)
        self.assertEqual(task["status"], "succeeded")

    def test_lease_expired_takeover(self) -> None:
        task_id, _, _ = self._create_queued_task()
        self.store.update_task(
            task_id,
            status="running",
            claim_owner="stale-worker",
            lease_expires_at=time.time() - 1.0,
            heartbeat_at=time.time() - 10.0,
        )

        def fake_agent(*_args, **_kwargs):
            return "ok"

        task = self._run_with_worker(fake_agent, task_id)
        self.assertEqual(task["status"], "succeeded")
        self.assertIsNone(task.get("claim_owner"))

    def test_cancel_message_terminates_task(self) -> None:
        task_id, _, _ = self._create_queued_task()
        self.store.add_task_message(
            task_id,
            message_key=f"cancel:{task_id}:1",
            type="cancel",
            payload={},
        )

        def fake_agent(*_args, **_kwargs):
            return "ok"

        task = self._run_with_worker(fake_agent, task_id)
        self.assertEqual(task["status"], "canceled")

    def test_pause_message_pauses_task(self) -> None:
        task_id, _, _ = self._create_queued_task()
        self.store.add_task_message(
            task_id,
            message_key=f"pause:{task_id}:1",
            type="pause",
            payload={},
        )

        def fake_agent(*_args, **_kwargs):
            return "ok"

        task = self._run_with_worker(fake_agent, task_id)
        self.assertEqual(task["status"], "paused")
        event_store = ConversationEventStore(self.store)
        turn = event_store.get_turn_by_task(task_id, user_id="user")
        self.assertEqual(turn["status"], "paused")

    def test_pause_and_resume_lifecycle(self) -> None:
        task_id, _, _ = self._create_queued_task()
        self.assertTrue(jobs_mod.pause_job(task_id, "user"))
        task = self.store.get_task(task_id, "user")
        self.assertEqual(task["status"], "paused")
        self.assertTrue(jobs_mod.resume_job(task_id, "user"))
        task = self.store.get_task(task_id, "user")
        self.assertEqual(task["status"], "queued")
        self.assertIsNone(task.get("claim_owner"))

        def fake_agent(*_args, **_kwargs):
            return "ok"

        task = self._run_with_worker(fake_agent, task_id)
        self.assertEqual(task["status"], "succeeded")

    def test_task_message_idempotency(self) -> None:
        task_id, _, _ = self._create_queued_task()
        msg1 = self.store.add_task_message(
            task_id,
            message_key=f"msg:{task_id}:1",
            type="steer",
            payload={"text": "x"},
        )
        msg2 = self.store.add_task_message(
            task_id,
            message_key=f"msg:{task_id}:1",
            type="steer",
            payload={"text": "x"},
        )
        self.assertEqual(msg1["id"], msg2["id"])
        pending = self.store.get_pending_messages(task_id)
        self.assertEqual(len(pending), 1)

    def test_steer_consumed_once_at_safe_point(self) -> None:
        from agent.loop import run_agent
        from agent.stream import (
            StreamedCompletion,
            _Choice,
            _Fn,
            _Message,
            _ToolCall,
            _Usage,
        )
        from agent.tools import ToolResult

        task_id, _, _ = self._create_queued_task()
        self.store.add_task_message(
            task_id,
            message_key=f"steer:{task_id}:1",
            type="steer",
            payload={"text": "steer text"},
        )

        captured_messages: list[list[dict]] = []
        call_count = {"n": 0}

        def fake_stream(client, *, model, messages, on_event=None, cancel_check=None, **kwargs):
            captured_messages.append(list(messages))
            call_count["n"] += 1
            if call_count["n"] == 1:
                return StreamedCompletion(
                    choices=[
                        _Choice(
                            message=_Message(
                                content=None,
                                tool_calls=[
                                    _ToolCall(
                                        id="call-1",
                                        function=_Fn(
                                            name="read_file",
                                            arguments='{"path":"a.kt"}',
                                        ),
                                    )
                                ],
                            ),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=_Usage(),
                )
            return StreamedCompletion(
                choices=[
                    _Choice(
                        message=_Message(content="final"),
                        finish_reason="stop",
                    )
                ],
                usage=_Usage(),
            )

        def fake_dispatch_tool(*_args, **_kwargs):
            return ToolResult(True, "content")

        def get_steers() -> list[str]:
            messages = self.store.get_pending_messages(task_id, types=["steer"])
            texts = []
            for msg in messages:
                text = (msg.get("payload") or {}).get("text") or ""
                if text:
                    self.store.consume_message(msg["id"])
                    texts.append(str(text))
            return texts

        with (
            patch("agent.loop.stream_openai_chat", side_effect=fake_stream),
            patch("agent.loop.dispatch_tool", side_effect=fake_dispatch_tool),
        ):
            run_agent(
                _settings(),
                self.root,
                "user",
                "project",
                "prompt",
                get_steers=get_steers,
            )

        self.assertEqual(call_count["n"], 2)
        steer_found = any(
            msg.get("role") == "user" and msg.get("content") == "steer text"
            for msg in captured_messages[1]
        )
        self.assertTrue(steer_found)
        pending = self.store.get_pending_messages(task_id, types=["steer"])
        self.assertEqual(len(pending), 0)

    def test_follow_up_creates_next_turn(self) -> None:
        task_id, conversation_id, _ = self._create_queued_task()
        self.store.add_task_message(
            task_id,
            message_key=f"follow_up:{task_id}:1",
            type="follow_up",
            payload={"prompt": "follow up prompt"},
        )

        def fake_agent(*_args, **_kwargs):
            return "ok"

        task = self._run_with_worker(fake_agent, task_id)
        self.assertEqual(task["status"], "succeeded")

        all_tasks = self.store.list_tasks("user", "project")
        follow_up_task = next(
            (
                t
                for t in all_tasks
                if t.get("conversation_id") == conversation_id
                and t["prompt"] == "follow up prompt"
            ),
            None,
        )
        self.assertIsNotNone(follow_up_task)

    def test_same_project_serial_different_projects_parallel(self) -> None:
        order: list[str] = []
        lock = threading.Lock()

        def make_fake(name: str):
            def fake_agent(*_args, **_kwargs):
                with lock:
                    order.append(name)
                time.sleep(0.05)
                return name

            return fake_agent

        task1, _, _ = self._create_queued_task_for_project("project", "t1")
        task2, _, _ = self._create_queued_task_for_project("project", "t2")
        task3, _, _ = self._create_queued_task_for_project("other", "t3")

        fakes = {
            task1: make_fake("t1"),
            task2: make_fake("t2"),
            task3: make_fake("t3"),
        }

        def dispatch(*args, **kwargs):
            return fakes[kwargs["task_id"]](*args, **kwargs)

        with (
            patch("agent.jobs.load_project_meta", return_value={}),
            patch("agent.jobs.workspace_path", return_value=self.root),
            patch("agent.jobs.user_builds_dir", return_value=self.root / "builds"),
            patch("agent.jobs.snapshot_workspace", return_value={}),
            patch("agent.jobs.compare_snapshots", return_value=([], "")),
            patch("agent.jobs.run_agent", side_effect=dispatch),
        ):
            worker1 = TaskWorker(
                self.store, jobs_mod._run_job, _settings(), poll_interval=0.05
            )
            worker2 = TaskWorker(
                self.store, jobs_mod._run_job, _settings(), poll_interval=0.05
            )
            worker3 = TaskWorker(
                self.store, jobs_mod._run_job, _settings(), poll_interval=0.05
            )
            worker1.start()
            worker2.start()
            worker3.start()
            for task_id in (task1, task2, task3):
                _wait_for_task(self.store, task_id, "user")
            worker1.stop(wait=True, timeout=2.0)
            worker2.stop(wait=True, timeout=2.0)
            worker3.stop(wait=True, timeout=2.0)

        self.assertEqual(self.store.get_task(task1, "user")["status"], "succeeded")
        self.assertEqual(self.store.get_task(task2, "user")["status"], "succeeded")
        self.assertEqual(self.store.get_task(task3, "user")["status"], "succeeded")
        self.assertEqual(len(order), 3)
        # t1 and t2 must not overlap because they are in the same project.
        idx_t1 = order.index("t1")
        idx_t2 = order.index("t2")
        self.assertNotEqual(idx_t1, idx_t2)

    def test_task_messages_not_in_conversation_events(self) -> None:
        task_id, conversation_id, _ = self._create_queued_task()
        self.store.add_task_message(
            task_id,
            message_key=f"steer:{task_id}:1",
            type="steer",
            payload={"text": "x"},
        )
        event_store = ConversationEventStore(self.store)
        conv_events = event_store.list_events(conversation_id, user_id="user")
        self.assertFalse(
            any(e["event_type"] == "steer" for e in conv_events),
        )
        task_messages = self.store.get_pending_messages(task_id)
        self.assertEqual(len(task_messages), 1)

    def test_message_api_and_consumption(self) -> None:
        task_id, _, _ = self._create_queued_task()
        msg = jobs_mod.add_job_message(
            task_id,
            "user",
            message_key=f"steer:{task_id}:api",
            type="steer",
            payload={"text": "from api"},
        )
        self.assertIsNotNone(msg)
        pending = jobs_mod.list_job_messages(task_id, "user")
        self.assertIsNotNone(pending)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["message_key"], f"steer:{task_id}:api")


class WorkerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._workspaces = temp / "workspaces"
        self._builds = temp / "builds"
        self._data = temp / "data"
        self._template = temp / "template"
        self._workspaces.mkdir()
        self._builds.mkdir()
        self._data.mkdir()
        _make_minimal_template(self._template)
        self.patches = [
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
            patch("agent.paths.BUILDS_DIR", self._builds),
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.workspace.DATA_DIR", self._data),
            patch("agent.database.DATA_DIR", self._data),
            patch("agent.paths.TEMPLATE_DIR", self._template),
            patch("agent.project.TEMPLATE_DIR", self._template),
        ]
        for p in self.patches:
            p.start()
        # Keep run_agent patched until tearDown so the background worker does not
        # escape into the real network/model loop after a test body ends.
        self._run_agent_patch = patch("agent.jobs.run_agent", return_value="ok")
        self._run_agent_patch.start()
        self.addCleanup(self._run_agent_patch.stop)
        jobs_mod.stop_worker(wait=True, timeout=2.0)

    def tearDown(self) -> None:
        jobs_mod.stop_worker(wait=True, timeout=2.0)
        for p in reversed(self.patches):
            p.stop()
        self._temp.cleanup()

    def _client(self) -> TestClient:
        return TestClient(create_app(task_store=TaskStore(self._data / "agent.db")))

    def test_ask_persists_user_message_before_return(self) -> None:
        with patch("agent.jobs.run_agent", return_value="ok"):
            with self._client() as client:
                project = client.post("/api/projects", json={"name": "demo"}).json()
                project_id = project["id"]
                resp = client.post(
                    f"/api/projects/{project_id}/ask",
                    json={"prompt": "hello"},
                )
                self.assertEqual(resp.status_code, 200)
                job = resp.json()["job"]
                self.assertIn(job["status"], {"queued", "running"})
                event_store = ConversationEventStore(TaskStore(self._data / "agent.db"))
                turn = event_store.get_turn_by_task(job["id"], user_id="local")
                self.assertIsNotNone(turn)
                events = event_store.list_turn_events(turn["id"], user_id="local")
                self.assertTrue(
                    any(e["event_type"] == "user_message" for e in events)
                )

    def test_message_pause_resume_api(self) -> None:
        # Keep the task queued by preventing the worker from starting.
        with patch("agent.jobs.start_worker"):
            with self._client() as client:
                project = client.post("/api/projects", json={"name": "demo"}).json()
                project_id = project["id"]
                resp = client.post(
                    f"/api/projects/{project_id}/ask",
                    json={"prompt": "hello"},
                )
                job = resp.json()["job"]
                job_id = job["id"]
                resp = client.post(
                    f"/api/jobs/{job_id}/messages",
                    json={
                        "message_key": "steer:1",
                        "type": "steer",
                        "payload": {"text": "x"},
                    },
                )
                self.assertEqual(resp.status_code, 201)
                resp = client.get(f"/api/jobs/{job_id}/messages")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(resp.json()["messages"]), 1)
                resp = client.post(f"/api/jobs/{job_id}/pause")
                self.assertEqual(resp.status_code, 202)
                self.assertEqual(resp.json()["job"]["status"], "paused")
                resp = client.post(f"/api/jobs/{job_id}/resume")
                self.assertEqual(resp.status_code, 202)
                self.assertEqual(resp.json()["job"]["status"], "queued")

    def test_websocket_cursor_reconnect_no_duplicates(self) -> None:
        def fake_agent(*_args, **kwargs):
            on_event = kwargs["on_event"]
            for _ in range(3):
                on_event("progress", {"message": "step"})
            return "ok"

        with patch("agent.jobs.run_agent", side_effect=fake_agent):
            with self._client() as client:
                project = client.post("/api/projects", json={"name": "demo"}).json()
                project_id = project["id"]
                resp = client.post(
                    f"/api/projects/{project_id}/ask",
                    json={"prompt": "hello"},
                )
                job_id = resp.json()["job"]["id"]

                all_ids: list[int] = []
                with client.websocket_connect(f"/api/ws/jobs/{job_id}") as ws:
                    while True:
                        msg = ws.receive_json()
                        if msg.get("type") == "done":
                            break
                        eid = msg.get("id")
                        if isinstance(eid, int):
                            all_ids.append(eid)

                with client.websocket_connect(
                    f"/api/ws/jobs/{job_id}?after_event_id={all_ids[-1]}"
                ) as ws:
                    new_ids: list[int] = []
                    while True:
                        msg = ws.receive_json()
                        if msg.get("type") == "done":
                            break
                        eid = msg.get("id")
                        if isinstance(eid, int):
                            new_ids.append(eid)
                self.assertEqual(new_ids, [])
                self.assertEqual(sorted(set(all_ids)), sorted(all_ids))
