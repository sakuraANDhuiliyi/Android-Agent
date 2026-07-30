from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import agent.paths as paths
from agent.redaction import redact_sensitive_text, redact_sensitive_value


logger = logging.getLogger(__name__)


class DiagnosticStore:
    """Queryable, redacted diagnostics for optional extension failures."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else paths.DATA_DIR / "agent.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS diagnostics (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    project_id TEXT,
                    task_id TEXT,
                    turn_id TEXT,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_diagnostics_user_created
                    ON diagnostics(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_diagnostics_task
                    ON diagnostics(task_id, created_at DESC);
                """
            )

    def record(
        self,
        component: str,
        operation: str,
        message: str,
        *,
        severity: str = "warning",
        user_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        turn_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_message = redact_sensitive_text(str(message))[:4000]
        safe_details = redact_sensitive_value(details or {})
        raw_details = json.dumps(
            safe_details,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        item = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "project_id": project_id,
            "task_id": task_id,
            "turn_id": turn_id,
            "component": str(component)[:100],
            "operation": str(operation)[:100],
            "severity": severity if severity in {"info", "warning", "error"} else "warning",
            "message": safe_message,
            "details": safe_details,
            "created_at": time.time(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO diagnostics
                   (id,user_id,project_id,task_id,turn_id,component,operation,
                    severity,message,details_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"],
                    user_id,
                    project_id,
                    task_id,
                    turn_id,
                    item["component"],
                    item["operation"],
                    item["severity"],
                    safe_message,
                    raw_details,
                    item["created_at"],
                ),
            )
        logger.warning(
            "diagnostic component=%s operation=%s task=%s message=%s",
            component,
            operation,
            task_id,
            safe_message,
        )
        return item

    def list(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        after: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["user_id=?"]
        params: list[Any] = [user_id]
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        if task_id:
            clauses.append("task_id=?")
            params.append(task_id)
        if after is not None:
            clauses.append("created_at>?")
            params.append(float(after))
        params.append(max(1, min(int(limit), 500)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM diagnostics
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC LIMIT ?""",
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {"diagnostic_payload_corrupt": True}
                item.pop("details_json", None)
            result.append(item)
        return result


_stores: dict[str, DiagnosticStore] = {}
_stores_lock = threading.Lock()


def get_diagnostic_store(db_path: Path | None = None) -> DiagnosticStore:
    path = Path(db_path) if db_path else paths.DATA_DIR / "agent.db"
    key = str(path.resolve(strict=False))
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = DiagnosticStore(path)
            _stores[key] = store
        return store


def record_diagnostic(
    component: str,
    operation: str,
    message: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return get_diagnostic_store().record(
        component,
        operation,
        message,
        **kwargs,
    )
