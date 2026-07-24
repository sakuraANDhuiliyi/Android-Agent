from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


Decision = Literal["approved", "rejected", "timeout", "canceled"]

EmitFn = Callable[[str, dict[str, Any]], None]
StatusFn = Callable[[str], None]


@dataclass
class ApprovalRequest:
    id: str
    job_id: str
    user_id: str
    kind: str
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    decision: Decision | None = None
    event: threading.Event = field(default_factory=threading.Event)


_lock = threading.Lock()
_pending: dict[str, ApprovalRequest] = {}


def request_user_approval(
    *,
    job_id: str,
    user_id: str,
    kind: str,
    payload: dict[str, Any],
    on_event: EmitFn | None = None,
    set_status: StatusFn | None = None,
    timeout_sec: float = 300.0,
    cancel_check: Callable[[], None] | None = None,
) -> Decision:
    """Block until the user approves/rejects, or timeout/cancel.

    Emits ``approval_required`` then ``approval_resolved``.
    """
    approval_id = uuid.uuid4().hex[:12]
    req = ApprovalRequest(
        id=approval_id,
        job_id=job_id,
        user_id=user_id,
        kind=kind,
        payload=dict(payload),
    )
    with _lock:
        _pending[approval_id] = req

    message = payload.get("message") or f"等待用户确认: {kind}"
    if on_event:
        on_event(
            "approval_required",
            {
                "message": message,
                "approval_id": approval_id,
                "job_id": job_id,
                "kind": kind,
                **{k: v for k, v in payload.items() if k != "message"},
            },
        )
    if set_status:
        set_status("awaiting_approval")

    # Keep the agent paused until the UI card is answered (default 10 min).
    deadline = time.time() + max(30.0, float(timeout_sec))
    try:
        while not req.event.wait(timeout=0.4):
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    resolve_approval(
                        approval_id,
                        user_id,
                        approved=False,
                        reason="canceled",
                        force_decision="canceled",
                    )
                    break
            if time.time() >= deadline:
                resolve_approval(
                    approval_id,
                    user_id,
                    approved=False,
                    reason="timeout",
                    force_decision="timeout",
                )
                break
    finally:
        decision = req.decision or "timeout"
        if set_status and decision in {"approved", "rejected", "timeout", "canceled"}:
            # Resume agent loop so the tool can return success/failure (unless whole job ends).
            if decision != "canceled":
                set_status("running")
            # canceled: job will end via CancellationRequested / cancel path
        if on_event:
            on_event(
                "approval_resolved",
                {
                    "message": f"用户确认结果: {decision}",
                    "approval_id": approval_id,
                    "job_id": job_id,
                    "kind": kind,
                    "decision": decision,
                },
            )
        with _lock:
            _pending.pop(approval_id, None)
    return req.decision or "timeout"


def resolve_approval(
    approval_id: str,
    user_id: str,
    *,
    approved: bool,
    reason: str = "",
    force_decision: Decision | None = None,
) -> dict[str, Any] | None:
    with _lock:
        req = _pending.get(approval_id)
        if not req:
            return None
        if req.user_id != user_id:
            return None
        if req.decision is not None:
            return {
                "id": req.id,
                "job_id": req.job_id,
                "kind": req.kind,
                "decision": req.decision,
                "payload": req.payload,
            }
        if force_decision:
            req.decision = force_decision
        else:
            req.decision = "approved" if approved else "rejected"
        if reason:
            req.payload["resolve_reason"] = reason
        req.event.set()
        return {
            "id": req.id,
            "job_id": req.job_id,
            "kind": req.kind,
            "decision": req.decision,
            "payload": req.payload,
        }


def reject_job_approvals(job_id: str, user_id: str, *, reason: str = "canceled") -> int:
    """Reject all pending approvals for a job (e.g. on cancel)."""
    ids: list[str] = []
    with _lock:
        for approval_id, req in _pending.items():
            if req.job_id == job_id and req.user_id == user_id and req.decision is None:
                ids.append(approval_id)
    count = 0
    for approval_id in ids:
        if resolve_approval(
            approval_id,
            user_id,
            approved=False,
            reason=reason,
            force_decision="canceled",
        ):
            count += 1
    return count


def get_pending_approvals(job_id: str, user_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [
            {
                "id": req.id,
                "job_id": req.job_id,
                "kind": req.kind,
                "payload": req.payload,
                "created_at": req.created_at,
            }
            for req in _pending.values()
            if req.job_id == job_id and req.user_id == user_id and req.decision is None
        ]
