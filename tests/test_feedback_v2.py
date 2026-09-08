from __future__ import annotations

import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from agent.api import create_app
from agent.database import TaskStore
from agent.feedback import FeedbackStore, parse_log, read_test_reports, run_feedback_cycle, feedback_summary
from agent.project import init_project
from agent.users import UserStore
from agent.workspace import WorkspaceRepository
from agent.tools import ToolResult
from agent import jobs as jobs_mod
from tests.test_workspace import IsolatedWorkspaceMixin, _api_settings
from tests import test_conversation_integration as integration


class FeedbackParsingTests(unittest.TestCase):
    def test_compiler_locations_and_gradle_counts(self):
        log = """e: file:///work/app/src/main/kotlin/Login.kt:82:9 Unresolved reference 'foo'
w: /work/app/src/main/AndroidManifest.xml:4: warning: exported activity
BUILD FAILED in 1m 2s
50 actionable tasks: 38 executed, 10 from-cache, 2 up-to-date"""
        result = parse_log(log, Path("/work"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["duration_ms"], 62_000)
        self.assertEqual(result["tasks"], {"total": 50, "executed": 38, "cached": 10, "up_to_date": 2})
        self.assertEqual(result["problems"][0]["path"], "app/src/main/kotlin/Login.kt")
        self.assertEqual(result["problems"][0]["line"], 82)
        self.assertEqual(result["warnings"], 1)
        escaped = parse_log("e: /elsewhere/Secret.kt:3:1 error", Path("/work"))
        self.assertIsNone(escaped["problems"][0]["path"])

    def test_junit_uses_fresh_reports_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reports = root / "app/build/test-results/testDebugUnitTest"
            reports.mkdir(parents=True)
            stale = reports / "TEST-old.xml"
            stale.write_text('<testsuite><testcase name="old"><failure message="stale"/></testcase></testsuite>')
            os.utime(stale, (1, 1))
            (reports / "TEST-current.xml").write_text('<testsuite><testcase name="pass"/><testcase name="skip"><skipped/></testcase><testcase classname="LoginTest" name="login"><failure message="expected true">stack</failure></testcase></testsuite>')
            totals, issues = read_test_reports(root, time.time() - 10)
            self.assertEqual(totals, {"total": 3, "passed": 1, "failed": 1, "skipped": 1, "reported": True})
            self.assertIn("LoginTest.login", issues[0]["message"])
            self.assertEqual(len(issues), 1)

    def test_cycle_orders_build_test_fix_and_stops_on_success(self):
        calls = []
        outcomes = iter([False, True, False, True, True])
        def run(task):
            calls.append(task)
            return next(outcomes)
        run_feedback_cycle({"run_tests": True, "fix_failures": True}, run, lambda n, task: calls.append(f"fix{n}"), lambda: None)
        self.assertEqual(calls, ["assembleDebug", "fix1", "assembleDebug", "testDebugUnitTest", "fix2", "assembleDebug", "testDebugUnitTest"])

    def test_cycle_retry_bound_and_disabled_repairs(self):
        for enabled, expected_calls in [(True, 3), (False, 1)]:
            run = Mock(return_value=False)
            fix = Mock()
            with self.assertRaises(RuntimeError):
                run_feedback_cycle({"fix_failures": enabled}, run, fix, lambda: None)
            self.assertEqual(run.call_count, expected_calls)
            self.assertEqual(fix.call_count, expected_calls - 1)

    def test_cancel_does_not_start_another_build(self):
        run = Mock()
        with self.assertRaises(InterruptedError):
            run_feedback_cycle({}, run, Mock(), Mock(side_effect=InterruptedError()))
        run.assert_not_called()


class ExplorerFeedbackApiTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.client = TestClient(create_app(settings=_api_settings(), task_store=self.store, user_store=UserStore(self._data / "users.db")), headers={"Authorization": "Bearer test-token"})
        self.project = init_project("explorer", package="com.example.explorer", user_id="local")
        self.workspace = self._workspaces / "local" / self.project
        self.path = "app/src/main/java/com/example/explorer/MainActivity.kt"

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_search_filters_and_symlink_exclusion(self):
        (self.workspace / "app/src/main/java/secret.kt").symlink_to(self._data / "agent.db")
        repo = WorkspaceRepository("local", self.project, task_store=self.store)
        repo.create_checkpoint("before_turn", idempotency_key="before:search")
        (self.workspace / self.path).write_text("class Modified")
        url = f"/api/projects/{self.project}/files/search"
        result = self.client.get(url, params={"q": "MainActivity", "modified_only": True}).json()
        self.assertEqual([row["path"] for row in result["entries"]], [self.path])
        resources = self.client.get(url, params={"kind": "resources"}).json()["entries"]
        self.assertTrue(resources)
        self.assertTrue(all("/res/" in row["path"] for row in resources))
        self.assertEqual(self.client.get(url, params={"q": "secret"}).json()["entries"], [])

    def test_save_conflict_keeps_server_change_and_rejects_active_job(self):
        url = f"/api/projects/{self.project}/files/content"
        content = self.client.get(url, params={"path": self.path}).json()
        (self.workspace / self.path).write_text("Agent change")
        body = {"path": self.path, "content": "my draft", "expected_revision": content["revision"]}
        self.assertEqual(self.client.put(url, json=body).status_code, 409)
        self.assertEqual((self.workspace / self.path).read_text(), "Agent change")
        body["expected_revision"] = self.client.get(url, params={"path": self.path}).json()["revision"]
        self.assertEqual(self.client.put(url, json=body).status_code, 200)
        self.store.create_task({"id": "active", "user_id": "local", "project_id": self.project, "status": "running", "created_at": time.time(), "prompt": "work"})
        self.assertEqual(self.client.put(url, json={"path": self.path, "content": "overwritten"}).status_code, 409)
        self.assertEqual((self.workspace / self.path).read_text(), "my draft")

    def test_settings_persist_and_feedback_scopes_jobs(self):
        url = f"/api/projects/{self.project}/feedback"
        options = {"build_after_changes": True, "run_tests": True, "fix_failures": True}
        self.assertEqual(self.client.put(url + "/settings", json=options).status_code, 200)
        self.assertEqual(self.client.get(url).json()["settings"], options)
        self.assertEqual(self.client.get(url, params={"job_id": "missing"}).status_code, 404)
        self.assertFalse(FeedbackStore(self.store.db_path).settings("other", self.project)["run_tests"])

    def test_runtime_diagnostic_is_visible_and_cannot_cross_project(self):
        url = f"/api/projects/{self.project}/feedback"
        self.assertEqual(self.client.post(url + "/runtime", json={"message": "IllegalStateException: login failed"}).status_code, 201)
        issues = self.client.get(url).json()["problems"]
        self.assertEqual(issues[0]["source"], "Runtime")
        self.assertIn("login failed", issues[0]["message"])
        self.assertEqual(self.client.post("/api/projects/missing/feedback/runtime", json={"message": "x"}).status_code, 404)

    def test_selected_context_reaches_model_with_path_and_lines(self):
        from agent.explicit_context import build_context_bundle
        bundle = build_context_bundle("local", self.project, "Explain", [{"kind": "selection", "label": "MainActivity.kt", "path": self.path, "line_start": 11, "line_end": 13, "text": "fun login() {}"}], self.store, include_automatic=False)
        self.assertIn(f"File: {self.path}", bundle["model_context"])
        self.assertIn("Lines: 11-13", bundle["model_context"])
        self.assertIn("fun login() {}", bundle["model_context"])

    def test_successful_rebuild_drops_old_failures_and_tests(self):
        old = parse_log("e: app/src/main/java/A.kt:2:1 old error", self.workspace, ok=False)
        passed = parse_log("BUILD SUCCESSFUL", self.workspace, ok=True)
        self.store.create_task({"id": "build", "user_id": "local", "project_id": self.project, "status": "succeeded", "created_at": time.time(), "prompt": "build", "context": {"feedback_runs": [old, {**old, "task": "testDebugUnitTest"}, passed]}})
        summary = feedback_summary(self.store, "local", self.project)
        self.assertEqual(summary["problems"], [])
        self.assertIsNone(summary["tests"])
        self.assertEqual(summary["build"]["status"], "success")


class FeedbackJobIntegrationTests(unittest.TestCase):
    def test_denied_build_respects_run_mode_and_never_repairs(self):
        helper = integration.JobCanonicalIntegrationTests()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = TaskStore(root / "tasks.db")
            conv = store.create_conversation("user", "project")
            FeedbackStore(store.db_path).save_settings("user", "project", {"fix_failures": True})
            fake_agent = Mock(return_value="should not run")
            dispatch = Mock(return_value=ToolResult(False, "denied", "PermissionDenied"))
            locks = set()
            with ExitStack() as stack:
                for p in helper._patch_job_environment(store, root, locks, fake_agent): stack.enter_context(p)
                stack.enter_context(patch("agent.jobs.dispatch_agent_tool", dispatch))
                job = jobs_mod.start_ask_job("user", "project", "Build", integration.settings(), conversation_id=conv["id"], feedback_requested=True, run_mode="read_only")
                task = helper._wait(store, job["id"], locks, timeout=10)
            self.assertEqual(task["status"], "failed")
            self.assertEqual(dispatch.call_count, 1)
            self.assertEqual(dispatch.call_args.kwargs["run_mode"], "read_only")
            fake_agent.assert_not_called()

    def test_manual_build_runs_two_repairs_in_one_task_with_distinct_events(self):
        helper = integration.JobCanonicalIntegrationTests()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = TaskStore(root / "tasks.db")
            conv = store.create_conversation("user", "project")
            FeedbackStore(store.db_path).save_settings("user", "project", {"build_after_changes": True, "run_tests": True, "fix_failures": True})
            repair_ids = []
            def fake_agent(*args, **kwargs):
                from agent.loop import _new_message_id
                identity = _new_message_id(kwargs["turn_id"], "openai", 1)
                repair_ids.append(identity)
                kwargs["on_event"]("assistant_message", {"message_id": identity, "text_blocks": [{"block_index": 0, "type": "text", "text": "fixed"}], "is_final": True})
                return "fixed"
            outcomes = iter([False, True, False, True, True])
            tasks = []
            def dispatch(*args, **kwargs):
                tasks.append(args[4]["task"])
                ok = next(outcomes)
                return ToolResult(ok, "BUILD SUCCESSFUL" if ok else "FAILURE: build or test error")
            locks = set()
            with ExitStack() as stack:
                for p in helper._patch_job_environment(store, root, locks, fake_agent): stack.enter_context(p)
                stack.enter_context(patch("agent.jobs.dispatch_agent_tool", side_effect=dispatch))
                job = jobs_mod.start_ask_job("user", "project", "Build", integration.settings(), conversation_id=conv["id"], feedback_requested=True)
                task = helper._wait(store, job["id"], locks, timeout=10)
            self.assertEqual(task["status"], "succeeded", task.get("error_message"))
            self.assertEqual(len(repair_ids), 2)
            self.assertEqual(len(set(repair_ids)), 2)
            self.assertEqual(task["context"]["feedback_fix_attempts"], 2)
            self.assertEqual(tasks, ["assembleDebug", "assembleDebug", "testDebugUnitTest", "assembleDebug", "testDebugUnitTest"])
            self.assertEqual(len(task["context"]["feedback_runs"]), 5)
