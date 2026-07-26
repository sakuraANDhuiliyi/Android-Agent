from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"

_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"
    ),
    re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}|tvly-[A-Za-z0-9_-]{12,}|"
        r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"AIza[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
    ),
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b("
    r"api[_-]?key|api[_-]?token|authorization|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|secret|password"
    r")(\s*[:=]\s*)([\"']?)([^\s,;}\]\"']{6,})([\"']?)"
)
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(https?://)([^/\s:@]+):([^/\s@]+)@"
)


def redact_sensitive_text(value: str) -> str:
    redacted = value
    for pattern in _TEXT_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    redacted = _ASSIGNMENT_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}"
            f"{match.group(3)}{REDACTED}{match.group(5)}"
        ),
        redacted,
    )
    redacted = _URL_CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTED}@",
        redacted,
    )
    return redacted


def redact_sensitive_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {
            key: redact_sensitive_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_value(item) for item in value]
    return value
