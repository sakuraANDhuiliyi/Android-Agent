from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.database import TaskStore
from agent.redaction import redact_sensitive_value


TURN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "awaiting_approval",
        "succeeded",
        "failed",
        "canceled",
        "interrupted",
        "paused",
    }
)


class ConversationEventType:
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTEXT_CHECKPOINT = "context_checkpoint"
    CONTEXT_CHECKPOINT_INVALIDATED = "context_checkpoint_invalidated"
    SYSTEM_NOTE = "system_note"
    RECOVERY_NOTE = "recovery_note"
    TURN_STARTED = "turn_started"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELED = "turn_canceled"
    TURN_INTERRUPTED = "turn_interrupted"
    CHANGES = "changes"
    USAGE = "usage"
    PROVIDER_SWITCH = "provider_switch"
    MODEL_SWITCH = "model_switch"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"
    MALFORMED_TOOL_CALL = "malformed_tool_call"
    LIFECYCLE_RECONCILED = "lifecycle_reconciled"


CONTEXT_EVENT_TYPES = frozenset(
    {
        ConversationEventType.USER_MESSAGE,
        ConversationEventType.ASSISTANT_MESSAGE,
        ConversationEventType.TOOL_CALL,
        ConversationEventType.TOOL_RESULT,
        ConversationEventType.CONTEXT_CHECKPOINT,
    }
)

CONTEXT_NOTE_EVENT_TYPES = frozenset(
    {
        ConversationEventType.SYSTEM_NOTE,
        ConversationEventType.RECOVERY_NOTE,
    }
)


_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "apikey",
        "apitoken",
        "authorization",
        "proxyauthorization",
        "xapikey",
        "token",
        "accesstoken",
        "refreshtoken",
        "secret",
        "clientsecret",
        "password",
        "deepseekapikey",
        "anthropicapikey",
        "tavilyapikey",
    }
)


class ConversationEventError(ValueError):
    """Base error for invalid conversation event store operations."""


class ConversationNotFoundError(ConversationEventError):
    pass


class TurnNotFoundError(ConversationEventError):
    pass


class InvalidTurnStatusError(ConversationEventError):
    pass


class PayloadValidationError(ConversationEventError):
    pass


class PayloadSerializationError(ConversationEventError):
    pass


class CorruptEventPayloadError(ConversationEventError):
    pass


