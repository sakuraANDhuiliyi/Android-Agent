from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.database import TaskStore
from agent.users import UserStore


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
        registration_enabled=True,
        minimum_free_disk_bytes=0,
    )


class AccountApiTests(unittest.TestCase):
    def test_register_login_profile_and_device_revocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(
                settings(),
                user_store=UserStore(root / "users.db"),
                task_store=TaskStore(root / "agent.db"),
            )
            with (
                patch("agent.api.user_workspaces_dir", side_effect=lambda user: root / "workspaces" / user),
                patch("agent.api.user_builds_dir", side_effect=lambda user: root / "builds" / user),
                TestClient(app) as client,
            ):
                registered = client.post(
                    "/api/auth/register",
                    json={
                        "email": "linchu@example.com",
                        "password": "secure-123",
                        "display_name": "林初",
                        "device": {
                            "device_id": "pixel",
                            "device_name": "Pixel 8 Pro",
                            "device_type": "android",
                            "platform": "Android 15",
                            "app_version": "1.0",
                        },
                    },
                )
                self.assertEqual(registered.status_code, 201, registered.text)
                first = registered.json()
                self.assertFalse(first["requires_verification"])
                self.assertTrue(first["token"])

                logged_in = client.post(
                    "/api/auth/login",
                    json={
                        "email": "linchu@example.com",
                        "password": "secure-123",
                        "device": {
                            "device_id": "galaxy",
                            "device_name": "Galaxy S24",
                        },
                    },
                )
                self.assertEqual(logged_in.status_code, 200, logged_in.text)
                second = logged_in.json()
                headers = {"Authorization": f"Bearer {second['token']}"}

                account = client.patch(
                    "/api/account",
                    headers=headers,
                    json={"display_name": "林初 · 云端"},
                )
                self.assertEqual(account.status_code, 200)
                self.assertEqual(account.json()["display_name"], "林初 · 云端")

                devices = client.get("/api/devices", headers=headers).json()["devices"]
                self.assertEqual({item["device_id"] for item in devices}, {"pixel", "galaxy"})
                current = next(item for item in devices if item["current"])
                self.assertEqual(current["session_id"], second["session_id"])

                revoked = client.post("/api/devices/logout-others", headers=headers)
                self.assertEqual(revoked.json()["revoked"], 1)
                stale = client.get(
                    "/api/account",
                    headers={"Authorization": f"Bearer {first['token']}"},
                )
                self.assertEqual(stale.status_code, 401)
                self.assertEqual(client.get("/api/account", headers=headers).status_code, 200)

                deleted = client.request(
                    "DELETE",
                    "/api/account",
                    headers=headers,
                    json={"password": "secure-123"},
                )
                self.assertEqual(deleted.status_code, 204, deleted.text)
                self.assertEqual(client.get("/api/account", headers=headers).status_code, 401)
                self.assertFalse((root / "workspaces" / first["user_id"]).exists())
                self.assertFalse((root / "builds" / first["user_id"]).exists())

    def test_login_errors_keep_machine_readable_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(
                settings(),
                user_store=UserStore(root / "users.db"),
                task_store=TaskStore(root / "agent.db"),
            )
            with TestClient(app) as client:
                response = client.post(
                    "/api/auth/login",
                    json={
                        "email": "missing@example.com",
                        "password": "incorrect",
                        "device": {"device_id": "phone", "device_name": "Phone"},
                    },
                )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "account_not_found")


if __name__ == "__main__":
    unittest.main()
