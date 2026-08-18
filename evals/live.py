"""Gated live-provider eval. Default path never calls a real model."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent.redaction import redact_sensitive_value

LIVE_EVAL_ENV = "AGENT_LIVE_EVAL"
LIVE_EVAL_MAX_CALLS_ENV = "AGENT_LIVE_EVAL_MAX_CALLS"
LIVE_EVAL_PROVIDER_ENV = "AGENT_LIVE_EVAL_PROVIDER"


def live_eval_enabled() -> bool:
    return os.environ.get(LIVE_EVAL_ENV) == "1"


def live_eval_budget() -> int:
    raw = os.environ.get(LIVE_EVAL_MAX_CALLS_ENV, "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def live_eval_dir(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return base / ".artifacts" / "live-eval"


def run_live_eval(
    *,
    root: Path | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Write an isolated report. Never performs network I/O from the default harness."""
    if not live_eval_enabled():
        return {
            "skipped": True,
            "reason": f"{LIVE_EVAL_ENV} is not 1",
            "calls_made": 0,
        }
    budget = live_eval_budget()
    if budget <= 0:
        return {
            "skipped": True,
            "reason": "budget is 0",
            "calls_made": 0,
        }
    out_dir = live_eval_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = redact_sensitive_value(
        {
            "skipped": False,
            "provider": provider or os.environ.get(LIVE_EVAL_PROVIDER_ENV, ""),
            "max_calls": budget,
            "calls_made": 0,
            "note": "live provider adapter is isolated; default tests do not invoke paid APIs",
        }
    )
    (out_dir / "last.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report
