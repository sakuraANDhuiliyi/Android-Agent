"""Historical, task-scoped verification receipts. Reading a DTO never scans files."""
from __future__ import annotations

import math
from typing import Any

from agent.workspace_inputs import compare_workspace_inputs


def valid_counts(value: Any) -> bool:
    return (isinstance(value, dict)
            and all(type(value.get(k)) is int and value[k] >= 0 for k in ("total", "passed", "failed", "skipped"))
            and value["total"] == value["passed"] + value["failed"] + value["skipped"])


def _step(job: dict, task: str, report: dict | None) -> dict:
    tests = task == "testDebugUnitTest"
    step = {"state": "not_run", "task": task, "run_id": None,
            "evidence_time": None, "duration_ms": None, "source_match": "unknown", "reason": "not_run"}
    if tests:
        step["counts"] = None
    if report is None:
        return step
    receipt = report.get("verification_receipt")
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or receipt.get("job_id") != job.get("id") or receipt.get("task") != task:
        return {**step, "state": "unknown", "reason": "legacy_evidence"}
    stamp = receipt.get("evidence_time")
    run_id = receipt.get("run_id")
    if not isinstance(run_id, str) or not run_id or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0:
        return {**step, "state": "unknown", "reason": "invalid_report"}
    duration = receipt.get("duration_ms")
    step.update(run_id=run_id, evidence_time=stamp,
                duration_ms=duration if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0 else None)
    before, after = receipt.get("inputs_before"), receipt.get("inputs_after")
    relation = compare_workspace_inputs(before, after)
    final = (job.get("context") or {}).get("verification_final_inputs")
    if final is not None:
        final_relation = compare_workspace_inputs(after, final)
        relation = "changed" if "changed" in (relation, final_relation) else "unknown" if "unknown" in (relation, final_relation) else "match"
    elif job.get("status") in {"succeeded", "failed", "canceled", "interrupted"}:
        relation = "unknown"
    step["source_match"] = relation
    state = receipt.get("state")
    if state not in {"passed", "failed", "canceled", "interrupted", "unknown"}:
        state = "unknown"
    reason = {"failed": "command_failed", "canceled": "canceled", "interrupted": "interrupted", "unknown": "invalid_report"}.get(state)
    if tests:
        counts = receipt.get("counts")
        report_state = receipt.get("report_state")
        if valid_counts(counts) and report_state == "complete":
            step["counts"] = {key: counts[key] for key in ("total", "passed", "failed", "skipped")}
            if state == "passed":
                if counts["failed"]:
                    state, reason = "failed", "command_failed"
                elif counts["total"] == 0:
                    state, reason = "no_tests", "no_tests"
                elif counts["skipped"] == counts["total"]:
                    state, reason = "skipped", "all_skipped"
        elif state == "passed":
            state, reason = "unknown", "no_fresh_report" if report_state == "missing" else "invalid_report"
    step.update(state=state, reason=reason or ("inputs_changed" if relation == "changed" else "inputs_unknown" if relation == "unknown" else None))
    return step


def verification_for_job(job: dict) -> dict:
    context = job.get("context") or {}
    runs = [run for run in context.get("feedback_runs", []) if isinstance(run, dict)]
    build_index = next((i for i in range(len(runs) - 1, -1, -1) if runs[i].get("task") == "assembleDebug"), None)
    build = runs[build_index] if build_index is not None else None
    tests = next((r for r in reversed(runs) if r.get("task") == "testDebugUnitTest"), None)
    return {"schema_version": 1, "job_id": job.get("id"), "scope": "job",
            "build": _step(job, "assembleDebug", build),
            "unit_tests": _step(job, "testDebugUnitTest", tests),
            "installation": {"state": "unknown", "evidence_time": None, "source_match": "unknown", "reason": "no_device_receipt"}}
