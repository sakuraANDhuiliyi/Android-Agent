from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent.api import create_app
from agent.history import branch_snapshot, history, restore_preview, restore_snapshot
from agent.project import init_project
from agent.workspace import WorkspaceRepository
from tests.test_workspace import IsolatedWorkspaceMixin, _api_settings, _git_init, _git_commit


class HistoryTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.project = init_project("history", package="com.example.test", user_id="local")
        self.repo = WorkspaceRepository("local", self.project, task_store=self.store)
        self.file = self.repo.workspace / "app/build.gradle.kts"
        self.original = self.file.read_bytes()

    def test_restore_keeps_conversation_and_can_undo_restore(self):
        self.store.create_conversation("local", self.project, conversation_id="c1", title="Keep me")
        self.store.append_conversation_turn("c1", user="Please fix", assistant="Done")
        before_conversation = self.store.get_conversation("c1", "local")
        cp = self.repo.create_checkpoint("manual")
        self.file.write_text("new code")
        new = self.repo.workspace / "app/src/main/res/values/new.xml"
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_text("<resources/>")
        preview = restore_preview(self.repo, cp["id"])
        restored = restore_snapshot(self.repo, cp["id"], preview["revision"])
        self.assertTrue(restored["ok"])
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertFalse(new.exists())
        self.assertEqual(self.store.get_conversation("c1", "local"), before_conversation)
        undo = restore_preview(self.repo, restored["backup_checkpoint_id"])
        self.assertTrue(restore_snapshot(self.repo, restored["backup_checkpoint_id"], undo["revision"])["ok"])
        self.assertEqual(self.file.read_text(), "new code")
        self.assertTrue(new.exists())

    def test_stale_preview_rejects_without_backup_or_write(self):
        cp = self.repo.create_checkpoint("manual")
        preview = restore_preview(self.repo, cp["id"])
        self.file.write_text("external edit")
        self.assertFalse(restore_snapshot(self.repo, cp["id"], preview["revision"])["ok"])
        self.assertEqual(self.file.read_text(), "external edit")
        self.assertEqual(len(self.repo.list_checkpoints()), 1)

    def test_repeated_checkpoint_key_preserves_original_before_turn(self):
        before = self.repo.create_checkpoint("before_turn", turn_id="t1", idempotency_key="before:t1")
        original = self.repo.get_checkpoint(before["id"])
        self.file.write_text("Agent edit after checkpoint")
        repeated = self.repo.create_checkpoint("before_turn", turn_id="t1", idempotency_key="before:t1")
        self.assertEqual(before, repeated)
        self.assertEqual(self.repo.get_checkpoint(before["id"]), original)

    def test_missing_blob_does_not_partially_restore(self):
        cp = self.repo.create_checkpoint("manual")
        self.file.write_text("new code")
        revision = restore_preview(self.repo, cp["id"])["revision"]
        with patch("agent.history._load_blob", side_effect=FileNotFoundError("missing")):
            with self.assertRaises(FileNotFoundError):
                restore_snapshot(self.repo, cp["id"], revision)
        self.assertEqual(self.file.read_text(), "new code")

    def test_history_counts_turn_changes_not_all_snapshot_files(self):
        self.repo.create_checkpoint("before_turn", turn_id="t1")
        self.file.write_text("changed")
        self.repo.create_checkpoint("after_turn", turn_id="t1")
        entries = history(self.repo)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["changed_files"], 1)

    def test_branch_snapshot_preserves_head_index_and_dirty_files(self):
        _git_init(self.repo.workspace)
        _git_commit(self.repo.workspace, "initial")
        git = lambda *args: subprocess.check_output(["git", *args], cwd=self.repo.workspace)
        self.file.write_text("snapshot version")
        cp = self.repo.create_checkpoint("manual")
        self.file.write_text("newer user edit")
        git("add", "app/build.gradle.kts")
        before = (git("rev-parse", "HEAD"), git("diff", "--cached"), git("status", "--porcelain"))
        result = branch_snapshot(self.repo, cp["id"], "codex/history-test")
        self.assertFalse(result["checked_out"])
        self.assertEqual(git("show", "codex/history-test:app/build.gradle.kts"), b"snapshot version")
        self.assertEqual((git("rev-parse", "HEAD"), git("diff", "--cached"), git("status", "--porcelain")), before)
        self.assertEqual(self.file.read_text(), "newer user edit")
        with self.assertRaises(ValueError):
            branch_snapshot(self.repo, cp["id"], "codex/history-test")

    def test_non_git_branch_is_explicitly_unsupported(self):
        cp = self.repo.create_checkpoint("manual")
        with self.assertRaises(ValueError):
            branch_snapshot(self.repo, cp["id"], "codex/test")

    def test_restore_preview_describes_deletion_direction(self):
        cp = self.repo.create_checkpoint("manual")
        new = self.repo.workspace / "app/src/main/res/values/new.xml"
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_text("<resources/>")
        preview = restore_preview(self.repo, cp["id"])
        self.assertEqual(preview["diff"]["files"][0]["change"], "deleted")

    def test_restore_refuses_internal_symlink_without_overwriting_target(self):
        cp = self.repo.create_checkpoint("manual")
        target = self.repo.workspace / "build.gradle.kts"
        target.write_text("preserve target")
        self.file.unlink()
        self.file.symlink_to(target)
        preview = restore_preview(self.repo, cp["id"])
        with self.assertRaises(ValueError):
            restore_snapshot(self.repo, cp["id"], preview["revision"])
        self.assertEqual(target.read_text(), "preserve target")

    def test_branch_preserves_executable_snapshot_and_deletes_removed_files(self):
        self.file.chmod(0o755)
        deleted = self.repo.workspace / "app/src/main/res/layout/activity_main.xml"
        _git_init(self.repo.workspace)
        _git_commit(self.repo.workspace, "initial")
        deleted.unlink()
        cp = self.repo.create_checkpoint("manual")
        branch_snapshot(self.repo, cp["id"], "codex/executable")
        tree = subprocess.check_output(["git", "ls-tree", "-r", "codex/executable"], cwd=self.repo.workspace).decode()
        self.assertIn("100755 blob", tree)
        self.assertNotIn("activity_main.xml", tree)

    def test_api_restore_guards_active_task_and_ownership(self):
        client = TestClient(create_app(_api_settings(), task_store=self.store))
        client.headers["Authorization"] = "Bearer test-token"
        cp = self.repo.create_checkpoint("manual")
        path = f"/api/projects/{self.project}/checkpoints/{cp['id']}"
        preview = client.get(path + "/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.store.create_task({"id": "active", "user_id": "local", "project_id": self.project,
                                "prompt": "working", "status": "running", "created_at": 1})
        result = client.post(path + "/restore-snapshot", json={"expected_revision": preview.json()["revision"]})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(client.post(path + "/restore", json={}).status_code, 409)
        self.assertEqual(client.get(path.replace(cp["id"], "missing") + "/preview").status_code, 404)
        client.headers.clear()
        self.assertEqual(client.get(path + "/preview").status_code, 401)

    def test_terminal_output_checks_owner_before_reading(self):
        settings = _api_settings()
        settings.terminal_enabled = True
        client = TestClient(create_app(settings, task_store=self.store))
        client.headers["Authorization"] = "Bearer test-token"
        with patch("agent.api.get_terminal", return_value=None), patch("agent.api.terminal_outputs") as outputs:
            self.assertEqual(client.get("/api/terminals/foreign/output").status_code, 404)
            outputs.assert_not_called()
        with patch("agent.api.get_terminal", return_value={"id": "t1", "status": "running"}), \
                patch("agent.api.terminal_outputs", return_value=[{"seq": 4, "data": "hello"}]) as outputs:
            response = client.get("/api/terminals/t1/output?after_seq=3")
            self.assertEqual(response.json()["next_seq"], 4)
            outputs.assert_called_once_with("t1", after_seq=3, limit=200)

    def test_app_construction_does_not_invalidate_live_terminal_sessions(self):
        with patch("agent.api.mark_interrupted_terminals") as recover:
            app = create_app(_api_settings(), task_store=self.store)
            recover.assert_not_called()
            with TestClient(app):
                recover.assert_called_once()

    def test_approval_wrong_job_cannot_resolve_the_request(self):
        from agent.approvals import ApprovalRequest, resolve_approval, _pending
        approval = ApprovalRequest(id="scope-test", job_id="job-a", user_id="local", kind="command", payload={})
        with patch.dict(_pending, {approval.id: approval}, clear=True):
            self.assertIsNone(resolve_approval(approval.id, "local", approved=True, expected_job_id="job-b"))
            self.assertIsNone(approval.decision)
            self.assertFalse(approval.event.is_set())
            self.assertEqual(resolve_approval(approval.id, "local", approved=False, expected_job_id="job-a")["decision"], "rejected")


if __name__ == "__main__":
    unittest.main()