def serialize_event_payload(payload: Mapping[str, Any] | None) -> str:
    value: Mapping[str, Any] = {} if payload is None else payload
    if not isinstance(value, Mapping):
        raise PayloadValidationError(
            "conversation event payload must be a mapping"
        )
    redacted = redact_sensitive_value(value)
    _validate_event_payload_value(redacted, path="payload")
    try:
        return json.dumps(
            dict(redacted),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise PayloadSerializationError(
            f"conversation event payload is not JSON serializable: {exc}"
        ) from exc


def deserialize_event_payload(
    raw_payload: Any,
    *,
    event_id: str | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(raw_payload or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        raise CorruptEventPayloadError(
            f"conversation event {event_id} has invalid payload_json: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CorruptEventPayloadError(
            f"conversation event {event_id} payload_json must decode to an object"
        )
    return redact_sensitive_value(payload)


def _validate_event_payload_value(value: Any, *, path: str) -> None:
    if isinstance(value, sqlite3.Connection) or isinstance(value, sqlite3.Cursor):
        raise PayloadValidationError(
            f"{path} contains a SQLite connection or cursor"
        )
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = "".join(
                char for char in str(key).lower() if char.isalnum()
            )
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                raise PayloadValidationError(
                    f"{path}.{key} is a forbidden credential field"
                )
            _validate_event_payload_value(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _validate_event_payload_value(nested, path=f"{path}[{index}]")


class ConversationEventStore:
    """Append-only storage for durable conversation turns and events."""

    def __init__(self, store_or_path: TaskStore | Path | str | None = None):
        if isinstance(store_or_path, TaskStore):
            self._store = store_or_path
        else:
            db_path = Path(store_or_path) if store_or_path is not None else None
            self._store = TaskStore(db_path)

    @property
    def db_path(self) -> Path:
        return self._store.db_path

    def create_turn(
        self,
        conversation_id: str,
        user_id: str,
        project_id: str,
        *,
        task_id: str | None = None,
        status: str = "queued",
        provider: str | None = None,
        model: str | None = None,
        turn_id: str | None = None,
        created_at: float | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
        error_message: str | None = None,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        self._validate_status(status)
        turn_id = turn_id or uuid.uuid4().hex
        created_at = time.time() if created_at is None else created_at
        with self._store._connect() as conn:
            conversation = conn.execute(
                """SELECT id FROM conversations
                   WHERE id=? AND user_id=? AND project_id=?""",
                (conversation_id, user_id, project_id),
            ).fetchone()
            if not conversation:
                raise ConversationNotFoundError(
                    "conversation does not exist or does not belong to the user and project"
                )
            conn.execute(
                """INSERT INTO conversation_turns
                   (id, conversation_id, task_id, user_id, project_id, status,
                    provider, model, created_at, started_at, finished_at,
                    error_message, schema_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    turn_id,
                    conversation_id,
                    task_id,
                    user_id,
                    project_id,
                    status,
                    provider,
                    model,
                    created_at,
                    started_at,
                    finished_at,
                    error_message,
                    schema_version,
                ),
            )
        turn = self.get_turn(turn_id, user_id=user_id)
        if turn is None:
            raise ConversationEventError(f"created turn could not be read: {turn_id}")
        return turn

    def get_turn(
        self,
        turn_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM conversation_turns WHERE id=?"
        params: tuple[Any, ...] = (turn_id,)
        if user_id is not None:
            query += " AND user_id=?"
            params += (user_id,)
        with self._store._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def get_turn_by_task(
        self,
        task_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM conversation_turns WHERE task_id=?"
        params: tuple[Any, ...] = (task_id,)
        if user_id is not None:
            query += " AND user_id=?"
            params += (user_id,)
        with self._store._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def has_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self._store._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM conversations
                   WHERE id=? AND user_id=? LIMIT 1""",
                (conversation_id, user_id),
            ).fetchone()
        return row is not None

    def update_turn_status(
        self,
        turn_id: str,
        status: str,
        *,
        user_id: str | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        self._validate_status(status)
        values: dict[str, Any] = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if finished_at is not None:
            values["finished_at"] = finished_at
        if error_message is not None:
            values["error_message"] = error_message
        columns = ", ".join(f"{column}=?" for column in values)
        query = f"UPDATE conversation_turns SET {columns} WHERE id=?"
        params: tuple[Any, ...] = (*values.values(), turn_id)
        if user_id is not None:
            query += " AND user_id=?"
            params += (user_id,)
        with self._store._connect() as conn:
            cursor = conn.execute(query, params)
        if cursor.rowcount == 0:
            raise TurnNotFoundError("turn does not exist or does not belong to the user")
        turn = self.get_turn(turn_id, user_id=user_id)
        if turn is None:
            raise TurnNotFoundError(f"updated turn could not be read: {turn_id}")
        return turn

    def finalize_lifecycle(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        task_id: str,
        user_id: str,
        event_type: str,
        event_key: str,
        event_payload: Mapping[str, Any],
        status: str,
        finished_at: float,
        final_message: str | None = None,
        error_message: str | None = None,
        apk_path: str | None = None,
        build_log_path: str | None = None,
        task_event_type: str | None = None,
        task_event_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically append the terminal event and finalize Turn + Task."""
        self._validate_status(status)
        if status not in {"succeeded", "failed", "canceled", "interrupted"}:
            raise InvalidTurnStatusError(
                f"lifecycle finalization requires terminal status, got {status!r}"
            )
        payload_json = self._serialize_payload(event_payload)
        safe_task_payload = redact_sensitive_value(task_event_payload or {})
        task_payload_json = json.dumps(
            safe_task_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        created_at = time.time()
        with self._store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            turn = conn.execute(
                """SELECT * FROM conversation_turns
                   WHERE id=? AND conversation_id=? AND task_id=? AND user_id=?""",
                (turn_id, conversation_id, task_id, user_id),
            ).fetchone()
            task = conn.execute(
                "SELECT id FROM tasks WHERE id=? AND user_id=?",
                (task_id, user_id),
            ).fetchone()
            if not turn or not task:
                raise TurnNotFoundError(
                    "turn/task lifecycle identity does not match"
                )
            existing = conn.execute(
                """SELECT * FROM conversation_events
                   WHERE conversation_id=? AND event_key=?""",
                (conversation_id, event_key),
            ).fetchone()
            if existing is None:
                next_seq = conn.execute(
                    """SELECT COALESCE(MAX(seq), 0) + 1
                       FROM conversation_events WHERE conversation_id=?""",
                    (conversation_id,),
                ).fetchone()[0]
                event_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO conversation_events
                       (id,conversation_id,turn_id,task_id,seq,event_type,role,
                        context_visible,provider,model,payload_json,event_key,
                        created_at,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (
                        event_id,
                        conversation_id,
                        turn_id,
                        task_id,
                        next_seq,
                        event_type,
                        None,
                        0,
                        turn["provider"],
                        turn["model"],
                        payload_json,
                        event_key,
                        created_at,
                    ),
                )
                existing = conn.execute(
                    "SELECT * FROM conversation_events WHERE id=?",
                    (event_id,),
                ).fetchone()
            conn.execute(
                """UPDATE conversation_turns
                   SET status=?, finished_at=?, error_message=?
                   WHERE id=?""",
                (status, finished_at, error_message, turn_id),
            )
            conn.execute(
                """UPDATE tasks
                   SET status=?, finished_at=?, final_message=?, error_message=?,
                       apk_path=COALESCE(?, apk_path),
                       build_log_path=COALESCE(?, build_log_path)
                   WHERE id=?""",
                (
                    status,
                    finished_at,
                    final_message,
                    error_message,
                    apk_path,
                    build_log_path,
                    task_id,
                ),
            )
            if task_event_type:
                conn.execute(
                    """INSERT INTO task_events
                       (task_id,type,message,payload,created_at)
                       VALUES (?,?,?,?,?)""",
                    (
                        task_id,
                        task_event_type,
                        safe_task_payload.get("message"),
                        task_payload_json,
                        created_at,
                    ),
                )
        if existing is None:
            raise ConversationEventError("terminal lifecycle event was not persisted")
        return self._row_to_event(existing)

    def start_lifecycle(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        task_id: str,
        user_id: str,
        project_id: str,
        provider: str | None,
        model: str | None,
        started_at: float,
    ) -> dict[str, Any]:
        """Atomically record turn_started and mark its Turn + Task running."""
        payload = {
            "status": "running",
            "provider": provider,
            "model": model,
        }
        payload_json = self._serialize_payload(payload)
        event_key = f"turn:{turn_id}:started"
        with self._store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            turn = conn.execute(
                """SELECT id FROM conversation_turns
                   WHERE id=? AND conversation_id=? AND task_id=? AND user_id=?""",
                (turn_id, conversation_id, task_id, user_id),
            ).fetchone()
            task = conn.execute(
                "SELECT id FROM tasks WHERE id=? AND user_id=?",
                (task_id, user_id),
            ).fetchone()
            if not turn or not task:
                raise TurnNotFoundError(
                    "turn/task lifecycle identity does not match"
                )
            existing = conn.execute(
                """SELECT * FROM conversation_events
                   WHERE conversation_id=? AND event_key=?""",
                (conversation_id, event_key),
            ).fetchone()
            if existing is None:
                next_seq = conn.execute(
                    """SELECT COALESCE(MAX(seq), 0) + 1
                       FROM conversation_events WHERE conversation_id=?""",
                    (conversation_id,),
                ).fetchone()[0]
                event_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO conversation_events
                       (id,conversation_id,turn_id,task_id,seq,event_type,role,
                        context_visible,provider,model,payload_json,event_key,
                        created_at,schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (
                        event_id,
                        conversation_id,
                        turn_id,
                        task_id,
                        next_seq,
                        ConversationEventType.TURN_STARTED,
                        None,
                        0,
                        provider,
                        model,
                        payload_json,
                        event_key,
                        started_at,
                    ),
                )
                existing = conn.execute(
                    "SELECT * FROM conversation_events WHERE id=?",
                    (event_id,),
                ).fetchone()
            conn.execute(
                """UPDATE conversation_turns
                   SET status='running', started_at=?, provider=?, model=?
                   WHERE id=?""",
                (started_at, provider, model, turn_id),
            )
            conn.execute(
                """UPDATE tasks SET status='running', started_at=?
                   WHERE id=?""",
                (started_at, task_id),
            )
            conn.execute(
                """INSERT INTO task_events
                   (task_id,type,message,payload,created_at)
                   VALUES (?,?,?,?,?)""",
                (
                    task_id,
                    "started",
                    "任务开始",
                    json.dumps(
                        {
                            "message": "任务开始",
                            "project_id": project_id,
                            "conversation_id": conversation_id,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    started_at,
                ),
            )
        if existing is None:
            raise ConversationEventError("turn_started was not persisted")
        return self._row_to_event(existing)

    def append_event(
        self,
        conversation_id: str,
        turn_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
        role: str | None = None,
        context_visible: bool = False,
        provider: str | None = None,
        model: str | None = None,
        event_key: str | None = None,
        event_id: str | None = None,
        created_at: float | None = None,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        return self._append_event(
            conversation_id,
            turn_id,
            event_type,
            payload,
            task_id=task_id,
            role=role,
            context_visible=context_visible,
            provider=provider,
            model=model,
            event_key=event_key,
            event_id=event_id,
            created_at=created_at,
            schema_version=schema_version,
            idempotent=False,
        )

    def append_event_idempotent(
        self,
        conversation_id: str,
        turn_id: str,
        event_type: str,
        event_key: str,
        payload: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
        role: str | None = None,
        context_visible: bool = False,
        provider: str | None = None,
        model: str | None = None,
        event_id: str | None = None,
        created_at: float | None = None,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        if not event_key or not event_key.strip():
            raise ConversationEventError("event_key is required for idempotent append")
        return self._append_event(
            conversation_id,
            turn_id,
            event_type,
            payload,
            task_id=task_id,
            role=role,
            context_visible=context_visible,
            provider=provider,
            model=model,
            event_key=event_key,
            event_id=event_id,
            created_at=created_at,
            schema_version=schema_version,
            idempotent=True,
        )

    def list_events(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
        after_seq: int | None = None,
        before_seq: int | None = None,
        limit: int | None = None,
        context_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List conversation events ordered by seq.

        ``after_seq`` pages forward (events with seq > after_seq, ascending).
        ``before_seq`` pages backward: it returns the LAST ``limit`` events
        with seq < before_seq, still in ascending order, so the desktop can
        prepend older history without jumping the scroll anchor.
        """
        query = """
            SELECT e.*
            FROM conversation_events AS e
            JOIN conversations AS c ON c.id=e.conversation_id
            WHERE e.conversation_id=?
        """
        params: list[Any] = [conversation_id]
        if user_id is not None:
            query += " AND c.user_id=?"
            params.append(user_id)
        if after_seq is not None:
            query += " AND e.seq>?"
            params.append(after_seq)
        if before_seq is not None:
            query += " AND e.seq<?"
            params.append(before_seq)
        if context_only:
            query += " AND e.context_visible=1"
        backward = before_seq is not None and after_seq is None
        query += " ORDER BY e.seq DESC" if backward else " ORDER BY e.seq"
        if limit is not None:
            if limit < 1:
                raise ConversationEventError("limit must be at least 1")
            query += " LIMIT ?"
            params.append(limit)
        with self._store._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        events = [self._row_to_event(row) for row in rows]
        if backward:
            events.reverse()
        return events

    def list_turn_events(
        self,
        turn_id: str,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT e.*
            FROM conversation_events AS e
            JOIN conversation_turns AS t ON t.id=e.turn_id
            WHERE e.turn_id=?
        """
        params: list[Any] = [turn_id]
        if user_id is not None:
            query += " AND t.user_id=?"
            params.append(user_id)
        query += " ORDER BY e.seq"
        with self._store._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def project_legacy_turns(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        with self._store._connect() as conn:
            turns = conn.execute(
                """SELECT id, created_at FROM conversation_turns
                   WHERE conversation_id=?
                   ORDER BY created_at, rowid""",
                (conversation_id,),
            ).fetchall()
            events = conn.execute(
                """SELECT * FROM conversation_events
                   WHERE conversation_id=? ORDER BY seq""",
                (conversation_id,),
            ).fetchall()

        events_by_turn: dict[str, list[dict[str, Any]]] = {
            turn["id"]: [] for turn in turns
        }
        for row in events:
            event = self._row_to_event(row)
            events_by_turn.setdefault(event["turn_id"], []).append(event)

        projected: list[dict[str, Any]] = []
        for turn in turns:
            turn_events = events_by_turn.get(turn["id"], [])
            user = ""
            assistant_events: list[dict[str, Any]] = []
            changed_files: list[Any] = []
            for event in turn_events:
                if event["event_type"] == "user_message":
                    user = self._user_message_text(event["payload"])
                elif event["event_type"] == "assistant_message":
                    assistant_events.append(event)
                elif event["event_type"] == "changes":
                    files = event["payload"].get(
                        "changed_files",
                        event["payload"].get("files", []),
                    )
                    if isinstance(files, list):
                        changed_files = files

            final_events = [
                event
                for event in assistant_events
                if event["payload"].get("is_final") is True
            ]
            selected = (
                final_events[-1]
                if final_events
                else assistant_events[-1] if assistant_events else None
            )
            assistant = (
                self._assistant_message_text(selected["payload"])
                if selected
                else ""
            )
            projected.append(
                {
                    "user": user,
                    "assistant": assistant,
                    "changed_files": changed_files,
                    "ts": turn["created_at"],
                }
            )
        return projected

    def _append_event(
        self,
        conversation_id: str,
        turn_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None,
        *,
        task_id: str | None,
        role: str | None,
        context_visible: bool,
        provider: str | None,
        model: str | None,
        event_key: str | None,
        event_id: str | None,
        created_at: float | None,
        schema_version: int,
        idempotent: bool,
    ) -> dict[str, Any]:
        if not event_type or not event_type.strip():
            raise ConversationEventError("event_type is required")
        payload_json = self._serialize_payload(payload)
        event_id = event_id or uuid.uuid4().hex
        created_at = time.time() if created_at is None else created_at

        with self._store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotent:
                existing = conn.execute(
                    """SELECT * FROM conversation_events
                       WHERE conversation_id=? AND event_key=?""",
                    (conversation_id, event_key),
                ).fetchone()
                if existing:
                    return self._row_to_event(existing)

            turn = conn.execute(
                """SELECT conversation_id, task_id FROM conversation_turns
                   WHERE id=?""",
                (turn_id,),
            ).fetchone()
            if not turn or turn["conversation_id"] != conversation_id:
                raise TurnNotFoundError(
                    "turn does not exist or does not belong to the conversation"
                )
            effective_task_id = task_id if task_id is not None else turn["task_id"]
            max_events = int(
                getattr(self._store, "max_events_per_conversation", 100_000)
            )
            current_count = conn.execute(
                "SELECT COUNT(*) FROM conversation_events WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            if current_count >= max_events:
                raise ConversationEventError(
                    f"conversation event quota exceeded ({max_events})"
                )
            next_seq = conn.execute(
                """SELECT COALESCE(MAX(seq), 0) + 1
                   FROM conversation_events WHERE conversation_id=?""",
                (conversation_id,),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO conversation_events
                   (id, conversation_id, turn_id, task_id, seq, event_type, role,
                    context_visible, provider, model, payload_json, event_key,
                    created_at, schema_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    conversation_id,
                    turn_id,
                    effective_task_id,
                    next_seq,
                    event_type,
                    role,
                    int(context_visible),
                    provider,
                    model,
                    payload_json,
                    event_key,
                    created_at,
                    schema_version,
                ),
            )
            row = conn.execute(
                "SELECT * FROM conversation_events WHERE id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise ConversationEventError(f"created event could not be read: {event_id}")
        return self._row_to_event(row)

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in TURN_STATUSES:
            allowed = ", ".join(sorted(TURN_STATUSES))
            raise InvalidTurnStatusError(
                f"invalid turn status {status!r}; expected one of: {allowed}"
            )

    @classmethod
    def _serialize_payload(cls, payload: Mapping[str, Any] | None) -> str:
        return serialize_event_payload(payload)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        raw_payload = item.pop("payload_json", None) or "{}"
        item["context_visible"] = bool(item["context_visible"])
        item["payload"] = deserialize_event_payload(
            raw_payload,
            event_id=item.get("id"),
        )
        return item

    @staticmethod
    def _user_message_text(payload: dict[str, Any]) -> str:
        content = payload.get("content", "")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )

    @staticmethod
    def _assistant_message_text(payload: dict[str, Any]) -> str:
        blocks = payload.get("text_blocks")
        if isinstance(blocks, list):
            return "".join(
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        text = payload.get("text", "")
        return text if isinstance(text, str) else ""
