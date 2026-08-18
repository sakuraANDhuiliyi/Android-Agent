from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from agent.governance import QuotaExceededError, SlidingWindowLimiter


class MemoryRateLimiter(SlidingWindowLimiter):
    pass


class SqliteRateLimiter:
    """Cross-process sliding window using the shared SQLite file."""

    def __init__(self, db_path: Path, table: str = "rate_windows") -> None:
        if not table.replace("_", "").isalnum():
            raise ValueError(f"invalid limiter table: {table}")
        self.db_path = Path(db_path)
        self.table = table
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    rate_key TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.table}_key_ts "
                f"ON {self.table}(rate_key, ts)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.time()
        cutoff = now - max(1, window_seconds)
        cap = max(1, limit)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"DELETE FROM {self.table} WHERE rate_key=? AND ts < ?",
                (key, cutoff),
            )
            count = conn.execute(
                f"SELECT COUNT(*) FROM {self.table} WHERE rate_key=?",
                (key,),
            ).fetchone()[0]
            if int(count) >= cap:
                raise QuotaExceededError("请求过于频繁，请稍后重试")
            conn.execute(
                f"INSERT INTO {self.table} (rate_key, ts) VALUES (?, ?)",
                (key, now),
            )
