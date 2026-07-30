from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.approvals import resolve_approval
from agent.permissions import classify_risk, decide_permission
from agent.processes import (
    CancelToken,
    CancellationRequested,
    ProcessTimeoutError,
    build_minimal_env,
    run_command as process_run_command,
)
from agent.tool_registry import (
    DuplicateToolError,
    ToolRegistry,
    ToolSpec,
    get_anthropic_tool_definitions,
    get_openai_tool_definitions,
    get_tool_spec,
    list_tool_specs,
    register_tool,
)
from agent.tool_runtime import execute_tool
from agent.tools import ToolResult, dispatch_tool, get_tool_definitions


class ToolRegistryTests(unittest.TestCase):
    def test_register_tool_and_duplicate_rejected(self) -> None:
        registry = ToolRegistry()
        spec = ToolSpec(
            name="test_reg_tool",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=lambda _ctx, _input: ToolResult(True, "ok"),
        )
        self.assertEqual(registry.register(spec), spec)
        with self.assertRaises(DuplicateToolError):
            registry.register(spec)

    def test_get_tool_spec_and_list_specs(self) -> None:
        self.assertIsNotNone(get_tool_spec("read_file"))
        self.assertIsNotNone(get_tool_spec("write_file"))
        self.assertIsNone(get_tool_spec("nonexistent_tool"))
        names = {spec.name for spec in list_tool_specs()}
        self.assertIn("read_file", names)
        self.assertIn("run_command", names)

    def test_openai_and_anthropic_projections_share_registry(self) -> None:
        openai_names = {tool["function"]["name"] for tool in get_openai_tool_definitions()}
        anthropic_names = {tool["name"] for tool in get_anthropic_tool_definitions()}
        self.assertEqual(openai_names, anthropic_names)
        self.assertIn("read_file", openai_names)
        self.assertIn("write_file", openai_names)

    def test_get_tool_definitions_filters_web_search_by_api_key(self) -> None:
        without = get_tool_definitions(SimpleNamespace(max_gradle_retries=3, tavily_api_key=""))
        self.assertFalse(any(t["name"] == "web_search" for t in without))
        self.assertTrue(any(t["name"] == "download_file" for t in without))
        with_key = get_tool_definitions(SimpleNamespace(max_gradle_retries=3, tavily_api_key="tvly-test"))
        self.assertTrue(any(t["name"] == "web_search" for t in with_key))


class RiskClassificationTests(unittest.TestCase):
    def test_builtin_tool_risk_levels(self) -> None:
        self.assertEqual(classify_risk(get_tool_spec("read_file")), "read")
        self.assertEqual(classify_risk(get_tool_spec("list_dir")), "read")
        self.assertEqual(classify_risk(get_tool_spec("write_file")), "workspace_write")
        self.assertEqual(classify_risk(get_tool_spec("str_replace")), "workspace_write")
        self.assertEqual(classify_risk(get_tool_spec("web_search")), "network")
        self.assertEqual(classify_risk(get_tool_spec("download_file")), "destructive")
        self.assertEqual(classify_risk(get_tool_spec("run_command")), "destructive")
        self.assertEqual(classify_risk(get_tool_spec("run_gradle")), "process")


class PermissionModelTests(unittest.TestCase):
    def test_read_only_mode(self) -> None:
        read_spec = get_tool_spec("read_file")
        write_spec = get_tool_spec("write_file")
        network_spec = get_tool_spec("web_search")

        self.assertTrue(decide_permission(read_spec, "read_only").allow)
        self.assertTrue(decide_permission(write_spec, "read_only").deny)
        self.assertTrue(decide_permission(network_spec, "read_only").deny)

    def test_workspace_mode(self) -> None:
        read_spec = get_tool_spec("read_file")
        write_spec = get_tool_spec("write_file")
        download_spec = get_tool_spec("download_file")
        run_spec = get_tool_spec("run_command")

        self.assertTrue(decide_permission(read_spec, "workspace").allow)
        self.assertTrue(decide_permission(write_spec, "workspace").allow)
        self.assertTrue(decide_permission(download_spec, "workspace").ask)
        self.assertTrue(decide_permission(run_spec, "workspace").ask)

    def test_ask_mode(self) -> None:
        read_spec = get_tool_spec("read_file")
        write_spec = get_tool_spec("write_file")  # no approval_kind -> deny
        download_spec = get_tool_spec("download_file")
        run_spec = get_tool_spec("run_command")

        self.assertTrue(decide_permission(read_spec, "ask").allow)
        self.assertTrue(decide_permission(write_spec, "ask").deny)
        self.assertTrue(decide_permission(download_spec, "ask").ask)
        self.assertTrue(decide_permission(run_spec, "ask").ask)

    def test_custom_tool_with_approval_kind_asks_in_ask_mode(self) -> None:
        spec = ToolSpec(
            name="custom_ask_write",
            description="custom",
            input_schema={"type": "object", "properties": {}},
            workspace_write=True,
            approval_kind="workspace_write",
            handler=lambda _ctx, _input: ToolResult(True, "ok"),
        )
        self.assertEqual(decide_permission(spec, "workspace").action, "ask")
        self.assertEqual(decide_permission(spec, "ask").action, "ask")
        self.assertEqual(decide_permission(spec, "read_only").action, "deny")


