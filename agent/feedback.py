"""Build/test feedback, persisted per project and per task (never inferred from chat)."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from agent.diagnostics import get_diagnostic_store
from agent.paths import user_builds_dir, workspace_path, latest_apk_path
from agent.redaction import redact_sensitive_text
from agent.safe_paths import resolve_workspace_path


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


def read_test_reports(workspace: Path, since: float) -> tuple[dict, list[dict]]:
    totals = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "reported": False}
    issues = []
    for path in workspace.glob("**/build/test-results/**/TEST-*.xml"):
        try:
            safe = resolve_workspace_path(workspace, path.relative_to(workspace).as_posix())
            if safe.stat().st_mtime < since or safe.stat().st_size > 5_000_000:
                continue
            body = safe.read_text(encoding="utf-8")
            if "<!DOCTYPE" in body or "<!ENTITY" in body:
                continue
            root = ET.fromstring(body)
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
            continue
    return totals, issues


def capture_gradle_result(store: Any, user_id: str, project_id: str, task_id: str, payload: dict, since: float) -> None:
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
    report.update({"created_at": time.time(), "log_path": log_path})
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
        report_since = time.time() - payload["duration_ms"] / 1000 - 1 if payload.get("duration_ms") else since
        report["tests"], test_issues = read_test_reports(workspace_path(user_id, project_id), report_since)
        report["problems"].extend(test_issues)
        if report["tests"]["failed"]:
            report["status"] = "failed"
    job = store.get_task(task_id, user_id) or {}
    context = job.get("context") or {}
    runs = list(context.get("feedback_runs") or [])
    runs.append(report)
    store.update_task(task_id, context={**context, "feedback_runs": runs[-30:]}, **({"build_log_path": log_path} if log_path else {}))
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
    job = next((j for j in jobs if (j.get("context") or {}).get("feedback_runs") or j.get("build_log_path")), None)
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
    return {"project_id": project_id, "job_id": job["id"] if job else None, "build": public_build, "tests": public_tests, "problems": issues, "artifact": artifact, "max_fix_attempts": 2}


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
