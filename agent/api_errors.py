"""Unified HTTP error envelope for public API responses."""

from __future__ import annotations

from typing import Any

ERROR_SCHEMA_VERSION = 1

DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
    507: "insufficient_storage",
}

RETRYABLE_STATUS_CODES = frozenset({429, 503, 507})


def default_error_code(status_code: int) -> str:
    return DEFAULT_CODES.get(status_code, "http_error")


def is_retryable(status_code: int, code: str | None = None) -> bool:
    if code in {"rate_limited", "service_unavailable", "insufficient_storage"}:
        return True
    return status_code in RETRYABLE_STATUS_CODES


def user_message_from_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail or "请求失败"
    if isinstance(detail, list):
        return "请求参数无效"
    if isinstance(detail, dict):
        for key in ("user_message", "message"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return "请求失败"
    return "请求失败"


def build_error_body(
    status_code: int,
    detail: Any,
    *,
    code: str | None = None,
) -> dict[str, Any]:
    resolved_code = code or default_error_code(status_code)
    user_message = user_message_from_detail(detail)
    body: dict[str, Any] = {
        "detail": detail,
        "error": {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": resolved_code,
            "retryable": is_retryable(status_code, resolved_code),
            "user_message": user_message,
        },
    }
    if isinstance(detail, str):
        body["detail"] = detail
    return body
