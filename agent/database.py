from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agent.paths import DATA_DIR


class TaskStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATA_DIR / "agent.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()
        self._migrate_legacy_sessions()

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
                    conversation_id TEXT,
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
                CREATE TABLE IF NOT EXISTS project_sessions (
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    turns_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, project_id)
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '新对话',
                    status TEXT NOT NULL DEFAULT 'active',
                    turns_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_project
                    ON conversations(user_id, project_id, updated_at DESC);
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "conversation_id" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN conversation_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id, created_at DESC)"
            )

    def _migrate_legacy_sessions(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, project_id, turns_json, updated_at FROM project_sessions"
            ).fetchall()
            for row in rows:
                existing = conn.execute(
                    """SELECT id FROM conversations
                       WHERE user_id=? AND project_id=? AND title=? LIMIT 1""",
                    (row["user_id"], row["project_id"], "默认对话"),
                ).fetchone()
                if existing:
                    continue
                now = time.time()
                conv_id = uuid.uuid4().hex[:12]
                conn.execute(
                    """INSERT INTO conversations
                       (id, user_id, project_id, title, status, turns_json, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        conv_id,
                        row["user_id"],
                        row["project_id"],
                        "默认对话",
                        "active",
                        row["turns_json"] or "[]",
                        row["updated_at"] or now,
                        row["updated_at"] or now,
                    ),
                )

    def recover_interrupted(self) -> None:
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, status FROM tasks
                   WHERE status IN ('queued', 'running', 'awaiting_approval')"""
            ).fetchall()
            for row in rows:
                if row["status"] == "awaiting_approval":
                    msg = "Agent 服务重启，等待中的下载确认已失效，请重新发送需求"
                else:
                    msg = "Agent 服务重启，任务执行已中断"
                conn.execute(
                    """UPDATE tasks SET status='failed', finished_at=?, error_message=?
                       WHERE id=?""",
                    (now, msg, row["id"]),
                )
                conn.execute(
                    """INSERT INTO task_events(task_id,type,message,payload,created_at)
                       VALUES(?,?,?,?,?)""",
                    (
                        row["id"],
                        "failed",
                        msg,
                        json.dumps({"message": msg, "error": msg}, ensure_ascii=False),
                        now,
                    ),
                )

    def create_conversation(
        self,
        user_id: str,
        project_id: str,
        *,
        title: str = "新对话",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        conv_id = conversation_id or uuid.uuid4().hex[:12]
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO conversations
                   (id, user_id, project_id, title, status, turns_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (conv_id, user_id, project_id, title or "新对话", "active", "[]", now, now),
            )
        return self.get_conversation(conv_id, user_id) or {}

    def get_conversation(self, conversation_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM conversations WHERE id=?"
        params: tuple[Any, ...] = (conversation_id,)
        if user_id is not None:
            query += " AND user_id=?"
            params += (user_id,)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._row_to_conversation(row) if row else None

    def list_conversations(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM conversations
                   WHERE user_id=? AND project_id=? AND status!='archived'
                   ORDER BY updated_at DESC""",
                (user_id, project_id),
            ).fetchall()
        return [self._row_to_conversation(row) for row in rows]

    def update_conversation(self, conversation_id: str, user_id: str, **values: Any) -> dict[str, Any] | None:
        if not values:
            return self.get_conversation(conversation_id, user_id)
        encoded = dict(values)
        if "turns" in encoded:
            encoded["turns_json"] = json.dumps(encoded.pop("turns"), ensure_ascii=False)
        encoded["updated_at"] = time.time()
        columns = ", ".join(f"{key}=?" for key in encoded)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE conversations SET {columns} WHERE id=? AND user_id=?",
                (*encoded.values(), conversation_id, user_id),
            )
        return self.get_conversation(conversation_id, user_id)

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET status='archived', updated_at=? WHERE id=? AND user_id=?",
                (time.time(), conversation_id, user_id),
            )
        return cursor.rowcount > 0

    def get_or_create_default_conversation(self, user_id: str, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM conversations
                   WHERE user_id=? AND project_id=? AND status!='archived' AND title=?
                   ORDER BY created_at ASC LIMIT 1""",
                (user_id, project_id, "默认对话"),
            ).fetchone()
        if row:
            return self._row_to_conversation(row)
        convs = self.list_conversations(user_id, project_id)
        if convs:
            # Prefer the oldest active conversation as a stable legacy default
            oldest = min(convs, key=lambda c: c.get("created_at") or 0)
            return oldest
        return self.create_conversation(user_id, project_id, title="默认对话")

    def get_conversation_turns(self, conversation_id: str) -> list[dict[str, Any]]:
        conv = self.get_conversation(conversation_id)
        if not conv:
            return []
        return list(conv.get("turns") or [])

    def append_conversation_turn(
        self,
        conversation_id: str,
        *,
        user: str,
        assistant: str,
        changed_files: list | None = None,
        auto_title: bool = True,
    ) -> list[dict[str, Any]]:
        conv = self.get_conversation(conversation_id)
        if not conv:
            raise ValueError(f"对话不存在: {conversation_id}")
        turns = list(conv.get("turns") or [])
        turns.append(
            {
                "user": user,
                "assistant": assistant,
                "changed_files": changed_files or [],
                "ts": time.time(),
            }
        )
        trimmed = turns[-24:]
        updates: dict[str, Any] = {"turns": trimmed}
        if auto_title and (conv.get("title") in {"新对话", "默认对话", ""}) and user.strip():
            updates["title"] = user.strip()[:40]
        self.update_conversation(conversation_id, conv["user_id"], **updates)
        return trimmed

    def create_task(self, task: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tasks
                   (id,user_id,project_id,conversation_id,prompt,status,provider,model,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    task["id"],
                    task["user_id"],
                    task["project_id"],
                    task.get("conversation_id"),
                    task["prompt"],
                    task["status"],
                    task.get("provider"),
                    task.get("model"),
                    task["created_at"],
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
                (
                    task_id,
                    event_type,
                    message,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    created_at,
                ),
            )
        return {"id": cursor.lastrowid, "type": event_type, "ts": created_at, **payload}

    def request_cancel(self, task_id: str, user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET cancel_requested=1
                   WHERE id=? AND user_id=? AND status IN ('queued','running','awaiting_approval')""",
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

    def list_tasks(
        self,
        user_id: str,
        project_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks WHERE user_id=?"
        params: list[Any] = [user_id]
        if project_id:
            query += " AND project_id=?"
            params.append(project_id)
        if conversation_id:
            query += " AND conversation_id=?"
            params.append(conversation_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_session_turns(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        conv = self.get_or_create_default_conversation(user_id, project_id)
        return list(conv.get("turns") or [])

    def append_session_turn(
        self,
        user_id: str,
        project_id: str,
        *,
        user: str,
        assistant: str,
        changed_files: list | None = None,
    ) -> list[dict[str, Any]]:
        """Compatibility: append to the project's default conversation."""
        conv = self.get_or_create_default_conversation(user_id, project_id)
        return self.append_conversation_turn(
            conv["id"],
            user=user,
            assistant=assistant,
            changed_files=changed_files,
        )

    def clear_session(self, user_id: str, project_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM project_sessions WHERE user_id=? AND project_id=?",
                (user_id, project_id),
            )
        conv = self.get_or_create_default_conversation(user_id, project_id)
        self.update_conversation(conv["id"], user_id, turns=[])


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

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["turns"] = json.loads(item.pop("turns_json", None) or "[]")
        except json.JSONDecodeError:
            item["turns"] = []
        item["turn_count"] = len(item["turns"])
        return item
