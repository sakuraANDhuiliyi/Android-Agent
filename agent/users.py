from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.paths import DATA_DIR, validate_id


class UserStore:
    """Small persistent user registry backed by SQLite.

    Only token hashes are stored. The plain token is returned once at registration
    and then kept by the client.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DATA_DIR / "users.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
                """
            )
            # Existing installations stored one token directly on the user
            # row. Mirror it into the token table so a desktop and an Android
            # client can receive separate credentials without invalidating the
            # token already in use on the other device.
            db.execute(
                """
                INSERT OR IGNORE INTO user_tokens (token_hash, user_id, created_at)
                SELECT token_hash, user_id, created_at FROM users
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(self) -> tuple[str, str]:
        user_id = validate_id(f"usr_{uuid.uuid4().hex}", kind="user_id")
        token = secrets.token_urlsafe(32)
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO users (user_id, token_hash, created_at) VALUES (?, ?, ?)",
                (user_id, self._token_hash(token), created_at),
            )
            db.execute(
                "INSERT INTO user_tokens (token_hash, user_id, created_at) VALUES (?, ?, ?)",
                (self._token_hash(token), user_id, created_at),
            )
        return user_id, token

    def issue_token(self, user_id: str) -> str:
        """Issue an additional credential without revoking existing clients."""
        normalized_user_id = validate_id(user_id, kind="user_id")
        token = secrets.token_urlsafe(32)
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM users WHERE user_id = ?",
                (normalized_user_id,),
            ).fetchone()
            if not exists:
                raise ValueError(f"用户不存在: {normalized_user_id}")
            db.execute(
                "INSERT INTO user_tokens (token_hash, user_id, created_at) VALUES (?, ?, ?)",
                (self._token_hash(token), normalized_user_id, created_at),
            )
        return token

    def authenticate(self, token: str) -> str | None:
        if not token:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT user_id FROM user_tokens WHERE token_hash = ?",
                (self._token_hash(token),),
            ).fetchone()
        return str(row[0]) if row else None
