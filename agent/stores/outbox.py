from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping


def enqueue_on_connection(
    conn: sqlite3.Connection,
    *,
    topic: str,
    payload: Mapping[str, Any],
    created_at: float | None = None,
) -> str:
    entry_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO outbox
           (id, topic, payload_json, status, attempts, created_at, available_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            entry_id,
            topic,
            json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")),
            "pending",
            0,
            created_at if created_at is not None else time.time(),
            created_at if created_at is not None else time.time(),
        ),
    )
    return entry_id


def ensure_outbox_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','claimed','delivered','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            available_at REAL NOT NULL,
            claimed_by TEXT,
            claimed_at REAL,
            delivered_at REAL,
            last_error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outbox_status_available "
        "ON outbox(status, available_at)"
    )


class SqliteOutboxStore:
    """Durable notification outbox. Claim is exclusive across processes."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            ensure_outbox_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def enqueue(self, topic: str, payload: Mapping[str, Any]) -> str:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return enqueue_on_connection(conn, topic=topic, payload=payload)

    def claim_batch(
        self,
        worker_id: str,
        *,
        limit: int = 16,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        stamp = now if now is not None else time.time()
        claimed: list[dict[str, Any]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT id FROM outbox
                   WHERE status='pending' AND available_at<=?
                   ORDER BY created_at ASC, id ASC
                   LIMIT ?""",
                (stamp, max(1, limit)),
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    """UPDATE outbox
                       SET status='claimed', claimed_by=?, claimed_at=?, attempts=attempts+1
                       WHERE id=? AND status='pending'""",
                    (worker_id, stamp, row["id"]),
                )
                if cursor.rowcount == 0:
                    continue
                full = conn.execute(
                    "SELECT * FROM outbox WHERE id=?", (row["id"],)
                ).fetchone()
                claimed.append(dict(full))
        return claimed

    def mark_delivered(self, entry_id: str, worker_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE outbox SET status='delivered', delivered_at=?
                   WHERE id=? AND claimed_by=? AND status='claimed'""",
                (time.time(), entry_id, worker_id),
            )
        return cursor.rowcount > 0

    def mark_failed(self, entry_id: str, worker_id: str, error: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE outbox
                   SET status='pending', claimed_by=NULL, claimed_at=NULL,
                       available_at=?, last_error=?
                   WHERE id=? AND claimed_by=? AND status='claimed'""",
                (time.time() + 5, error[:500], entry_id, worker_id),
            )
        return cursor.rowcount > 0

    def deliver_batch(
        self,
        worker_id: str,
        sink: Callable[[dict[str, Any]], None],
        *,
        limit: int = 16,
    ) -> int:
        delivered = 0
        for entry in self.claim_batch(worker_id, limit=limit):
            try:
                sink(entry)
            except Exception as exc:  # pragma: no cover - exercised in tests
                self.mark_failed(entry["id"], worker_id, str(exc))
                continue
            if self.mark_delivered(entry["id"], worker_id):
                delivered += 1
        return delivered
