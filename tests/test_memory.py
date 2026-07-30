from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.context_planner import ContextPlanner
from agent.database import TaskStore
from agent.memory_extract import (
    DeterministicMemoryExtractor,
    generate_candidates_for_turn,
)
from agent.memory_retrieve import retrieve_memories_for_task
from agent.memory_store import MemoryStore, reset_memory_store
from agent.project import init_project
from agent.users import UserStore


def _settings() -> Settings:
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


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.db = Path(self._temp.name) / "mem.db"
        self.store = MemoryStore(self.db)
        self.user = "mem_user"
        self.project = "mem_proj"

    def tearDown(self) -> None:
        self._temp.cleanup()
        reset_memory_store()

    def test_candidate_approve_search_reject_isolation(self) -> None:
        c = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="convention",
            title="Use ViewBinding",
            content="UI 约定：必须使用 ViewBinding，禁止 findViewById。",
            tags=["convention", "android"],
            status="candidate",
            source_conversation_id="conv1",
            source_event_seq=12,
        )
        self.assertEqual(c["status"], "candidate")
        # Candidates not searchable as active
        hits = self.store.search(self.user, "ViewBinding", project_id=self.project)
        self.assertEqual(hits, [])

        approved = self.store.approve(c["id"], self.user)
        self.assertEqual(approved["status"], "active")
        hits = self.store.search(self.user, "ViewBinding", project_id=self.project)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], c["id"])

        # Reject another candidate — never in retrieve
        r = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="decision",
            title="Rejected decision",
            content="决定采用 Foo 框架（将被拒绝）",
            tags=["decision"],
            status="candidate",
        )
        self.store.reject(r["id"], self.user)
        plan = retrieve_memories_for_task(
            user_id=self.user,
            project_id=self.project,
            prompt="ViewBinding Foo 框架",
            store=self.store,
        )
        ids = {m["id"] for m in plan["selected"]}
        self.assertIn(c["id"], ids)
        self.assertNotIn(r["id"], ids)
        self.assertTrue(any("project memory" in t for t in plan["context_text"].split("\n") if t))

        # User isolation
        other = self.store.search("other_user", "ViewBinding", project_id=self.project)
        self.assertEqual(other, [])

    def test_dedupe_and_edit_delete(self) -> None:
        a = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="workflow",
            title="Build flow",
            content="构建流程：改完 UI 后运行 assembleDebug。",
            tags=["workflow"],
        )
        b = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="workflow",
            title="Build flow",
            content="构建流程：改完 UI 后运行 assembleDebug。",
            tags=["workflow"],
        )
        self.assertTrue(b.get("deduped"))
        self.assertEqual(a["id"], b["id"])

        edited = self.store.update_memory(
            a["id"],
            self.user,
            content="构建流程：优先 assembleDebug，失败再看日志。",
        )
        self.assertIn("优先 assembleDebug", edited["content"])
        self.assertTrue(self.store.delete_memory(a["id"], self.user))
        self.assertIsNone(self.store.get_memory(a["id"], self.user))

    def test_secret_filtered(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_memory(
                user_id=self.user,
                project_id=self.project,
                scope="project",
                memory_type="preference",
                title="keys",
                content="api_key=sk-abcdefg123456 password=hunter2",
            )

    def test_fts_recency_and_budget(self) -> None:
        old = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="architecture",
            title="Old arch",
            content="架构：旧的模块划分说明 Gradle 模块",
            tags=["architecture"],
            status="active",
        )
        new = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="architecture",
            title="New arch",
            content="架构：新的模块划分与 Gradle 约定",
            tags=["architecture", "gradle"],
            status="active",
        )
        # Touch new as used more recently
        self.store.record_usage(new["id"], self.user, project_id=self.project, reason="test")
        hits = self.store.search(self.user, "Gradle 架构", project_id=self.project)
        self.assertGreaterEqual(len(hits), 2)
        self.assertEqual(hits[0]["id"], new["id"])

        tiny = retrieve_memories_for_task(
            user_id=self.user,
            project_id=self.project,
            prompt="架构 Gradle",
            store=self.store,
            budget_chars=80,
            limit=5,
            record_usage=False,
        )
        self.assertLessEqual(tiny["total_chars"], 80)
        self.assertTrue(any("预算" in r or tiny["selected"] for r in tiny["reasons"] + [""]))

    def test_sqlite_reopen(self) -> None:
        self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="known_issue",
            title="Issue",
            content="已知问题：Manifest merger 失败时检查依赖冲突。",
            tags=["known_issue"],
            status="active",
        )
        reopened = MemoryStore(self.db)
        hits = reopened.search(self.user, "Manifest merger", project_id=self.project)
        self.assertTrue(hits)

    def test_extractor_and_source_tracking(self) -> None:
        events = [
            {
                "event_type": "assistant_message",
                "seq": 5,
                "payload": {
                    "text_blocks": [
                        {
                            "type": "text",
                            "text": (
                                "结论：我们决定采用 ViewBinding。\n"
                                "MEMORY: [convention] 禁止 findViewById\n"
                            ),
                        }
                    ]
                },
            }
        ]
        created = generate_candidates_for_turn(
            user_id=self.user,
            project_id=self.project,
            conversation_id="c1",
            events=events,
            user_prompt="请确认 UI 约定",
            final_answer="MEMORY: [convention] 禁止 findViewById",
            store=self.store,
            extractor=DeterministicMemoryExtractor(),
        )
        self.assertTrue(created)
        self.assertTrue(all(c["status"] == "candidate" for c in created))
        self.assertTrue(any(c.get("source_conversation_id") == "c1" for c in created))
        self.assertTrue(any(c.get("source_event_seq") == 5 for c in created))

    def test_context_planner_skips_candidates(self) -> None:
        cand = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="preference",
            title="cand",
            content="偏好：候选记忆不应进入上下文",
            status="candidate",
        )
        active = self.store.create_memory(
            user_id=self.user,
            project_id=self.project,
            scope="project",
            memory_type="preference",
            title="active pref",
            content="偏好：已批准的记忆可以进入上下文",
            status="active",
        )

        class FakeIndex:
            _workspace = Path(self._temp.name)

        planner = ContextPlanner(FakeIndex(), user_id=self.user, project_id=self.project)
        # Point store used by retrieve to our test store via patch
        with patch("agent.memory_retrieve.get_memory_store", return_value=self.store):
            with patch("agent.memory_store.get_memory_store", return_value=self.store):
                plan = planner.plan("偏好 上下文", budget_chars=20_000)
        mem_ids = [s.get("memory_id") for s in plan["selected"] if s.get("kind") == "memory"]
        self.assertIn(active["id"], mem_ids)
        self.assertNotIn(cand["id"], mem_ids)
        self.assertTrue(any("project memory" in r for r in plan["reasons"]))


class MemoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self.data = temp / "data"
        self.workspaces = temp / "workspaces"
        self.data.mkdir()
        self.workspaces.mkdir()
        template = temp / "template"
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
        self.patches = [
            patch("agent.paths.DATA_DIR", self.data),
            patch("agent.paths.WORKSPACES_DIR", self.workspaces),
            patch("agent.database.DATA_DIR", self.data),
            patch("agent.paths.TEMPLATE_DIR", template),
            patch("agent.project.TEMPLATE_DIR", template),
        ]
        for p in self.patches:
            p.start()
        reset_memory_store()
        self.user_store = UserStore(self.data / "users.db")
        self.uid, self.token = self.user_store.register()
        store = TaskStore(self.data / "agent.db")
        from agent import jobs

        jobs.configure_task_store(store)
        self.client = TestClient(
            create_app(settings=_settings(), user_store=self.user_store, task_store=store)
        )
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.project_id = init_project(
            "mem-api", package="com.example.mem", user_id=self.uid
        )

    def tearDown(self) -> None:
        reset_memory_store()
        for p in reversed(self.patches):
            p.stop()
        self._temp.cleanup()

    def test_api_approve_search_delete(self) -> None:
        created = self.client.post(
            f"/api/projects/{self.project_id}/memories",
            headers=self.headers,
            json={
                "title": "ViewBinding rule",
                "content": "约定：使用 ViewBinding",
                "memory_type": "convention",
                "tags": ["convention"],
                "status": "candidate",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        mid = created.json()["memory"]["id"]

        cand = self.client.get(
            f"/api/projects/{self.project_id}/memories/candidates",
            headers=self.headers,
        )
        self.assertEqual(cand.status_code, 200)
        self.assertTrue(any(x["id"] == mid for x in cand.json()["candidates"]))

        ap = self.client.post(f"/api/memories/{mid}/approve", headers=self.headers)
        self.assertEqual(ap.status_code, 200)
        self.assertEqual(ap.json()["memory"]["status"], "active")

        search = self.client.get(
            f"/api/projects/{self.project_id}/memories/search",
            headers=self.headers,
            params={"q": "ViewBinding"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["hits"])

        edited = self.client.patch(
            f"/api/memories/{mid}",
            headers=self.headers,
            json={"content": "约定：始终使用 ViewBinding，禁止 findViewById"},
        )
        self.assertEqual(edited.status_code, 200)

        usage = self.client.post(
            f"/api/projects/{self.project_id}/memories/retrieve?q=ViewBinding",
            headers=self.headers,
        )
        self.assertEqual(usage.status_code, 200)
        self.assertTrue(usage.json()["selected"])

        logs = self.client.get(
            f"/api/projects/{self.project_id}/memories/usage",
            headers=self.headers,
        )
        self.assertEqual(logs.status_code, 200)
        self.assertTrue(logs.json()["usage"])

        deleted = self.client.delete(f"/api/memories/{mid}", headers=self.headers)
        self.assertEqual(deleted.status_code, 204)


if __name__ == "__main__":
    unittest.main()