class RuntimeExecutionTests(unittest.TestCase):
    def _approval_event_handler(self, approvals: dict) -> callable:
        def handler(event_type: str, payload: dict) -> None:
            if event_type == "approval_required":
                approvals["required"] = payload
                resolve_approval(payload["approval_id"], "user", approved=True)
            if event_type == "approval_resolved":
                approvals["resolved"] = payload
        return handler

    def test_deny_in_read_only_does_not_execute_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "app" / "src" / "main" / "java" / "Demo.kt"
            target.parent.mkdir(parents=True)
            result = dispatch_tool(
                root,
                "user",
                "project",
                "write_file",
                {"path": "app/src/main/java/Demo.kt", "content": "x"},
                run_mode="read_only",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "PermissionDenied")
        self.assertFalse(target.exists())

    def test_ask_mode_runs_approval_event_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            approvals: dict = {}
            result = execute_tool(
                root,
                "user",
                "project",
                "run_command",
                {"argv": ["python3", "-c", "print('ok')"]},
                task_id="task-1",
                tool_call_id="call-1",
                on_event=self._approval_event_handler(approvals),
                set_status=lambda _s: None,
                run_mode="workspace",
            )
        self.assertTrue(result.ok)
        self.assertEqual(approvals["required"]["kind"], "process")
        self.assertEqual(approvals["resolved"]["decision"], "approved")
        self.assertIn("ok", str(result.output))

    def test_run_command_success_failure_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            approvals: dict = {}

            # success
            result = execute_tool(
                root,
                "user",
                "project",
                "run_command",
                {"argv": ["python3", "-c", "print('hello')"]},
                task_id="task-1",
                tool_call_id="call-ok",
                on_event=self._approval_event_handler(approvals),
                set_status=lambda _s: None,
                run_mode="workspace",
            )
            self.assertTrue(result.ok)
            self.assertIn("hello", str(result.output))

            # failure
            result = execute_tool(
                root,
                "user",
                "project",
                "run_command",
                {"argv": ["python3", "-c", "import sys; sys.exit(1)"]},
                task_id="task-1",
                tool_call_id="call-fail",
                on_event=self._approval_event_handler(approvals),
                set_status=lambda _s: None,
                run_mode="workspace",
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_type, "NonZeroExitCode")

            # timeout
            result = execute_tool(
                root,
                "user",
                "project",
                "run_command",
                {
                    "argv": ["python3", "-c", "import time; time.sleep(10)"],
                    "timeout_seconds": 0.5,
                },
                task_id="task-1",
                tool_call_id="call-timeout",
                on_event=self._approval_event_handler(approvals),
                set_status=lambda _s: None,
                run_mode="workspace",
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_type, "Timeout")

    def test_handler_exception_returns_error_type(self) -> None:
        def raising_handler(_ctx, _input):
            raise ValueError("forced failure")

        spec = ToolSpec(
            name="_test_exception_tool",
            description="test",
            input_schema={"type": "object", "properties": {}},
            read_only=True,
            handler=raising_handler,
        )
        register_tool(spec)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = dispatch_tool(
                root,
                "user",
                "project",
                "_test_exception_tool",
                {},
                run_mode="read_only",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "ValueError")


class ProcessRunnerTests(unittest.TestCase):
    def test_run_command_captures_output_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = process_run_command(
                ["python3", "-c", "print('stdout'); print('stderr', file=__import__('sys').stderr)"],
                cwd=root,
                workspace=root,
                combine_output=False,
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.returncode, 0)
            self.assertIn("stdout", result.stdout)
            self.assertIn("stderr", result.stderr)

    def test_run_command_cancels_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = CancelToken()

            def cancel_after_start() -> None:
                time.sleep(0.5)
                token.cancel()

            threading.Thread(target=cancel_after_start, daemon=True).start()
            started = time.monotonic()
            result = process_run_command(
                ["python3", "-c", "import time; time.sleep(10)"],
                cwd=root,
                workspace=root,
                cancel_token=token,
                timeout_seconds=10,
            )
            elapsed = time.monotonic() - started
            self.assertFalse(result.ok)
            self.assertEqual(result.error_type, "CancellationRequested")
            self.assertLess(elapsed, 5)

    def test_environment_does_not_leak_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = (
                "import os, json; "
                "print(json.dumps({k: os.environ.get(k) for k in os.environ}))"
            )
            with patch.dict(os.environ, {"AGENT_API_KEY": "secret123", "PATH": os.environ.get("PATH", "")}):
                result = process_run_command(
                    ["python3", "-c", script],
                    cwd=root,
                    workspace=root,
                )
            self.assertTrue(result.ok)
            self.assertNotIn("AGENT_API_KEY", result.stdout)
            self.assertNotIn("secret123", result.stdout)

    def test_cwd_must_stay_in_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            other = Path(temp) / ".."
            with self.assertRaises(PermissionError):
                process_run_command(
                    ["python3", "-c", "print(1)"],
                    cwd=other,
                    workspace=root,
                )

    def test_build_minimal_env_filters_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENT_API_KEY": "secret",
                "TAVILY_API_KEY": "tvly-secret",
                "PATH": "/usr/bin",
                "HOME": "/home/user",
            },
        ):
            env = build_minimal_env()
            self.assertNotIn("AGENT_API_KEY", env)
            self.assertNotIn("TAVILY_API_KEY", env)
            self.assertIn("PATH", env)
            self.assertIn("HOME", env)


if __name__ == "__main__":
    unittest.main()
