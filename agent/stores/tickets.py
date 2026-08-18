from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class TicketBackend(Protocol):
    def put(
        self,
        digest: str,
        user_id: str,
        resource_type: str,
        resource_id: str,
        expires_at: float,
    ) -> None: ...

    def take(
        self,
        digest: str,
        resource_type: str,
        resource_id: str,
        now: float,
    ) -> str | None: ...


@dataclass(frozen=True)
class WebSocketTicket:
    user_id: str
    resource_type: str
    resource_id: str
    expires_at: float


class MemoryTicketBackend:
    """Process-local backend. Used by unit tests and as a Redis stand-in."""

    def __init__(self) -> None:
        self._tickets: dict[str, WebSocketTicket] = {}
        self._lock = threading.Lock()

    def put(
        self,
        digest: str,
        user_id: str,
        resource_type: str,
        resource_id: str,
        expires_at: float,
    ) -> None:
        with self._lock:
            self._purge_locked(time.time())
            self._tickets[digest] = WebSocketTicket(
                user_id=user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                expires_at=expires_at,
            )

    def take(
        self,
        digest: str,
        resource_type: str,
        resource_id: str,
        now: float,
    ) -> str | None:
        with self._lock:
            self._purge_locked(now)
            record = self._tickets.pop(digest, None)
        if (
            record is None
            or record.expires_at < now
            or record.resource_type != resource_type
            or record.resource_id != resource_id
        ):
            return None
        return record.user_id

    def _purge_locked(self, now: float) -> None:
        expired = [
            digest
            for digest, record in self._tickets.items()
            if record.expires_at < now
        ]
        for digest in expired:
            self._tickets.pop(digest, None)


class SqliteTicketBackend:
    """Shared one-time tickets. Two API processes on the same DB cannot both consume."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ws_tickets (
                    digest TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ws_tickets_expires ON ws_tickets(expires_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def put(
        self,
        digest: str,
        user_id: str,
        resource_type: str,
        resource_id: str,
        expires_at: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM ws_tickets WHERE expires_at < ?", (time.time(),))
            conn.execute(
                """INSERT INTO ws_tickets
                   (digest, user_id, resource_type, resource_id, expires_at)
                   VALUES (?,?,?,?,?)""",
                (digest, user_id, resource_type, resource_id, expires_at),
            )

    def take(
        self,
        digest: str,
        resource_type: str,
        resource_id: str,
        now: float,
    ) -> str | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM ws_tickets WHERE expires_at < ?", (now,))
            row = conn.execute(
                """SELECT user_id FROM ws_tickets
                   WHERE digest=? AND resource_type=? AND resource_id=? AND expires_at>=?""",
                (digest, resource_type, resource_id, now),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM ws_tickets WHERE digest=?", (digest,))
            return str(row[0])


class RedisLikeTicketBackend:
    """Atomic ticket + rate-limit backend.

    Production uses redis-py when `redis_url` starts with redis://.
    Tests inject an in-memory map that still serializes consume with a lock.
    """

    def __init__(self, client: object | None = None) -> None:
        self._client = client
        self._memory = MemoryTicketBackend()
        self._counts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_url(cls, url: str) -> "RedisLikeTicketBackend":
        parsed = urlparse(url or "")
        if parsed.scheme in {"redis", "rediss"}:
            try:
                import redis  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "deployment_mode=hybrid 需要安装 redis 包: pip install redis"
                ) from exc
            return cls(redis.Redis.from_url(url, decode_responses=True))
        if parsed.scheme in {"memory", "mem", ""}:
            return cls(None)
        raise RuntimeError(f"不支持的 redis_url scheme: {parsed.scheme}")

    def put(
        self,
        digest: str,
        user_id: str,
        resource_type: str,
        resource_id: str,
        expires_at: float,
    ) -> None:
        ttl = max(1, int(expires_at - time.time()))
        payload = f"{user_id}\0{resource_type}\0{resource_id}\0{expires_at}"
        if self._client is not None:
            self._client.setex(f"ws_ticket:{digest}", ttl, payload)
            return
        self._memory.put(digest, user_id, resource_type, resource_id, expires_at)

    def take(
        self,
        digest: str,
        resource_type: str,
        resource_id: str,
        now: float,
    ) -> str | None:
        if self._client is not None:
            key = f"ws_ticket:{digest}"
            getter = getattr(self._client, "getdel", None)
            raw = getter(key) if callable(getter) else None
            if raw is None and getter is None:
                pipe = self._client.pipeline()
                pipe.get(key)
                pipe.delete(key)
                raw, _ = pipe.execute()
            if not raw:
                return None
            user_id, stored_type, stored_id, expires_s = str(raw).split("\0", 3)
            if (
                stored_type != resource_type
                or stored_id != resource_id
                or float(expires_s) < now
            ):
                return None
            return user_id
        return self._memory.take(digest, resource_type, resource_id, now)

    def as_rate_limiter(self) -> "RedisLikeRateLimiter":
        return RedisLikeRateLimiter(self)


class RedisLikeRateLimiter:
    def __init__(self, backend: RedisLikeTicketBackend) -> None:
        self._backend = backend

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        from agent.governance import QuotaExceededError

        now = time.time()
        cutoff = now - max(1, window_seconds)
        client = self._backend._client
        if client is not None:
            redis_key = f"rl:{key}"
            client.zremrangebyscore(redis_key, 0, cutoff)
            count = client.zcard(redis_key)
            if int(count or 0) >= max(1, limit):
                raise QuotaExceededError("请求过于频繁，请稍后重试")
            client.zadd(redis_key, {f"{now}:{secrets.token_hex(4)}": now})
            client.expire(redis_key, max(1, window_seconds) + 5)
            return
        with self._backend._lock:
            entries = self._backend._counts.setdefault(key, [])
            entries[:] = [ts for ts in entries if ts >= cutoff]
            if len(entries) >= max(1, limit):
                raise QuotaExceededError("请求过于频繁，请稍后重试")
            entries.append(now)


class WebSocketTicketStore:
    """One-time WebSocket tickets with a swappable backend.

    Browsers cannot attach an Authorization header to WebSocket handshakes.
    Tickets keep long-lived bearer credentials out of URLs and access logs.
    """

    def __init__(self, backend: TicketBackend | None = None) -> None:
        self._backend: TicketBackend = backend or MemoryTicketBackend()

    @staticmethod
    def _digest(ticket: str) -> str:
        return hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    def issue(
        self,
        user_id: str,
        resource_type: str,
        resource_id: str,
        *,
        ttl_seconds: int = 30,
    ) -> tuple[str, float]:
        now = time.time()
        expires_at = now + max(5, min(int(ttl_seconds), 120))
        ticket = secrets.token_urlsafe(32)
        self._backend.put(
            self._digest(ticket),
            user_id,
            resource_type,
            resource_id,
            expires_at,
        )
        return ticket, expires_at

    def consume(
        self,
        ticket: str,
        resource_type: str,
        resource_id: str,
    ) -> str | None:
        if not ticket:
            return None
        now = time.time()
        return self._backend.take(
            self._digest(ticket),
            resource_type,
            resource_id,
            now,
        )
