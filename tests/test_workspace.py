from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.api import create_app
from agent.config import Settings, UserAccount
from agent.database import TaskStore
from agent.paths import BUILDS_DIR, DATA_DIR, TEMPLATE_DIR, WORKSPACES_DIR
from agent.project import import_project, init_project
from agent.workspace import WorkspaceRepository, _is_inside_workspace
from fastapi.testclient import TestClient


def _git_init(repo: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


def _git_commit(repo: Path, message: str) -> None:
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


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
    (gradle_dir / "gradle-wrapper.properties").write_text("distributionBase=GRADLE_USER_HOME", encoding="utf-8")


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
        users=[UserAccount(id="local", token="test-token")],
    )


class IsolatedWorkspaceMixin:
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._orig_workspaces = WORKSPACES_DIR
        self._orig_builds = BUILDS_DIR
        self._orig_data = DATA_DIR
        self._orig_template = TEMPLATE_DIR
        self._template_copy = temp / "template"
        _make_minimal_template(self._template_copy)
        self._workspaces = temp / "workspaces"
        self._builds = temp / "builds"
        self._data = temp / "data"
        self._workspaces.mkdir()
        self._builds.mkdir()
        self._data.mkdir()
        self.patches = [
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
            patch("agent.paths.BUILDS_DIR", self._builds),
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.workspace.DATA_DIR", self._data),
            patch("agent.database.DATA_DIR", self._data),
            patch("agent.paths.TEMPLATE_DIR", self._template_copy),
            patch("agent.project.TEMPLATE_DIR", self._template_copy),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        self._temp.cleanup()

    def _store(self) -> TaskStore:
        return TaskStore(self._data / "agent.db")


class WorkspaceRepositoryTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def test_template_project_checkpoint(self) -> None:
        project_id = init_project("tpl", package="com.example.test", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        cp = repo.create_checkpoint(
            "manual",
            conversation_id="conv1",
            turn_id="turn1",
            task_id="task1",
        )
        self.assertTrue(cp["id"])
        self.assertGreater(cp["file_count"], 0)
        self.assertIn(cp["id"], {c["id"] for c in repo.list_checkpoints()})

    def test_local_git_fixture_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "fixture"
            source.mkdir()
            java_dir = source / "app" / "src" / "main" / "java" / "com" / "example" / "fixture"
            java_dir.mkdir(parents=True)
            (java_dir / "Main.kt").write_text("class Main {}", encoding="utf-8")
            _git_init(source)
            (source / "README.md").write_text("hello", encoding="utf-8")
            _git_commit(source, "initial")

            project_id = import_project("u1", source, name="imported", package="com.example.test")
            meta_path = self._workspaces / "u1" / project_id / ".agent-project.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["source_kind"], "imported")
            self.assertEqual(meta["source_url"], str(source.resolve()))
            self.assertEqual(meta["default_branch"], "main")
            self.assertTrue((self._workspaces / "u1" / project_id / ".git").is_dir())

    def test_git_status_dirty_untracked_renamed_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "fixture"
            source.mkdir()
            java_dir = source / "app" / "src" / "main" / "java" / "com" / "example" / "fixture"
            java_dir.mkdir(parents=True)
            (java_dir / "Main.kt").write_text("class Main {}", encoding="utf-8")
            (java_dir / "Old.kt").write_text("class Old {}", encoding="utf-8")
            (java_dir / "Extra.kt").write_text("class Extra {}", encoding="utf-8")
            _git_init(source)
            _git_commit(source, "initial")

            project_id = import_project("u1", source, name="gitstatus", package="com.example.fixture")
            ws = self._workspaces / "u1" / project_id
            java_dir = ws / "app" / "src" / "main" / "java" / "com" / "example" / "fixture"
            subprocess.run(
                ["git", "mv", "app/src/main/java/com/example/fixture/Main.kt", "app/src/main/java/com/example/fixture/Renamed.kt"],
                cwd=str(ws),
                capture_output=True,
                text=True,
                check=True,
            )
            (java_dir / "New.kt").write_text("class New {}", encoding="utf-8")
            (java_dir / "Old.kt").unlink()
            (java_dir / "Extra.kt").write_text("class Extra { fun x() {} }", encoding="utf-8")

            repo = WorkspaceRepository("u1", project_id, task_store=self._store())
            status = repo.git_status()
            self.assertTrue(status["ok"])
            self.assertTrue(status["dirty"])
            by_path = {f["path"]: f for f in status["files"]}
            self.assertIn("app/src/main/java/com/example/fixture/Renamed.kt", by_path)
            self.assertIn("app/src/main/java/com/example/fixture/New.kt", by_path)
            self.assertIn("app/src/main/java/com/example/fixture/Old.kt", by_path)
            self.assertTrue(any(f["status"] == "renamed" for f in status["files"]))
            self.assertTrue(any(f["status"] == "deleted" for f in status["files"]))
            self.assertTrue(any(f["status"] == "untracked" for f in status["files"]))
            self.assertTrue(any(f["status"] == "modified" for f in status["files"]))

    def test_turn_diff_and_checkpoint_diff(self) -> None:
        project_id = init_project("diff", package="com.example.diff", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        java_dir = self._workspaces / "u1" / project_id / "app" / "src" / "main" / "java" / "com" / "example" / "diff"

        repo.create_checkpoint(
            "before_turn",
            conversation_id="conv1",
            turn_id="turn1",
            task_id="task1",
            idempotency_key="before:turn1",
        )
        (java_dir / "MainActivity.kt").write_text("modified", encoding="utf-8")
        repo.create_checkpoint(
            "after_turn",
            conversation_id="conv1",
            turn_id="turn1",
            task_id="task1",
            idempotency_key="after:turn1",
        )

        diff = repo.turn_diff("turn1")
        self.assertTrue(diff["ok"])
        self.assertEqual(len(diff["files"]), 1)
        self.assertEqual(diff["files"][0]["change"], "modified")
        self.assertIn("modified", diff["diff"])

        cp = repo.list_checkpoints()[0]
        cp_diff = repo.checkpoint_diff(cp["id"])
        self.assertTrue(cp_diff["ok"])

    def test_content_deduplication(self) -> None:
        project_id = init_project("dedup", package="com.example.dedup", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        repo.create_checkpoint("manual", idempotency_key="cp1")
        content_dir = self._data / "users" / "u1" / "checkpoints" / "content"
        blobs_before = {p for p in content_dir.rglob("*") if p.is_file()}
        repo.create_checkpoint("manual", idempotency_key="cp2")
        blobs_after = {p for p in content_dir.rglob("*") if p.is_file()}
        # No new blobs should be created for identical content
        self.assertEqual(blobs_before, blobs_after)
        cp1 = repo.get_checkpoint("cp1")
        cp2 = repo.get_checkpoint("cp2")
        self.assertEqual(
            [f["sha256"] for f in cp1["files"]],
            [f["sha256"] for f in cp2["files"]],
        )

    def test_single_file_and_whole_checkpoint_restore(self) -> None:
        project_id = init_project("restore", package="com.example.restore", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        java_dir = self._workspaces / "u1" / project_id / "app" / "src" / "main" / "java" / "com" / "example" / "restore"
        original = (java_dir / "MainActivity.kt").read_text(encoding="utf-8")

        repo.create_checkpoint(
            "before_turn",
            turn_id="turn1",
            idempotency_key="before:turn1",
        )
        (java_dir / "MainActivity.kt").write_text("changed", encoding="utf-8")
        repo.create_checkpoint(
            "after_turn",
            turn_id="turn1",
            idempotency_key="after:turn1",
        )

        single = repo.restore_file("before:turn1", "app/src/main/java/com/example/restore/MainActivity.kt")
        self.assertTrue(single["ok"])
        self.assertEqual(
            (java_dir / "MainActivity.kt").read_text(encoding="utf-8"),
            original,
        )

        (java_dir / "MainActivity.kt").write_text("changed", encoding="utf-8")
        whole = repo.restore_checkpoint("before:turn1")
        self.assertTrue(whole["ok"])
        self.assertEqual(
            (java_dir / "MainActivity.kt").read_text(encoding="utf-8"),
            original,
        )

    def test_manual_modification_conflict(self) -> None:
        project_id = init_project("conflict", package="com.example.conflict", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        java_dir = self._workspaces / "u1" / project_id / "app" / "src" / "main" / "java" / "com" / "example" / "conflict"

        repo.create_checkpoint(
            "before_turn",
            turn_id="turn1",
            idempotency_key="before:turn1",
        )
        (java_dir / "MainActivity.kt").write_text("agent change", encoding="utf-8")
        repo.create_checkpoint(
            "after_turn",
            turn_id="turn1",
            idempotency_key="after:turn1",
        )

        (java_dir / "MainActivity.kt").write_text("manual edit", encoding="utf-8")
        result = repo.restore_checkpoint("before:turn1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "conflict")
        self.assertTrue(any(c["path"].endswith("MainActivity.kt") for c in result["conflicts"]))
        self.assertEqual((java_dir / "MainActivity.kt").read_text(encoding="utf-8"), "manual edit")

    def test_checkpoint_idempotency(self) -> None:
        project_id = init_project("idempotent", package="com.example.idempotent", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        cp1 = repo.create_checkpoint("manual", idempotency_key="same-key")
        cp2 = repo.create_checkpoint("manual", idempotency_key="same-key")
        self.assertEqual(cp1["id"], cp2["id"])
        self.assertEqual(len(repo.list_checkpoints()), 1)

    def test_path_escape_and_symlink(self) -> None:
        project_id = init_project("escape", package="com.example.escape", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        ws = self._workspaces / "u1" / project_id

        outside = Path(self._temp.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (ws / "app" / "src" / "main" / "java" / "link.kt").symlink_to(outside)

        cp = repo.create_checkpoint("manual")
        paths = {f["path"] for f in cp.get("files", repo.get_checkpoint(cp["id"])["files"])}
        self.assertNotIn("app/src/main/java/link.kt", paths)
        self.assertFalse(_is_inside_workspace(outside, ws))

    def test_user_isolation(self) -> None:
        project_id = init_project("iso", package="com.example.iso", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        cp = repo.create_checkpoint("manual", idempotency_key="iso-cp")

        # u2 should not see u1's checkpoints even if they share a project id in the db query
        other_store = TaskStore(self._data / "other.db")
        with self.assertRaises(FileNotFoundError):
            WorkspaceRepository("u2", project_id, task_store=other_store)

        # create the same project for u2 and verify independent checkpoints
        init_project("iso", package="com.example.iso", user_id="u2")
        other_repo = WorkspaceRepository("u2", project_id, task_store=other_store)
        self.assertEqual(other_repo.list_checkpoints(), [])
        self.assertIsNone(other_repo.get_checkpoint(cp["id"]))

    def test_old_project_without_git(self) -> None:
        project_id = init_project("nogit", package="com.example.nogit", user_id="u1")
        store = self._store()
        repo = WorkspaceRepository("u1", project_id, task_store=store)
        status = repo.git_status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["error"], "not_a_git_repo")
        log = repo.git_log()
        self.assertFalse(log["ok"])

    def test_no_auto_commit_or_push(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "fixture"
            source.mkdir()
            java_dir = source / "app" / "src" / "main" / "java" / "com" / "example" / "fixture"
            java_dir.mkdir(parents=True)
            (java_dir / "Main.kt").write_text("class Main {}", encoding="utf-8")
            _git_init(source)
            _git_commit(source, "initial")
            initial = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(source),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            project_id = import_project("u1", source, name="nocommit")
            ws = self._workspaces / "u1" / project_id
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(ws),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(head, initial)

            repo = WorkspaceRepository("u1", project_id, task_store=self._store())
            repo.create_checkpoint("manual")
            head2 = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(ws),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(head2, initial)


class WorkspacePathTests(unittest.TestCase):
    def test_is_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir()
            inside = ws / "app" / "src" / "main.kt"
            inside.parent.mkdir(parents=True)
            inside.write_text("x", encoding="utf-8")
            outside = Path(td) / "outside.kt"
            outside.write_text("x", encoding="utf-8")
            self.assertTrue(_is_inside_workspace(inside, ws))
            self.assertFalse(_is_inside_workspace(outside, ws))


class WorkspaceApiTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = self._store()
        from agent.users import UserStore
        self.user_store = UserStore(self._data / "users.db")
        self.alice_token = "test-token"
        self.client = TestClient(
            create_app(
                settings=_api_settings(),
                task_store=self.store,
                user_store=self.user_store,
            ),
            headers={"Authorization": f"Bearer {self.alice_token}"},
        )

    def tearDown(self) -> None:
        self.client.close()
        super().tearDown()

    def test_workspace_status_endpoint(self) -> None:
        project_id = init_project("api-status", package="com.example.apistatus", user_id="local")
        resp = self.client.get(f"/api/projects/{project_id}/workspace/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["project_id"], project_id)
        self.assertEqual(data["source_kind"], "template")
        self.assertFalse(data["is_git"])

    def test_diff_and_checkpoints_and_restore_endpoints(self) -> None:
        project_id = init_project("api-restore", package="com.example.apirestore", user_id="local")
        repo = WorkspaceRepository("local", project_id, task_store=self.store)
        repo.create_checkpoint(
            "before_turn",
            turn_id="turn1",
            idempotency_key="before:turn1",
        )
        java_dir = self._workspaces / "local" / project_id / "app" / "src" / "main" / "java" / "com" / "example" / "apirestore"
        (java_dir / "MainActivity.kt").write_text("changed", encoding="utf-8")
        repo.create_checkpoint(
            "after_turn",
            turn_id="turn1",
            idempotency_key="after:turn1",
        )

        diff_resp = self.client.get(f"/api/projects/{project_id}/diff?turn_id=turn1")
        self.assertEqual(diff_resp.status_code, 200)
        self.assertTrue(diff_resp.json()["ok"])

        cp_resp = self.client.get(f"/api/projects/{project_id}/checkpoints")
        self.assertEqual(cp_resp.status_code, 200)
        checkpoints = cp_resp.json()["checkpoints"]
        self.assertEqual(len(checkpoints), 2)

        restore_resp = self.client.post(
            f"/api/projects/{project_id}/checkpoints/before:turn1/restore",
            json={},
        )
        self.assertEqual(restore_resp.status_code, 200)
        self.assertTrue(restore_resp.json()["ok"])

        preview_resp = self.client.post(
            f"/api/projects/{project_id}/checkpoints/before:turn1/restore",
            json={"preview": True},
        )
        self.assertEqual(preview_resp.status_code, 200)
        preview = preview_resp.json()
        self.assertTrue(preview.get("preview"))
        self.assertIn("conflicts", preview)
        self.assertIn("file_count", preview)

    def test_user_isolation_on_endpoints(self) -> None:
        project_id = init_project("api-iso", package="com.example.apiiso", user_id="local")
        resp = self.client.get(f"/api/projects/{project_id}/workspace/status")
        self.assertEqual(resp.status_code, 200)
        _, bob_token = self.user_store.register()
        iso = self.client.get(
            f"/api/projects/{project_id}/workspace/status",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        self.assertEqual(iso.status_code, 404)


if __name__ == "__main__":
    unittest.main()
