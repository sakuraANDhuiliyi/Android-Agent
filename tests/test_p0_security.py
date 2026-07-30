from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings, UserAccount, load_settings
from agent.database import TaskStore
from agent.users import UserStore


def _settings(**overrides) -> Settings:
    values = {
        "provider": "openai",
        "api_key": "fake",
        "model": "fake",
        "model_candidates": ["fake"],
        "max_turns": 2,
        "max_auto_continuations": 0,
        "max_gradle_retries": 1,
        "compact_max_chars": 50_000,
        "max_output_tokens": 1024,
        "base_url": "https://example.test",
        "auto_build_after_edit": False,
        "server_host": "127.0.0.1",
        "server_port": 8000,
        "api_token": "",
        "users": [UserAccount(id="local", token="known-token")],
    }
    values.update(overrides)
    return Settings(**values)


class ApiBoundarySecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.data = root / "data"
        self.workspaces = root / "workspaces"
        self.builds = root / "builds"
        self.patches = [
            patch("agent.paths.DATA_DIR", self.data),
            patch("agent.paths.WORKSPACES_DIR", self.workspaces),
            patch("agent.paths.BUILDS_DIR", self.builds),
        ]
        for item in self.patches:
            item.start()
        self.user_store = UserStore(self.data / "users.db")
        self.task_store = TaskStore(self.data / "agent.db")

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _client(self, settings: Settings | None = None) -> TestClient:
        return TestClient(
            create_app(
                settings=settings or _settings(),
                user_store=self.user_store,
                task_store=self.task_store,
            )
        )

    def test_every_api_request_requires_bearer_token(self) -> None:
        with self._client() as client:
            self.assertEqual(client.get("/api/health").status_code, 401)
            self.assertEqual(
                client.get(
                    "/api/health",
                    headers={"Authorization": "known-token"},
                ).status_code,
                401,
            )
            self.assertEqual(
                client.get(
                    "/api/health",
                    headers={"Authorization": "Bearer wrong-token"},
                ).status_code,
                401,
            )
            response = client.get(
                "/api/health",
                headers={"Authorization": "Bearer known-token"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user_id"], "local")

    def test_network_registration_is_disabled_by_default(self) -> None:
        with self._client() as client:
            response = client.post("/api/register")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "网络注册未启用")

    def test_network_registration_requires_separate_secret(self) -> None:
        settings = replace(
            _settings(),
            registration_enabled=True,
            registration_token="registration-secret",
        )
        with self._client(settings) as client:
            self.assertEqual(client.post("/api/register").status_code, 401)
            self.assertEqual(
                client.post(
                    "/api/register",
                    headers={"X-Registration-Token": "wrong"},
                ).status_code,
                401,
            )
            response = client.post(
                "/api/register",
                headers={"X-Registration-Token": "registration-secret"},
            )
            self.assertEqual(response.status_code, 201)
            issued = response.json()
            health = client.get(
                "/api/health",
                headers={"Authorization": f"Bearer {issued['token']}"},
            )
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["user_id"], issued["user_id"])

    def test_enabled_registration_without_secret_fails_closed(self) -> None:
        settings = replace(
            _settings(),
            registration_enabled=True,
            registration_token="",
        )
        with self._client(settings) as client:
            response = client.post("/api/register")
        self.assertEqual(response.status_code, 503)

    def test_terminal_endpoints_are_disabled_by_default(self) -> None:
        with patch("agent.api.create_terminal") as create_terminal:
            with self._client() as client:
                response = client.post(
                    "/api/projects/demo/terminals",
                    headers={"Authorization": "Bearer known-token"},
                    json={"argv": ["sh", "-c", "touch /tmp/owned"]},
                )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "终端功能未启用")
        create_terminal.assert_not_called()

    def test_cors_does_not_reflect_untrusted_origin(self) -> None:
        with self._client() as client:
            denied = client.options(
                "/api/health",
                headers={
                    "Origin": "https://attacker.example",
                    "Access-Control-Request-Method": "GET",
                },
            )
            allowed = client.options(
                "/api/health",
                headers={
                    "Origin": "http://127.0.0.1:8000",
                    "Access-Control-Request-Method": "GET",
                },
            )
        self.assertNotIn("access-control-allow-origin", denied.headers)
        self.assertEqual(
            allowed.headers.get("access-control-allow-origin"),
            "http://127.0.0.1:8000",
        )


class ConfigurationSecurityTests(unittest.TestCase):
    def test_default_bind_address_is_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "missing.yaml"
            with patch("agent.config.CONFIG_PATH", config_path):
                settings = load_settings()
        self.assertEqual(settings.server_host, "127.0.0.1")
        self.assertFalse(settings.registration_enabled)
        self.assertFalse(settings.terminal_enabled)

    def test_string_false_does_not_enable_sensitive_features(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.yaml"
            config_path.write_text(
                "registration_enabled: 'false'\nterminal_enabled: 'false'\n",
                encoding="utf-8",
            )
            with patch("agent.config.CONFIG_PATH", config_path):
                settings = load_settings()
        self.assertFalse(settings.registration_enabled)
        self.assertFalse(settings.terminal_enabled)

    def test_wildcard_cors_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.yaml"
            config_path.write_text(
                "cors_allowed_origins:\n  - '*'\n",
                encoding="utf-8",
            )
            with patch("agent.config.CONFIG_PATH", config_path):
                with self.assertRaisesRegex(ValueError, "不允许使用通配符"):
                    load_settings()


if __name__ == "__main__":
    unittest.main()
