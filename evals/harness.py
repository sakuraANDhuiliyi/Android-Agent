from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.metrics import EvalResult, write_report
from evals.scenarios import FIXTURE_VERSION, SCENARIO_RUNNERS


class EvalHarness:
    """Run versioned offline eval scenarios and collect metrics."""

    def __init__(self, fixture_version: str = FIXTURE_VERSION) -> None:
        self.fixture_version = fixture_version
        manifest_path = (
            Path(__file__).resolve().parent / "fixtures" / fixture_version / "manifest.json"
        )
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def list_scenarios(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("scenarios") or [])

    def run_one(self, scenario_id: str) -> EvalResult:
        runner = SCENARIO_RUNNERS.get(scenario_id)
        if runner is None:
            return EvalResult(
                scenario_id=scenario_id,
                title=scenario_id,
                fixture_version=self.fixture_version,
                passed=False,
                metrics=__import__("evals.metrics", fromlist=["EvalMetrics"]).EvalMetrics(),
                error=f"unknown scenario: {scenario_id}",
            )
        return runner()

    def run_all(self) -> list[EvalResult]:
        results: list[EvalResult] = []
        for item in self.list_scenarios():
            sid = item["id"]
            results.append(self.run_one(sid))
        return results


def run_all_evals(*, report_path: str | Path | None = None) -> list[EvalResult]:
    harness = EvalHarness()
    results = harness.run_all()
    if report_path:
        write_report(results, str(report_path))
    return results
