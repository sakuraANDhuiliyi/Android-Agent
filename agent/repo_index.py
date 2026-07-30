from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from agent.paths import DATA_DIR, workspace_path
from agent.safe_paths import is_workspace_file


logger = logging.getLogger(__name__)

DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".gradle",
        "build",
        "node_modules",
        "__pycache__",
        ".idea",
    }
)

TEXT_SUFFIXES = frozenset(
    {
        ".kt",
        ".kts",
        ".java",
        ".xml",
        ".gradle",
        ".properties",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
    }
)

BINARY_SUFFIXES = frozenset(
    {
        ".apk",
        ".jar",
        ".aar",
        ".so",
        ".dex",
        ".class",
        ".o",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".mp3",
        ".mp4",
        ".zip",
        ".tar",
        ".gz",
    }
)

LANGUAGE_BY_SUFFIX = {
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".java": "java",
    ".xml": "xml",
    ".gradle": "gradle",
    ".properties": "properties",
}


class RepoIndex:
    """Project-level code index backed by SQLite with FTS5.

    The index is a cache: it can be deleted and rebuilt at any time without
    losing canonical conversation data. Incremental updates use SHA-256 file
    hashes so unchanged files are skipped.
    """

    def __init__(self, user_id: str, project_id: str):
        self.user_id = user_id
        self.project_id = project_id
        self._workspace = workspace_path(user_id, project_id)
        self._db_dir = DATA_DIR / "index" / user_id / project_id
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._db_dir / "repo_index.db"
        self._lock = threading.RLock()
        try:
            self._init_db()
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            logger.warning(
                "Index DB for %s/%s is corrupt (%s); recreating.",
                user_id,
                project_id,
                exc,
            )
            self._delete_db_files()
            self._init_db()

    def _delete_db_files(self) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                (self._db_path.parent / (self._db_path.name + suffix)).unlink(
                    missing_ok=True
                )
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        # Autocommit mode avoids long-running transactions that can leave the
        # DB locked when connections are reopened quickly in sequence.
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspace_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT NOT NULL UNIQUE,
                    language TEXT,
                    size INTEGER,
                    hash TEXT,
                    indexed_at REAL,
                    error_message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_files_lang ON workspace_files(language);
                CREATE INDEX IF NOT EXISTS idx_files_hash ON workspace_files(hash);

                CREATE VIRTUAL TABLE IF NOT EXISTS fts_content USING fts5(
                    rel_path UNINDEXED,
                    content,
                    content_rowid=rowid,
                    tokenize='porter unicode61'
                );

                CREATE TABLE IF NOT EXISTS workspace_symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT NOT NULL,
                    symbol_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT,
                    line INTEGER,
                    column INTEGER,
                    signature TEXT,
                    extra TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON workspace_symbols(name);
                CREATE INDEX IF NOT EXISTS idx_symbols_path ON workspace_symbols(rel_path);
                CREATE INDEX IF NOT EXISTS idx_symbols_type ON workspace_symbols(symbol_type);

                CREATE TABLE IF NOT EXISTS workspace_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT NOT NULL,
                    symbol_name TEXT NOT NULL,
                    ref_type TEXT,
                    line INTEGER,
                    column INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_refs_name ON workspace_references(symbol_name);
                CREATE INDEX IF NOT EXISTS idx_refs_path ON workspace_references(rel_path);

                CREATE TABLE IF NOT EXISTS index_status (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    indexed_at REAL,
                    file_count INTEGER,
                    status TEXT,
                    error_message TEXT
                );
                """
            )

    def _status(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM index_status WHERE id = 1"
            ).fetchone()
            if not row:
                return {
                    "status": "none",
                    "indexed_at": None,
                    "file_count": 0,
                    "error_message": None,
                }
            return dict(row)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status()

    def _set_status(self, status: str, file_count: int, error_message: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO index_status (id, indexed_at, file_count, status, error_message)
                   VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       indexed_at=excluded.indexed_at,
                       file_count=excluded.file_count,
                       status=excluded.status,
                       error_message=excluded.error_message""",
                (time.time(), file_count, status, error_message),
            )

    def _file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _is_ignored(self, path: Path, ignore_patterns: list[str] | None = None) -> bool:
        rel = path.relative_to(self._workspace).as_posix()
        parts = rel.split("/")
        for part in parts[:-1]:
            if part in DEFAULT_IGNORE_DIRS:
                return True
        if path.suffix.lower() in BINARY_SUFFIXES:
            return True
        if ignore_patterns:
            for pattern in ignore_patterns:
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
                    return True
        return False

    def _collect_files(
        self,
        max_size: int = 1_000_000,
        ignore_patterns: list[str] | None = None,
    ) -> list[tuple[Path, str, int, str]]:
        """Return (path, rel_path, size, hash) for text files under workspace."""
        results: list[tuple[Path, str, int, str]] = []
        if not self._workspace.is_dir():
            return results
        for path in self._workspace.rglob("*"):
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            if not is_workspace_file(self._workspace, path):
                continue
            if self._is_ignored(path, ignore_patterns):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            size = path.stat().st_size
            if size > max_size:
                continue
            try:
                file_hash = self._file_hash(path)
            except OSError as exc:
                logger.warning("Cannot hash %s: %s", path, exc)
                continue
            rel = path.relative_to(self._workspace).as_posix()
            results.append((path, rel, size, file_hash))
        return results

    def _remove_missing_files(self, current_rels: set[str]) -> None:
        with self._connect() as conn:
            existing = conn.execute("SELECT rel_path FROM workspace_files").fetchall()
            for (rel_path,) in existing:
                if rel_path not in current_rels:
                    conn.execute("DELETE FROM workspace_files WHERE rel_path = ?", (rel_path,))

    def _delete_file_index(self, conn: sqlite3.Connection, rel_path: str) -> None:
        conn.execute("DELETE FROM workspace_files WHERE rel_path = ?", (rel_path,))
        conn.execute("DELETE FROM workspace_symbols WHERE rel_path = ?", (rel_path,))
        conn.execute("DELETE FROM workspace_references WHERE rel_path = ?", (rel_path,))
        row = conn.execute(
            "SELECT rowid FROM fts_content WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM fts_content WHERE rowid = ?", (row["rowid"],))

    def _index_file(
        self,
        conn: sqlite3.Connection,
        path: Path,
        rel_path: str,
        size: int,
        file_hash: str,
    ) -> str | None:
        from agent.repo_parser import extract_file

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "无法以 UTF-8 解码"
        except OSError as exc:
            return f"读取失败: {exc}"

        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
        try:
            extraction = extract_file(text, rel_path, language)
        except Exception as exc:
            extraction = {"symbols": [], "references": [], "lightweight": True}
            error = f"解析失败: {exc}"
        else:
            error = None

        self._delete_file_index(conn, rel_path)
        conn.execute(
            """INSERT INTO workspace_files (rel_path, language, size, hash, indexed_at, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rel_path, language, size, file_hash, time.time(), error),
        )
        conn.execute(
            "INSERT INTO fts_content (rel_path, content) VALUES (?, ?)",
            (rel_path, text),
        )
        for symbol in extraction.get("symbols", []):
            conn.execute(
                """INSERT INTO workspace_symbols
                   (rel_path, symbol_type, name, qualified_name, line, column, signature, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rel_path,
                    symbol.get("symbol_type", "unknown"),
                    symbol.get("name", ""),
                    symbol.get("qualified_name"),
                    symbol.get("line"),
                    symbol.get("column"),
                    symbol.get("signature"),
                    symbol.get("extra") and json.dumps(symbol.get("extra"), ensure_ascii=False),
                ),
            )
        for ref in extraction.get("references", []):
            conn.execute(
                """INSERT INTO workspace_references
                   (rel_path, symbol_name, ref_type, line, column)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    rel_path,
                    ref.get("symbol_name", ""),
                    ref.get("ref_type"),
                    ref.get("line"),
                    ref.get("column"),
                ),
            )
        return error

    def rebuild(
        self,
        max_size: int = 1_000_000,
        ignore_patterns: list[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Full rebuild of the project index."""
        with self._lock:
            # Start from a clean DB so rebuild is truly full and recovers from
            # corruption without manual intervention.
            self._delete_db_files()
            self._init_db()
            self._set_status("indexing", 0)
            try:
                files = self._collect_files(max_size, ignore_patterns)
                current_rels = {rel for _, rel, _, _ in files}
                self._remove_missing_files(current_rels)
                with self._connect() as conn:
                    for path, rel_path, size, file_hash in files:
                        if progress:
                            progress(rel_path)
                        self._index_file(conn, path, rel_path, size, file_hash)
                self._set_status("ready", len(files))
                return {
                    "status": "ready",
                    "file_count": len(files),
                    "error_message": None,
                }
            except Exception as exc:
                logger.exception("Index rebuild failed for %s/%s", self.user_id, self.project_id)
                self._set_status("error", 0, error_message=str(exc))
                return {
                    "status": "error",
                    "file_count": 0,
                    "error_message": str(exc),
                }

    def update(
        self,
        max_size: int = 1_000_000,
        ignore_patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Incremental update: only re-index files whose hash changed."""
        with self._lock:
            try:
                files = self._collect_files(max_size, ignore_patterns)
                current_rels = {rel for _, rel, _, _ in files}
                self._remove_missing_files(current_rels)
                with self._connect() as conn:
                    existing_hashes = {
                        row["rel_path"]: row["hash"]
                        for row in conn.execute(
                            "SELECT rel_path, hash FROM workspace_files"
                        ).fetchall()
                    }
                    updated = 0
                    for path, rel_path, size, file_hash in files:
                        if existing_hashes.get(rel_path) == file_hash:
                            continue
                        self._index_file(conn, path, rel_path, size, file_hash)
                        updated += 1
                    self._set_status("ready", len(files))
                return {
                    "status": "ready",
                    "file_count": len(files),
                    "updated": updated,
                    "error_message": None,
                }
            except Exception as exc:
                logger.exception("Index update failed for %s/%s", self.user_id, self.project_id)
                self._set_status("error", 0, error_message=str(exc))
                return {
                    "status": "error",
                    "file_count": 0,
                    "updated": 0,
                    "error_message": str(exc),
                }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                try:
                    rows = conn.execute(
                        """SELECT fts_content.rel_path, rank
                           FROM fts_content
                           WHERE fts_content MATCH ?
                           ORDER BY rank
                           LIMIT ?""",
                        (query, limit),
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    logger.warning("FTS search failed: %s", exc)
                    return []
                return [
                    {
                        "rel_path": row["rel_path"],
                        "rank": row["rank"],
                    }
                    for row in rows
                ]

    def find_symbol(
        self,
        name: str | None = None,
        symbol_type: str | None = None,
        rel_path: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._lock:
            conditions = []
            params: list[Any] = []
            if name:
                conditions.append("LOWER(name) = LOWER(?) OR LOWER(qualified_name) = LOWER(?)")
                params.append(name)
                params.append(name)
            if symbol_type:
                conditions.append("symbol_type = ?")
                params.append(symbol_type)
            if rel_path:
                conditions.append("rel_path = ?")
                params.append(rel_path)
            where = " AND ".join(conditions) if conditions else "1"
            query = f"SELECT * FROM workspace_symbols WHERE {where} LIMIT ?"
            params.append(limit)
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(row) for row in rows]

    def find_references(
        self,
        symbol_name: str,
        rel_path: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._lock:
            params: list[Any] = [symbol_name]
            conditions = ["LOWER(symbol_name) = LOWER(?)"]
            if rel_path:
                conditions.append("rel_path = ?")
                params.append(rel_path)
            where = " AND ".join(conditions)
            query = f"SELECT * FROM workspace_references WHERE {where} LIMIT ?"
            params.append(limit)
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(row) for row in rows]

    def repo_map(self, max_files: int = 100) -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                files = conn.execute(
                    """SELECT rel_path, language, size FROM workspace_files
                       ORDER BY rel_path LIMIT ?""",
                    (max_files,),
                ).fetchall()
                symbols = conn.execute(
                    """SELECT symbol_type, COUNT(*) AS count FROM workspace_symbols
                       GROUP BY symbol_type"""
                ).fetchall()
                return {
                    "files": [dict(row) for row in files],
                    "symbol_summary": [dict(row) for row in symbols],
                }

    def related_files(self, rel_path: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return files that share symbols with the given file."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT DISTINCT r.rel_path, COUNT(*) AS shared
                       FROM workspace_references r
                       JOIN workspace_symbols s
                         ON s.name = r.symbol_name
                       WHERE s.rel_path = ? AND r.rel_path != ?
                       GROUP BY r.rel_path
                       ORDER BY shared DESC
                       LIMIT ?""",
                    (rel_path, rel_path, limit),
                ).fetchall()
                return [dict(row) for row in rows]

    def get_file(self, rel_path: str) -> dict[str, Any] | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM workspace_files WHERE rel_path = ?", (rel_path,)
                ).fetchone()
                return dict(row) if row else None


def get_repo_index(user_id: str, project_id: str) -> RepoIndex:
    return RepoIndex(user_id, project_id)
