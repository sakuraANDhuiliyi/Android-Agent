"""Canonical task/job status semantics shared by API and clients."""

from __future__ import annotations

from typing import Any

# Persisted in SQLite `tasks.status`
TASK_STATUSES = frozenset(
    {
        "queued",
        "running",
        "awaiting_approval",
        "paused",
        "succeeded",
        "failed",
        "canceled",
        "interrupted",
    }
)

# Derived for UI when `cancel_requested` is set on a non-terminal task
DERIVED_STATUSES = frozenset({"cancel_requested"})

ALL_KNOWN_STATUSES = TASK_STATUSES | DERIVED_STATUSES

ACTIVE_STATUSES = frozenset(
    {
        "queued",
        "running",
        "awaiting_approval",
        "paused",
        "cancel_requested",
    }
)

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled", "interrupted"})

# Cancel API applies to these stored statuses (plus idempotent re-cancel).
CANCELABLE_STATUSES = frozenset(
    {"queued", "running", "awaiting_approval", "paused"}
)

PAUSABLE_STATUSES = frozenset({"queued", "running"})
RESUMABLE_STATUSES = frozenset({"paused"})

STATUS_LABELS_ZH: dict[str, str] = {
    "queued": "排队中",
    "running": "运行中",
    "awaiting_approval": "等待审批",
    "paused": "已暂停",
    "cancel_requested": "正在停止",
    "succeeded": "已完成",
    "failed": "失败",
    "canceled": "已停止",
    "interrupted": "已中断",
}


def display_job_status(
    status: str | None,
    *,
    cancel_requested: bool = False,
) -> str:
    """Map stored status + flags to the status clients should render."""
    raw = (status or "").strip() or "queued"
    if cancel_requested and raw not in TERMINAL_STATUSES:
        return "cancel_requested"
    return raw


def status_label_zh(status: str | None) -> str:
    key = (status or "").strip()
    return STATUS_LABELS_ZH.get(key, key or "—")


def is_active_status(status: str | None, *, cancel_requested: bool = False) -> bool:
    return display_job_status(status, cancel_requested=cancel_requested) in ACTIVE_STATUSES


def enrich_job_dict(job: dict[str, Any]) -> dict[str, Any]:
    """Add display_status and status_label without mutating the input."""
    out = dict(job)
    out["cancel_requested"] = bool(out.get("cancel_requested"))
    display = display_job_status(
        out.get("status"),
        cancel_requested=out["cancel_requested"],
    )
    out["display_status"] = display
    out["status_label"] = status_label_zh(display)
    return out
