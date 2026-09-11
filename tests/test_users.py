from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.users import UserStore


class UserTokenTests(unittest.TestCase):
    def test_additional_token_keeps_original_token_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.db")
            user_id, original = store.register()

            additional = store.issue_token(user_id)

            self.assertNotEqual(additional, original)
            self.assertEqual(store.authenticate(original), user_id)
            self.assertEqual(store.authenticate(additional), user_id)

    def test_tokens_are_isolated_across_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.db")
            user_a, token_a = store.register()
            user_b, token_b = store.register()
            extra_a = store.issue_token(user_a)

            self.assertEqual(store.authenticate(token_a), user_a)
            self.assertEqual(store.authenticate(extra_a), user_a)
            self.assertEqual(store.authenticate(token_b), user_b)
            self.assertNotEqual(user_a, user_b)
            self.assertIsNone(store.authenticate("not-a-real-token"))

    def test_existing_database_is_migrated_to_token_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "users.db"
            first = UserStore(db_path)
            user_id, token = first.register()

            reopened = UserStore(db_path)

            self.assertEqual(reopened.authenticate(token), user_id)

    def test_account_login_creates_independent_device_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.db")
            first = store.register_account(
                "LinChu@example.com",
                "secure-123",
                display_name="林初",
                device={"device_id": "pixel", "device_name": "Pixel 8 Pro", "platform": "Android 15"},
            )
            second = store.login(
                "linchu@example.com",
                "secure-123",
                device={"device_id": "mac", "device_name": "MacBook Pro", "platform": "macOS 14.5"},
            )

            self.assertEqual(first["account"]["user_id"], second["account"]["user_id"])
            self.assertEqual(store.authenticate(first["token"]), first["account"]["user_id"])
            self.assertEqual(store.authenticate(second["token"]), first["account"]["user_id"])
            sessions = store.list_sessions(
                first["account"]["user_id"],
                current_session_id=second["session_id"],
            )
            self.assertEqual({item["device_id"] for item in sessions}, {"pixel", "mac"})
            self.assertEqual(sum(bool(item["current"]) for item in sessions), 1)

            self.assertTrue(store.revoke_session(first["account"]["user_id"], first["session_id"]))
            self.assertIsNone(store.authenticate(first["token"]))
            self.assertEqual(store.authenticate(second["token"]), first["account"]["user_id"])

    def test_password_change_revokes_other_devices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.db")
            first = store.register_account(
                "user@example.com", "old-pass-1", device={"device_id": "one", "device_name": "One"}
            )
            second = store.login(
                "user@example.com", "old-pass-1", device={"device_id": "two", "device_name": "Two"}
            )
            store.change_password(
                first["account"]["user_id"],
                "old-pass-1",
                "new-pass-2",
                current_session_id=first["session_id"],
            )

            self.assertEqual(store.authenticate(first["token"]), first["account"]["user_id"])
            self.assertIsNone(store.authenticate(second["token"]))
            with self.assertRaisesRegex(ValueError, "密码错误"):
                store.login("user@example.com", "old-pass-1")
            third = store.login("user@example.com", "new-pass-2", device={"device_id": "three", "device_name": "Three"})
            self.assertEqual(store.authenticate(third["token"]), first["account"]["user_id"])

    def test_email_verification_code_is_one_time_and_issues_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.db")
            pending = store.register_account(
                "verify@example.com", "secure-123", email_verified=False
            )
            code = store.create_code(pending["account"]["user_id"], "verify_email")
            verified = store.verify_email_and_login(
                "verify@example.com",
                code,
                device={"device_id": "phone", "device_name": "Phone"},
            )
            self.assertTrue(verified["account"]["email_verified"])
            self.assertEqual(store.authenticate(verified["token"]), pending["account"]["user_id"])
            with self.assertRaisesRegex(ValueError, "验证码无效"):
                store.verify_email_and_login(
                    "verify@example.com",
                    code,
                    device={"device_id": "other", "device_name": "Other"},
                )

    def test_deleted_account_revokes_tokens_and_removes_pii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "users.db")
            account = store.register_account("delete@example.com", "secure-123")
            store.delete_account(account["account"]["user_id"], "secure-123")
            self.assertIsNone(store.authenticate(account["token"]))
            self.assertIsNone(store.account_for_email("delete@example.com"))


if __name__ == "__main__":
    unittest.main()
