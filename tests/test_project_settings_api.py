from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.memory_store import get_memory_store
from agent.permissions import decide_permission
from agent.project import init_project
from agent.project_settings import get_permission_profile
from agent.prompts import build_system_prompt
from agent.rules import load_rules_for_turn
from agent.skills import list_skills, load_skill
from agent.tool_registry import get_tool_spec
from agent.users import UserStore


def _make_minimal_template(root: Path) -> None:
    java_dir = root / "app" / "src" / "main" / "java" / "com" / "example" / "template"
    java_dir.mkdir(parents=True)
    (java_dir / "MainActivity.kt").write_text(
        "package com.example.template\nclass MainActivity {}", encoding="utf-8"
    )
    (root / "app" / "build.gradle.kts").write_text("plugins {}", encoding="utf-8")
    (root / "build.gradle.kts").write_text("// root", encoding="utf-8")
    (root / "settings.gradle.kts").write_text("// settings", encoding="utf-8")


def _api_settings() -> Settings:
    return Settings(
        provider="openai",
        api_key="fake",
        model="fake",
        model_candidates=["fake"],
        max_turns=2,
        max_auto_continuations=0,
        max_gradle_retries=1,
        compact_max_chars=50_000,
        max_output_tokens=1024,
        base_url="https://example.test",
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
        tavily_api_key="",
        users=[],
        provider_fallbacks=[],
    )


class ProjectSettingsApiFixture:
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._template_copy = temp / "template"
        _make_minimal_template(self._template_copy)
        self._workspaces = temp / "workspaces"
        self._data = temp / "data"
        self._workspaces.mkdir()
        self._data.mkdir()
        self.user_store = UserStore(self._data / "users.db")
        self.task_store = TaskStore(self._data / "tasks.db")
        self.user_id, self.token = self.user_store.register()
        self.patches = [
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
            patch("agent.paths.BUILDS_DIR", temp / "builds"),
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.workspace.DATA_DIR", self._data),
            patch("agent.terminal.DATA_DIR", self._data),
            patch("agent.database.DATA_DIR", self._data),
            patch("agent.paths.TEMPLATE_DIR", self._template_copy),
            patch("agent.project.TEMPLATE_DIR", self._template_copy),
        ]
        for p in self.patches:
            p.start()
        self.project_id = init_project(
            "cfg", package="com.example.cfg", user_id=self.user_id
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
        for p in reversed(self.patches):
            p.stop()
        self._temp.cleanup()

    @property
    def workspace(self) -> Path:
        return self._workspaces / self.user_id / self.project_id

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _url(self, suffix: str) -> str:
        return f"/api/projects/{self.project_id}{suffix}"

    def _write_skill(self, name: str, description: str = "demo skill") -> None:
        skill_dir = self.workspace / ".android-agent" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nDo the thing.",
            encoding="utf-8",
        )


