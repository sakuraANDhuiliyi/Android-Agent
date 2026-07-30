from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.config import Settings
from agent.database import TaskStore
from agent.jobs import configure_task_store, request_cancel, stop_worker
from agent.subagent_roles import get_role
from agent.subagents import (
    get_subagent,
    resolve_worktree_decision,
    spawn_subagent,
    wait_subagents,
)
from agent.tool_registry import get_tool_spec
from agent.tools import dispatch_tool
from agent.worktrees import (
    create_worktree,
    secrets_would_copy,
    worktree_has_changes,
)
from agent import jobs as jobs_mod


def _settings() -> Settings:
    return Settings(
        provider="openai",
        api_key="fake",
        model="fake",
        model_candidates=["fake"],
        max_turns=4,
        max_auto_continuations=0,
        max_gradle_retries=1,
        compact_max_chars=50_000,
        max_output_tokens=1024,
        base_url="https://example.test",
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app" / "src" / "main" / "java").mkdir(parents=True)
    (repo / "app" / "src" / "main" / "java" / "Main.kt").write_text(
        "class Main {}\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=super-secret-value\n", encoding="utf-8")
    (repo / "local.properties").write_text("sdk.dir=/tmp\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\nlocal.properties\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return rev


class SubagentFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self.data = temp / "data"
        self.workspaces = temp / "workspaces"
        self.data.mkdir()
        self.workspaces.mkdir()
        self.user_id = "u1"
        self.project_id = "p1"
        self.workspace = self.workspaces / self.user_id / self.project_id
        self.base_rev = _init_git_repo(self.workspace)
        # Project meta
        (self.workspace / ".agent-project.json").write_text(
            json.dumps(
                {
                    "id": self.project_id,
                    "name": "p1",
                    "repo_root": str(self.workspace),
                }
            ),
            encoding="utf-8",
        )
        self.patches = [
            patch("agent.paths.DATA_DIR", self.data),
            patch("agent.paths.WORKSPACES_DIR", self.workspaces),
            patch("agent.database.DATA_DIR", self.data),
            patch("agent.worktrees.paths.DATA_DIR", self.data),
            patch("agent.jobs.ensure_worker_started"),
        ]
        for p in self.patches:
            p.start()
        self.fake_results: dict[str, dict] = {}
        self.executor_patch = patch(
            "agent.subagents._execute_subagent_agent",
            side_effect=self._fake_subagent_execution,
        )
        self.executor_patch.start()
        self.store = TaskStore(self.data / "agent.db")
        configure_task_store(self.store, _settings())
        stop_worker(wait=True, timeout=2)
        # Parent main task
        self.parent_id = "parent001"
        self.store.create_task(
            {
                "id": self.parent_id,
                "user_id": self.user_id,
                "project_id": self.project_id,
                "conversation_id": None,
                "prompt": "parent",
                "status": "running",
                "provider": "openai",
                "model": "fake",
                "created_at": time.time(),
                "write_lock_key": f"main:{self.user_id}:{self.project_id}",
                "lease_expires_at": time.time() + 600,
                "claim_owner": "test",
            }
        )
        # Need a conversation for parent tools context
        conv = self.store.create_conversation(self.user_id, self.project_id, title="parent")
        self.parent_conv = conv["id"]
        self.store.update_task(self.parent_id, conversation_id=self.parent_conv)

    def tearDown(self) -> None:
        stop_worker(wait=True, timeout=2)
        self.executor_patch.stop()
        for p in reversed(self.patches):
            p.stop()
        self._temp.cleanup()

    def _fake_subagent_execution(
        self,
        settings,
        workspace,
        user_id,
        project_id,
        prompt,
        **kwargs,
    ):
        _ = (settings, user_id, project_id, kwargs)
        result = dict(self.fake_results.get(prompt) or {"text": prompt})
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        workspace = Path(workspace).resolve()
        for edit in result.get("edits") or []:
            target = (workspace / str(edit["path"])).resolve()
            target.relative_to(workspace)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(edit.get("content", "")), encoding="utf-8")
        return result

    def _spawn(self, role: str, prompt: str, **kwargs):
        fake_result = kwargs.pop("fake_result", None)
        if fake_result is not None:
            self.fake_results[prompt] = fake_result
        return spawn_subagent(
            user_id=self.user_id,
            project_id=self.project_id,
            parent_task_id=self.parent_id,
            role_name=role,
            prompt=prompt,
            settings=_settings(),
            **kwargs,
        )


class SubagentRoleTests(SubagentFixture):
    def test_roles_defined(self) -> None:
        for name in ("explore", "reviewer", "test_runner", "implementer"):
            role = get_role(name)
            self.assertTrue(role.allowed_tools)
            self.assertIn(role.permission_mode, {"read_only", "workspace", "ask"})

    def test_readonly_explore_parallel_order(self) -> None:
        a = self._spawn(
            "explore",
            "find Main",
            fake_result={"text": "found Main.kt", "findings": ["app/.../Main.kt"]},
        )
        b = self._spawn(
            "explore",
            "find README",
            fake_result={"text": "found README", "findings": ["README.md"]},
        )
        # Both queued — claim should allow parallel (no write lock)
        claimed = []
        for _ in range(5):
            t = self.store.claim_next_task("w1", lease_seconds=60)
            if t:
                claimed.append(t["id"])
        self.assertEqual(set(claimed), {a["child_task_id"], b["child_task_id"]})

        # Finish via wait (drives run_once)
        # Re-queue: they were claimed as running; release back or run wrapper
        for tid in claimed:
            self.store.release_task(tid, "w1", "queued")
            # claim_next may need status queued — release_task sets status
        # Actually release_task with queued might work — check
        results = wait_subagents(
            [a["child_task_id"], b["child_task_id"]],
            self.user_id,
            timeout_seconds=5,
        )
        ordered_ids = [r["child_task_id"] for r in results["results"]]
        self.assertEqual(ordered_ids, [a["child_task_id"], b["child_task_id"]])
        texts = [r.get("summary", {}).get("text") for r in results["results"]]
        self.assertEqual(texts[0], "found Main.kt")
        self.assertEqual(texts[1], "found README")
        # Parent context only summaries
        for r in results["results"]:
            self.assertNotIn("events", r)
            self.assertIn("summary", r)

    def test_child_failure(self) -> None:
        child = self._spawn(
            "explore",
            "boom",
            fake_result={"error": "simulated"},
        )
        wait_subagents([child["child_task_id"]], self.user_id, timeout_seconds=5)
        info = get_subagent(child["child_task_id"], self.user_id)
        self.assertEqual(info["status"], "failed")

    def test_parent_cancel_cascades(self) -> None:
        child = self._spawn(
            "explore",
            "slow",
            fake_result={"text": "should not finish"},
        )
        # Keep child queued
        changed = request_cancel(self.parent_id, self.user_id)
        self.assertTrue(changed)
        child_task = self.store.get_task(child["child_task_id"], self.user_id)
        self.assertTrue(child_task["cancel_requested"])

    def test_no_nesting(self) -> None:
        child = self._spawn("explore", "x", fake_result={"text": "ok"})
        with self.assertRaises(PermissionError):
            spawn_subagent(
                user_id=self.user_id,
                project_id=self.project_id,
                parent_task_id=child["child_task_id"],
                role_name="explore",
                prompt="nested",
                settings=_settings(),
            )
        # Via tool
        result = dispatch_tool(
            self.workspace,
            self.user_id,
            self.project_id,
            "spawn_subagent",
            {"role": "explore", "prompt": "n"},
            task_id=child["child_task_id"],
        )
        self.assertFalse(result.ok)
        payload = result.output if isinstance(result.output, dict) else {}
        self.assertTrue(
            payload.get("error_type") == "NoNesting"
            or "Subagent" in str(result.output)
        )

    def test_model_cannot_inject_fake_subagent_result(self) -> None:
        result = dispatch_tool(
            self.workspace,
            self.user_id,
            self.project_id,
            "spawn_subagent",
            {
                "role": "implementer",
                "prompt": "write outside",
                "fake_result": {
                    "edits": [{"path": "../../owned.txt", "content": "owned"}],
                },
            },
            task_id=self.parent_id,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "InvalidToolInput")
        self.assertIn("未声明参数 fake_result", str(result.output))
        self.assertEqual(self.store.count_active_children(self.parent_id), 0)
        self.assertFalse((self.workspaces / "owned.txt").exists())

    def test_dependency_atomic_claim(self) -> None:
        first = self._spawn("explore", "A", fake_result={"text": "A done"})
        second = self._spawn(
            "explore",
            "B",
            depends_on=[first["child_task_id"]],
            fake_result={"text": "B done"},
        )
        # Claim should get first only
        t1 = self.store.claim_next_task("w1", 60)
        self.assertIsNotNone(t1)
        self.assertEqual(t1["id"], first["child_task_id"])
        # Second blocked while first running
        t2 = self.store.claim_next_task("w2", 60)
        self.assertIsNone(t2)
        # Complete first
        self.store.release_task(first["child_task_id"], "w1", "succeeded")
        t3 = self.store.claim_next_task("w2", 60)
        self.assertIsNotNone(t3)
        self.assertEqual(t3["id"], second["child_task_id"])


class WorktreeIsolationTests(SubagentFixture):
    def test_worktree_rejects_tracked_secrets(self) -> None:
        secrets = secrets_would_copy(self.workspace)
        self.assertTrue(any(".env" in s or "local.properties" in s for s in secrets))
        _git(self.workspace, "add", "-f", ".env", "local.properties")
        _git(self.workspace, "commit", "-m", "tracked secrets")
        with self.assertRaises(PermissionError):
            create_worktree(
                self.user_id,
                self.project_id,
                self.workspace,
                repo_root=self.workspace,
            )

    def test_worktree_isolation(self) -> None:
        wt = create_worktree(
            self.user_id,
            self.project_id,
            self.workspace,
            base_revision=self.base_rev,
            repo_root=self.workspace,
        )
        target = wt.path / "app" / "src" / "main" / "java" / "Main.kt"
        target.write_text("class Main { fun x(){} }\n", encoding="utf-8")
        main_file = self.workspace / "app" / "src" / "main" / "java" / "Main.kt"
        self.assertEqual(main_file.read_text(encoding="utf-8"), "class Main {}\n")
        self.assertTrue(worktree_has_changes(wt))

    def test_implementer_no_change_auto_clean(self) -> None:
        child = self._spawn(
            "implementer",
            "noop",
            fake_result={"text": "nothing to do", "edits": []},
            base_revision=self.base_rev,
        )
        self.assertTrue(child.get("worktree_id"))
        wait_subagents([child["child_task_id"]], self.user_id, timeout_seconds=5)
        info = get_subagent(child["child_task_id"], self.user_id)
        self.assertEqual(info["status"], "succeeded")
        summary = info["summary"]
        self.assertEqual(summary.get("worktree_action"), "auto_cleaned")

    def test_implementer_keeps_changes_and_conflict_detect(self) -> None:
        child = self._spawn(
            "implementer",
            "edit",
            fake_result={
                "text": "edited Main",
                "edits": [
                    {
                        "path": "app/src/main/java/Main.kt",
                        "content": "class Main { fun wt(){} }\n",
                    }
                ],
            },
            base_revision=self.base_rev,
        )
        wait_subagents([child["child_task_id"]], self.user_id, timeout_seconds=5)
        info = get_subagent(child["child_task_id"], self.user_id)
        self.assertEqual(info["summary"].get("worktree_action"), "awaiting_decision")
        wt_id = info["summary"]["worktree_id"]

        # Dirty main same file → conflict
        main_file = self.workspace / "app" / "src" / "main" / "java" / "Main.kt"
        main_file.write_text("class Main { fun main(){} }\n", encoding="utf-8")
        merge = resolve_worktree_decision(
            self.user_id, self.project_id, wt_id, "merge"
        )
        self.assertFalse(merge.get("ok"))
        self.assertEqual(merge.get("error"), "conflicts_detected")

        # Keep preserves worktree
        kept = resolve_worktree_decision(
            self.user_id, self.project_id, wt_id, "keep"
        )
        self.assertTrue(kept.get("ok"))
        self.assertEqual(kept["worktree"]["status"], "kept")

    def test_parent_receives_summary_only_via_tool(self) -> None:
        child = self._spawn(
            "reviewer",
            "review",
            fake_result={"text": "LGTM", "findings": ["ok"]},
        )
        result = dispatch_tool(
            self.workspace,
            self.user_id,
            self.project_id,
            "wait_subagents",
            {"child_task_ids": [child["child_task_id"]], "timeout_seconds": 5},
            task_id=self.parent_id,
        )
        self.assertTrue(result.ok)
        payload = result.output
        self.assertTrue(payload["ok"])
        item = payload["results"][0]
        self.assertEqual(item["summary"]["text"], "LGTM")
        dumped = json.dumps(payload)
        self.assertNotIn("tool_call", dumped)
        self.assertNotIn("SECRET=super-secret", dumped)


class BuiltinRegressionTests(unittest.TestCase):
    def test_spawn_tools_registered_and_builtins_remain(self) -> None:
        self.assertIsNotNone(get_tool_spec("spawn_subagent"))
        self.assertIsNotNone(get_tool_spec("get_subagent"))
        self.assertIsNotNone(get_tool_spec("wait_subagents"))
        self.assertIsNotNone(get_tool_spec("read_file"))
        self.assertIsNotNone(get_tool_spec("write_file"))


if __name__ == "__main__":
    unittest.main()
