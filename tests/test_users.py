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

    def test_existing_database_is_migrated_to_token_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "users.db"
            first = UserStore(db_path)
            user_id, token = first.register()

            reopened = UserStore(db_path)

            self.assertEqual(reopened.authenticate(token), user_id)


if __name__ == "__main__":
    unittest.main()
