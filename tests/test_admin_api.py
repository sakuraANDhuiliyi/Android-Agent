from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings, validate_deployment_settings
from agent.database import TaskStore
from agent.users import UserStore


ADMIN_TOKEN = "admin-token-with-at-least-24-characters"


def settings() -> Settings:
    return Settings(
        provider="deepseek",
        api_key="model-key",
        model="test-model",
        model_candidates=["test-model"],
        max_turns=4,
        max_auto_continuations=0,
        max_gradle_retries=1,
        compact_max_chars=100_000,
        max_output_tokens=4096,
        base_url="https://example.test",
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
        users=[],
        minimum_free_disk_bytes=0,
        admin_ui_enabled=True,
        admin_token=ADMIN_TOKEN,
    )


class AdminApiTests(unittest.TestCase):
    def test_enabled_admin_ui_requires_a_long_independent_token(self) -> None:
        configured = settings()
        configured.admin_token = "too-short"
        with self.assertRaisesRegex(ValueError, "至少 24 位"):
            validate_deployment_settings(configured)

    def test_admin_can_manage_accounts_and_one_time_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(
                settings(),
                user_store=UserStore(root / "users.db"),
                task_store=TaskStore(root / "agent.db"),
            )
            admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
            with (
                patch("agent.api.user_workspaces_dir", side_effect=lambda user: root / "workspaces" / user),
                patch("agent.api.user_builds_dir", side_effect=lambda user: root / "builds" / user),
                TestClient(app) as client,
            ):
                self.assertEqual(client.get("/api/admin/overview").status_code, 401)
                self.assertEqual(client.get("/admin/").status_code, 200)
                self.assertEqual(client.get("/admin/").headers["cache-control"], "no-store")

                created = client.post(
                    "/api/admin/accounts",
                    headers=admin,
                    json={
                        "email": "admin-created@example.com",
                        "password": "initial-123",
                        "display_name": "后台创建",
                        "email_verified": True,
                    },
                )
                self.assertEqual(created.status_code, 201, created.text)
                user_id = created.json()["user_id"]

                issued = client.post(
                    f"/api/admin/accounts/{user_id}/tokens",
                    headers=admin,
                    json={"name": "CI 构建机"},
                )
                self.assertEqual(issued.status_code, 201, issued.text)
                access_token = issued.json()["token"]
                session_id = issued.json()["session_id"]
                self.assertEqual(
                    client.get("/api/account", headers={"Authorization": f"Bearer {access_token}"}).status_code,
                    200,
                )

                listing = client.get("/api/admin/accounts", headers=admin).json()
                serialized = str(listing).lower()
                self.assertNotIn(access_token.lower(), serialized)
                self.assertNotIn("password_hash", serialized)
                self.assertNotIn("token_hash", serialized)
                self.assertEqual(listing["accounts"][0]["active_tokens"], 1)

                detail = client.get(f"/api/admin/accounts/{user_id}", headers=admin).json()
                token_row = next(item for item in detail["tokens"] if item["session_id"] == session_id)
                self.assertTrue(token_row["token_hint"].startswith("••••"))

                disabled = client.patch(
                    f"/api/admin/accounts/{user_id}", headers=admin, json={"disabled": True}
                )
                self.assertTrue(disabled.json()["disabled"])
                self.assertEqual(
                    client.get("/api/account", headers={"Authorization": f"Bearer {access_token}"}).status_code,
                    401,
                )
                login = client.post(
                    "/api/auth/login",
                    json={
                        "email": "admin-created@example.com",
                        "password": "initial-123",
                        "device": {"device_id": "phone", "device_name": "Phone"},
                    },
                )
                self.assertEqual(login.json()["error"]["code"], "account_disabled")

                client.patch(f"/api/admin/accounts/{user_id}", headers=admin, json={"disabled": False})
                reset = client.post(
                    f"/api/admin/accounts/{user_id}/reset-password",
                    headers=admin,
                    json={"new_password": "changed-456"},
                )
                self.assertEqual(reset.status_code, 200, reset.text)
                login = client.post(
                    "/api/auth/login",
                    json={
                        "email": "admin-created@example.com",
                        "password": "changed-456",
                        "device": {"device_id": "phone", "device_name": "Phone"},
                    },
                )
                self.assertEqual(login.status_code, 200, login.text)

                deleted = client.delete(f"/api/admin/accounts/{user_id}", headers=admin)
                self.assertEqual(deleted.status_code, 204, deleted.text)
                self.assertEqual(client.get(f"/api/admin/accounts/{user_id}", headers=admin).status_code, 404)

    def test_regular_user_token_is_not_an_admin_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = UserStore(root / "users.db")
            _, user_token = store.register()
            app = create_app(settings(), user_store=store, task_store=TaskStore(root / "agent.db"))
            with TestClient(app) as client:
                response = client.get(
                    "/api/admin/overview",
                    headers={"Authorization": f"Bearer {user_token}"},
                )
            self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
