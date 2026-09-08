#!/usr/bin/env python3
"""Backend full-link E2E runner.

For every scenario script under tests/e2e/scenarios/ it drives the real
agent service over real HTTP + WebSocket, with the scenario stub model and
real tool execution:

    user asks -> task -> tool execution -> approval -> file change
              -> build -> conversation timeline

Usage:
    .venv/bin/python tests/e2e/run_e2e.py                 # all scenarios
    .venv/bin/python tests/e2e/run_e2e.py 01 08           # scenario subset
    .venv/bin/python tests/e2e/run_e2e.py --keep          # keep temp dirs
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.e2e.e2e_harness import (  # noqa: E402
    ApprovalPump,
    E2EClient,
    E2EStack,
    TERMINAL_STATUSES,
    load_scenarios,
)


class ScenarioFailure(AssertionError):
    pass


def payload_strings(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(payload_strings(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(payload_strings(v) for v in value)
    return ""


class ScenarioContext:
    def __init__(self, runner: "E2ERunner", scenario: dict[str, Any]) -> None:
        self.runner = runner
        self.scenario = scenario
        self.client = runner.client
        self.project_id = ""
        self.conversation_id = ""
        self.job: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.extra: dict[str, Any] = {}

    # —— event accessors ——

    def event_types(self) -> list[str]:
        return [str(e.get("event_type")) for e in self.events]

    def payloads(self, event_type: str) -> list[dict[str, Any]]:
        return [e.get("payload") or {} for e in self.events if e.get("event_type") == event_type]

    def tool_call_names(self) -> list[str]:
        return [str(p.get("name") or "") for p in self.payloads("tool_call")]

    def tool_result_payloads(self, name: str) -> list[dict[str, Any]]:
        return [p for p in self.payloads("tool_result") if p.get("name") == name]

    def final_text(self) -> str:
        texts = []
        for payload in self.payloads("assistant_message"):
            if payload.get("is_final"):
                blocks = payload.get("text_blocks") or []
                texts.append(
                    "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict))
                )
        return "\n".join(texts)

    def changes_files(self) -> set[str]:
        files: set[str] = set()
        for payload in self.payloads("changes"):
            for item in payload.get("files") or []:
                if isinstance(item, dict) and item.get("path"):
                    files.add(str(item["path"]))
                elif isinstance(item, str):
                    files.add(item)
        return files

    def build_summaries(self) -> list[dict[str, Any]]:
        return self.payloads("build_summary")

    def workspace_file(self, rel_path: str) -> str:
        target = self.client.workspace_path(self.project_id) / rel_path
        return target.read_text(encoding="utf-8") if target.is_file() else ""

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            raise ScenarioFailure(message)


class E2ERunner:
    def __init__(self, keep: bool = False) -> None:
        self.stack = E2EStack(keep=keep)
        self.client: E2EClient | None = None
        self.pump: ApprovalPump | None = None
        self.scenarios = load_scenarios()
        self.projects: dict[str, str] = {}
        self.conversations: dict[str, str] = {}
        self.results: list[dict[str, Any]] = []

    # —— lifecycle ——

    def setup(self) -> None:
        self.stack.start()
        self.client = E2EClient(self.stack)
        self.client.guest_login()
        self.pump = ApprovalPump(self.client)
        self.pump.start()

    def teardown(self) -> None:
        if self.pump:
            self.pump.stop()
        if self.client:
            self.client.close()
        self.stack.stop()

    # —— scenario entry ——

    def run(self, scenario_ids: list[str] | None = None) -> int:
        wanted = scenario_ids or sorted(self.scenarios)
        unknown = [sid for sid in wanted if sid not in self.scenarios]
        if unknown:
            print(f"unknown scenarios: {unknown}", file=sys.stderr)
            return 2
        try:
            self.setup()
            for scenario_id in wanted:
                self.run_one(self.scenarios[scenario_id])
        finally:
            self.teardown()
        self.report()
        failures = [r for r in self.results if not r["ok"]]
        return 1 if failures else 0

    def run_one(self, scenario: dict[str, Any]) -> None:
        scenario_id = str(scenario["id"])
        started = time.monotonic()
        record = {"id": scenario_id, "ok": False, "duration_ms": 0, "error": ""}
        print(f"—— {scenario_id}: {scenario.get('title', '')}", flush=True)
        try:
            context = ScenarioContext(self, scenario)
            self.prepare(context)
            driver_name = str(scenario.get("driver") or "default")
            getattr(self, f"driver_{driver_name}")(context)
            self.assert_expectations(context)
            record["ok"] = True
            print(f"   PASS ({record.get('duration_ms', 0)}ms)", flush=True)
        except ScenarioFailure as exc:
            record["error"] = str(exc)
            print(f"   FAIL: {exc}", flush=True)
        except Exception:  # noqa: BLE001 - report and continue with other scenarios
            record["error"] = traceback.format_exc(limit=8)
            print(f"   ERROR:\n{record['error']}", flush=True)
        record["duration_ms"] = round((time.monotonic() - started) * 1000)
        self.results.append(record)

    # —— preparation ——

    def prepare(self, context: ScenarioContext) -> None:
        scenario = context.scenario
        reuse = scenario.get("reuse_project")
        if reuse:
            context.check(reuse in self.projects, f"reuse_project {reuse} has not run yet")
            context.project_id = self.projects[reuse]
            context.conversation_id = self.conversations.get(reuse, "")
        else:
            project = context.client.create_project(f"e2e-{scenario['id']}")
            context.project_id = str(project["id"])
            context.conversation_id = ""
        self.projects[scenario["id"]] = context.project_id
        context.client.install_setup_files(context.project_id, scenario.get("setup_files"))

    def send_prompt(self, context: ScenarioContext) -> dict[str, Any]:
        scenario = context.scenario
        marker = f"[[{scenario['id']}]] "
        job = context.client.ask(
            context.project_id,
            marker + str(scenario.get("prompt") or ""),
            conversation_id=context.conversation_id or None,
        )
        context.job = job
        context.conversation_id = str(job.get("conversation_id") or context.conversation_id)
        self.conversations[scenario["id"]] = context.conversation_id
        if scenario.get("auto_approve", True):
            self.pump.watch(str(job["id"]))
        return job

    def refresh_events(self, context: ScenarioContext) -> list[dict[str, Any]]:
        events = context.client.conversation_events(context.conversation_id)
        marker = f"[[{context.scenario['id']}]]"
        turn_id = None
        for event in events:
            if event.get("event_type") != "user_message":
                continue
            payload = event.get("payload") or {}
            text = payload.get("content")
            if isinstance(text, list):
                text = "".join(
                    str(block.get("text") or "")
                    for block in text
                    if isinstance(block, dict)
                )
            if marker in str(text or ""):
                turn_id = str(event.get("turn_id") or "")
                break
        context.events = (
            [e for e in events if str(e.get("turn_id") or "") == turn_id]
            if turn_id
            else events
        )
        return context.events

    def wait_terminal_events(self, context: ScenarioContext, timeout: float = 10.0) -> list[dict[str, Any]]:
        """Conversation events may persist a beat after the job reaches its terminal status."""
        terminal = {"turn_completed", "turn_failed", "turn_canceled", "turn_interrupted"}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.refresh_events(context)
            if terminal.intersection(context.event_types()):
                return context.events
            time.sleep(0.2)
        return context.events

    # —— drivers ——

    def driver_default(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        context.job = context.client.wait_job(str(job["id"]))
        self.wait_terminal_events(context)

    def driver_approval(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        job_id = str(job["id"])
        approval = context.client.wait_approval(job_id, timeout=60)
        context.client.wait_event(
            context.conversation_id,
            lambda event: event.get("event_type") == "approval_required",
            timeout=15,
        )
        decided = context.client.decide_approval(job_id, approval["id"], approved=True)
        context.check(bool(decided), "approval decision was not accepted")
        decision = str((decided or {}).get("decision") or "approved")
        context.check(
            decision == "approved",
            f"approval decision {decision!r} != 'approved'",
        )
        context.job = context.client.wait_job(job_id)
        self.wait_terminal_events(context)

    def driver_cancel_after_tool_call(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        job_id = str(job["id"])
        context.client.wait_event(
            context.conversation_id,
            lambda event: event.get("event_type") == "tool_call",
            timeout=30,
        )
        time.sleep(1.0)
        context.client.cancel_job(job_id)
        context.job = context.client.wait_job(job_id, timeout=45, until={"canceled"})
        self.wait_terminal_events(context)

    def driver_pause_resume(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        job_id = str(job["id"])
        context.client.wait_event(
            context.conversation_id,
            lambda event: event.get("event_type") == "tool_result",
            timeout=30,
        )
        context.client.pause_job(job_id)
        paused_seen = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = context.client.get_job(job_id).get("status")
            if status == "paused":
                paused_seen = True
                break
            if status in TERMINAL_STATUSES:
                break
            time.sleep(0.1)
        context.check(paused_seen, f"job never reached paused (status={context.client.get_job(job_id).get('status')})")
        context.client.resume_job(job_id)
        context.job = context.client.wait_job(job_id, timeout=90)
        self.wait_terminal_events(context)

    def driver_ws_disconnect(self, context: ScenarioContext) -> None:
        from tests.e2e.e2e_harness import WsCollector

        job = self.send_prompt(context)
        job_id = str(job["id"])
        first = WsCollector(context.client, job_id).start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(first.events) < 2:
            time.sleep(0.1)
        first.stop()  # simulate connection drop mid-job
        last_id = max((int(e.get("id") or 0) for e in first.events), default=0)
        context.job = context.client.wait_job(job_id)
        second = WsCollector(context.client, job_id, after_event_id=last_id).start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and second.done is None:
            time.sleep(0.1)
        second.stop()
        context.check(not second.errors, f"reconnect ws errors: {second.errors}")
        context.check(second.done is not None, "reconnect ws never received done")
        ids = [int(e.get("id") or 0) for e in first.events + second.events]
        context.check(len(ids) == len(set(ids)), "ws replay delivered duplicate event ids")
        context.check(
            ids == sorted(ids) and (not ids or ids[-1] - ids[0] + 1 == len(ids)),
            f"ws replay has gaps: {ids}",
        )
        self.refresh_events(context)
        context.extra["ws_ids"] = ids

    def driver_app_restart(self, context: ScenarioContext) -> None:
        from tests.e2e.e2e_harness import WsCollector

        job = self.send_prompt(context)
        job_id = str(job["id"])
        watcher = WsCollector(context.client, job_id).start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(watcher.events) < 2:
            time.sleep(0.1)
        watcher.stop()  # app "crashes": drop all in-memory state

        context.job = context.client.wait_job(job_id)
        # App restart: a brand-new client session reusing the persisted token
        # (same user) must be able to rebuild everything from the server.
        restart_client = E2EClient(self.stack)
        restart_client.token = context.client.token
        restart_client.user_id = context.client.user_id
        restart_client.http.headers["Authorization"] = f"Bearer {restart_client.token}"
        context.client = restart_client
        context.client.get_job(job_id)
        self.wait_terminal_events(context)
        context.extra["restarted"] = True

    def driver_duplicate_events(self, context: ScenarioContext) -> None:
        from tests.e2e.e2e_harness import WsCollector

        job = self.send_prompt(context)
        job_id = str(job["id"])
        context.job = context.client.wait_job(job_id)
        first_fetch = context.client.conversation_events(context.conversation_id)
        second_fetch = context.client.conversation_events(context.conversation_id)
        context.check(
            [e.get("seq") for e in first_fetch] == [e.get("seq") for e in second_fetch],
            "repeated conversation event fetch diverged",
        )
        seqs = [e.get("seq") for e in first_fetch]
        context.check(
            len(seqs) == len(set(seqs)) and seqs == sorted(seqs),
            f"conversation seq not strictly increasing/unique: {seqs}",
        )
        replay = WsCollector(context.client, job_id, after_event_id=0).start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and replay.done is None:
            time.sleep(0.1)
        replay.stop()
        context.check(replay.done is not None, "full ws replay never finished")
        after_replay = context.client.conversation_events(context.conversation_id)
        context.check(
            [e.get("seq") for e in after_replay] == seqs,
            "conversation store changed after ws full replay (duplicate rows?)",
        )
        self.refresh_events(context)

    # —— expectations ——

    def assert_expectations(self, context: ScenarioContext) -> None:
        expect = context.scenario.get("expect") or {}
        job = context.job
        types = context.event_types()

        if "job_status" in expect:
            context.check(
                job.get("status") == expect["job_status"],
                f"job status {job.get('status')} != {expect['job_status']}",
            )
        if "tool_calls" in expect:
            context.check(
                context.tool_call_names() == expect["tool_calls"],
                f"tool calls {context.tool_call_names()} != {expect['tool_calls']}",
            )
        if "files_changed" in expect:
            context.check(
                context.changes_files() == set(expect["files_changed"]),
                f"changed files {sorted(context.changes_files())} != {sorted(expect['files_changed'])}",
            )
        for rel_path, needle in (expect.get("file_contains") or {}).items():
            content = context.workspace_file(rel_path)
            context.check(needle in content, f"{rel_path} does not contain {needle!r}: {content[:200]!r}")
        if "final_text_contains" in expect:
            combined = context.final_text() + "\n" + str(job.get("result") or "")
            context.check(
                expect["final_text_contains"] in combined,
                f"final text missing {expect['final_text_contains']!r}: {combined[:300]!r}",
            )
        for event_type in expect.get("event_types_include") or []:
            context.check(event_type in types, f"missing event type {event_type} in {types}")
        for event_type in expect.get("event_types_exclude") or []:
            context.check(event_type not in types, f"unexpected event type {event_type} in {types}")
        for name, needle in (expect.get("tool_result_contains") or {}).items():
            results = context.tool_result_payloads(name)
            context.check(bool(results), f"no tool_result for {name}")
            blob = "\n".join(payload_strings(p) for p in results)
            context.check(needle in blob, f"{name} result missing {needle!r}: {blob[:300]!r}")
        for name, expected_ok in (expect.get("tool_result_ok") or {}).items():
            results = context.tool_result_payloads(name)
            context.check(bool(results), f"no tool_result for {name}")
            ok_values = [bool(p.get("ok")) for p in results]
            context.check(
                expected_ok in ok_values,
                f"{name} result ok={ok_values} never equals {expected_ok}",
            )
        if "has_apk" in expect:
            context.check(
                bool(job.get("has_apk")) == bool(expect["has_apk"]),
                f"has_apk={job.get('has_apk')} != {expect['has_apk']}",
            )
        if "has_build_log" in expect:
            context.check(
                bool(job.get("has_build_log")) == bool(expect["has_build_log"]),
                f"has_build_log={job.get('has_build_log')} != {expect['has_build_log']}",
            )
        if "build_summary_success" in expect:
            summaries = context.build_summaries()
            context.check(bool(summaries), "no build_summary event")
            successes = [bool(p.get("success")) for p in summaries]
            context.check(
                expect["build_summary_success"] in successes,
                f"build_summary success={successes} never equals {expect['build_summary_success']}",
            )
        if "build_log_contains" in expect:
            log = context.client.job_log_full(str(job["id"]))
            context.check(
                expect["build_log_contains"] in log,
                f"build log missing {expect['build_log_contains']!r} (len={len(log)})",
            )
        if "min_tool_results" in expect:
            count = len(context.payloads("tool_result"))
            context.check(count >= expect["min_tool_results"], f"only {count} tool_result events")
        if "log_page_size_chars" in expect:
            page = context.client.build_log_page(str(job["id"]), 0, expect["log_page_size_chars"])
            context.check(
                len(str(page.get("content") or "")) == expect["log_page_size_chars"],
                f"first log page size {len(str(page.get('content') or ''))} != {expect['log_page_size_chars']}",
            )
            context.check(bool(page.get("has_more")), "large log should have more pages")
        if expect.get("approval_required"):
            context.check(bool(context.payloads("approval_required")), "no approval_required event")
        if "approval_decision" in expect:
            resolved = context.payloads("approval_resolved")
            context.check(bool(resolved), "no approval_resolved event")
            decisions = [str(p.get("decision")) for p in resolved]
            context.check(
                expect["approval_decision"] in decisions,
                f"approval decisions {decisions} never equal {expect['approval_decision']}",
            )

    # —— reporting ——

    def report(self) -> None:
        print("\n==================== E2E SUMMARY ====================", flush=True)
        for record in self.results:
            status = "PASS" if record["ok"] else "FAIL"
            print(f"  {status}  {record['id']:<28} {record['duration_ms']:>7}ms  {record['error'][:120]}")
        failed = sum(1 for r in self.results if not r["ok"])
        print(f"  total {len(self.results)}, failed {failed}", flush=True)


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    keep = "--keep" in sys.argv[1:]
    # Allow both bare ids ("01") and full ids ("01_simple_answer").
    scenarios = load_scenarios()
    ids: list[str] = []
    for arg in args:
        matches = [sid for sid in scenarios if sid == arg or sid.startswith(f"{arg}_")]
        if len(matches) == 1:
            ids.append(matches[0])
        elif matches:
            ids.extend(matches)
        else:
            print(f"unknown scenario: {arg}", file=sys.stderr)
            return 2
    runner = E2ERunner(keep=keep)
    return runner.run(ids or None)


if __name__ == "__main__":
    raise SystemExit(main())
