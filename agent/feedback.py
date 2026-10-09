"""Build/test feedback, persisted per project and per task (never inferred from chat)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from agent.diagnostics import get_diagnostic_store
from agent.paths import user_builds_dir, workspace_path, latest_apk_path
from agent.redaction import redact_sensitive_text, redact_sensitive_value
from agent.safe_paths import open_workspace_file, resolve_workspace_path
from agent.verification import verification_for_job


class FeedbackStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS project_feedback_settings (user_id TEXT, project_id TEXT, settings TEXT NOT NULL, PRIMARY KEY(user_id, project_id))")

    def settings(self, user_id: str, project_id: str, default_build: bool = False) -> dict:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute("SELECT settings FROM project_feedback_settings WHERE user_id=? AND project_id=?", (user_id, project_id)).fetchone()
        return json.loads(row[0]) if row else {"build_after_changes": default_build, "run_tests": False, "fix_failures": False}

    def save_settings(self, user_id: str, project_id: str, settings: dict) -> dict:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("INSERT OR REPLACE INTO project_feedback_settings VALUES (?,?,?)", (user_id, project_id, json.dumps(settings)))
        return settings


def problem(source: str, severity: str, message: str, path: str | None = None, line: int | None = None) -> dict:
    message = redact_sensitive_text(message)[:4000]
    return {"id": hashlib.sha256(f"{source}:{path}:{line}:{message}".encode()).hexdigest()[:20], "source": source, "severity": severity, "message": message, "path": path, "line": line}


def _relative_path(raw: str, workspace: Path) -> str | None:
    raw = raw.strip().removeprefix("file://")
    try:
        path = Path(raw)
        rel = path.relative_to(workspace.resolve()).as_posix() if path.is_absolute() else raw
        resolve_workspace_path(workspace, rel)
        return rel
    except (ValueError, PermissionError, OSError):
        return None


def parse_log(log: str, workspace: Path, task: str = "assembleDebug", ok: bool | None = None, duration_ms: int | None = None) -> dict:
    issues: list[dict] = []
    for raw in log.splitlines():
        text = raw.strip()
        location = re.match(r"(?:[ew]:\s*)?(.+?\.(?:kt|kts|java|xml)):(?:\s*\((\d+),\s*\d+\)|(\d+)(?::\d+)?):?\s*(.*)", text)
        severity = "warning" if re.search(r"^w:|\bwarning\b", text, re.I) else "error"
        if location:
            issues.append(problem("Compiler", severity, location[4], _relative_path(location[1], workspace), int(location[2] or location[3])))
        elif re.search(r"^e:|^w:|\berror:|\bwarning:", text, re.I):
            issues.append(problem("Compiler", severity, text))
        elif " > " in text and text.endswith("FAILED"):
            issues.append(problem("Tests", "error", text))
        elif text.startswith("Execution failed for task") or text.startswith("FAILURE:"):
            issues.append(problem("Gradle", "error", text))
    status = "success" if ok is True else "failed" if ok is False else "unknown"
    if ok is None:
        matches = list(re.finditer(r"BUILD (SUCCESSFUL|FAILED)", log))
        if matches:
            status = "success" if matches[-1][1] == "SUCCESSFUL" else "failed"
    duration = list(re.finditer(r"BUILD (?:SUCCESSFUL|FAILED) in (.+)", log))
    if duration and duration_ms is None:
        seconds = sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+)([hms])", duration[-1][1]))
        duration_ms = seconds * 1000
    tasks = list(re.finditer(r"(\d+) actionable tasks?: (.+)", log))
    counts = {"total": 0, "executed": 0, "cached": 0, "up_to_date": 0}
    if tasks:
        counts["total"] = int(tasks[-1][1])
        for value, kind in re.findall(r"(\d+) (executed|from-cache|up-to-date)", tasks[-1][2]):
            counts[{"executed": "executed", "from-cache": "cached", "up-to-date": "up_to_date"}[kind]] = int(value)
    if status == "failed" and not any(p["severity"] == "error" for p in issues):
        issues.append(problem("Gradle", "error", log[-2000:] or f"{task} failed without a log"))
    issues = list({p["id"]: p for p in issues}.values())
    return {"task": task, "status": status, "duration_ms": duration_ms, "tasks": counts, "warnings": sum(p["severity"] == "warning" for p in issues), "problems": issues, "tests": None}


def snapshot_test_reports(workspace: Path, task: str = "testDebugUnitTest") -> dict:
    """Inventory exact-variant XML before/after one invocation, without deleting it."""
    files = {}
    try:
        for path in workspace.glob(f"**/build/test-results/{task}/TEST-*.xml"):
            if len(files) >= 2000:
                return {"status": "unknown", "files": {}}
            relative = path.relative_to(workspace).as_posix()
            with open_workspace_file(workspace, relative) as handle:
                stat = os.fstat(handle.fileno())
                if stat.st_size > 5_000_000:
                    return {"status": "unknown", "files": {}}
                body = handle.read(5_000_001)
                end_stat = os.fstat(handle.fileno())
            identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            if len(body) > 5_000_000 or identity(end_stat) != identity(stat) or identity(path.stat()) != identity(stat):
                return {"status": "unknown", "files": {}}
            files[relative] = (stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, hashlib.sha256(body).hexdigest())
    except (OSError, PermissionError, ValueError):
        return {"status": "unknown", "files": {}}
    return {"status": "complete", "files": files}


def fresh_test_reports(workspace: Path, baseline: dict, task: str = "testDebugUnitTest") -> tuple[dict, list[dict]]:
    after = snapshot_test_reports(workspace, task)
    if baseline.get("status") != "complete" or after["status"] != "complete":
        return {"report_state": "invalid", "counts": None}, []
    fresh = [workspace / name for name, identity in after["files"].items() if baseline["files"].get(name) != identity]
    if not fresh:
        return {"report_state": "missing", "counts": None}, []
    totals, issues = read_test_reports(workspace, 0, paths=fresh, strict=True)
    # Reject a concurrent report replacement while parsing, including newly
    # appearing reports that would make a partial test suite appear complete.
    if totals.pop("invalid", False) or snapshot_test_reports(workspace, task) != after:
        return {"report_state": "invalid", "counts": None}, issues
    totals.pop("reported", None)
    return {"report_state": "complete", "counts": totals}, issues


def _valid_junit(root: ET.Element) -> bool:
    """Validate declared totals instead of mistaking partial XML for a pass."""
    suites = [root] if root.tag == "testsuite" else list(root) if root.tag == "testsuites" else []
    if any(suite.tag != "testsuite" for suite in suites):
        return False

    def declared_matches(node: ET.Element, cases: list[ET.Element]) -> bool:
        expected = {"tests": len(cases), "failures": sum(c.find("failure") is not None for c in cases),
                    "errors": sum(c.find("error") is not None for c in cases),
                    "skipped": sum(c.find("skipped") is not None for c in cases)}
        for name, count in expected.items():
            raw = node.get(name)
            if raw is not None and (not re.fullmatch(r"[0-9]+", raw) or int(raw) != count):
                return False
        return True

    for suite in suites:
        cases = list(suite.findall("testcase"))
        if len(list(suite.iter("testcase"))) != len(cases) or suite.find("testsuite") is not None:
            return False
        if any(sum(len(case.findall(kind)) for kind in ("failure", "error", "skipped")) > 1 for case in cases):
            return False
        if not declared_matches(suite, cases):
            return False
    return root.tag == "testsuite" or declared_matches(root, list(root.iter("testcase")))


def read_test_reports(workspace: Path, since: float, *, paths: list[Path] | None = None, strict: bool = False) -> tuple[dict, list[dict]]:
    totals = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "reported": False}
    issues = []
    for path in paths if paths is not None else workspace.glob("**/build/test-results/testDebugUnitTest/TEST-*.xml"):
        try:
            safe = resolve_workspace_path(workspace, path.relative_to(workspace).as_posix())
            if safe.stat().st_mtime < since or safe.stat().st_size > 5_000_000:
                if strict:
                    totals["invalid"] = True
                continue
            with open_workspace_file(workspace, path.relative_to(workspace).as_posix()) as handle:
                body = handle.read(5_000_001).decode("utf-8")
            if "<!DOCTYPE" in body or "<!ENTITY" in body:
                if strict:
                    totals["invalid"] = True
                continue
            root = ET.fromstring(body)
            if root.tag not in {"testsuite", "testsuites"} or (strict and not _valid_junit(root)):
                if strict:
                    totals["invalid"] = True
                continue
            totals["reported"] = True
            for case in root.iter("testcase"):
                totals["total"] += 1
                failure = case.find("failure")
                if failure is None:
                    failure = case.find("error")
                if failure is not None:
                    totals["failed"] += 1
                    class_name = case.get("classname", "").split("$")[0].split(".")[-1]
                    source_path = None
                    source_line = None
                    if class_name and re.fullmatch(r"[\w]+", class_name):
                        for source in workspace.glob(f"**/src/test/**/{class_name}.*"):
                            if source.suffix not in {".kt", ".java"}:
                                continue
                            rel = source.relative_to(workspace).as_posix()
                            try:
                                resolve_workspace_path(workspace, rel)
                                source_path = rel
                            except PermissionError:
                                continue
                            frame = re.search(re.escape(source.name) + r":(\d+)", failure.text or "")
                            source_line = int(frame[1]) if frame else 1
                            break
                    issues.append(problem("Tests", "error", f"{case.get('classname', '')}.{case.get('name', '')}: {failure.get('message', '')}\n{failure.text or ''}", source_path, source_line))
                elif case.find("skipped") is not None:
                    totals["skipped"] += 1
                else:
                    totals["passed"] += 1
        except (OSError, PermissionError, ET.ParseError, ValueError):
            if strict:
                totals["invalid"] = True
            continue
    return totals, issues


def _save_feedback_report(store: Any, user_id: str, task_id: str, report: dict, *, enrich: bool = False) -> dict:
    """One durable run per tool call; enrichment cannot create another run."""
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT context_json FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
        if row is None:
            raise ValueError("verification task not found")
        context = json.loads(row["context_json"] or "{}")
        runs = list(context.get("feedback_runs") or [])
        call_id = report.get("tool_call_id")
        index = next((i for i, run in enumerate(runs) if call_id and (run.get("tool_call_id") or (run.get("verification_receipt") or {}).get("tool_call_id")) == call_id), None)
        if index is not None:
            if not enrich:
                return runs[index]
            runs[index] = {**runs[index], **report, "created_at": runs[index].get("created_at", report["created_at"])}
            saved = runs[index]
        else:
            runs.append(report)
            runs.sort(key=lambda run: run.get("created_at") or 0)
            saved = report
        context["feedback_runs"] = redact_sensitive_value(runs[-30:])
        conn.execute("UPDATE tasks SET context_json=? WHERE id=? AND user_id=?",
                     (json.dumps(context, ensure_ascii=False), task_id, user_id))
        return saved


def persist_gradle_receipt(store: Any, user_id: str, task_id: str, payload: dict, *, created_at: float | None = None) -> dict:
    """Persist result evidence before its canonical event, without filesystem IO."""
    task = str((payload.get("input") or {}).get("task") or "assembleDebug")
    stamp = created_at or time.time()
    summary = payload.get("summary") or {}
    receipt = summary.get("verification_receipt") if isinstance(summary, dict) else None
    if isinstance(receipt, dict) and receipt.get("task") == task and receipt.get("schema_version") == 1:
        receipt = {**receipt, "job_id": task_id, "tool_call_id": payload.get("tool_call_id")}
    else:
        error = payload.get("error_type") or ""
        state = "unknown" if payload.get("ok") else "canceled" if error in {"CancellationRequested", "ApprovalCanceled"} else "interrupted" if payload.get("interrupted") or error == "Timeout" else "failed"
        receipt = {"schema_version": 1, "job_id": task_id, "task": task,
                   "run_id": payload.get("tool_call_id"), "tool_call_id": payload.get("tool_call_id"),
                   "evidence_time": stamp, "duration_ms": payload.get("duration_ms"), "state": state}
    report = {"task": task, "tool_call_id": payload.get("tool_call_id"), "created_at": stamp,
              "status": "success" if payload.get("ok") else "failed", "problems": [], "tests": None,
              "verification_receipt": receipt}
    return _save_feedback_report(store, user_id, task_id, report)


def capture_gradle_result(store: Any, user_id: str, project_id: str, task_id: str, payload: dict, since: float) -> None:
    base = persist_gradle_receipt(store, user_id, task_id, payload)
    task = str((payload.get("input") or {}).get("task") or "assembleDebug")
    output = str(payload.get("model_output") or payload.get("preview") or "")
    match = re.search(r"日志:\s*(.+)", output)
    log = output
    log_path = None
    if match:
        candidate = Path(match[1].strip())
        root = (user_builds_dir(user_id) / project_id).resolve()
        try:
            candidate.resolve().relative_to(root)
            if candidate.is_file() and not candidate.is_symlink():
                log = candidate.read_text(encoding="utf-8", errors="replace")[-2_000_000:]
                log_path = str(candidate)
        except (OSError, ValueError):
            pass
    report = parse_log(log, workspace_path(user_id, project_id), task, bool(payload.get("ok")), payload.get("duration_ms"))
    receipt = base["verification_receipt"]
    report.update({"created_at": base["created_at"], "tool_call_id": base["tool_call_id"], "log_path": log_path, "verification_receipt": receipt})

    if task == "lintDebug":
        for path in workspace_path(user_id, project_id).glob("**/build/reports/lint-results*.xml"):
            try:
                workspace = workspace_path(user_id, project_id)
                safe = resolve_workspace_path(workspace, path.relative_to(workspace).as_posix())
                if safe.stat().st_mtime < since or safe.stat().st_size > 5_000_000:
                    continue
                body = safe.read_text(encoding="utf-8")
                if "<!DOCTYPE" in body or "<!ENTITY" in body:
                    continue
                for issue in ET.fromstring(body).iter("issue"):
                    loc = issue.find("location")
                    report["problems"].append(problem("Lint", "error" if issue.get("severity", "").lower() in {"error", "fatal"} else "warning", issue.get("message", ""),
                        _relative_path(loc.get("file", ""), workspace) if loc is not None else None,
                        int(loc.get("line", "1")) if loc is not None else None))
            except (OSError, PermissionError, ValueError, ET.ParseError):
                continue
    if task == "testDebugUnitTest":
        counts = receipt.get("counts") if isinstance(receipt, dict) else None
        report["tests"] = {**counts, "reported": True} if counts and receipt.get("report_state") == "complete" else {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "reported": False}
        report["problems"].extend(receipt.get("test_problems") or [] if isinstance(receipt, dict) else [])
        if report["tests"]["failed"]:
            report["status"] = "failed"
    _save_feedback_report(store, user_id, task_id, report, enrich=True)
    if log_path:
        store.update_task(task_id, build_log_path=log_path)
    # Keep valid artifacts installable even when subsequent tests fail.
    if task == "assembleDebug":
        store.update_task(task_id, apk_path=None)
        apk = latest_apk_path(user_id, project_id)
        if payload.get("ok") and apk.is_file() and apk.stat().st_mtime >= since:
            target = user_builds_dir(user_id) / project_id / f"{task_id}.apk"
            temp = target.with_suffix(".apk.part")
            shutil.copy2(apk, temp)
            temp.replace(target)
            store.update_task(task_id, apk_path=str(target))


def feedback_summary(store: Any, user_id: str, project_id: str, job_id: str | None = None) -> dict:
    jobs = store.list_tasks(user_id, project_id=project_id)
    if job_id:
        jobs = [job for job in jobs if job["id"] == job_id]
    jobs.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
    job = jobs[0] if job_id and jobs else next((j for j in jobs if (j.get("context") or {}).get("feedback_runs") or j.get("build_log_path")), None)
    runs = list((job.get("context") or {}).get("feedback_runs") or []) if job else []
    if job and not runs and job.get("build_log_path"):
        try:
            log_path = Path(job["build_log_path"])
            log_path.resolve().relative_to((user_builds_dir(user_id) / project_id).resolve())
            runs = [parse_log(log_path.read_text(encoding="utf-8", errors="replace")[-2_000_000:], workspace_path(user_id, project_id))]
        except (ValueError, OSError):
            pass
    # A newer successful build invalidates older compiler failures; tests belong to that build cycle.
    build_index = next((i for i in range(len(runs) - 1, -1, -1) if runs[i]["task"] == "assembleDebug"), None)
    build = runs[build_index] if build_index is not None else None
    cycle = runs[build_index:] if build_index is not None else runs
    tests = next((r for r in reversed(cycle) if r["task"] == "testDebugUnitTest"), None)
    latest_by_task = {r["task"]: r for r in cycle}
    issues = [p for run in latest_by_task.values() for p in run["problems"]]
    latest = jobs[0] if jobs else None
    if latest and latest.get("error_message") and latest.get("status") == "failed":
        issues.append(problem("Agent diagnostics", "error", latest["error_message"]))
    for diagnostic in get_diagnostic_store(store.db_path).list(user_id, project_id=project_id, task_id=job_id, after=job.get("created_at") if job and not job_id else None):
        detail = diagnostic.get("details") or {}
        raw_path = detail.get("path")
        issues.append(problem("Runtime" if diagnostic["component"] == "runtime" else "Agent diagnostics", diagnostic["severity"], diagnostic["message"], _relative_path(raw_path, workspace_path(user_id, project_id)) if raw_path else None, detail.get("line")))
    issues = list({p["id"]: p for p in issues}.values())
    artifact = None
    if job and job.get("apk_path"):
        path = Path(job["apk_path"])
        try:
            path.resolve().relative_to((user_builds_dir(user_id) / project_id).resolve())
            if path.is_file():
                artifact = {"name": "app-debug.apk", "size": path.stat().st_size, "job_id": job["id"]}
        except (OSError, ValueError):
            pass
    public_build = {k: v for k, v in build.items() if k != "log_path"} if build else None
    public_tests = {k: v for k, v in tests.items() if k != "log_path"} if tests else None
    return {"project_id": project_id, "job_id": job["id"] if job else None, "job_status": job.get("status") if job else None, "build": public_build, "tests": public_tests, "problems": issues, "artifact": artifact, "max_fix_attempts": 2, "verification": verification_for_job(job or {"id": None})}


def run_feedback_cycle(options: dict, run_gradle: Callable[[str], bool], fix: Callable[[int, str], None], check: Callable[[], None]) -> None:
    for attempt in range(3):
        check()
        failed_task = "assembleDebug"
        success = run_gradle(failed_task)
        if success and options.get("run_tests"):
            check()
            failed_task = "testDebugUnitTest"
            success = run_gradle(failed_task)
        if success:
            return
        if not options.get("fix_failures") or attempt == 2:
            raise RuntimeError(f"{failed_task} 未成功；自动修复 {attempt}/2，请查看 Problems")
        check()
        fix(attempt + 1, failed_task)
