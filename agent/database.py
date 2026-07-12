from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from agent.paths import DATA_DIR


class TaskStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATA_DIR / "agent.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    final_message TEXT,
                    error_message TEXT,
                    apk_path TEXT,
                    build_log_path TEXT,
                    changed_files TEXT NOT NULL DEFAULT '[]',
                    diff TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_user_project
                    ON tasks(user_id, project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    message TEXT,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_task
                    ON task_events(task_id, id);
                """
            )

    def recover_interrupted(self) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """UPDATE tasks SET status='failed', finished_at=?,
                   error_message='Agent 服务重启，任务执行已中断'
                   WHERE status IN ('queued', 'running')""",
                (now,),
            )

    def create_task(self, task: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tasks
                   (id,user_id,project_id,prompt,status,provider,model,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    task["id"], task["user_id"], task["project_id"], task["prompt"],
                    task["status"], task.get("provider"), task.get("model"), task["created_at"],
                ),
            )

    def update_task(self, task_id: str, **values: Any) -> None:
        if not values:
            return
        encoded = dict(values)
        if "changed_files" in encoded:
            encoded["changed_files"] = json.dumps(encoded["changed_files"], ensure_ascii=False)
        columns = ", ".join(f"{key}=?" for key in encoded)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {columns} WHERE id=?",
                (*encoded.values(), task_id),
            )

    def add_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        created_at = time.time()
        message = payload.get("message")
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO task_events(task_id,type,message,payload,created_at) VALUES(?,?,?,?,?)",
                (task_id, event_type, message, json.dumps(payload, ensure_ascii=False, default=str), created_at),
            )
        return {"id": cursor.lastrowid, "type": event_type, "ts": created_at, **payload}

    def request_cancel(self, task_id: str, user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET cancel_requested=1
                   WHERE id=? AND user_id=? AND status IN ('queued','running')""",
                (task_id, user_id),
            )
        return cursor.rowcount > 0

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and row[0])

    def get_task(self, task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM tasks WHERE id=?"
        params: tuple[Any, ...] = (task_id,)
        if user_id is not None:
            query += " AND user_id=?"
            params += (user_id,)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
            if not row:
                return None
            task = self._row_to_task(row)
            events = conn.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,)
            ).fetchall()
        task["events"] = [self._row_to_event(item) for item in events]
        return task

    def list_tasks(self, user_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks WHERE user_id=?"
        params: list[Any] = [user_id]
        if project_id:
            query += " AND project_id=?"
            params.append(project_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["cancel_requested"] = bool(task["cancel_requested"])
        task["changed_files"] = json.loads(task["changed_files"] or "[]")
        return task

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload"] or "{}")
        return {"id": row["id"], "type": row["type"], "ts": row["created_at"], **payload}

