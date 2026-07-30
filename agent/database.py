from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agent.paths import DATA_DIR
from agent.redaction import redact_sensitive_value


logger = logging.getLogger(__name__)


class TaskStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATA_DIR / "agent.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()
        self._migrate_legacy_sessions()
        self._migrate_legacy_conversation_turns()

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
                    total_tokens INTEGER,
                    recovery_of_task_id TEXT,
                    recovery_attempt INTEGER NOT NULL DEFAULT 0,
                    context_json TEXT,
                    claim_owner TEXT,
                    lease_expires_at REAL,
                    heartbeat_at REAL,
                    attempt INTEGER NOT NULL DEFAULT 0
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
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    task_id TEXT UNIQUE,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    error_message TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation_created
                    ON conversation_turns(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_task
                    ON conversation_turns(task_id);
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_user_project
                    ON conversation_turns(user_id, project_id);
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    turn_id TEXT NOT NULL
                        REFERENCES conversation_turns(id) ON DELETE CASCADE,
                    task_id TEXT,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    role TEXT,
                    context_visible INTEGER NOT NULL DEFAULT 0
                        CHECK (context_visible IN (0, 1)),
                    provider TEXT,
                    model TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    event_key TEXT,
                    created_at REAL NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(conversation_id, seq),
                    UNIQUE(conversation_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_events_conversation_seq
                    ON conversation_events(conversation_id, seq);
                CREATE INDEX IF NOT EXISTS idx_conversation_events_turn_seq
                    ON conversation_events(turn_id, seq);
                CREATE INDEX IF NOT EXISTS idx_conversation_events_task
                    ON conversation_events(task_id);
                CREATE INDEX IF NOT EXISTS idx_conversation_events_type
                    ON conversation_events(event_type);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    conversation_id TEXT,
                    turn_id TEXT,
                    task_id TEXT,
                    kind TEXT NOT NULL CHECK (kind IN ('before_turn', 'after_turn', 'manual')),
                    base_revision TEXT,
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoints_user_project
                    ON checkpoints(user_id, project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_turn
                    ON checkpoints(turn_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS task_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    message_key TEXT NOT NULL,
                    type TEXT NOT NULL CHECK (type IN ('steer', 'follow_up', 'cancel', 'pause', 'resume')),
                    payload TEXT NOT NULL DEFAULT '{}',
                    consumed_at REAL,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id, message_key)
                );
                CREATE INDEX IF NOT EXISTS idx_task_messages_task
                    ON task_messages(task_id, consumed_at, created_at);
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id, depends_on_task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_task_dependencies_task
                    ON task_dependencies(task_id);
                CREATE INDEX IF NOT EXISTS idx_task_dependencies_depends
                    ON task_dependencies(depends_on_task_id);
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "conversation_id" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN conversation_id TEXT")
            if "recovery_of_task_id" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN recovery_of_task_id TEXT"
                )
            if "recovery_attempt" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN recovery_attempt INTEGER NOT NULL DEFAULT 0"
                )
            if "claim_owner" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN claim_owner TEXT")
            if "lease_expires_at" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN lease_expires_at REAL")
            if "heartbeat_at" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN heartbeat_at REAL")
            if "attempt" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
            if "context_json" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN context_json TEXT")
            if "parent_task_id" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT")
            if "role" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN role TEXT")
            if "write_lock_key" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN write_lock_key TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id, created_at DESC)"
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_tasks_recovery
                   ON tasks(recovery_of_task_id, recovery_attempt)"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id)"
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_tasks_write_lock
                   ON tasks(write_lock_key, status)"""
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

    def _migrate_legacy_conversation_turns(self) -> None:
        with self._connect() as conn:
            conversations = conn.execute(
                """SELECT c.id, c.user_id, c.project_id, c.turns_json,
                          c.created_at,
                          EXISTS(
                              SELECT 1 FROM conversation_events AS e
                              WHERE e.conversation_id=c.id
                          ) AS has_events
                   FROM conversations AS c
                   ORDER BY c.created_at, c.id"""
            ).fetchall()
        for conversation in conversations:
            if conversation["has_events"]:
                continue
            try:
                raw_turns = json.loads(conversation["turns_json"] or "[]")
                if not isinstance(raw_turns, list):
                    raise ValueError("turns_json must decode to a list")
                legacy_turns = self._normalize_legacy_turns(conversation, raw_turns)
                if legacy_turns:
                    self._import_legacy_turns(conversation, legacy_turns)
            except Exception as exc:
                logger.warning(
                    "Skipping legacy turns migration for conversation %s: %s",
                    conversation["id"],
                    exc,
                )

    @staticmethod
    def _normalize_legacy_turns(
        conversation: sqlite3.Row,
        raw_turns: list[Any],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        base_time = float(conversation["created_at"] or time.time())
        for index, raw_turn in enumerate(raw_turns):
            if not isinstance(raw_turn, dict):
                logger.warning(
                    "Skipping malformed legacy turn %s in conversation %s: expected object",
                    index,
                    conversation["id"],
                )
                continue
            timestamp = raw_turn.get("ts")
            if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
                timestamp = base_time + index * 0.000001
            changed_files = raw_turn.get("changed_files") or []
            if not isinstance(changed_files, list):
                logger.warning(
                    "Ignoring malformed changed_files in legacy turn %s of conversation %s",
                    index,
                    conversation["id"],
                )
                changed_files = []
            user = raw_turn.get("user", "")
            assistant = raw_turn.get("assistant", "")
            normalized.append(
                {
                    "legacy_index": index,
                    "user": user if isinstance(user, str) else str(user),
                    "assistant": (
                        assistant if isinstance(assistant, str) else str(assistant)
                    ),
                    "changed_files": changed_files,
                    "created_at": float(timestamp),
                }
            )
        return normalized

    def _import_legacy_turns(
        self,
        conversation: sqlite3.Row,
        legacy_turns: list[dict[str, Any]],
    ) -> None:
        from agent.conversation_events import (
            ConversationEventType as EventType,
            serialize_event_payload,
        )

        conversation_id = conversation["id"]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """SELECT 1 FROM conversation_events
                   WHERE conversation_id=? LIMIT 1""",
                (conversation_id,),
            ).fetchone()
            if existing:
                return

            seq = 0
            for legacy_turn in legacy_turns:
                index = legacy_turn["legacy_index"]
                created_at = legacy_turn["created_at"]
                turn_id = self._legacy_stable_id(
                    conversation_id, index, "conversation_turn"
                )
                conn.execute(
                    """INSERT OR IGNORE INTO conversation_turns
                       (id, conversation_id, task_id, user_id, project_id, status,
                        provider, model, created_at, started_at, finished_at,
                        error_message, schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        turn_id,
                        conversation_id,
                        None,
                        conversation["user_id"],
                        conversation["project_id"],
                        "succeeded",
                        None,
                        None,
                        created_at,
                        created_at,
                        created_at,
                        None,
                        1,
                    ),
                )
                user_message_id = self._legacy_stable_id(
                    conversation_id, index, "user_message"
                )
                assistant_message_id = self._legacy_stable_id(
                    conversation_id, index, "assistant_message"
                )
                event_specs = [
                    (
                        EventType.USER_MESSAGE,
                        "user",
                        True,
                        {
                            "message_id": user_message_id,
                            "content": [
                                {"type": "text", "text": legacy_turn["user"]}
                            ],
                            "source": "legacy",
                            "legacy_imported": True,
                        },
                    ),
                    (
                        EventType.ASSISTANT_MESSAGE,
                        "assistant",
                        True,
                        {
                            "message_id": assistant_message_id,
                            "text_blocks": [
                                {
                                    "block_index": 0,
                                    "type": "text",
                                    "text": legacy_turn["assistant"],
                                }
                            ],
                            "finish_reason": "stop",
                            "is_final": True,
                            "streamed": False,
                            "legacy_imported": True,
                        },
                    ),
                ]
                if legacy_turn["changed_files"]:
                    event_specs.append(
                        (
                            EventType.CHANGES,
                            None,
                            True,
                            {
                                "changed_files": legacy_turn["changed_files"],
                                "legacy_imported": True,
                            },
                        )
                    )
                event_specs.append(
                    (
                        EventType.TURN_COMPLETED,
                        None,
                        False,
                        {"status": "succeeded", "legacy_imported": True},
                    )
                )
                for event_type, role, context_visible, payload in event_specs:
                    seq += 1
                    event_id = self._legacy_stable_id(
                        conversation_id, index, f"event:{event_type}"
                    )
                    event_key = (
                        f"legacy:{conversation_id}:{index}:{event_type}"
                    )
                    conn.execute(
                        """INSERT OR IGNORE INTO conversation_events
                           (id, conversation_id, turn_id, task_id, seq,
                            event_type, role, context_visible, provider, model,
                            payload_json, event_key, created_at, schema_version)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event_id,
                            conversation_id,
                            turn_id,
                            None,
                            seq,
                            event_type,
                            role,
                            int(context_visible),
                            None,
                            None,
                            serialize_event_payload(payload),
                            event_key,
                            created_at,
                            1,
                        ),
                    )

    @staticmethod
    def _legacy_stable_id(
        conversation_id: str,
        turn_index: int,
        kind: str,
    ) -> str:
        value = f"android-agent:legacy:{conversation_id}:{turn_index}:{kind}"
        return uuid.uuid5(uuid.NAMESPACE_URL, value).hex

    def recover_interrupted(self) -> list[dict[str, Any]]:
        now = time.time()
        recovered: list[dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, status FROM tasks
                   WHERE status IN ('queued', 'running', 'awaiting_approval', 'paused')"""
            ).fetchall()
            for row in rows:
                if row["status"] == "queued":
                    conn.execute(
                        """UPDATE tasks SET claim_owner=NULL, lease_expires_at=NULL,
                           heartbeat_at=NULL, attempt=0
                           WHERE id=?""",
                        (row["id"],),
                    )
                    continue
                if row["status"] == "paused":
                    conn.execute(
                        """UPDATE tasks SET status='queued', claim_owner=NULL,
                           lease_expires_at=NULL, heartbeat_at=NULL
                           WHERE id=?""",
                        (row["id"],),
                    )
                    continue
                if row["status"] == "awaiting_approval":
                    msg = "Agent 服务重启，等待中的下载确认已失效，请重新发送需求"
                else:
                    msg = "Agent 服务重启，任务执行已中断"
                conn.execute(
                    """UPDATE tasks SET status='failed', finished_at=?, error_message=?,
                       claim_owner=NULL, lease_expires_at=NULL, heartbeat_at=NULL
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

        from agent.conversation_events import (
            ConversationEventStore,
            ConversationEventType as EventType,
        )

        event_store = ConversationEventStore(self)
        with self._connect() as conn:
            turns = conn.execute(
                """SELECT t.*, j.prompt AS original_prompt,
                          j.cancel_requested AS task_cancel_requested,
                          j.recovery_of_task_id,
                          COALESCE(j.recovery_attempt, 0) AS recovery_attempt
                   FROM conversation_turns AS t
                   LEFT JOIN tasks AS j ON j.id=t.task_id
                   WHERE t.status IN ('running', 'awaiting_approval')
                   ORDER BY t.created_at, t.id"""
            ).fetchall()

        for row in turns:
            turn = dict(row)
            turn_id = turn["id"]
            conversation_id = turn["conversation_id"]
            task_id = turn.get("task_id")
            prior_status = turn["status"]
            message = (
                "Agent 服务重启，等待中的下载确认已失效"
                if prior_status == "awaiting_approval"
                else "Agent 服务重启，任务执行已中断"
            )
            events = event_store.list_turn_events(
                turn_id,
                user_id=turn["user_id"],
            )
            calls: dict[str, dict[str, Any]] = {}
            completed_call_ids: set[str] = set()
            for event in events:
                payload = event.get("payload") or {}
                tool_call_id = payload.get("tool_call_id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    continue
                if event["event_type"] == EventType.TOOL_CALL:
                    calls.setdefault(tool_call_id, event)
                elif event["event_type"] == EventType.TOOL_RESULT:
                    if tool_call_id in calls:
                        completed_call_ids.add(tool_call_id)
                    else:
                        logger.warning(
                            "Ignoring orphan tool_result %s while recovering turn %s",
                            tool_call_id,
                            turn_id,
                        )

            for tool_call_id, call_event in calls.items():
                if tool_call_id in completed_call_ids:
                    continue
                call_payload = call_event.get("payload") or {}
                event_store.append_event_idempotent(
                    conversation_id,
                    turn_id,
                    EventType.TOOL_RESULT,
                    f"recovery:{turn_id}:tool_result:{tool_call_id}",
                    {
                        "tool_call_id": tool_call_id,
                        "name": str(call_payload.get("name") or ""),
                        "ok": False,
                        "model_output": "工具调用因 Agent 服务中断，未完成执行。",
                        "structured_output": None,
                        "duration_ms": None,
                        "error_type": "service_interrupted",
                        "interrupted": True,
                    },
                    task_id=task_id,
                    context_visible=True,
                )

            event_store.append_event_idempotent(
                conversation_id,
                turn_id,
                EventType.TURN_INTERRUPTED,
                f"recovery:{turn_id}:interrupted",
                {
                    "message": message,
                    "previous_status": prior_status,
                },
                task_id=task_id,
                context_visible=False,
            )
            event_store.update_turn_status(
                turn_id,
                "interrupted",
                user_id=turn["user_id"],
                finished_at=now,
                error_message=message,
            )
            if (
                task_id
                and not bool(turn.get("task_cancel_requested"))
                and int(turn.get("recovery_attempt") or 0) < 3
            ):
                recovered.append(
                    {
                        "conversation_id": conversation_id,
                        "interrupted_turn_id": turn_id,
                        "original_task_id": task_id,
                        "recovery_root_task_id": (
                            turn.get("recovery_of_task_id") or task_id
                        ),
                        "recovery_attempt": int(
                            turn.get("recovery_attempt") or 0
                        )
                        + 1,
                        "user_id": turn["user_id"],
                        "project_id": turn["project_id"],
                        "provider": turn.get("provider"),
                        "model": turn.get("model"),
                        "original_prompt": turn.get("original_prompt") or "",
                    }
                )
        return recovered

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

    def list_conversations(
        self, user_id: str, project_id: str, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM conversations WHERE user_id=? AND project_id=?"
        params: tuple[Any, ...] = (user_id, project_id)
        if not include_archived:
            query += " AND status!='archived'"
        query += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
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

    def restore_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET status='active', updated_at=? WHERE id=? AND user_id=?",
                (time.time(), conversation_id, user_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_conversation(conversation_id, user_id)

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
        """Return the legacy final-turn projection for older clients only."""
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
        """Compatibility writer that translates one legacy turn into events.

        New Agent jobs write canonical events directly and never call this method.
        """
        conv = self.get_conversation(conversation_id)
        if not conv:
            raise ValueError(f"对话不存在: {conversation_id}")
        from agent.conversation_events import (
            ConversationEventStore,
            ConversationEventType as EventType,
        )

        event_store = ConversationEventStore(self)
        now = time.time()
        turn = event_store.create_turn(
            conversation_id,
            conv["user_id"],
            conv["project_id"],
            status="succeeded",
            created_at=now,
            started_at=now,
            finished_at=now,
        )
        turn_id = turn["id"]
        event_store.append_event_idempotent(
            conversation_id,
            turn_id,
            EventType.USER_MESSAGE,
            f"turn:{turn_id}:user_message",
            {
                "message_id": uuid.uuid4().hex,
                "content": [{"type": "text", "text": user}],
            },
            role="user",
            context_visible=True,
            created_at=now,
        )
        event_store.append_event_idempotent(
            conversation_id,
            turn_id,
            EventType.ASSISTANT_MESSAGE,
            f"turn:{turn_id}:assistant_message",
            {
                "message_id": uuid.uuid4().hex,
                "text_blocks": [
                    {"block_index": 0, "type": "text", "text": assistant}
                ],
                "finish_reason": "stop",
                "is_final": True,
                "streamed": False,
            },
            role="assistant",
            context_visible=True,
            created_at=now,
        )
        if changed_files:
            event_store.append_event_idempotent(
                conversation_id,
                turn_id,
                EventType.CHANGES,
                f"turn:{turn_id}:changes",
                {"changed_files": changed_files},
                context_visible=True,
                created_at=now,
            )
        event_store.append_event_idempotent(
            conversation_id,
            turn_id,
            EventType.TURN_COMPLETED,
            f"turn:{turn_id}:completed",
            {"status": "succeeded"},
            created_at=now,
        )
        if auto_title and (conv.get("title") in {"新对话", "默认对话", ""}) and user.strip():
            self.update_conversation(
                conversation_id,
                conv["user_id"],
                title=user.strip()[:40],
            )
        return event_store.project_legacy_turns(conversation_id)

    def create_task(self, task: dict[str, Any]) -> None:
        safe_task = redact_sensitive_value(task)
        context = safe_task.get("context_json")
        if context is None and isinstance(safe_task.get("context"), dict):
            context = safe_task["context"]
        if isinstance(context, dict):
            context = json.dumps(context, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tasks
                   (id,user_id,project_id,conversation_id,prompt,status,provider,
                    model,created_at,recovery_of_task_id,recovery_attempt,context_json,
                    claim_owner,lease_expires_at,heartbeat_at,attempt,
                    parent_task_id,role,write_lock_key)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    safe_task["id"],
                    safe_task["user_id"],
                    safe_task["project_id"],
                    safe_task.get("conversation_id"),
                    safe_task["prompt"],
                    safe_task["status"],
                    safe_task.get("provider"),
                    safe_task.get("model"),
                    safe_task["created_at"],
                    safe_task.get("recovery_of_task_id"),
                    int(safe_task.get("recovery_attempt") or 0),
                    context,
                    safe_task.get("claim_owner"),
                    safe_task.get("lease_expires_at"),
                    safe_task.get("heartbeat_at"),
                    int(safe_task.get("attempt") or 0),
                    safe_task.get("parent_task_id"),
                    safe_task.get("role"),
                    safe_task.get("write_lock_key"),
                ),
            )

    def update_task(self, task_id: str, **values: Any) -> None:
        if not values:
            return
        encoded = dict(redact_sensitive_value(values))
        if "changed_files" in encoded:
            encoded["changed_files"] = json.dumps(encoded["changed_files"], ensure_ascii=False)
        if "context" in encoded:
            encoded["context_json"] = json.dumps(encoded.pop("context"), ensure_ascii=False)
        if "context_json" in encoded and isinstance(encoded["context_json"], dict):
            encoded["context_json"] = json.dumps(encoded["context_json"], ensure_ascii=False)
        columns = ", ".join(f"{key}=?" for key in encoded)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {columns} WHERE id=?",
                (*encoded.values(), task_id),
            )

    def add_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        created_at = time.time()
        safe_payload = dict(redact_sensitive_value(payload))
        message = safe_payload.get("message")
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO task_events(task_id,type,message,payload,created_at) VALUES(?,?,?,?,?)",
                (
                    task_id,
                    event_type,
                    message,
                    json.dumps(safe_payload, ensure_ascii=False, default=str),
                    created_at,
                ),
            )
        return {
            "id": cursor.lastrowid,
            "type": event_type,
            "ts": created_at,
            **safe_payload,
        }

    def request_cancel(self, task_id: str, user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET cancel_requested=1
                   WHERE id=? AND user_id=? AND status IN ('queued','running','awaiting_approval')""",
                (task_id, user_id),
            )
        return cursor.rowcount > 0

    def list_child_tasks(self, parent_task_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks WHERE parent_task_id=?"
        params: list[Any] = [parent_task_id]
        if user_id is not None:
            query += " AND user_id=?"
            params.append(user_id)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def request_cancel_cascade(self, task_id: str, user_id: str) -> list[str]:
        """Cancel task and all descendants. Returns cancelled task ids."""
        cancelled: list[str] = []
        queue = [task_id]
        seen: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if self.request_cancel(current, user_id):
                cancelled.append(current)
            for child in self.list_child_tasks(current, user_id=user_id):
                queue.append(child["id"])
        return cancelled

    def count_active_children(self, parent_task_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS n FROM tasks
                   WHERE parent_task_id=? AND status IN ('queued','running','awaiting_approval')""",
                (parent_task_id,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and row[0])

    def claim_next_task(
        self,
        worker_id: str,
        lease_seconds: float = 300.0,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = now or time.time()
        lease_expires = now + lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT * FROM tasks
                   WHERE status='queued' OR (status='running' AND lease_expires_at<?)
                   ORDER BY created_at ASC, id ASC""",
                (now,),
            ).fetchall()
            for row in rows:
                lock_key = row["write_lock_key"] if "write_lock_key" in row.keys() else None
                if not lock_key:
                    # Fallback for legacy rows / read-only without column
                    try:
                        ctx = json.loads(row["context_json"] or "{}")
                    except Exception:
                        ctx = {}
                    lock_key = ctx.get("write_lock_key")
                if lock_key:
                    conflict = conn.execute(
                        """SELECT 1 FROM tasks
                           WHERE write_lock_key=? AND status='running'
                           AND lease_expires_at>=? AND id!=?""",
                        (lock_key, now, row["id"]),
                    ).fetchone()
                    if conflict:
                        continue
                else:
                    # Legacy main-agent tasks without write_lock_key: keep
                    # single-writer on (user_id, project_id) for non-child tasks.
                    role = row["role"] if "role" in row.keys() else None
                    parent = row["parent_task_id"] if "parent_task_id" in row.keys() else None
                    if not parent and not role:
                        conflict = conn.execute(
                            """SELECT 1 FROM tasks
                               WHERE project_id=? AND user_id=? AND status='running'
                               AND lease_expires_at>=? AND id!=?
                               AND (parent_task_id IS NULL OR parent_task_id='')
                               AND (write_lock_key IS NULL OR write_lock_key=''
                                    OR write_lock_key=?)""",
                            (
                                row["project_id"],
                                row["user_id"],
                                now,
                                row["id"],
                                f"main:{row['user_id']}:{row['project_id']}",
                            ),
                        ).fetchone()
                        if conflict:
                            continue
                deps = conn.execute(
                    "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?",
                    (row["id"],),
                ).fetchall()
                blocked = False
                for dep in deps:
                    dep_row = conn.execute(
                        "SELECT status FROM tasks WHERE id=?",
                        (dep["depends_on_task_id"],),
                    ).fetchone()
                    if not dep_row or dep_row["status"] != "succeeded":
                        blocked = True
                        break
                if blocked:
                    continue
                attempt = int(row["attempt"] or 0) + 1
                cursor = conn.execute(
                    """UPDATE tasks SET status='running', claim_owner=?,
                       lease_expires_at=?, heartbeat_at=?, attempt=?, started_at=?
                       WHERE id=? AND (status='queued' OR (status='running' AND lease_expires_at<?))""",
                    (worker_id, lease_expires, now, attempt, now, row["id"], now),
                )
                if cursor.rowcount == 0:
                    continue
                conn.execute(
                    """INSERT INTO task_events(task_id,type,message,payload,created_at)
                       VALUES(?,?,?,?,?)""",
                    (
                        row["id"],
                        "claimed",
                        "任务被 worker 认领",
                        json.dumps({"worker_id": worker_id}, ensure_ascii=False),
                        now,
                    ),
                )
                updated = conn.execute(
                    "SELECT * FROM tasks WHERE id=?", (row["id"],)
                ).fetchone()
                return self._row_to_task(updated)
        return None

    def heartbeat_task(self, task_id: str, worker_id: str, lease_seconds: float = 300.0) -> bool:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET heartbeat_at=?, lease_expires_at=?
                   WHERE id=? AND claim_owner=? AND status='running'""",
                (now, now + lease_seconds, task_id, worker_id),
            )
        return cursor.rowcount > 0

    def release_task(
        self,
        task_id: str,
        worker_id: str,
        status: str,
        **values: Any,
    ) -> bool:
        encoded = dict(redact_sensitive_value(values))
        if "changed_files" in encoded:
            encoded["changed_files"] = json.dumps(encoded["changed_files"], ensure_ascii=False)
        encoded["status"] = status
        encoded["claim_owner"] = None
        encoded["lease_expires_at"] = None
        encoded["heartbeat_at"] = None
        if status in {"succeeded", "failed", "canceled", "interrupted"}:
            encoded["finished_at"] = time.time()
        columns = ", ".join(f"{key}=?" for key in encoded)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {columns} WHERE id=? AND claim_owner=?",
                (*encoded.values(), task_id, worker_id),
            )
        return cursor.rowcount > 0

    def add_task_message(
        self,
        task_id: str,
        message_key: str,
        type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO task_messages
                   (task_id, message_key, type, payload, created_at)
                   VALUES (?,?,?,?,?)""",
                (
                    task_id,
                    message_key,
                    type,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                    now,
                ),
            )
            if cursor.rowcount == 0:
                existing = conn.execute(
                    "SELECT * FROM task_messages WHERE task_id=? AND message_key=?",
                    (task_id, message_key),
                ).fetchone()
                return self._row_to_message(existing) if existing else {}
            row = conn.execute(
                "SELECT * FROM task_messages WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return self._row_to_message(row)

    def get_pending_messages(
        self,
        task_id: str,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM task_messages WHERE task_id=? AND consumed_at IS NULL"
        params: list[Any] = [task_id]
        if types:
            query += f" AND type IN ({','.join('?' for _ in types)})"
            params.extend(types)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_message(row) for row in rows]

    def consume_message(self, message_id: int, worker_id: str | None = None) -> bool:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE task_messages SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (now, message_id),
            )
        return cursor.rowcount > 0

    def pause_task(self, task_id: str, user_id: str) -> bool:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET status='paused', cancel_requested=0
                   WHERE id=? AND user_id=? AND status IN ('queued','running')""",
                (task_id, user_id),
            )
            if cursor.rowcount > 0:
                conn.execute(
                    """INSERT OR IGNORE INTO task_messages
                       (task_id, message_key, type, payload, created_at)
                       VALUES (?,?,?,?,?)""",
                    (task_id, f"pause:{task_id}", "pause", "{}", now),
                )
        return cursor.rowcount > 0

    def resume_task(self, task_id: str, user_id: str) -> bool:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET status='queued', claim_owner=NULL,
                   lease_expires_at=NULL, heartbeat_at=NULL
                   WHERE id=? AND user_id=? AND status='paused'""",
                (task_id, user_id),
            )
            if cursor.rowcount > 0:
                conn.execute(
                    """INSERT OR IGNORE INTO task_messages
                       (task_id, message_key, type, payload, created_at)
                       VALUES (?,?,?,?,?)""",
                    (task_id, f"resume:{task_id}", "resume", "{}", now),
                )
                conn.execute(
                    """UPDATE task_messages SET consumed_at=?
                       WHERE task_id=? AND type='pause' AND consumed_at IS NULL""",
                    (now, task_id),
                )
        return cursor.rowcount > 0

    def add_dependency(self, task_id: str, depends_on_task_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO task_dependencies
                   (task_id, depends_on_task_id, created_at)
                   VALUES (?,?,?)""",
                (task_id, depends_on_task_id, time.time()),
            )

    def get_dependency_status(self, task_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?",
                (task_id,),
            ).fetchall()
            if not rows:
                return {"blocking": []}
            blocking: list[str] = []
            for row in rows:
                dep_row = conn.execute(
                    "SELECT id, status FROM tasks WHERE id=?",
                    (row["depends_on_task_id"],),
                ).fetchone()
                if not dep_row or dep_row["status"] != "succeeded":
                    blocking.append(dep_row["id"] if dep_row else row["depends_on_task_id"])
        return {"blocking": blocking}

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.get("payload") or "{}")
        return item

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
        self.update_conversation(conv["id"], user_id, status="archived")
        self.create_conversation(user_id, project_id, title="默认对话")


    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(redact_sensitive_value(dict(row)))
        task["cancel_requested"] = bool(task["cancel_requested"])
        task["changed_files"] = json.loads(task["changed_files"] or "[]")
        try:
            task["context"] = json.loads(task.get("context_json") or "{}")
        except json.JSONDecodeError:
            task["context"] = {}
        return task

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        payload = redact_sensitive_value(
            json.loads(row["payload"] or "{}")
        )
        return {"id": row["id"], "type": row["type"], "ts": row["created_at"], **payload}

    def _row_to_conversation(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item.pop("turns_json", None)
        from agent.conversation_events import ConversationEventStore

        item["turns"] = ConversationEventStore(self).project_legacy_turns(
            item["id"]
        )
        item["turn_count"] = len(item["turns"])
        return item
