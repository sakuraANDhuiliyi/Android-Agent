from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class WebSocketTicket:
    user_id: str
    resource_type: str
    resource_id: str
    expires_at: float


class WebSocketTicketStore:
    """Process-local, one-time WebSocket tickets.

    Browsers cannot attach an Authorization header to WebSocket handshakes.
    Tickets keep long-lived bearer credentials out of URLs and access logs.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, WebSocketTicket] = {}
        self._lock = threading.Lock()

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
        record = WebSocketTicket(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            expires_at=expires_at,
        )
        with self._lock:
            self._purge_locked(now)
            self._tickets[self._digest(ticket)] = record
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
        with self._lock:
            self._purge_locked(now)
            record = self._tickets.pop(self._digest(ticket), None)
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
