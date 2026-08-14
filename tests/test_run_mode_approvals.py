from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pydantic
from fastapi.testclient import TestClient

from agent import jobs as jobs_mod
from agent.api import AskRequest, ConversationAskRequest, create_app
from agent.approvals import resolve_approval
from agent.config import Settings
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.permissions import PermissionDecision
from agent.tool_registry import ToolSpec, get_tool_spec
from agent.tool_runtime import _build_approval_payload, execute_tool
from agent.tools import ToolResult
from agent.users import UserStore


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider="openai",
        api_key="fake-key",
        model="fake-model",
        model_candidates=["fake-model"],
        max_turns=2,
        max_auto_continuations=0,
        max_gradle_retries=2,
        compact_max_chars=100_000,
        max_output_tokens=4096,
        base_url=None,
        auto_build_after_edit=False,
        provider_fallbacks=[],
    )


def _api_settings() -> Settings:
    return Settings(
        provider="openai",
        api_key="fake-key",
        model="fake-model",
        model_candidates=["fake-model"],
        max_turns=2,
        max_auto_continuations=0,
        max_gradle_retries=2,
        compact_max_chars=100_000,
        max_output_tokens=4096,
        base_url=None,
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
        users=[],
        provider_fallbacks=[],
    )


class RunModeRequestValidationTests(unittest.TestCase):
    def test_ask_request_accepts_valid_run_mode(self) -> None:
        for mode in ("read_only", "workspace", "ask"):
            req = AskRequest(prompt="hi", run_mode=mode)
            self.assertEqual(req.run_mode, mode)
        self.assertIsNone(AskRequest(prompt="hi").run_mode)

    def test_ask_request_rejects_invalid_run_mode(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            AskRequest(prompt="hi", run_mode="yolo")
        with self.assertRaises(pydantic.ValidationError):
            AskRequest(prompt="hi", run_mode="full_access")

    def test_conversation_ask_request_run_mode(self) -> None:
        req = ConversationAskRequest(prompt="hi", run_mode="ask")
        self.assertEqual(req.run_mode, "ask")
        with self.assertRaises(pydantic.ValidationError):
            ConversationAskRequest(prompt="hi", run_mode="everything")

    def test_unknown_fields_still_forbidden(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            AskRequest(prompt="hi", allowlist=["npm"])


class RunModeApiPassthroughTests(unittest.TestCase):
    """The ask endpoints must forward run_mode to start_ask_job."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.task_store = TaskStore(root / "tasks.db")
        self.user_store = UserStore(root / "users.db")
        self.alice, self.alice_token = self.user_store.register()
        self.conversation = self.task_store.create_conversation(
            self.alice, "project", title="对话"
        )
        self.client = TestClient(
            create_app(
                settings=_api_settings(),
                user_store=self.user_store,
                task_store=self.task_store,
            )
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.alice_token}"}

    def _fake_job(self) -> dict:
        return {
            "id": "job1",
            "conversation_id": self.conversation["id"],
            "status": "queued",
            "provider": "fake",
            "model": "fake",
            "created_at": time.time(),
            "final_message": None,
            "error_message": None,
        }

    def test_conversation_ask_forwards_run_mode(self) -> None:
        with (
            patch("agent.api.load_project_meta", return_value={}),
            patch("agent.api.start_ask_job", return_value=self._fake_job()) as start,
            patch("agent.api.get_conversation", return_value=dict(self.conversation)),
        ):
            resp = self.client.post(
                f"/api/conversations/{self.conversation['id']}/ask",
                json={"prompt": "hello", "run_mode": "read_only"},
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(start.call_args.kwargs.get("run_mode"), "read_only")

    def test_project_ask_forwards_run_mode(self) -> None:
        with (
            patch("agent.api.load_project_meta", return_value={}),
            patch("agent.api.start_ask_job", return_value=self._fake_job()) as start,
        ):
            resp = self.client.post(
                "/api/projects/project/ask",
                json={"prompt": "hello", "run_mode": "ask"},
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(start.call_args.kwargs.get("run_mode"), "ask")

    def test_invalid_run_mode_rejected_by_api(self) -> None:
        resp = self.client.post(
            f"/api/conversations/{self.conversation['id']}/ask",
            json={"prompt": "hello", "run_mode": "full_access"},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_omitted_run_mode_defaults_to_none(self) -> None:
        with (
            patch("agent.api.load_project_meta", return_value={}),
            patch("agent.api.start_ask_job", return_value=self._fake_job()) as start,
            patch("agent.api.get_conversation", return_value=dict(self.conversation)),
        ):
            resp = self.client.post(
                f"/api/conversations/{self.conversation['id']}/ask",
                json={"prompt": "hello"},
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(start.call_args.kwargs.get("run_mode"))


class RunModeJobChainTests(unittest.TestCase):
    """run_mode must survive start_ask_job → task context → _run_job → run_agent."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.store = TaskStore(self.root / "tasks.db")
        jobs_mod.stop_worker(wait=True, timeout=2.0)

    def tearDown(self) -> None:
        jobs_mod.stop_worker(wait=True, timeout=2.0)
        self._temp.cleanup()

    def _job_patches(self, fake_agent):
        builds = self.root / "builds"
        builds.mkdir(exist_ok=True)
        return (
            patch.object(jobs_mod, "_store", self.store),
            patch("agent.jobs.load_project_meta", return_value={}),
            patch("agent.jobs.workspace_path", return_value=self.root),
            patch("agent.jobs.user_builds_dir", return_value=builds),
            patch("agent.jobs.snapshot_workspace", return_value={}),
            patch("agent.jobs.compare_snapshots", return_value=([], "")),
            patch("agent.jobs.run_agent", side_effect=fake_agent),
            patch("agent.jobs.start_worker"),
        )

    def test_start_ask_job_records_and_exposes_run_mode(self) -> None:
        captured: dict = {}

        def fake_agent(*_args, **kwargs):
            captured.update(kwargs)
            return "done"

        patches = self._job_patches(fake_agent)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            job = jobs_mod.start_ask_job(
                "user",
                "project",
                "做个页面",
                _settings(),
                run_mode="read_only",
            )
        task = self.store.get_task(job["id"], "user")
        self.assertEqual(task["context"].get("run_mode"), "read_only")
        self.assertEqual(jobs_mod.job_to_dict(task)["run_mode"], "read_only")

    def test_start_ask_job_defaults_to_workspace(self) -> None:
        patches = self._job_patches(lambda *a, **k: "done")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            job = jobs_mod.start_ask_job("user", "project", "做个页面", _settings())
        task = self.store.get_task(job["id"], "user")
        self.assertEqual(task["context"].get("run_mode"), "workspace")
        self.assertEqual(jobs_mod.job_to_dict(task)["run_mode"], "workspace")

    def test_start_ask_job_rejects_invalid_run_mode(self) -> None:
        patches = self._job_patches(lambda *a, **k: "done")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            with self.assertRaises(RuntimeError):
                jobs_mod.start_ask_job(
                    "user", "project", "做个页面", _settings(), run_mode="full_access"
                )

    def test_run_job_passes_run_mode_to_run_agent(self) -> None:
        captured: dict = {}

        def fake_agent(*_args, **kwargs):
            captured.update(kwargs)
            return "done"

        conversation = self.store.create_conversation("user", "project", title="c")
        self.store.create_task(
            {
                "id": "task-rm",
                "user_id": "user",
                "project_id": "project",
                "conversation_id": conversation["id"],
                "prompt": "p",
                "status": "queued",
                "provider": "openai",
                "model": "fake-model",
                "created_at": time.time(),
            }
        )
        event_store = ConversationEventStore(self.store)
        turn = event_store.create_turn(
            conversation["id"], "user", "project", task_id="task-rm", status="queued"
        )
        patches = self._job_patches(fake_agent)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            jobs_mod._run_job(
                "task-rm",
                "user",
                "project",
                conversation["id"],
                turn["id"],
                "p",
                _settings(),
                [],
                0,
                run_mode="ask",
            )
        self.assertEqual(captured.get("run_mode"), "ask")


class ApprovalPayloadTests(unittest.TestCase):
    """Approval payloads must carry server-verified structured fields."""

    def _auto_approve(self, sink: dict):
        def handler(event_type: str, payload: dict) -> None:
            if event_type == "approval_required":
                sink["required"] = payload
                resolve_approval(payload["approval_id"], "user", approved=True)
            if event_type == "approval_resolved":
                sink["resolved"] = payload

        return handler

    def test_run_command_payload_has_command_cwd_and_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "app").mkdir()
            sink: dict = {}
            # NB: process spawn may be blocked in sandboxed CI; the approval
            # payload is emitted before execution, so we assert on it rather
            # than on the final exit code.
            execute_tool(
                Path(temp),
                "user",
                "project",
                "run_command",
                {"argv": ["python3", "-c", "print(1)"], "cwd": "app"},
                task_id="task-1",
                tool_call_id="call-1",
                on_event=self._auto_approve(sink),
                set_status=lambda _s: None,
                run_mode="workspace",
            )
        payload = sink["required"]
        self.assertEqual(payload["risk"], "destructive")
        self.assertEqual(payload["requested_capability"], "process")
        self.assertEqual(payload["tool_name"], "run_command")
        self.assertEqual(payload["command"], "python3 -c print(1)")
        self.assertEqual(payload["argv"], ["python3", "-c", "print(1)"])
        self.assertEqual(payload["cwd"], "app")
        self.assertTrue(payload["reason"])
        self.assertEqual(payload["tool_call_id"], "call-1")

    def test_download_file_payload_fields(self) -> None:
        spec = get_tool_spec("download_file")
        decision = PermissionDecision(
            action="ask",
            reason="workspace 模式下网络、进程或破坏性操作需要审批",
            matched_rule="workspace:risk_ask",
            approval_kind="download_file",
            risk="destructive",
        )
        payload = _build_approval_payload(
            decision,
            {"url": "https://example.com/a.png", "path": "downloads/a.png"},
            spec,
        )
        self.assertEqual(payload["url"], "https://example.com/a.png")
        self.assertEqual(payload["path"], "downloads/a.png")
        self.assertEqual(payload["max_bytes"], 50 * 1024 * 1024)
        self.assertEqual(payload["domains"], ["example.com"])
        self.assertEqual(payload["target_paths"], ["downloads/a.png"])
        self.assertEqual(payload["risk"], "destructive")
        self.assertEqual(payload["requested_capability"], "download_file")

    def test_mcp_tool_payload_fields(self) -> None:
        spec = ToolSpec(
            name="mcp__shop__create_order",
            description="[MCP:shop] create order",
            input_schema={"type": "object", "properties": {}},
            network_access=True,
            starts_process=True,
            approval_kind="mcp_tool",
        )
        decision = PermissionDecision(
            action="ask",
            reason="ask 模式下风险操作需要审批",
            matched_rule="ask:risk_ask",
            approval_kind="mcp_tool",
            risk="network",
        )
        payload = _build_approval_payload(decision, {"sku": "1"}, spec)
        self.assertEqual(payload["mcp_server"], "shop")
        self.assertEqual(payload["mcp_tool"], "create_order")
        self.assertEqual(payload["input"], {"sku": "1"})
        self.assertEqual(payload["requested_capability"], "mcp_tool")

    def test_recovery_replay_payload_fields(self) -> None:
        spec = get_tool_spec("write_file")
        decision = PermissionDecision(
            action="ask",
            reason="恢复任务中重放有副作用的工具调用需要重新确认",
            matched_rule="recovery_replay",
            approval_kind="recovery_tool_replay",
            risk="workspace_write",
        )
        payload = _build_approval_payload(
            decision,
            {"path": "app/x.kt", "content": "x"},
            spec,
            recovery_tool_call_id="call-old",
        )
        self.assertEqual(payload["interrupted_tool_call_id"], "call-old")
        self.assertEqual(payload["input"]["path"], "app/x.kt")
        self.assertEqual(payload["requested_capability"], "recovery_tool_replay")
        self.assertTrue(payload["reason"])

    def test_reject_keeps_tool_result_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sink: dict = {}

            def handler(event_type: str, payload: dict) -> None:
                if event_type == "approval_required":
                    sink["required"] = payload
                    resolve_approval(payload["approval_id"], "user", approved=False)

            result = execute_tool(
                Path(temp),
                "user",
                "project",
                "run_command",
                {"argv": ["python3", "-c", "print(1)"]},
                task_id="task-1",
                tool_call_id="call-2",
                on_event=handler,
                set_status=lambda _s: None,
                run_mode="workspace",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "ApprovalDenied")


if __name__ == "__main__":
    unittest.main()
