from __future__ import annotations

import json
import logging
import os
import re
import select
import signal
import sqlite3
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
    import pty
    import termios
    _PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore
    pty = None  # type: ignore
    termios = None  # type: ignore
    _PTY_AVAILABLE = False

from agent.paths import DATA_DIR, workspace_path
from agent.processes import (
    DEFAULT_ALLOWED_ENV_VARS,
    CancellationRequested,
    ProcessTimeoutError,
    _kill_process_group,
    _resolve_cwd,
    _terminate_process_group,
    _wait_for_process,
    build_minimal_env,
)
from agent.redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

MAX_TERMINALS_PER_PROJECT = 5
MAX_IDLE_SECONDS = 3600
MAX_OUTPUT_BUFFER_CHUNKS = 2000
MAX_CHUNK_BYTES = 16 * 1024
MAX_TOTAL_BUFFER_BYTES = 10 * 1024 * 1024

TERMINAL_STATUSES = frozenset(
    {"starting", "running", "exited", "failed", "terminated", "interrupted"}
)

_DANGEROUS_PATTERNS = [
    # Simple destructive/network patterns used for input scanning.
    re.compile(r"(^|[;|&]|\s)rm\s+-rf\s+/(\s|$)", re.IGNORECASE),
]


def _redact_output(data: str) -> str:
    return redact_sensitive_text(data)


