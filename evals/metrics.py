from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EvalMetrics:
    """Per-scenario metrics required by Stage 19."""

    goal_completed: bool = False
    files_modified: bool = False
    modified_paths: list[str] = field(default_factory=list)
    build_result: str = "skipped"  # success|failed|skipped|mocked
    tool_calls: int = 0
    chars_estimate: int = 0
    tokens_estimate: int = 0
    wall_time_ms: float = 0.0
    approvals: int = 0
    recoveries: int = 0
    security_violations: int = 0
    constraint_recall: float = 0.0
    tool_chain_complete: float = 0.0
    hallucination_rate: float = 0.0
    unresolved_retention: float = 0.0
    token_savings: float = 0.0
    first_token_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    def estimate_tokens_from_chars(self) -> None:
        self.tokens_estimate = max(self.tokens_estimate, self.chars_estimate // 4)


@dataclass
class EvalResult:
    scenario_id: str
    title: str
    fixture_version: str
    passed: bool
    metrics: EvalMetrics
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


class Timer:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    def ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0


def write_report(results: list[EvalResult], path: str) -> None:
    data = {
        "fixture_version": results[0].fixture_version if results else "v1",
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [r.to_dict() for r in results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
