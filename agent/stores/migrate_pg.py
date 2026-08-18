from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

TABLES = (
    "users",
    "user_tokens",
    "tasks",
    "task_events",
    "task_messages",
    "conversations",
    "conversation_turns",
    "conversation_events",
    "checkpoints",
    "outbox",
)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def summarize_sqlite(db_path: Path, extra: Iterable[Path] = ()) -> dict[str, Any]:
    """Count rows and hash ids so a Postgres import can be verified."""
    paths = [Path(db_path), *[Path(p) for p in extra]]
    summary: dict[str, Any] = {"databases": [], "tables": {}, "row_total": 0}
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            continue
        db_entry = {"path": path.name, "tables": {}}
        with _connect(path) as conn:
            names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            for table in names:
                if table.startswith("sqlite_"):
                    continue
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                ids = conn.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone()
                id_col = ids.keys()[0] if ids else "rowid"
                try:
                    id_rows = conn.execute(
                        f'SELECT CAST("{id_col}" AS TEXT) FROM "{table}" ORDER BY 1'
                    ).fetchall()
                    id_blob = "\n".join(str(r[0]) for r in id_rows).encode("utf-8")
                except sqlite3.Error:
                    id_blob = str(count).encode("utf-8")
                table_hash = hashlib.sha256(id_blob).hexdigest()
                db_entry["tables"][table] = {"count": int(count), "id_hash": table_hash}
                digest.update(f"{path.name}:{table}:{count}:{table_hash}".encode("utf-8"))
                summary["row_total"] += int(count)
                summary["tables"].setdefault(table, {"count": 0})
                summary["tables"][table]["count"] += int(count)
        summary["databases"].append(db_entry)
    summary["aggregate_hash"] = digest.hexdigest()
    return summary


def compare_summaries(before: MappingLike, after: MappingLike) -> list[str]:
    errors: list[str] = []
    if before.get("row_total") != after.get("row_total"):
        errors.append(
            f"row_total {before.get('row_total')} != {after.get('row_total')}"
        )
    left_tables = before.get("tables") or {}
    right_tables = after.get("tables") or {}
    names = sorted(set(left_tables) | set(right_tables))
    for name in names:
        lc = (left_tables.get(name) or {}).get("count")
        rc = (right_tables.get(name) or {}).get("count")
        if lc != rc:
            errors.append(f"{name} count {lc} != {rc}")
    if before.get("aggregate_hash") != after.get("aggregate_hash"):
        errors.append("aggregate_hash mismatch")
    return errors


MappingLike = dict[str, Any]


def render_postgres_sql(sqlite_path: Path, *, schema: str = "public") -> str:
    """Generate INSERT statements. Does not require a live Postgres server."""
    statements = [
        f"-- Generated from {sqlite_path.name}",
        "BEGIN;",
    ]
    with _connect(sqlite_path) as conn:
        for table in TABLES:
            if not _table_exists(conn, table):
                continue
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            if not rows:
                continue
            columns = rows[0].keys()
            col_sql = ", ".join(f'"{c}"' for c in columns)
            statements.append(f"-- {table}: {len(rows)} rows")
            for row in rows:
                values = ", ".join(_sql_literal(row[c]) for c in columns)
                statements.append(
                    f'INSERT INTO {schema}."{table}" ({col_sql}) VALUES ({values});'
                )
    statements.append("COMMIT;")
    return "\n".join(statements) + "\n"


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return "'" + text.replace("'", "''") + "'"


def write_manifest(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
