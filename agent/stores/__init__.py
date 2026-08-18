"""Pluggable runtime stores for single-node SQLite and optional multi-instance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent.config import Settings, validate_deployment_settings
from agent.paths import DATA_DIR
from agent.stores.artifacts import ArtifactStore, LocalArtifactStore, ObjectArtifactStore
from agent.stores.outbox import SqliteOutboxStore
from agent.stores.rate_limit import MemoryRateLimiter, SqliteRateLimiter
from agent.stores.tickets import (
    MemoryTicketBackend,
    RedisLikeTicketBackend,
    SqliteTicketBackend,
    WebSocketTicketStore,
)


class TicketStore(Protocol):
    def issue(
        self,
        user_id: str,
        resource_type: str,
        resource_id: str,
        *,
        ttl_seconds: int = 30,
    ) -> tuple[str, float]: ...

    def consume(
        self,
        ticket: str,
        resource_type: str,
        resource_id: str,
    ) -> str | None: ...


class RateLimiter(Protocol):
    def check(self, key: str, *, limit: int, window_seconds: int) -> None: ...


@dataclass
class RuntimeStores:
    tickets: TicketStore
    rate_limiter: RateLimiter
    registration_limiter: RateLimiter
    artifacts: ArtifactStore
    outbox: SqliteOutboxStore
    mode: str


def build_runtime_stores(
    settings: Settings,
    *,
    db_path: Path | None = None,
    data_dir: Path | None = None,
) -> RuntimeStores:
    validate_deployment_settings(settings)
    root = Path(data_dir or DATA_DIR)
    sqlite_path = Path(db_path or (root / "agent.db"))
    mode = (settings.deployment_mode or "sqlite").strip().lower()

    if settings.redis_url:
        redis_backend = RedisLikeTicketBackend.from_url(settings.redis_url)
        tickets: TicketStore = WebSocketTicketStore(backend=redis_backend)
        limiter: RateLimiter = redis_backend.as_rate_limiter()
        registration: RateLimiter = limiter
    else:
        tickets = WebSocketTicketStore(backend=SqliteTicketBackend(sqlite_path))
        limiter = SqliteRateLimiter(sqlite_path)
        registration = SqliteRateLimiter(sqlite_path, table="registration_windows")

    if (settings.artifact_backend or "local").strip().lower() == "object":
        artifacts: ArtifactStore = ObjectArtifactStore.from_url(
            settings.object_store_url,
            prefix=settings.object_store_prefix,
        )
    else:
        artifacts = LocalArtifactStore(root / "artifacts")

    return RuntimeStores(
        tickets=tickets,
        rate_limiter=limiter,
        registration_limiter=registration,
        artifacts=artifacts,
        outbox=SqliteOutboxStore(sqlite_path),
        mode=mode,
    )


__all__ = [
    "ArtifactStore",
    "LocalArtifactStore",
    "MemoryRateLimiter",
    "MemoryTicketBackend",
    "ObjectArtifactStore",
    "RateLimiter",
    "RuntimeStores",
    "TicketStore",
    "WebSocketTicketStore",
    "build_runtime_stores",
]