class ProjectSettingsApiTests(ProjectSettingsApiFixture, unittest.TestCase):
    def test_settings_get_and_patch_permission_profile(self) -> None:
        resp = self.client.get(self._url("/settings"), headers=self._headers())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["settings"]["permission_profile"], "standard")
        self.assertEqual(body["settings"]["disabled_rules"], [])
        self.assertEqual(body["settings"]["disabled_skills"], [])
        profiles = {item["profile"] for item in body["permission_profiles"]}
        self.assertEqual(profiles, {"safe", "standard", "full_access"})

        resp = self.client.patch(
            self._url("/settings"),
            json={"permission_profile": "safe"},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["settings"]["permission_profile"], "safe")
        self.assertEqual(get_permission_profile(self.workspace), "safe")

    def test_settings_patch_invalid_profile(self) -> None:
        resp = self.client.patch(
            self._url("/settings"),
            json={"permission_profile": "yolo"},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_settings_requires_auth(self) -> None:
        resp = self.client.get(self._url("/settings"))
        self.assertEqual(resp.status_code, 401)


class PermissionProfileTests(unittest.TestCase):
    def test_safe_profile_asks_for_writes_but_allows_build(self) -> None:
        write_spec = get_tool_spec("write_file")
        decision = decide_permission(write_spec, "workspace", profile="safe")
        self.assertEqual(decision.action, "ask")

        gradle_spec = get_tool_spec("run_gradle")
        decision = decide_permission(gradle_spec, "ask", profile="safe")
        self.assertTrue(decision.allow)

    def test_standard_profile_allows_workspace_write(self) -> None:
        write_spec = get_tool_spec("write_file")
        decision = decide_permission(write_spec, "workspace", profile="standard")
        self.assertTrue(decision.allow)

    def test_full_access_still_asks_for_destructive(self) -> None:
        for name in ("delete_project", "run_command"):
            spec = get_tool_spec(name)
            if spec is None:
                continue
            decision = decide_permission(spec, "workspace", profile="full_access")
            self.assertEqual(
                decision.action, "ask", msg=f"{name} should ask under full_access"
            )


class RulesCrudApiTests(ProjectSettingsApiFixture, unittest.TestCase):
    def test_rule_create_list_update_toggle_delete(self) -> None:
        resp = self.client.post(
            self._url("/rules"),
            json={
                "name": "kotlin-style",
                "description": "Kotlin 约定",
                "content": "Always use Kotlin.",
                "always": True,
            },
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 201)
        rule = resp.json()["rule"]
        self.assertEqual(rule["id"], "rules:kotlin-style.md")
        self.assertTrue(rule["enabled"])

        resp = self.client.get(self._url("/rules"), headers=self._headers())
        self.assertEqual(resp.status_code, 200)
        ids = {item["id"] for item in resp.json()["candidates"]}
        self.assertIn("rules:kotlin-style.md", ids)

        resp = self.client.patch(
            self._url("/rules/rules:kotlin-style.md"),
            json={"content": "Always use Kotlin. Prefer Compose."},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.json()["rule"]["body_chars"], 0)

        # disable via toggle → excluded from prompt bundle
        resp = self.client.post(
            self._url("/rules/rules:kotlin-style.md/toggle"),
            json={"enabled": False},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["enabled"])

        resp = self.client.get(self._url("/rules"), headers=self._headers())
        target = next(
            item
            for item in resp.json()["candidates"]
            if item["id"] == "rules:kotlin-style.md"
        )
        self.assertFalse(target["enabled"])

        bundle = load_rules_for_turn(self.workspace, self.user_id)
        self.assertNotIn(
            "rules:kotlin-style.md", [item.rule.id for item in bundle.loaded]
        )
        skipped_ids = {
            item.get("id") for item in bundle.skipped if item.get("reason") == "disabled_by_user"
        }
        self.assertIn("rules:kotlin-style.md", skipped_ids)

        # re-enable
        resp = self.client.post(
            self._url("/rules/rules:kotlin-style.md/toggle"),
            json={"enabled": True},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        bundle = load_rules_for_turn(self.workspace, self.user_id)
        self.assertIn(
            "rules:kotlin-style.md", [item.rule.id for item in bundle.loaded]
        )

        # delete
        resp = self.client.delete(
            self._url("/rules/rules:kotlin-style.md"), headers=self._headers()
        )
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(
            (self.workspace / ".android-agent" / "rules" / "kotlin-style.md").exists()
        )

    def test_rule_create_duplicate_conflict(self) -> None:
        for _ in range(2):
            resp = self.client.post(
                self._url("/rules"),
                json={"name": "dup", "content": "x"},
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 409)

    def test_rule_update_rejects_non_managed_ids(self) -> None:
        self._write_agents_md()
        resp = self.client.patch(
            self._url("/rules/workspace:AGENTS.md"),
            json={"content": "hijack"},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_rule_update_missing(self) -> None:
        resp = self.client.patch(
            self._url("/rules/rules:ghost.md"),
            json={"content": "x"},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 404)

    def _write_agents_md(self) -> None:
        (self.workspace / "AGENTS.md").write_text("root marker", encoding="utf-8")


class SkillsToggleApiTests(ProjectSettingsApiFixture, unittest.TestCase):
    def test_skill_toggle_and_prompt_effect(self) -> None:
        self._write_skill("android-build", "构建说明")
        resp = self.client.get(self._url("/skills"), headers=self._headers())
        self.assertEqual(resp.status_code, 200)
        skills = resp.json()["skills"]
        self.assertTrue(any(s["name"] == "android-build" and s["enabled"] for s in skills))

        resp = self.client.post(
            self._url("/skills/toggle"),
            json={"scope": "project", "name": "android-build", "enabled": False},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["enabled"])

        # listing keeps the skill with enabled=False
        skills = self.client.get(
            self._url("/skills"), headers=self._headers()
        ).json()["skills"]
        target = next(s for s in skills if s["name"] == "android-build")
        self.assertFalse(target["enabled"])

        # discovery + loader enforce the disable
        names = [s.name for s in list_skills(self.workspace, self.user_id)]
        self.assertNotIn("android-build", names)
        with self.assertRaises(PermissionError):
            load_skill(self.workspace, self.user_id, "android-build")

        prompt, _ = build_system_prompt(
            _api_settings(),
            workspace=self.workspace,
            user_id=self.user_id,
        )
        self.assertNotIn("android-build", prompt)

        # re-enable
        resp = self.client.post(
            self._url("/skills/toggle"),
            json={"scope": "project", "name": "android-build", "enabled": True},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        names = [s.name for s in list_skills(self.workspace, self.user_id)]
        self.assertIn("android-build", names)

    def test_skill_toggle_unknown(self) -> None:
        resp = self.client.post(
            self._url("/skills/toggle"),
            json={"scope": "project", "name": "ghost", "enabled": False},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 404)


class UsageInspectorApiTests(ProjectSettingsApiFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.event_store = ConversationEventStore(self.task_store)
        self.conversation = self.task_store.create_conversation(
            self.user_id, self.project_id, title="usage"
        )
        conv_id = self.conversation["id"]
        turn = self.event_store.create_turn(
            conv_id,
            self.user_id,
            self.project_id,
            status="succeeded",
            started_at=100.0,
            finished_at=160.0,
        )
        turn_id = turn["id"]
        self.event_store.append_event(
            conv_id,
            turn_id,
            "usage",
            {
                "usage": {
                    "input_tokens": 10_000,
                    "output_tokens": 2_400,
                    "cached_tokens": 7_000,
                    "cache_creation_tokens": 500,
                    "total_tokens": 12_400,
                },
                "model": "gpt-4o",
                "provider": "openai",
            },
            model="gpt-4o",
        )
        self.event_store.append_event(
            conv_id, turn_id, "tool_call", {"tool": "read_file"}, task_id=None
        )
        self.event_store.append_event(
            conv_id, turn_id, "tool_call", {"tool": "grep"}, task_id=None
        )

    def test_conversation_usage(self) -> None:
        resp = self.client.get(
            f"/api/conversations/{self.conversation['id']}/usage",
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        totals = body["totals"]
        self.assertEqual(totals["turns"], 1)
        self.assertEqual(totals["input_tokens"], 10_000)
        self.assertEqual(totals["output_tokens"], 2_400)
        self.assertEqual(totals["cached_tokens"], 7_000)
        self.assertEqual(totals["tool_calls"], 2)
        self.assertTrue(totals["cost_available"])
        self.assertIsNotNone(totals["cost_usd"])
        self.assertGreater(totals["cost_usd"], 0)
        turn = body["turns"][0]
        self.assertEqual(turn["tool_calls"], 2)
        self.assertEqual(turn["cached_ratio"], 0.7)
        self.assertEqual(turn["duration_seconds"], 60.0)
        self.assertEqual(turn["models"], ["gpt-4o"])

    def test_conversation_usage_missing(self) -> None:
        resp = self.client.get(
            "/api/conversations/nope/usage", headers=self._headers()
        )
        self.assertEqual(resp.status_code, 404)

    def test_usage_summary_by_project(self) -> None:
        resp = self.client.get(
            "/api/usage/summary",
            params={"project_id": self.project_id, "days": 30},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["project_id"], self.project_id)
        self.assertEqual(body["totals"]["input_tokens"], 10_000)
        self.assertEqual(body["totals"]["tool_calls"], 2)
        self.assertEqual(len(body["by_model"]), 1)
        self.assertEqual(body["by_model"][0]["model"], "gpt-4o")
        self.assertEqual(len(body["by_day"]), 1)
        self.assertEqual(body["project_count"], 1)

    def test_usage_summary_all_projects(self) -> None:
        resp = self.client.get(
            "/api/usage/summary", headers=self._headers()
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["totals"]["turns"], 1)

    def test_usage_summary_unknown_project(self) -> None:
        resp = self.client.get(
            "/api/usage/summary",
            params={"project_id": "ghost"},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 404)


class MemoryUsageFilterTests(ProjectSettingsApiFixture, unittest.TestCase):
    def test_usage_filter_by_task_id(self) -> None:
        resp = self.client.post(
            self._url("/memories"),
            json={
                "title": "架构",
                "content": "项目使用 MVVM",
                "memory_type": "architecture",
                "status": "active",
            },
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 201)
        memory_id = resp.json()["memory"]["id"]

        store = get_memory_store(self._data / "agent.db")
        store.record_usage(
            memory_id,
            self.user_id,
            project_id=self.project_id,
            task_id="task-a",
            reason="context",
        )
        store.record_usage(
            memory_id,
            self.user_id,
            project_id=self.project_id,
            task_id="task-b",
            reason="context",
        )

        resp = self.client.get(
            self._url("/memories/usage"),
            params={"task_id": "task-a"},
            headers=self._headers(),
        )
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["usage"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_id"], "task-a")

        resp = self.client.get(
            self._url("/memories/usage"), headers=self._headers()
        )
        self.assertEqual(len(resp.json()["usage"]), 2)


if __name__ == "__main__":
    unittest.main()
