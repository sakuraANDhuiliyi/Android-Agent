"""Stage 19 eval suite runner (unittest wrapper)."""

from __future__ import annotations

import unittest
from pathlib import Path

from evals.harness import EvalHarness, run_all_evals


class EvalSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        artifacts = Path(__file__).resolve().parent.parent / ".artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        report = artifacts / "eval-last-report.json"
        cls.results = run_all_evals(report_path=report)

    def test_all_scenarios_registered(self) -> None:
        harness = EvalHarness()
        ids = [s["id"] for s in harness.list_scenarios()]
        self.assertEqual(len(ids), 24)
        self.assertEqual(len(self.results), 24)

    def test_every_scenario_passes(self) -> None:
        failed = [r for r in self.results if not r.passed]
        msg = "\n".join(f"{r.scenario_id}: {r.error}" for r in failed)
        self.assertEqual(failed, [], msg)

    def test_metrics_populated(self) -> None:
        for r in self.results:
            self.assertGreaterEqual(r.metrics.wall_time_ms, 0)
            self.assertIn(r.metrics.build_result, {"success", "failed", "skipped", "mocked"})


if __name__ == "__main__":
    unittest.main()
