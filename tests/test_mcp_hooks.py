from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.approvals import resolve_approval
from agent.config import Settings
from agent.database import TaskStore
from agent.hooks import (
    HookDecision,
    combine_with_permission,
    merge_decisions,
    run_hooks,
)
from agent.mcp_client import (
    McpTimeoutError,
    McpTransportError,
    StdioMcpTransport,
    create_transport,
)
from agent.mcp_config import (
    McpServerConfig,
    is_project_mcp_trusted,
    project_mcp_config_path,
    public_env_preview,
    resolve_env_secrets,
    trust_project_mcp,
    user_mcp_config_path,
)
from agent.mcp_manager import (
    get_mcp_manager,
    mcp_tool_name,
    reset_mcp_managers,
)
from agent.permissions import PermissionDecision, decide_permission
from agent.project import init_project
from agent.tool_registry import (
    clear_dynamic_tools,
    get_tool_spec,
    list_builtin_tool_specs,
    list_tool_specs,
)
from agent.tools import dispatch_tool
from agent.users import UserStore


FAKE_SERVER = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"


def _settings() -> Settings:
    return Settings(
        provider="openai",
        api_key="fake",
        model="fake",
        model_candidates=["fake"],
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


class McpHooksFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._data = temp / "data"
        self._workspaces = temp / "workspaces"
        self._data.mkdir()
        self._workspaces.mkdir()
        self.workspace = temp / "ws"
        self.workspace.mkdir()
        self.user_id = "mcp_user"
        self.project_id = "mcp_proj"
        self.patches = [
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
            patch("agent.database.DATA_DIR", self._data),
        ]
        for p in self.patches:
            p.start()
        reset_mcp_managers()
        clear_dynamic_tools(prefix="mcp__")

    def tearDown(self) -> None:
        reset_mcp_managers()
        clear_dynamic_tools(prefix="mcp__")
        for p in reversed(self.patches):
            p.stop()
        self._temp.cleanup()

    def _stdio_config(self, name: str = "fake", **env) -> McpServerConfig:
        return McpServerConfig(
            name=name,
            transport="stdio",
            command=sys.executable,
            args=[str(FAKE_SERVER)],
            env_refs=dict(env),
            enabled=True,
            timeout_seconds=5.0,
            scope="user",
        )


class FakeStdioMcpTests(McpHooksFixture):
    def test_initialize_list_and_call(self) -> None:
        transport = StdioMcpTransport(self._stdio_config(), workspace=self.workspace)
        info = transport.start()
        self.assertIn("capabilities", info)
        tools = transport.list_tools()
        names = {t.name for t in tools}
        self.assertEqual(names, {"echo", "add"})
        result = transport.call_tool("echo", {"message": "hi"})
        self.assertTrue(result.ok)
        self.assertIn("hi", json.dumps(result.content))
        add = transport.call_tool("add", {"a": 2, "b": 3})
        self.assertTrue(add.ok)
        self.assertIn("5", json.dumps(add.content))
        transport.close()
        self.assertFalse(transport.healthy())

    def test_schema_refresh_adds_tool(self) -> None:
        cfg = self._stdio_config(FAKE_MCP_TOOLS_VERSION="1")
        transport = StdioMcpTransport(cfg, workspace=self.workspace)
        transport.start()
        self.assertEqual({t.name for t in transport.list_tools()}, {"echo", "add"})
        transport.close()

        cfg2 = self._stdio_config(FAKE_MCP_TOOLS_VERSION="2")
        transport2 = StdioMcpTransport(cfg2, workspace=self.workspace)
        transport2.start()
        self.assertIn("ping", {t.name for t in transport2.list_tools()})
        transport2.close()

    def test_timeout(self) -> None:
        cfg = self._stdio_config(FAKE_MCP_MODE="timeout")
        cfg.timeout_seconds = 0.5
        transport = StdioMcpTransport(cfg, workspace=self.workspace)
        transport.start()
        with self.assertRaises(McpTimeoutError):
            transport.call_tool("echo", {"message": "x"}, timeout=0.4)
        transport.close()

    def test_crash_and_reconnect_via_manager(self) -> None:
        user_cfg = {
            "mcpServers": {
                "fake": {
                    "command": sys.executable,
                    "args": [str(FAKE_SERVER)],
                    "env": {"FAKE_MCP_MODE": "normal"},
                    "timeout_seconds": 5,
                }
            }
        }
        path = user_mcp_config_path(self.user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(user_cfg), encoding="utf-8")

        events: list[tuple[str, dict]] = []

        def on_event(etype: str, payload: dict) -> None:
            events.append((etype, payload))

        mgr = get_mcp_manager(
            self.user_id, self.project_id, self.workspace, on_event=on_event
        )
        state = mgr.start_server("fake")
        self.assertEqual(state["status"], "ready")
        self.assertTrue(get_tool_spec("mcp__fake__echo"))

        # Force crash mode on reconnect
        mgr._servers["fake"].config.env_refs["FAKE_MCP_MODE"] = "crash"  # noqa: SLF001
        # First call with crash server after reconnect should fail start or call
        mgr._servers["fake"].config.env_refs["FAKE_MCP_MODE"] = "normal"  # noqa: SLF001
        re = mgr.reconnect("fake")
        self.assertEqual(re["status"], "ready")
        self.assertGreaterEqual(re["reconnect_attempts"], 1)

        result = mgr.call_tool("fake", "echo", {"message": "ok"})
        self.assertTrue(result.ok)
        self.assertTrue(any(t == "mcp_status" for t, _ in events))

    def test_duplicate_namespaced_tools_stable(self) -> None:
        self.assertEqual(mcp_tool_name("svc", "echo"), "mcp__svc__echo")
        self.assertNotEqual(mcp_tool_name("a", "t"), mcp_tool_name("b", "t"))

    def test_project_trust_required(self) -> None:
        project_mcp_config_path(self.workspace).parent.mkdir(parents=True)
        project_mcp_config_path(self.workspace).write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "proj": {
                            "command": sys.executable,
                            "args": [str(FAKE_SERVER)],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.assertFalse(
            is_project_mcp_trusted(self.user_id, self.project_id, self.workspace)
        )
        mgr = get_mcp_manager(self.user_id, self.project_id, self.workspace)
        mgr.reload_configs()
        servers = {s["name"]: s for s in mgr.list_servers()}
        self.assertEqual(servers["proj"]["status"], "untrusted")
        started = mgr.start_server("proj")
        self.assertEqual(started["status"], "untrusted")

        trust_project_mcp(self.user_id, self.project_id, self.workspace)
        mgr.reload_configs()
        ready = mgr.start_server("proj")
        self.assertEqual(ready["status"], "ready")

    def test_secret_not_in_public_config_or_events(self) -> None:
        os.environ["MCP_TEST_SECRET"] = "sk-super-secret-value-xyz"
        try:
            refs = {"TOKEN": "${MCP_TEST_SECRET}"}
            resolved = resolve_env_secrets(refs)
            self.assertEqual(resolved["TOKEN"], "sk-super-secret-value-xyz")
            preview = public_env_preview(refs)
            self.assertNotIn("sk-super-secret-value-xyz", json.dumps(preview))

            events: list[tuple[str, dict]] = []

            def on_event(etype: str, payload: dict) -> None:
                events.append((etype, payload))

            cfg_path = user_mcp_config_path(self.user_id)
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "sec": {
                                "command": sys.executable,
                                "args": [str(FAKE_SERVER)],
                                "env": {
                                    "FAKE_MCP_MODE": "secret",
                                    "FAKE_MCP_SECRET": "${MCP_TEST_SECRET}",
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            mgr = get_mcp_manager(
                self.user_id, self.project_id, self.workspace, on_event=on_event
            )
            public = mgr.start_server("sec")
            blob = json.dumps(public)
            self.assertNotIn("sk-super-secret-value-xyz", blob)
            for _, payload in events:
                self.assertNotIn("sk-super-secret-value-xyz", json.dumps(payload))
        finally:
            os.environ.pop("MCP_TEST_SECRET", None)

    def test_mcp_tool_goes_through_permission_approval(self) -> None:
        cfg_path = user_mcp_config_path(self.user_id)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fake": {
                            "command": sys.executable,
                            "args": [str(FAKE_SERVER)],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        events: list[tuple[str, dict]] = []

        def on_event(etype: str, payload: dict) -> None:
            events.append((etype, payload))
            if etype == "approval_required":
                resolve_approval(payload["approval_id"], self.user_id, approved=True)

        mgr = get_mcp_manager(
            self.user_id, self.project_id, self.workspace, on_event=on_event
        )
        mgr.start_server("fake")
        spec = get_tool_spec("mcp__fake__echo")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.approval_kind, "mcp_tool")
        decision = decide_permission(spec, "workspace")
        self.assertTrue(decision.ask)

        result = dispatch_tool(
            self.workspace,
            self.user_id,
            self.project_id,
            "mcp__fake__echo",
            {"message": "approved"},
            on_event=on_event,
            task_id="task1",
            tool_call_id="call1",
        )
        self.assertTrue(result.ok)
        self.assertIn("approved", json.dumps(result.output))

    def test_streamable_http_stub(self) -> None:
        cfg = McpServerConfig(
            name="httpish",
            transport="streamable_http",
            url="http://127.0.0.1:9/mcp",
        )
        with self.assertRaises(McpTransportError):
            create_transport(cfg)


class HookDecisionTests(McpHooksFixture):
    def test_priority_deny_over_ask_over_allow(self) -> None:
        merged = merge_decisions(
            [
                HookDecision(action="allow"),
                HookDecision(action="ask"),
                HookDecision(action="deny", reason="blocked"),
            ]
        )
        self.assertEqual(merged.action, "deny")

    def test_hook_cannot_weaken_permission_deny(self) -> None:
        permission = PermissionDecision(
            action="deny",
            reason="hard deny",
            matched_rule="workspace:risk_deny",
            risk="destructive",
        )
        hook = HookDecision(action="allow")
        combined = combine_with_permission(permission, hook)
        self.assertTrue(combined.deny)

    def test_input_modification_blocks_path_escape(self) -> None:
        hooks_path = self._data / "users" / self.user_id / "hooks.json"
        hooks_path.parent.mkdir(parents=True)
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": [
                        {
                            "event": "PreToolUse",
                            "matcher": "read_file",
                            "decision": "allow",
                            "modify_input": {"path": "../etc/passwd"},
                        },
                        {
                            "event": "PreToolUse",
                            "matcher": "read_file",
                            "decision": "allow",
                            "modify_input": {"path": "app/src/main/AndroidManifest.xml"},
                            "append_context": "hook-note",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        # Create a readable file
        target = self.workspace / "app" / "src" / "main" / "AndroidManifest.xml"
        target.parent.mkdir(parents=True)
        target.write_text("<manifest/>", encoding="utf-8")

        decision = run_hooks(
            "PreToolUse",
            user_id=self.user_id,
            workspace=self.workspace,
            tool_name="read_file",
            tool_input={"path": "app/src/main/AndroidManifest.xml"},
            execute_actions=False,
        )
        self.assertEqual(
            decision.modified_input.get("path"),
            "app/src/main/AndroidManifest.xml",
        )
        self.assertIn("hook-note", decision.append_context)

    def test_pre_tool_deny(self) -> None:
        hooks_path = self._data / "users" / self.user_id / "hooks.json"
        hooks_path.parent.mkdir(parents=True)
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": [
                        {
                            "id": "deny-write",
                            "event": "PreToolUse",
                            "matcher": "write_file",
                            "decision": "deny",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = dispatch_tool(
            self.workspace,
            self.user_id,
            self.project_id,
            "write_file",
            {
                "path": "app/src/main/res/values/strings.xml",
                "content": "x",
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "HookDenied")

    def test_builtin_tools_still_registered(self) -> None:
        names = {t.name for t in list_builtin_tool_specs()}
        self.assertIn("read_file", names)
        self.assertIn("write_file", names)
        self.assertIn("run_gradle", names)
        # Dynamic MCP tools are separate
        clear_dynamic_tools()
        self.assertTrue(any(t.name == "read_file" for t in list_tool_specs()))


class McpApiTests(McpHooksFixture):
    def setUp(self) -> None:
        super().setUp()
        template = Path(self._temp.name) / "template"
        java = template / "app" / "src" / "main" / "java" / "com" / "example" / "t"
        java.mkdir(parents=True)
        (java / "MainActivity.kt").write_text("class MainActivity {}", encoding="utf-8")
        res = template / "app" / "src" / "main" / "res" / "layout"
        res.mkdir(parents=True)
        (res / "activity_main.xml").write_text("<LinearLayout/>", encoding="utf-8")
        (template / "app" / "build.gradle.kts").write_text("plugins {}", encoding="utf-8")
        (template / "build.gradle.kts").write_text("//", encoding="utf-8")
        (template / "settings.gradle.kts").write_text("//", encoding="utf-8")
        (template / "gradle" / "wrapper").mkdir(parents=True)
        (template / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
            "x", encoding="utf-8"
        )
        extra = [
            patch("agent.paths.TEMPLATE_DIR", template),
            patch("agent.project.TEMPLATE_DIR", template),
        ]
        for p in extra:
            p.start()
            self.patches.append(p)

        self.user_store = UserStore(self._data / "users.db")
        self.uid, self.token = self.user_store.register()
        store = TaskStore(self._data / "agent.db")
        from agent import jobs

        jobs.configure_task_store(store)
        self.client = TestClient(
            create_app(settings=_settings(), user_store=self.user_store, task_store=store)
        )
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.project_id = init_project(
            "mcp-api", package="com.example.mcp", user_id=self.uid
        )
        self.ws = self._workspaces / self.uid / self.project_id
        cfg = user_mcp_config_path(self.uid)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fake": {
                            "command": sys.executable,
                            "args": [str(FAKE_SERVER)],
                            "env": {"TOKEN": "sk-should-not-leak-abcdef"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_list_enable_reconnect_no_secrets(self) -> None:
        r = self.client.get(
            f"/api/projects/{self.project_id}/mcp/servers",
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(any(s["name"] == "fake" for s in body["servers"]))
        self.assertNotIn("sk-should-not-leak-abcdef", r.text)

        en = self.client.post(
            f"/api/projects/{self.project_id}/mcp/servers/fake/enable",
            headers=self.headers,
            json={"enabled": True},
        )
        self.assertEqual(en.status_code, 200, en.text)
        self.assertEqual(en.json()["server"]["status"], "ready")
        self.assertNotIn("sk-should-not-leak-abcdef", en.text)

        tools = self.client.get(
            f"/api/projects/{self.project_id}/mcp/tools",
            headers=self.headers,
        )
        self.assertEqual(tools.status_code, 200)
        names = {t["namespaced"] for t in tools.json()["tools"]}
        self.assertIn("mcp__fake__echo", names)

        recon = self.client.post(
            f"/api/projects/{self.project_id}/mcp/servers/fake/reconnect",
            headers=self.headers,
        )
        self.assertEqual(recon.status_code, 200)
        self.assertEqual(recon.json()["server"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
