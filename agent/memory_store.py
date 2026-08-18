from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import agent.paths as paths
from agent.paths import validate_id
from agent.redaction import redact_sensitive_text, redact_sensitive_value


MemoryScope = Literal["project", "user", "local"]
MemoryType = Literal[
    "architecture",
    "convention",
    "decision",
    "workflow",
    "known_issue",
    "preference",
]
MemoryStatus = Literal["candidate", "active", "rejected", "archived"]

MEMORY_SCHEMA_VERSION = 1
MAX_CONTENT_CHARS = 4_000
MAX_TITLE_CHARS = 200

VALID_SCOPES = frozenset({"project", "user", "local"})
VALID_TYPES = frozenset(
    {
        "architecture",
        "convention",
        "decision",
        "workflow",
        "known_issue",
        "preference",
    }
)
VALID_STATUSES = frozenset({"candidate", "active", "rejected", "archived"})


def content_hash(title: str, content: str, memory_type: str) -> str:
    payload = f"{memory_type}\n{title.strip()}\n{content.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _default_db_path() -> Path:
    return paths.DATA_DIR / "agent.db"


class MemoryStore:
    """Project/user/local long-term memories with FTS5 (offline)."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    memory_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    source_conversation_id TEXT,
                    source_event_seq INTEGER,
                    status TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_used_at REAL,
                    confidence REAL NOT NULL DEFAULT 0.6,
                    conflict_of TEXT,
                    conflict_status TEXT NOT NULL DEFAULT 'none'
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user_project_status
                    ON memories(user_id, project_id, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memories_hash
                    ON memories(user_id, project_id, scope, content_hash);
                CREATE INDEX IF NOT EXISTS idx_memories_scope
                    ON memories(user_id, scope, status);
                """
            )
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "confidence" not in cols:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.6"
                )
            if "conflict_of" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN conflict_of TEXT")
            if "conflict_status" not in cols:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN conflict_status TEXT NOT NULL DEFAULT 'none'"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    task_id TEXT,
                    reason TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_usage_memory
                    ON memory_usage(memory_id, created_at DESC);
                """
            )
            # FTS5 — recreate safely if missing.
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            ).fetchone()
            if not row:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE memories_fts USING fts5(
                        title,
                        content,
                        tags,
                        memory_id UNINDEXED,
                        tokenize='porter unicode61'
                    )
                    """
                )

    def create_memory(
        self,
        *,
        user_id: str,
        scope: str,
        memory_type: str,
        title: str,
        content: str,
        project_id: str | None = None,
        tags: list[str] | None = None,
        status: str = "candidate",
        source_conversation_id: str | None = None,
        source_event_seq: int | None = None,
        memory_id: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        user_id = validate_id(user_id, kind="user_id")
        if scope not in VALID_SCOPES:
            raise ValueError(f"无效 scope: {scope}")
        if memory_type not in VALID_TYPES:
            raise ValueError(f"无效 memory_type: {memory_type}")
        if status not in VALID_STATUSES:
            raise ValueError(f"无效 status: {status}")
        if scope in {"project", "local"} and not project_id:
            raise ValueError(f"{scope} scope 需要 project_id")
        if project_id:
            project_id = validate_id(project_id, kind="project_id")
        if scope == "user":
            project_id = None

        raw_title = (title or "").strip()
        raw_content = (content or "").strip()
        if not raw_title or not raw_content:
            raise ValueError("title/content 不能为空")
        if _looks_like_secret_blob(raw_title) or _looks_like_secret_blob(raw_content):
            raise ValueError("内容疑似包含 secret，已拒绝保存")
        title = redact_sensitive_text(raw_title)[:MAX_TITLE_CHARS]
        content = redact_sensitive_text(raw_content)[:MAX_CONTENT_CHARS]
        if not title or not content:
            raise ValueError("title/content 不能为空")

        tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
        tags = redact_sensitive_value(tags)
        digest = content_hash(title, content, memory_type)
        score = 0.6 if confidence is None else max(0.0, min(1.0, float(confidence)))

        # Dedupe active/candidate with same hash.
        existing = self.find_by_hash(user_id, project_id, scope, digest)
        if existing and existing["status"] in {"candidate", "active"}:
            return {**existing, "deduped": True}

        conflict = self.find_title_conflict(
            user_id,
            project_id,
            scope=scope,
            memory_type=memory_type,
            title=title,
            content_hash=digest,
        )
        conflict_of = conflict["id"] if conflict else None
        conflict_status = "open" if conflict else "none"

        now = time.time()
        mid = memory_id or uuid.uuid4().hex[:12]
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO memories
                   (id,scope,user_id,project_id,memory_type,title,content,tags,
                    source_conversation_id,source_event_seq,status,content_hash,
                    schema_version,created_at,updated_at,last_used_at,
                    confidence,conflict_of,conflict_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
                (
                    mid,
                    scope,
                    user_id,
                    project_id,
                    memory_type,
                    title,
                    content,
                    json.dumps(tags, ensure_ascii=False),
                    source_conversation_id,
                    source_event_seq,
                    status,
                    digest,
                    MEMORY_SCHEMA_VERSION,
                    now,
                    now,
                    score,
                    conflict_of,
                    conflict_status,
                ),
            )
            conn.execute(
                """INSERT INTO memories_fts(memory_id, title, content, tags)
                   VALUES (?,?,?,?)""",
                (mid, title, content, " ".join(tags)),
            )
            if conflict_of:
                conn.execute(
                    """UPDATE memories SET conflict_status='open', updated_at=?
                       WHERE id=? AND user_id=? AND status IN ('candidate','active')""",
                    (now, conflict_of, user_id),
                )
        item = self.get_memory(mid, user_id)
        assert item is not None
        return item

    def find_by_hash(
        self,
        user_id: str,
        project_id: str | None,
        scope: str,
        digest: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            if project_id:
                row = conn.execute(
                    """SELECT * FROM memories
                       WHERE user_id=? AND project_id=? AND scope=? AND content_hash=?
                       ORDER BY updated_at DESC LIMIT 1""",
                    (user_id, project_id, scope, digest),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM memories
                       WHERE user_id=? AND project_id IS NULL AND scope=? AND content_hash=?
                       ORDER BY updated_at DESC LIMIT 1""",
                    (user_id, scope, digest),
                ).fetchone()
        return self._row_to_memory(row) if row else None

    def find_title_conflict(
        self,
        user_id: str,
        project_id: str | None,
        *,
        scope: str,
        memory_type: str,
        title: str,
        content_hash: str,
    ) -> dict[str, Any] | None:
        needle = re.sub(r"\s+", " ", title.strip().lower())
        if len(needle) < 8:
            return None
        candidates = self.list_memories(
            user_id,
            project_id=project_id,
            scope=scope,
            memory_type=memory_type,
            limit=80,
        )
        for item in candidates:
            if item.get("status") not in {"candidate", "active"}:
                continue
            if item.get("content_hash") == content_hash:
                continue
            other = re.sub(r"\s+", " ", str(item.get("title") or "").strip().lower())
            if other == needle or other[:40] == needle[:40]:
                return item
        return None

    def get_memory(self, memory_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id=? AND user_id=?",
                (memory_id, user_id),
            ).fetchone()
        return self._row_to_memory(row) if row else None

    def list_memories(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        scope: str | None = None,
        memory_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["user_id=?"]
        params: list[Any] = [user_id]
        if project_id is not None:
            # local is project-local; only user scope crosses project boundaries.
            clauses.append(
                "((project_id=? AND scope IN ('project','local')) OR scope='user')"
            )
            params.append(project_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        if scope:
            clauses.append("scope=?")
            params.append(scope)
        if memory_type:
            clauses.append("memory_type=?")
            params.append(memory_type)
        sql = (
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)} "
            f"ORDER BY updated_at DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 500)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def list_candidates(
        self,
        user_id: str,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.list_memories(
            user_id,
            project_id=project_id,
            status="candidate",
            limit=limit,
        )

    def count_memories(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM memories WHERE user_id=?"
        params: list[Any] = [user_id]
        if project_id is not None:
            query += (
                " AND ((project_id=? AND scope IN ('project','local')) "
                "OR scope='user')"
            )
            params.append(project_id)
        with self._lock, self._connect() as conn:
            return int(conn.execute(query, params).fetchone()[0])

    def update_memory(
        self,
        memory_id: str,
        user_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        memory_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_memory(memory_id, user_id)
        if not current:
            return None
        raw_title = title if title is not None else current["title"]
        raw_content = content if content is not None else current["content"]
        if _looks_like_secret_blob(str(raw_title)) or _looks_like_secret_blob(str(raw_content)):
            raise ValueError("内容疑似包含 secret，已拒绝保存")
        new_title = redact_sensitive_text(str(raw_title))
        new_content = redact_sensitive_text(str(raw_content))
        new_tags = tags if tags is not None else current["tags"]
        new_type = memory_type if memory_type is not None else current["memory_type"]
        new_status = status if status is not None else current["status"]
        if new_type not in VALID_TYPES or new_status not in VALID_STATUSES:
            raise ValueError("无效的 type/status")
        new_title = new_title.strip()[:MAX_TITLE_CHARS]
        new_content = new_content.strip()[:MAX_CONTENT_CHARS]
        if not new_title or not new_content:
            raise ValueError("title/content 不能为空")
        digest = content_hash(new_title, new_content, new_type)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE memories SET title=?, content=?, tags=?, memory_type=?,
                   status=?, content_hash=?, updated_at=?
                   WHERE id=? AND user_id=?""",
                (
                    new_title,
                    new_content,
                    json.dumps(new_tags, ensure_ascii=False),
                    new_type,
                    new_status,
                    digest,
                    now,
                    memory_id,
                    user_id,
                ),
            )
            conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            conn.execute(
                """INSERT INTO memories_fts(memory_id, title, content, tags)
                   VALUES (?,?,?,?)""",
                (memory_id, new_title, new_content, " ".join(new_tags)),
            )
        return self.get_memory(memory_id, user_id)

    def set_status(self, memory_id: str, user_id: str, status: str) -> dict[str, Any] | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"无效 status: {status}")
        return self.update_memory(memory_id, user_id, status=status)

    def approve(self, memory_id: str, user_id: str) -> dict[str, Any] | None:
        return self.set_status(memory_id, user_id, "active")

    def reject(self, memory_id: str, user_id: str) -> dict[str, Any] | None:
        return self.set_status(memory_id, user_id, "rejected")

    def archive(self, memory_id: str, user_id: str) -> dict[str, Any] | None:
        return self.set_status(memory_id, user_id, "archived")

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            cursor = conn.execute(
                "DELETE FROM memories WHERE id=? AND user_id=?",
                (memory_id, user_id),
            )
        return cursor.rowcount > 0

    def search(
        self,
        user_id: str,
        query: str,
        *,
        project_id: str | None = None,
        status: str = "active",
        scope: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid FTS + tags + scope + recency ranking. Offline only."""
        limit = max(1, min(limit, 100))
        query = (query or "").strip()
        now = time.time()
        candidates: dict[str, dict[str, Any]] = {}

        with self._lock, self._connect() as conn:
            # Base: active memories visible to this user/project.
            clauses = ["m.user_id=?", "m.status=?"]
            params: list[Any] = [user_id, status]
            if project_id:
                clauses.append(
                    "((m.project_id=? AND m.scope IN ('project','local')) "
                    "OR m.scope='user')"
                )
                params.append(project_id)
            if scope:
                clauses.append("m.scope=?")
                params.append(scope)

            rows = conn.execute(
                f"SELECT m.* FROM memories m WHERE {' AND '.join(clauses)}",
                params,
            ).fetchall()
            for row in rows:
                item = self._row_to_memory(row)
                candidates[item["id"]] = item

            fts_hits: dict[str, float] = {}
            if query:
                # Escape FTS special chars lightly.
                fts_q = " ".join(
                    token for token in query.replace('"', " ").split() if token
                )
                if fts_q:
                    try:
                        fts_rows = conn.execute(
                            """SELECT memory_id, rank FROM memories_fts
                               WHERE memories_fts MATCH ?
                               ORDER BY rank
                               LIMIT ?""",
                            (fts_q, limit * 3),
                        ).fetchall()
                        for fr in fts_rows:
                            # rank is more negative = better in FTS5
                            fts_hits[fr["memory_id"]] = float(fr["rank"])
                    except sqlite3.OperationalError:
                        # Fallback: LIKE search if MATCH fails.
                        like = f"%{query}%"
                        like_rows = conn.execute(
                            """SELECT id FROM memories
                               WHERE user_id=? AND status=?
                               AND (title LIKE ? OR content LIKE ?)""",
                            (user_id, status, like, like),
                        ).fetchall()
                        for i, lr in enumerate(like_rows):
                            fts_hits[lr["id"]] = -1000.0 + i

        tag_set = {t.lower() for t in (tags or []) if t}
        query_tokens = [
            t.lower()
            for t in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{1,}", query)
            if t.strip()
        ]
        scored: list[tuple[float, dict[str, Any]]] = []
        for mid, item in candidates.items():
            blob = f"{item['title']} {item['content']} {' '.join(item.get('tags') or [])}".lower()
            token_hits = sum(1 for t in query_tokens if t in blob) if query_tokens else 0
            if mid not in fts_hits and query:
                item_tags = {t.lower() for t in item.get("tags") or []}
                tag_overlap = bool(tag_set and tag_set & item_tags)
                if not tag_overlap and token_hits == 0 and query.lower() not in blob:
                    continue
            score = 0.0
            if mid in fts_hits:
                # Convert FTS rank to positive score.
                score += 50.0 + min(40.0, abs(fts_hits[mid]))
            score += token_hits * 8.0
            item_tags = {t.lower() for t in item.get("tags") or []}
            if tag_set:
                overlap = len(tag_set & item_tags)
                score += overlap * 15.0
            # Scope preference: project > local > user for project tasks.
            scope_boost = {"project": 10.0, "local": 6.0, "user": 3.0}.get(item["scope"], 0)
            score += scope_boost
            # Recency.
            ts = item.get("last_used_at") or item.get("updated_at") or 0
            age_days = max(0.0, (now - float(ts)) / 86400.0)
            score += max(0.0, 20.0 - age_days)
            scored.append((score, item))

        scored.sort(key=lambda x: (-x[0], -(x[1].get("updated_at") or 0)))
        results = []
        for score, item in scored[:limit]:
            results.append({**item, "score": round(score, 3)})
        return results

    def record_usage(
        self,
        memory_id: str,
        user_id: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        reason: str = "",
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE memories SET last_used_at=?, updated_at=updated_at
                   WHERE id=? AND user_id=?""",
                (now, memory_id, user_id),
            )
            conn.execute(
                """INSERT INTO memory_usage
                   (memory_id, user_id, project_id, task_id, reason, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (memory_id, user_id, project_id, task_id, reason[:500], now),
            )

    def list_usage(
        self,
        user_id: str,
        *,
        memory_id: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["user_id=?"]
        params: list[Any] = [user_id]
        if memory_id:
            clauses.append("memory_id=?")
            params.append(memory_id)
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        sql = (
            f"SELECT * FROM memory_usage WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 200)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except json.JSONDecodeError:
            item["tags"] = []
        item["confidence"] = float(item.get("confidence") if item.get("confidence") is not None else 0.6)
        item["conflict_status"] = item.get("conflict_status") or "none"
        item["source"] = {
            "conversation_id": item.get("source_conversation_id"),
            "event_seq": item.get("source_event_seq"),
        }
        return item


def _looks_like_secret_blob(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "-----begin ",
        "private_key",
        "client_secret=",
        "api_key=",
        "sk-",
        "ghp_",
        "password=",
    )
    hits = sum(1 for m in markers if m in lowered)
    return hits >= 2 or "-----begin rsa private key-----" in lowered


# Process-local default store (tests can construct their own).
_default_store: MemoryStore | None = None
_store_lock = threading.Lock()


def get_memory_store(db_path: Path | None = None) -> MemoryStore:
    global _default_store
    if db_path is not None:
        return MemoryStore(db_path)
    with _store_lock:
        if _default_store is None:
            _default_store = MemoryStore()
        return _default_store


def reset_memory_store() -> None:
    global _default_store
    with _store_lock:
        _default_store = None