class TerminalStore:
    """Persistent metadata and ring-buffer output for terminal sessions."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DATA_DIR / "terminals.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS terminal_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    argv TEXT,
                    shell TEXT,
                    pid INTEGER,
                    status TEXT NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    exit_code INTEGER,
                    last_seq INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_terminals_user_project
                    ON terminal_sessions(user_id, project_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_terminals_status
                    ON terminal_sessions(status);

                CREATE TABLE IF NOT EXISTS terminal_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    data TEXT,
                    is_stderr INTEGER DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_outputs_session_seq
                    ON terminal_outputs(session_id, seq);
                """
            )

    def create_session(
        self,
        session_id: str,
        user_id: str,
        project_id: str,
        cwd: str,
        argv: list[str] | None,
        shell: str | None,
    ) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO terminal_sessions
                   (id, user_id, project_id, cwd, argv, shell, status, started_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    user_id,
                    project_id,
                    cwd,
                    json.dumps(argv) if argv else None,
                    shell,
                    "starting",
                    now,
                    now,
                    now,
                ),
            )
        return self.get_session(session_id, user_id)

    def get_session(self, session_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM terminal_sessions WHERE id=? AND user_id=?",
                    (session_id, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM terminal_sessions WHERE id=?", (session_id,)
                ).fetchone()
            if not row:
                return None
            session = dict(row)
            session["argv"] = json.loads(session["argv"]) if session.get("argv") else None
            return session

    def list_sessions(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM terminal_sessions
                   WHERE user_id=? AND project_id=?
                   ORDER BY updated_at DESC""",
                (user_id, project_id),
            ).fetchall()
            result = []
            for row in rows:
                session = dict(row)
                session["argv"] = (
                    json.loads(session["argv"]) if session.get("argv") else None
                )
                result.append(session)
            return result

    def update_session(
        self,
        session_id: str,
        status: str | None = None,
        pid: int | None = None,
        exit_code: int | None = None,
        finished_at: float | None = None,
        last_seq: int | None = None,
    ) -> None:
        fields = []
        params: list[Any] = []
        if status is not None:
            fields.append("status=?")
            params.append(status)
        if pid is not None:
            fields.append("pid=?")
            params.append(pid)
        if exit_code is not None:
            fields.append("exit_code=?")
            params.append(exit_code)
        if finished_at is not None:
            fields.append("finished_at=?")
            params.append(finished_at)
        if last_seq is not None:
            fields.append("last_seq=?")
            params.append(last_seq)
        if not fields:
            return
        fields.append("updated_at=?")
        params.append(time.time())
        params.append(session_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE terminal_sessions SET {', '.join(fields)} WHERE id=?",
                params,
            )

    def append_output(
        self,
        session_id: str,
        seq: int,
        data: str,
        is_stderr: bool = False,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO terminal_outputs (session_id, seq, data, is_stderr, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, seq, data, 1 if is_stderr else 0, time.time()),
            )
            # Ring buffer: keep only the most recent chunks.
            count = conn.execute(
                "SELECT COUNT(*) FROM terminal_outputs WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            if count > MAX_OUTPUT_BUFFER_CHUNKS:
                conn.execute(
                    """DELETE FROM terminal_outputs WHERE id IN (
                        SELECT id FROM terminal_outputs WHERE session_id=?
                        ORDER BY seq ASC LIMIT ?
                    )""",
                    (session_id, count - MAX_OUTPUT_BUFFER_CHUNKS),
                )

    def read_outputs(
        self,
        session_id: str,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT seq, data, is_stderr, created_at FROM terminal_outputs
                   WHERE session_id=? AND seq > ?
                   ORDER BY seq ASC LIMIT ?""",
                (session_id, after_seq, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_interrupted(self) -> None:
        """Mark all running sessions as interrupted. Called on service restart."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE terminal_sessions
                   SET status='interrupted', finished_at=?, exit_code=-1, updated_at=?
                   WHERE status IN ('starting', 'running')""",
                (time.time(), time.time()),
            )


class TerminalSession:
    """A single terminal or long-running process session.

    Uses a PTY on Unix when possible, otherwise falls back to pipes. Output is
    redacted and stored in a ring-buffer-backed SQLite table.
    """

    def __init__(
        self,
        session_id: str,
        user_id: str,
        project_id: str,
        cwd: str,
        argv: list[str] | None = None,
        shell: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
        store: TerminalStore | None = None,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.project_id = project_id
        self.cwd = cwd
        self.argv = argv
        self.shell = shell or "/bin/bash"
        self.env = env or {}
        self.cols = cols
        self.rows = rows
        self.store = store or TerminalStore()
        self.status = "starting"
        self.pid: int | None = None
        self.exit_code: int | None = None
        self._lock = threading.RLock()
        self._master_fd: int | None = None
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._last_activity = time.monotonic()
        self._cancel_token = threading.Event()

    def _set_window_size(self) -> None:
        if self._master_fd is None:
            return
        try:
            size = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, size)
        except Exception as exc:
            logger.debug("ioctl resize failed: %s", exc)

    def _make_env(self) -> dict[str, str]:
        return build_minimal_env(self.env, DEFAULT_ALLOWED_ENV_VARS)

    def start(self) -> dict[str, Any]:
        workspace = workspace_path(self.user_id, self.project_id)
        resolved_cwd = _resolve_cwd(self.cwd, workspace)
        command = self.argv if self.argv else [self.shell]
        try:
            self._master_fd, slave_fd = pty.openpty()
            self._set_window_size()
            popen_kwargs: dict[str, Any] = {
                "args": command,
                "cwd": resolved_cwd,
                "env": self._make_env(),
                "stdin": slave_fd,
                "stdout": slave_fd,
                "stderr": slave_fd,
                "start_new_session": True,
                "close_fds": True,
            }
            self._proc = subprocess.Popen(**popen_kwargs)
            self.pid = self._proc.pid
            os.close(slave_fd)
            self.status = "running"
            self.store.update_session(
                self.session_id,
                status="running",
                pid=self.pid,
            )
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True
            )
            self._reader_thread.start()
        except Exception as exc:
            logger.exception("Terminal start failed: %s", self.session_id)
            self.status = "failed"
            self.store.update_session(
                self.session_id,
                status="failed",
                finished_at=time.time(),
                exit_code=-1,
            )
            raise
        return self.to_dict()

    def _reader_loop(self) -> None:
        if self._master_fd is None:
            return
        try:
            while True:
                ready, _, _ = select.select([self._master_fd], [], [], 0.2)
                if self._cancel_token.is_set():
                    break
                if not ready:
                    if self._proc is not None and self._proc.poll() is not None:
                        break
                    continue
                try:
                    chunk = os.read(self._master_fd, MAX_CHUNK_BYTES)
                except OSError:
                    break
                if not chunk:
                    if self._proc is not None and self._proc.poll() is not None:
                        break
                    continue
                try:
                    text = chunk.decode("utf-8", errors="replace")
                except Exception:
                    text = chunk.decode("latin-1", errors="replace")
                with self._lock:
                    self._last_activity = time.monotonic()
                    seq = self.store.get_session(self.session_id, self.user_id)["last_seq"] + 1
                    self.store.append_output(self.session_id, seq, _redact_output(text), False)
                    self.store.update_session(self.session_id, last_seq=seq)
        except Exception as exc:
            logger.exception("Terminal reader error: %s", self.session_id)
        finally:
            self._finalize()

    def _finalize(self) -> None:
        with self._lock:
            if self.status in {"exited", "failed", "terminated", "interrupted"}:
                return
            try:
                returncode = self._proc.wait(timeout=2) if self._proc else -1
            except Exception:
                returncode = -1
            self.exit_code = returncode
            if self.status == "running":
                self.status = "exited" if returncode == 0 else "failed"
            self.store.update_session(
                self.session_id,
                status=self.status,
                exit_code=returncode,
                finished_at=time.time(),
            )
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except Exception:
                    pass
                self._master_fd = None

    def write(self, data: str) -> None:
        with self._lock:
            self._last_activity = time.monotonic()
            if self._master_fd is None or self.status != "running":
                raise RuntimeError("Terminal session is not running")
            # Scan for dangerous patterns.
            for pattern in _DANGEROUS_PATTERNS:
                if pattern.search(data):
                    raise PermissionError("输入包含被禁止的危险命令")
            try:
                os.write(self._master_fd, data.encode("utf-8"))
            except OSError as exc:
                raise RuntimeError(f"写入终端失败: {exc}") from exc

    def resize(self, cols: int, rows: int) -> None:
        with self._lock:
            self.cols = max(1, cols)
            self.rows = max(1, rows)
            self._set_window_size()

    def terminate(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                _terminate_process_group(self._proc)
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    _kill_process_group(self._proc)
                    try:
                        self._proc.wait(timeout=2)
                    except Exception:
                        pass
            self._cancel_token.set()
            if self.status not in {"exited", "failed", "terminated", "interrupted"}:
                self.status = "terminated"
            self.store.update_session(
                self.session_id,
                status=self.status,
                exit_code=self.exit_code if self.exit_code is not None else -1,
                finished_at=time.time(),
            )
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except Exception:
                    pass
                self._master_fd = None

    def is_idle(self, timeout: float = MAX_IDLE_SECONDS) -> bool:
        with self._lock:
            return self.status == "running" and (
                time.monotonic() - self._last_activity > timeout
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.session_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "cwd": self.cwd,
            "argv": self.argv,
            "shell": self.shell,
            "pid": self.pid,
            "status": self.status,
            "exit_code": self.exit_code,
        }


class TerminalManager:
    """In-memory manager of active terminal sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        self._store = TerminalStore()

    def _check_limits(self, user_id: str, project_id: str) -> None:
        with self._lock:
            count = sum(
                1
                for s in self._sessions.values()
                if s.user_id == user_id and s.project_id == project_id and s.status == "running"
            )
        if count >= MAX_TERMINALS_PER_PROJECT:
            raise RuntimeError(f"项目最多允许 {MAX_TERMINALS_PER_PROJECT} 个运行中的终端")

    def create(
        self,
        user_id: str,
        project_id: str,
        cwd: str = ".",
        argv: list[str] | None = None,
        shell: str | None = None,
        cols: int = 80,
        rows: int = 24,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._check_limits(user_id, project_id)
        session_id = uuid.uuid4().hex[:12]
        store = TerminalStore()
        store.create_session(session_id, user_id, project_id, cwd, argv, shell)
        session = TerminalSession(
            session_id=session_id,
            user_id=user_id,
            project_id=project_id,
            cwd=cwd,
            argv=argv,
            shell=shell,
            env=env,
            cols=cols,
            rows=rows,
            store=store,
        )
        with self._lock:
            self._sessions[session_id] = session
        try:
            session.start()
        except Exception:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise
        return session.to_dict()

    def get(self, session_id: str, user_id: str) -> TerminalSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is not None and session.user_id == user_id:
            return session
        return None

    def list(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        return TerminalStore().list_sessions(user_id, project_id)

    def get_info(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        store = TerminalStore()
        session = store.get_session(session_id, user_id)
        if session is None:
            return None
        # Refresh status from active in-memory session if present.
        active = self.get(session_id, user_id)
        if active is not None:
            session["status"] = active.status
            session["exit_code"] = active.exit_code
            session["pid"] = active.pid
        return session

    def write(self, session_id: str, user_id: str, data: str) -> dict[str, Any] | None:
        session = self.get(session_id, user_id)
        if session is None:
            return None
        try:
            session.write(data)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def resize(self, session_id: str, user_id: str, cols: int, rows: int) -> dict[str, Any] | None:
        session = self.get(session_id, user_id)
        if session is None:
            return None
        try:
            session.resize(cols, rows)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def terminate(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        session = self.get(session_id, user_id)
        if session is None:
            # Also mark DB session as terminated if it exists.
            store = TerminalStore()
            db_session = store.get_session(session_id, user_id)
            if db_session is not None and db_session["status"] == "running":
                store.update_session(
                    session_id,
                    status="terminated",
                    exit_code=-1,
                    finished_at=time.time(),
                )
                return {"ok": True}
            return None
        session.terminate()
        with self._lock:
            self._sessions.pop(session_id, None)
        return {"ok": True}

    def read_outputs(
        self, session_id: str, after_seq: int = 0, limit: int = 1000
    ) -> list[dict[str, Any]]:
        return TerminalStore().read_outputs(session_id, after_seq, limit)

    def cleanup_idle(self) -> None:
        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.is_idle():
                    session.terminate()
                    self._sessions.pop(session_id, None)


_manager = TerminalManager()


def create_terminal(
    user_id: str,
    project_id: str,
    cwd: str = ".",
    argv: list[str] | None = None,
    shell: str | None = None,
    cols: int = 80,
    rows: int = 24,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    return _manager.create(
        user_id, project_id, cwd, argv, shell, cols, rows, env
    )


def list_terminals(user_id: str, project_id: str) -> list[dict[str, Any]]:
    return _manager.list(user_id, project_id)


def get_terminal(session_id: str, user_id: str) -> dict[str, Any] | None:
    return _manager.get_info(session_id, user_id)


def write_terminal_input(session_id: str, user_id: str, data: str) -> dict[str, Any] | None:
    return _manager.write(session_id, user_id, data)


def resize_terminal(session_id: str, user_id: str, cols: int, rows: int) -> dict[str, Any] | None:
    return _manager.resize(session_id, user_id, cols, rows)


def terminate_terminal(session_id: str, user_id: str) -> dict[str, Any] | None:
    return _manager.terminate(session_id, user_id)


def terminal_outputs(session_id: str, after_seq: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
    return _manager.read_outputs(session_id, after_seq, limit)


def mark_interrupted_terminals() -> None:
    TerminalStore().mark_interrupted()
    # Synchronize in-memory sessions so get_terminal reflects the restart state.
    with _manager._lock:
        for session in _manager._sessions.values():
            if session.status in {"starting", "running"}:
                session.status = "interrupted"
                session.exit_code = -1
