"""Helpers for the shared HTTP/WebSocket API contract."""

from __future__ import annotations

import json
from typing import Any

from agent.task_status import display_job_status, status_label_zh

WS_EVENT_SCHEMA_VERSION = 1
OPENAPI_CONTRACT_VERSION = 1


def public_job_ws_event(event: dict[str, Any]) -> dict[str, Any]:
    out = dict(event)
    out["schema_version"] = WS_EVENT_SCHEMA_VERSION
    return out


def public_job_ws_done(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "")
    cancel_requested = bool(job.get("cancel_requested"))
    display = display_job_status(status, cancel_requested=cancel_requested)
    return {
        "schema_version": WS_EVENT_SCHEMA_VERSION,
        "type": "done",
        "ts": job.get("finished_at"),
        "status": status,
        "display_status": display,
        "status_label": status_label_zh(display),
        "result": job.get("final_message") or job.get("result"),
        "error": job.get("error_message") or job.get("error"),
    }


def public_terminal_ws_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": WS_EVENT_SCHEMA_VERSION,
        "seq": chunk["seq"],
        "data": chunk.get("data"),
        "is_stderr": bool(chunk.get("is_stderr")),
        "ts": chunk.get("created_at") or chunk.get("ts"),
    }


def public_terminal_ws_done(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": WS_EVENT_SCHEMA_VERSION,
        "type": "done",
        "status": info.get("status"),
        "exit_code": info.get("exit_code"),
    }


def dump_openapi(app: Any) -> dict[str, Any]:
    spec = app.openapi()
    spec.pop("servers", None)
    spec["x-android-agent-contract"] = OPENAPI_CONTRACT_VERSION
    return json.loads(json.dumps(spec, ensure_ascii=False, default=str, sort_keys=True))


def openapi_path_index(spec: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for path, operations in sorted((spec.get("paths") or {}).items()):
        for method in sorted(operations):
            if method.startswith("x-") or method in {"parameters", "summary", "description", "servers"}:
                continue
            rows.append(f"{method.upper()} {path}")
    return rows
