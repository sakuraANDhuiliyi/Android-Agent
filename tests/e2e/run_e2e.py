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
        self.foreign_identity: tuple[str, str] | None = None
        # Keep the production 50-project/account limit intact as the suite grows.
        self.projects_per_account = 40
        self.accounts: dict[str, tuple[E2EClient, ApprovalPump]] = {}
        self.account_project_counts: dict[str, int] = {}
        self.project_owners: dict[str, str] = {}

    # —— lifecycle ——

    def setup(self) -> None:
        self.stack.start()
        self.new_scenario_account()

    def new_scenario_account(self) -> None:
        client = E2EClient(self.stack)
        try:
            client.register_account()
            pump = ApprovalPump(client)
            pump.start()
        except Exception:
            client.close()
            raise
        self.accounts[client.user_id] = (client, pump)
        self.account_project_counts[client.user_id] = 0
        self.client, self.pump = client, pump

    def select_scenario_account(self, scenario: dict[str, Any]) -> None:
        reuse = scenario.get("reuse_project")
        if reuse:
            # Dependent scenarios retain their original project owner, including
            # when an unrelated scenario used another account in between.
            owner = self.project_owners.get(reuse)
            if owner:
                self.client, self.pump = self.accounts[owner]
            return
        for owner in reversed(self.accounts):
            if self.account_project_counts[owner] < self.projects_per_account:
                self.client, self.pump = self.accounts[owner]
                return
        self.new_scenario_account()

    def teardown(self) -> None:
        for client, pump in self.accounts.values():
            pump.stop()
            client.close()
        self.stack.stop()

    def foreign_client(self) -> E2EClient:
        """Reuse one isolated second account across authorization checks.

        A fresh HTTP client still owns each check, but the growing scenario
        suite must not exhaust the production registration-per-IP limit.
        """
        client = E2EClient(self.stack)
        try:
            if self.foreign_identity is None:
                client.register_account()
                self.foreign_identity = (client.token, client.user_id)
            else:
                client.token, client.user_id = self.foreign_identity
                client.http.headers["Authorization"] = f"Bearer {client.token}"
            assert client.user_id != self.client.user_id
            return client
        except Exception:
            client.close()
            raise

    # —— scenario entry ——

    def run(self, scenario_ids: list[str] | None = None) -> int:
        wanted = scenario_ids or sorted(self.scenarios)
        unknown = [sid for sid in wanted if sid not in self.scenarios]
        if unknown:
            print(f"unknown scenarios: {unknown}", file=sys.stderr)
            return 2
        try:
            self.setup()
            if self.stack.keep:
                print(f"E2E artifacts: {self.stack.tmp_root}", flush=True)
            for scenario_id in wanted:
                self.run_one(self.scenarios[scenario_id])
            # Keep full diagnostics on failure: the release runner's output
            # tail may contain the summary but truncate the original exception.
            if any(not result["ok"] for result in self.results):
                self.stack.keep = True
            self.report()
        finally:
            self.teardown()
        failures = [r for r in self.results if not r["ok"]]
        return 1 if failures else 0

    def run_one(self, scenario: dict[str, Any]) -> None:
        scenario_id = str(scenario["id"])
        started = time.monotonic()
        record = {"id": scenario_id, "ok": False, "duration_ms": 0, "error": ""}
        print(f"—— {scenario_id}: {scenario.get('title', '')}", flush=True)
        try:
            self.select_scenario_account(scenario)
            context = ScenarioContext(self, scenario)
            self.prepare(context)
            driver_name = str(scenario.get("driver") or "default")
            getattr(self, f"driver_{driver_name}")(context)
            self.assert_expectations(context)
            record["ok"] = True
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
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
            self.account_project_counts[context.client.user_id] += 1
            context.project_id = str(project["id"])
            context.conversation_id = ""
        self.projects[scenario["id"]] = context.project_id
        self.project_owners[scenario["id"]] = context.client.user_id
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
        if context.extra.get("event_turn_id"):
            context.events = [event for event in events if event.get("turn_id") == context.extra["event_turn_id"]]
            return context.events
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

    def driver_validation_then_edit(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        context.client.wait_event(context.conversation_id, lambda event:
                                  event.get("event_type") == "tool_result"
                                  and (event.get("payload") or {}).get("name") == "run_gradle")
        # Simulate an editor changing a root build input while the task finishes.
        root = context.client.workspace_path(context.project_id)
        (root / "gradle.properties").write_text("verification.input=changed\n", encoding="utf-8")
        context.job = context.client.wait_job(str(job["id"]))
        self.wait_terminal_events(context)

    def message_receipts(self, context: ScenarioContext) -> list[dict[str, Any]]:
        response = context.client.http.get(f"/api/jobs/{context.job['id']}/messages", params={"include_consumed": "true"})
        response.raise_for_status()
        context.check(response.json().get("job_id") == context.job["id"], "message list belongs to another job")
        # Cancel/pause/resume control messages share this API but are not user
        # steer/follow-up delivery receipts.
        return [row for row in response.json()["messages"] if row.get("type") in {"steer", "follow_up"}]

    def wait_receipts(self, context: ScenarioContext, expected_count: int, state: str, timeout: float = 30) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        latest = []
        while time.monotonic() < deadline:
            latest = self.message_receipts(context)
            for item in latest:
                if item.get("follow_up_job_id"):
                    self.pump.watch(item["follow_up_job_id"])
            if len(latest) == expected_count and all(item.get("delivery_state") == state for item in latest):
                return latest
            time.sleep(0.15)
        raise ScenarioFailure(f"expected {expected_count} {state} receipts; got {latest}")

    def driver_steer_receipts(self, context: ScenarioContext) -> None:
        from concurrent.futures import ThreadPoolExecutor

        job = self.send_prompt(context)
        job_id = str(job["id"])
        context.client.wait_event(context.conversation_id, lambda event: event.get("event_type") == "tool_call")
        body = {"message_key": "same-intent", "type": "steer", "payload": {"text": context.scenario["steer"]}}
        # A successful server write whose first response the app does not use.
        first = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
        context.check(first.status_code == 201, f"first send status={first.status_code}")
        with ThreadPoolExecutor(max_workers=2) as pool:
            retries = [pool.submit(context.client.http.post, f"/api/jobs/{job_id}/messages", json=body) for _ in range(2)]
            replies = [future.result() for future in retries]
        context.check(all(r.status_code == 200 and r.json()["message"]["id"] == first.json()["message"]["id"] for r in replies),
                      "concurrent original-key retries created messages or rejected accepted delivery")
        second = context.client.http.post(f"/api/jobs/{job_id}/messages", json={**body, "message_key": "second-intent"})
        context.check(second.status_code == 201 and second.json()["message"]["id"] != first.json()["message"]["id"],
                      "intentional same-text message was deduplicated")
        conflict = context.client.http.post(f"/api/jobs/{job_id}/messages", json={**body, "payload": {"text": "DIFFERENT INTENT"}})
        context.check(conflict.status_code == 409, "same-key different-body message was accepted")
        context.job = context.client.wait_job(job_id)
        self.wait_terminal_events(context)
        receipts = self.wait_receipts(context, 2, "consumed")
        event_ids = [p.get("message_id") for p in context.payloads("user_message") if p.get("source") == "task_message"]
        context.check(len(event_ids) == 2 and set(event_ids) == {r["context_message_id"] for r in receipts},
                      "canonical context does not contain exactly the two receipt identities")
        context.check(all(r.get("schema_version") == 1 and r.get("consumed_at") for r in receipts), "missing consumption evidence")
        retry = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
        context.check(retry.status_code == 200 and retry.json()["message"]["id"] == first.json()["message"]["id"],
                      "terminal task rejected an already accepted retry")
        rejected = context.client.http.post(f"/api/jobs/{job_id}/messages", json={**body, "message_key": "new-after-terminal"})
        context.check(rejected.status_code == 409, "terminal task accepted a new steer")
        pending = context.client.http.get(f"/api/jobs/{job_id}/messages")
        pending.raise_for_status()
        context.check(pending.json()["messages"] == [], "consumed messages remain in pending-only list")
        stranger = self.foreign_client()
        try:
            context.check(stranger.http.get(f"/api/jobs/{job_id}/messages").status_code == 404, "cross-account receipts leaked")
            context.check(stranger.http.post(f"/api/jobs/{job_id}/messages", json=body).status_code == 404, "cross-account retry leaked")
        finally:
            stranger.close()

    def driver_followup_receipts(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        job_id = str(job["id"])
        context.client.wait_event(context.conversation_id, lambda event: event.get("event_type") == "tool_call")
        bodies = [{"message_key": f"follow-{index}", "type": "follow_up", "payload": {
            "text": f"[[{context.scenario['id']}]] 执行后续任务 {index}"}} for index in range(2)]
        for body in bodies:
            response = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
            context.check(response.status_code == 201, f"follow-up send status={response.status_code}")
        context.job = context.client.wait_job(job_id)
        self.wait_terminal_events(context)
        receipts = self.wait_receipts(context, 2, "follow_up_created", timeout=45)
        children = [context.client.wait_job(row["follow_up_job_id"]) for row in receipts]
        context.check(len({child["id"] for child in children}) == 2, "follow-ups share a child task")
        context.check(all(child["status"] == "succeeded" and child["conversation_id"] == context.conversation_id
                          and child["project_id"] == context.project_id for child in children), "child failed or changed scope")
        context.check(all(child["turn_id"] == row["follow_up_turn_id"] for child, row in zip(children, receipts)), "receipt child turn mismatch")
        # Claim timestamps have second precision. Creation is precise and must
        # itself wait for the preceding child to succeed, before any execution.
        context.check(children[1]["created_at"] >= children[0]["finished_at"], "queued follow-up was created before its predecessor finished")
        for row, body in zip(receipts, bodies):
            retry = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
            context.check(retry.status_code == 200 and retry.json()["message"]["follow_up_job_id"] == row["follow_up_job_id"],
                          "retry changed the follow-up task mapping")
        listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
        listing.raise_for_status()
        context.check({row["id"] for row in listing.json()["jobs"]} == {job_id, *(child["id"] for child in children)},
                      "follow-up dispatch created extra tasks")

    def driver_blocked_followup(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        job_id = str(job["id"])
        context.client.wait_event(context.conversation_id, lambda event: event.get("event_type") == "tool_call")
        body = {"message_key": "pending-follow", "type": "follow_up", "payload": {"text": "[[01_simple_answer]] queued child"}}
        response = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
        context.check(response.status_code == 201, "could not queue follow-up before interruption")
        mode = context.scenario["parent_end"]
        if mode in {"crash", "cancel"}:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and context.workspace_file("app/src/test/followup-started.txt") != "started":
                time.sleep(0.05)
            context.check(context.workspace_file("app/src/test/followup-started.txt") == "started", "parent tool never started")
        if mode == "crash":
            self.stack.restart_agent_after_crash(job_id)
        elif mode == "cancel":
            context.client.cancel_job(job_id)
        context.job = context.client.wait_job(job_id)
        self.wait_terminal_events(context)
        rows = self.wait_receipts(context, 1, "blocked")
        context.check(rows[0].get("reason") == context.scenario["blocked_reason"], f"wrong blocking reason: {rows[0]}")
        context.check(rows[0].get("follow_up_job_id") is None, "blocked message created a child")
        self.assert_blocking_target(context, rows[0], context.job)
        if mode == "cancel":
            self.stack.restart_idle_agent()
            context.check(self.wait_receipts(context, 1, "blocked")[0]["id"] == rows[0]["id"], "restart changed blocked receipt")
        retry = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
        context.check(retry.status_code == 200 and retry.json()["message"]["id"] == rows[0]["id"], "blocked retry lost receipt")
        listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
        listing.raise_for_status()
        context.check([row["id"] for row in listing.json()["jobs"]] == [job_id], "blocked follow-up was dispatched")

    def assert_blocking_target(self, context: ScenarioContext, receipt: dict, expected: dict) -> None:
        context.check(receipt.get("blocking_job_id") == expected["id"]
                      and bool(expected.get("turn_id")) and receipt.get("blocking_turn_id") == expected["turn_id"],
                      f"receipt does not identify the actual blocking task and turn: {receipt}")
        target = context.client.get_job(receipt["blocking_job_id"])
        context.check(target["id"] == expected["id"] and target["turn_id"] == receipt["blocking_turn_id"]
                      and target["project_id"] == context.project_id and target["conversation_id"] == context.conversation_id,
                      "blocking navigation target belongs to another scope or turn")
        unchanged = ("status", "cancel_requested", "recovery_job_id", "turn_id", "prompt")
        context.check(all(target.get(key) == expected.get(key) for key in unchanged), "viewing blocking task changed its execution state")
        stranger = self.foreign_client()
        try:
            context.check(stranger.http.get(f"/api/jobs/{target['id']}").status_code == 404,
                          "blocking task target leaked across accounts")
        finally:
            stranger.close()

    def driver_blocking_child(self, context: ScenarioContext) -> None:
        source = self.send_prompt(context)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not context.workspace_file("app/src/test/blocking-source.txt"):
            time.sleep(0.05)
        context.check(context.workspace_file("app/src/test/blocking-source.txt") == "source executed\n", "source tool did not execute exactly once")
        context.client.pause_job(source["id"])
        context.job = context.client.wait_job(source["id"], until={"paused"})
        bodies = [{"message_key": key, "type": "follow_up", "payload": {"text": text}} for key, text in (
            ("withdraw-before-blocker", "[[01_simple_answer]] withdrawn predecessor must never run"),
            ("actual-blocker", f"[[{context.scenario['child_scenario']}]] create actual blocking child"),
            ("blocked-tail", "[[01_simple_answer]] tail must wait for successful predecessor"))]
        originals = []
        for body in bodies:
            response = context.client.http.post(f"/api/jobs/{source['id']}/messages", json=body)
            context.check(response.status_code == 201, "could not queue blocker scenario")
            originals.append(response.json()["message"])
        self.withdraw_receipt(context, originals[0]["id"])
        edit_body = {"edit_key": "tail-edit-before-block", "expected_revision": 0,
                     "payload": {"text": "[[01_simple_answer]] edited tail must still wait"}}
        accepted = self.edit_receipt(context, originals[2]["id"], edit_body, 201)
        context.client.resume_job(source["id"])
        context.job = context.client.wait_job(source["id"])
        self.wait_terminal_events(context)
        deadline = time.monotonic() + 20
        child_id = None
        while time.monotonic() < deadline:
            rows = self.message_receipts(context)
            if len(rows) == 3 and rows[1].get("follow_up_job_id"):
                child_id = rows[1]["follow_up_job_id"]
                break
            time.sleep(0.1)
        context.check(bool(child_id), "no blocking candidate child created")
        mode = context.scenario["child_end"]
        if mode in {"cancel", "crash"}:
            # run_command waits for approval; run_gradle in the failure case is allowed directly.
            approval = context.client.wait_approval(child_id)
            before = self.message_receipts(context)
            context.check(before[2]["delivery_state"] == "pending", "active child incorrectly blocked remaining queue")
            context.check(all(row.get("blocking_job_id") is None and row.get("blocking_turn_id") is None for row in before),
                          "pending/created/withdrawn queue exposed a false blocker")
            context.check(approval["id"] in {item["id"] for item in context.client.pending_approvals(child_id)},
                          "receipt query implicitly resolved child approval")
            self.pump.watch(child_id)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and context.workspace_file("app/src/test/followup-started.txt") != "started":
                time.sleep(0.05)
            context.check(context.workspace_file("app/src/test/followup-started.txt") == "started", "candidate child never ran its real tool")
            if mode == "cancel":
                context.client.cancel_job(child_id)
            else:
                self.stack.restart_agent_after_crash(child_id)
        child = context.client.wait_job(child_id)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            rows = self.message_receipts(context)
            if rows[2]["delivery_state"] == "blocked":
                break
            time.sleep(0.1)
        tail = rows[2]
        context.check(tail["delivery_state"] == "blocked" and tail.get("reason") == context.scenario["blocked_reason"]
                      and tail.get("can_edit") is False and tail.get("can_withdraw") is True,
                      "failed child did not preserve correct tail state and capabilities")
        self.assert_blocking_target(context, tail, child)
        context.check(tail["blocking_job_id"] != source["id"], "successful source misidentified as failed child")
        context.check(context.client.get_job(source["id"])["status"] == "succeeded", "child failure changed source task status")
        replay = context.client.http.post(f"/api/jobs/{source['id']}/messages", json=bodies[2])
        context.check(replay.status_code == 200, "blocked original send retry rejected")
        self.assert_blocking_target(context, replay.json()["message"], child)
        edit_replay = self.edit_receipt(context, tail["id"], edit_body, 200)
        context.check(edit_replay["edit"] == accepted["edit"], "blocking changed immutable edit receipt")
        self.assert_blocking_target(context, edit_replay["message"], child)
        self.stack.restart_idle_agent()
        after = self.message_receipts(context)
        self.assert_blocking_target(context, after[2], context.client.get_job(child_id))
        context.check(all(row.get("blocking_job_id") is None and row.get("blocking_turn_id") is None for row in after[:2]),
                      "withdrawn or created receipt inherited tail blocker")
        listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
        listing.raise_for_status()
        context.check({job["id"] for job in listing.json()["jobs"]} == {source["id"], child_id},
                      "viewing or retrying a blocked receipt created recovery/tail tasks")

    def withdraw_receipt(self, context: ScenarioContext, message_id: int) -> dict[str, Any]:
        response = context.client.http.post(f"/api/jobs/{context.job['id']}/messages/{message_id}/withdraw")
        context.check(response.status_code == 200, f"withdraw status={response.status_code}: {response.text}")
        data = response.json()
        context.check(data.get("schema_version") == 1 and data.get("job_id") == context.job["id"], "wrong withdrawal envelope")
        row = data["message"]
        context.check(row["id"] == message_id and row["delivery_state"] == "withdrawn"
                      and row.get("can_withdraw") is False and (row.get("withdrawn_at") or 0) >= row["created_at"],
                      "withdrawal lacks authoritative identity/time")
        context.check(all(row.get(key) is None for key in ("consumed_at", "context_message_id", "follow_up_job_id", "follow_up_turn_id")),
                      "withdrawn message has execution evidence")
        context.check(row.get("blocking_job_id") is None and row.get("blocking_turn_id") is None, "withdrawn message kept a blocking navigation target")
        return row

    def driver_withdraw_queue(self, context: ScenarioContext) -> None:
        from concurrent.futures import ThreadPoolExecutor

        source = self.send_prompt(context)
        source_id = source["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not context.workspace_file("app/src/test/message-order.txt"):
            time.sleep(0.05)
        context.check(bool(context.workspace_file("app/src/test/message-order.txt")), "source command did not start")
        context.client.pause_job(source_id)
        context.job = context.client.wait_job(source_id, until={"paused"})
        bodies = [{"message_key": f"withdraw-{label}", "type": "follow_up",
                   "payload": {"text": f"[[{context.scenario['id']}]] follow-up {label}"}} for label in ("A", "B", "C")]
        rows = []
        for body in bodies:
            response = context.client.http.post(f"/api/jobs/{source_id}/messages", json=body)
            context.check(response.status_code == 201, "could not queue instruction on paused source")
            row = response.json()["message"]
            context.check(row.get("can_withdraw") is True, "pending follow-up is not withdrawable")
            rows.append(row)
        index = context.scenario["withdraw_index"]
        target = rows[index]
        stranger = self.foreign_client()
        try:
            denied = stranger.http.post(f"/api/jobs/{source_id}/messages/{target['id']}/withdraw")
            context.check(denied.status_code == 404, "cross-account withdrawal disclosed or changed message")
        finally:
            stranger.close()
        wrong = context.client.http.post(f"/api/jobs/missing-source/messages/{target['id']}/withdraw")
        context.check(wrong.status_code == 404, "withdrawal ignored source task binding")
        # Drop the first acknowledgement at the caller, then replay concurrently.
        first = self.withdraw_receipt(context, target["id"])
        with ThreadPoolExecutor(max_workers=3) as pool:
            retries = list(pool.map(lambda _: self.withdraw_receipt(context, target["id"]), range(3)))
        context.check(all(row["withdrawn_at"] == first["withdrawn_at"] for row in retries), "duplicate withdrawal changed its receipt")
        self.stack.restart_idle_agent()
        restarted = self.withdraw_receipt(context, target["id"])
        context.check(restarted["withdrawn_at"] == first["withdrawn_at"], "restart lost withdrawal evidence")
        pending = context.client.http.get(f"/api/jobs/{source_id}/messages")
        pending.raise_for_status()
        context.check(target["id"] not in {row["id"] for row in pending.json()["messages"]}, "withdrawn message remains dispatchable")
        context.client.resume_job(source_id)
        context.job = context.client.wait_job(source_id)
        self.wait_terminal_events(context)
        deadline = time.monotonic() + 45
        latest = []
        while time.monotonic() < deadline:
            latest = self.message_receipts(context)
            for row in latest:
                if row.get("follow_up_job_id"):
                    self.pump.watch(row["follow_up_job_id"])
            if len(latest) == 3 and sum(row["delivery_state"] == "follow_up_created" for row in latest) == 2:
                break
            time.sleep(0.1)
        context.check(len(latest) == 3 and latest[index]["delivery_state"] == "withdrawn", "withdrawal disappeared from complete history")
        survivors = [row for row in latest if row["delivery_state"] == "follow_up_created"]
        context.check(len(survivors) == 2, f"wrong surviving queue: {latest}")
        children = [context.client.wait_job(row["follow_up_job_id"]) for row in survivors]
        expected_bodies = [body for i, body in enumerate(bodies) if i != index]
        context.check(all(child["status"] == "succeeded" and child["prompt"] == body["payload"]["text"]
                          for child, body in zip(children, expected_bodies)), "wrong follow-up executed")
        context.check(children[1]["created_at"] >= children[0]["finished_at"], "withdrawal broke surviving FIFO order")
        retried = context.client.http.post(f"/api/jobs/{source_id}/messages", json=bodies[index])
        context.check(retried.status_code == 200 and retried.json()["message"]["delivery_state"] == "withdrawn"
                      and retried.json()["message"]["withdrawn_at"] == first["withdrawn_at"], "original send retry revived withdrawn message")
        listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
        listing.raise_for_status()
        context.check({row["id"] for row in listing.json()["jobs"]} == {source_id, *(child["id"] for child in children)},
                      "withdrawn queue item created an extra task")

    def driver_withdraw_created(self, context: ScenarioContext) -> None:
        source = self.send_prompt(context)
        source_id = source["id"]
        context.client.wait_event(context.conversation_id, lambda event: event.get("event_type") == "tool_call")
        body = {"message_key": "already-dispatched", "type": "follow_up",
                "payload": {"text": f"[[{context.scenario['id']}]] child needs separate approval"}}
        sent = context.client.http.post(f"/api/jobs/{source_id}/messages", json=body)
        context.check(sent.status_code == 201, "could not queue child")
        context.job = context.client.wait_job(source_id)
        self.wait_terminal_events(context)
        deadline = time.monotonic() + 20
        child_id = None
        while time.monotonic() < deadline:
            rows = self.message_receipts(context)
            if rows and rows[0].get("follow_up_job_id"):
                child_id = rows[0]["follow_up_job_id"]
                break
            time.sleep(0.1)
        context.check(bool(child_id), "follow-up dispatcher did not create child")
        approval = context.client.wait_approval(child_id)
        before = context.client.get_job(child_id)
        message_id = sent.json()["message"]["id"]
        refused = context.client.http.post(f"/api/jobs/{source_id}/messages/{message_id}/withdraw")
        context.check(refused.status_code == 409, "withdrawal pretended to remove an already-created child")
        after = context.client.get_job(child_id)
        context.check((after["status"], after.get("cancel_requested")) == (before["status"], before.get("cancel_requested")),
                      "withdrawal modified child execution state")
        context.check(approval["id"] in {row["id"] for row in context.client.pending_approvals(child_id)}, "withdrawal resolved child approval")
        row = self.message_receipts(context)[0]
        context.check(row["delivery_state"] == "follow_up_created" and row["follow_up_job_id"] == child_id
                      and row.get("can_withdraw") is False and row.get("withdrawn_at") is None, "created-child receipt regressed")
        self.pump.watch(child_id)
        child = context.client.wait_job(child_id)
        context.check(child["status"] == "succeeded", "explicitly approved child no longer runs")

    def driver_withdraw_blocked(self, context: ScenarioContext) -> None:
        self.driver_blocked_followup(context)
        row = self.message_receipts(context)[0]
        context.check(row.get("can_withdraw") is True, "unexecuted blocked message cannot be withdrawn")
        receipt = self.withdraw_receipt(context, row["id"])
        self.stack.restart_idle_agent()
        repeated = self.withdraw_receipt(context, row["id"])
        context.check(repeated["withdrawn_at"] == receipt["withdrawn_at"], "blocked withdrawal was not durable")
        listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
        listing.raise_for_status()
        context.check([job["id"] for job in listing.json()["jobs"]] == [context.job["id"]], "withdrawal executed blocked work")

    def paused_edit_queue(self, context: ScenarioContext, count: int = 3) -> tuple[list[dict], list[dict]]:
        source = self.send_prompt(context)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not context.workspace_file("app/src/test/edited-prompts.txt"):
            time.sleep(0.05)
        context.check(bool(context.workspace_file("app/src/test/edited-prompts.txt")), "source prompt never reached real tool")
        context.client.pause_job(source["id"])
        context.job = context.client.wait_job(source["id"], until={"paused"})
        bodies, rows = [], []
        for label in ("A", "B", "C")[:count]:
            body = {"message_key": f"edit-{label}", "type": "follow_up",
                    "payload": {"text": f"[[{context.scenario['id']}]] original {label}"}}
            reply = context.client.http.post(f"/api/jobs/{source['id']}/messages", json=body)
            context.check(reply.status_code == 201, "could not queue editable follow-up")
            row = reply.json()["message"]
            context.check(row.get("revision") == 0 and row.get("edited_at") is None and row.get("can_edit") is True,
                          "original queued receipt lacks edit capability or version")
            bodies.append(body)
            rows.append(row)
        return bodies, rows

    def edit_receipt(self, context: ScenarioContext, message_id: int, body: dict, status: int) -> dict:
        response = context.client.http.post(f"/api/jobs/{context.job['id']}/messages/{message_id}/edits", json=body)
        context.check(response.status_code == status, f"edit status={response.status_code}: {response.text}")
        data = response.json()
        context.check(data.get("schema_version") == 1 and data.get("job_id") == context.job["id"], "wrong edit envelope")
        edit, row = data["edit"], data["message"]
        context.check(edit.get("schema_version") == 1 and edit.get("task_id") == context.job["id"]
                      and edit.get("message_id") == message_id and edit.get("edit_key") == body["edit_key"]
                      and edit.get("expected_revision") == body["expected_revision"]
                      and edit.get("revision") == body["expected_revision"] + 1 and edit.get("payload") == body["payload"],
                      "immutable edit receipt does not identify accepted operation")
        context.check(row["id"] == message_id and row.get("revision", -1) >= edit["revision"]
                      and (edit.get("created_at") or 0) >= row["created_at"], "current receipt regressed behind accepted edit")
        return data

    def assert_edit_children(self, context: ScenarioContext, rows: list[dict], expected_texts: list[str], revisions: list[int]) -> list[dict]:
        children = [context.client.wait_job(row["follow_up_job_id"]) for row in rows]
        context.check(all(child["status"] == "succeeded" and child["prompt"] == text
                          and child["conversation_id"] == context.conversation_id and child["project_id"] == context.project_id
                          for child, text in zip(children, expected_texts)), "child executed old edited prompt or changed scope")
        context.check(all(right["created_at"] >= left["finished_at"] for left, right in zip(children, children[1:])),
                      "editing changed FIFO execution order")
        events = context.client.conversation_events(context.conversation_id)
        for row, child, text, revision in zip(rows, children, expected_texts, revisions):
            prompts = [event["payload"] for event in events if event.get("turn_id") == child["turn_id"]
                       and event.get("event_type") == "user_message" and (event.get("payload") or {}).get("task_message_id") == row["id"]]
            context.check(len(prompts) == 1 and prompts[0].get("task_message_revision") == revision
                          and prompts[0].get("content") == [{"type": "text", "text": text}], "canonical child prompt has wrong revision")
        listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
        listing.raise_for_status()
        context.check({row["id"] for row in listing.json()["jobs"]} == {context.job["id"], *(child["id"] for child in children)},
                      "edit or retry created an extra task")
        expected_lines = [f"[[{context.scenario['id']}]] {context.scenario['prompt']}", *expected_texts]
        context.check(context.workspace_file("app/src/test/edited-prompts.txt").splitlines() == expected_lines,
                      "actual model/tool execution did not receive exactly the latest prompts in queue order")
        return children

    def driver_edit_queue(self, context: ScenarioContext) -> None:
        from concurrent.futures import ThreadPoolExecutor

        bodies, originals = self.paused_edit_queue(context)
        index = context.scenario["edit_index"]
        target = originals[index]
        url = f"/api/jobs/{context.job['id']}/messages/{target['id']}/edits"
        first_body = {"edit_key": "revision-one", "expected_revision": 0,
                      "payload": {"text": f"[[{context.scenario['id']}]] edited once"}}
        stranger = self.foreign_client()
        try:
            context.check(stranger.http.post(url, json=first_body).status_code == 404, "cross-account edit disclosed or changed message")
        finally:
            stranger.close()
        wrong = context.client.http.post(f"/api/jobs/missing-source/messages/{target['id']}/edits", json=first_body)
        context.check(wrong.status_code == 404, "edit ignored source task binding")
        first = self.edit_receipt(context, target["id"], first_body, 201)
        with ThreadPoolExecutor(max_workers=2) as pool:
            repeats = list(pool.map(lambda _: self.edit_receipt(context, target["id"], first_body, 200), range(2)))
        context.check(all(reply["edit"] == first["edit"] for reply in repeats), "repeated edit changed immutable acknowledgement")
        second_body = {"edit_key": "revision-two", "expected_revision": 1,
                       "payload": {"text": f"[[{context.scenario['id']}]] latest revised instruction"}}
        second = self.edit_receipt(context, target["id"], second_body, 201)
        stale = context.client.http.post(url, json={**first_body, "edit_key": "stale-new-operation"})
        changed_key_body = context.client.http.post(url, json={**first_body, "payload": second_body["payload"]})
        context.check(stale.status_code == changed_key_body.status_code == 409, "stale version or changed same-key body overwrote latest edit")
        self.stack.restart_idle_agent()
        retry = self.edit_receipt(context, target["id"], first_body, 200)
        context.check(retry["edit"] == first["edit"] and retry["message"]["revision"] == 2
                      and retry["message"]["payload"] == second_body["payload"], "old edit retry lost acknowledgement or revived old text after restart")
        original_retry = context.client.http.post(f"/api/jobs/{context.job['id']}/messages", json=bodies[index])
        context.check(original_retry.status_code == 200 and original_retry.json()["message"]["payload"] == second_body["payload"],
                      "original send retry reset the current edited text")
        changed_send = context.client.http.post(f"/api/jobs/{context.job['id']}/messages", json={**bodies[index], "payload": second_body["payload"]})
        context.check(changed_send.status_code == 409, "editing changed original send identity/hash")
        current = self.message_receipts(context)
        context.check([(r["id"], r["message_key"], r["created_at"]) for r in current]
                      == [(r["id"], r["message_key"], r["created_at"]) for r in originals], "editing changed queue identity or position")
        context.client.resume_job(context.job["id"])
        context.job = context.client.wait_job(context.job["id"])
        self.wait_terminal_events(context)
        rows = self.wait_receipts(context, 3, "follow_up_created", timeout=60)
        expected_texts = [body["payload"]["text"] for body in bodies]
        expected_texts[index] = second_body["payload"]["text"]
        revisions = [0, 0, 0]
        revisions[index] = 2
        self.assert_edit_children(context, rows, expected_texts, revisions)
        context.check(not any(payload.get("task_message_id") in {row["id"] for row in rows}
                              for payload in context.payloads("user_message")), "queued edit leaked into parent canonical context")
        for body, accepted in ((first_body, first), (second_body, second)):
            confirmed = self.edit_receipt(context, target["id"], body, 200)
            context.check(confirmed["edit"] == accepted["edit"] and confirmed["message"]["revision"] == 2
                          and confirmed["message"]["follow_up_job_id"] == rows[index]["follow_up_job_id"],
                          "post-dispatch retry changed edit acknowledgement or child mapping")

    def driver_edit_created(self, context: ScenarioContext) -> None:
        bodies, originals = self.paused_edit_queue(context, 1)
        context.client.resume_job(context.job["id"])
        context.job = context.client.wait_job(context.job["id"])
        self.wait_terminal_events(context)
        deadline = time.monotonic() + 20
        row = None
        while time.monotonic() < deadline:
            candidate = self.message_receipts(context)[0]
            if candidate.get("follow_up_job_id"):
                row = candidate
                break
            time.sleep(0.1)
        context.check(row is not None, "dispatcher did not create the child before editing")
        child_id = row["follow_up_job_id"]
        approval = context.client.wait_approval(child_id)
        before = context.client.get_job(child_id)
        response = context.client.http.post(f"/api/jobs/{context.job['id']}/messages/{originals[0]['id']}/edits",
            json={"edit_key": "too-late", "expected_revision": 0, "payload": {"text": "must not change running child"}})
        context.check(response.status_code == 409, "already-created child accepted a new edit")
        after = context.client.get_job(child_id)
        context.check((after["status"], after.get("cancel_requested"), after["prompt"])
                      == (before["status"], before.get("cancel_requested"), before["prompt"]), "rejected edit changed child state or prompt")
        context.check(approval["id"] in {item["id"] for item in context.client.pending_approvals(child_id)}, "edit implicitly approved child")
        current = self.message_receipts(context)[0]
        context.check(current.get("revision") == 0 and current.get("can_edit") is False
                      and current["follow_up_job_id"] == child_id, "rejected edit changed queue revision or capability")
        self.pump.watch(child_id)
        self.assert_edit_children(context, [current], [bodies[0]["payload"]["text"]], [0])

    def driver_edit_withdrawn(self, context: ScenarioContext) -> None:
        _, originals = self.paused_edit_queue(context, 1)
        target = originals[0]
        body = {"edit_key": "accepted-before-ending", "expected_revision": 0,
                "payload": {"text": f"[[{context.scenario['id']}]] revised but never executed"}}
        accepted = self.edit_receipt(context, target["id"], body, 201)
        if context.scenario.get("cancel_parent"):
            context.client.cancel_job(context.job["id"])
            context.job = context.client.wait_job(context.job["id"], until={"canceled"})
            self.wait_terminal_events(context)
            row = self.message_receipts(context)[0]
            context.check(row["delivery_state"] == "blocked" and row.get("can_edit") is False and row.get("can_withdraw") is True,
                          "blocked queue offered editing or lost withdrawal")
            context.check(self.edit_receipt(context, target["id"], body, 200)["edit"] == accepted["edit"],
                          "blocking hid an accepted edit")
            rejected = context.client.http.post(f"/api/jobs/{context.job['id']}/messages/{target['id']}/edits",
                json={**body, "edit_key": "blocked-edit", "expected_revision": 1})
            context.check(rejected.status_code == 409, "new edit silently unblocked canceled queue")
        withdrawn = self.withdraw_receipt(context, target["id"])
        self.stack.restart_idle_agent()
        repeated = self.edit_receipt(context, target["id"], body, 200)
        context.check(repeated["edit"] == accepted["edit"] and repeated["message"]["delivery_state"] == "withdrawn"
                      and repeated["message"]["withdrawn_at"] == withdrawn["withdrawn_at"], "accepted edit retry revived withdrawn work")
        denied = context.client.http.post(f"/api/jobs/{context.job['id']}/messages/{target['id']}/edits",
            json={**body, "edit_key": "after-withdrawal", "expected_revision": 1})
        context.check(denied.status_code == 409, "new edit revived withdrawn message")
        if not context.scenario.get("cancel_parent"):
            context.client.resume_job(context.job["id"])
            context.job = context.client.wait_job(context.job["id"])
            self.wait_terminal_events(context)
        self.assert_edit_children(context, [], [], [])

    def queue_page(self, context: ScenarioContext) -> dict:
        response = context.client.http.get(f"/api/jobs/{context.job['id']}/messages", params={"include_consumed": "true"})
        response.raise_for_status()
        page = response.json()
        queue = page.get("queue") or {}
        context.check(page.get("schema_version") == 1 and page.get("job_id") == context.job["id"]
                      and queue.get("schema_version") == 1 and queue.get("task_id") == context.job["id"], "queue snapshot has wrong scope")
        ids = queue.get("message_ids", [])
        pending = queue.get("pending_message_ids", [])
        followups = [row for row in page["messages"] if row["type"] == "follow_up"]
        context.check(len(ids) == len(set(ids)) and set(ids) == {row["id"] for row in followups}
                      and len(pending) == len(set(pending)) and pending == [value for value in ids if value in pending],
                      "queue snapshot is incomplete, duplicated or unordered")
        context.check(type(queue.get("can_reorder")) is bool and type(queue.get("order_revision")) is int
                      and queue["order_revision"] >= 0 and isinstance(queue.get("version"), str), "queue lacks trustworthy version or capability")
        return page

    def reorder_receipt(self, context: ScenarioContext, body: dict, status: int) -> dict:
        response = context.client.http.post(f"/api/jobs/{context.job['id']}/messages/reorders", json=body)
        context.check(response.status_code == status, f"reorder status={response.status_code}: {response.text}")
        data = response.json()
        ack = data.get("reorder") or {}
        context.check(data.get("schema_version") == 1 and data.get("job_id") == context.job["id"]
                      and ack.get("schema_version") == 1 and ack.get("task_id") == context.job["id"]
                      and all(ack.get(key) == body[key] for key in ("reorder_key", "expected_version", "message_ids"))
                      and type(ack.get("order_revision")) is int and ack["order_revision"] > 0 and ack.get("created_at", 0) > 0,
                      "reorder ACK does not identify the immutable request")
        queue = data.get("queue") or {}
        context.check(queue.get("task_id") == context.job["id"] and queue.get("order_revision", -1) >= ack["order_revision"]
                      and isinstance(data.get("messages"), list), "reorder response lacks separate current queue snapshot")
        return data

    def assert_reordered_execution(self, context: ScenarioContext, ordered_ids: list[int], texts: dict[int, str],
                                   revisions: dict[int, int], order_revisions: list[int]) -> None:
        deadline = time.monotonic() + 65
        page = None
        while time.monotonic() < deadline:
            page = self.queue_page(context)
            rows = {row["id"]: row for row in page["messages"] if row["type"] == "follow_up"}
            for row in rows.values():
                if row.get("follow_up_job_id"):
                    self.pump.watch(row["follow_up_job_id"])
            if all(rows[message_id]["delivery_state"] == "follow_up_created" for message_id in ordered_ids):
                break
            time.sleep(0.1)
        ordered = [rows[message_id] for message_id in ordered_ids]
        context.check(all(row["delivery_state"] == "follow_up_created" for row in ordered), "reordered queue did not dispatch completely")
        children = self.assert_edit_children(context, ordered, [texts[value] for value in ordered_ids], [revisions[value] for value in ordered_ids])
        events = context.client.conversation_events(context.conversation_id)
        for child, expected_revision in zip(children, order_revisions):
            prompts = [event["payload"] for event in events if event.get("turn_id") == child["turn_id"]
                       and event.get("event_type") == "user_message" and (event.get("payload") or {}).get("task_message_id")]
            context.check(len(prompts) == 1 and prompts[0].get("queue_order_revision") == expected_revision,
                          "child's canonical input does not record the applied queue order")
        queue = self.queue_page(context)["queue"]
        context.check(queue["pending_message_ids"] == [] and queue["can_reorder"] is False,
                      "finished queue still offers reordering")

    def driver_reorder_queue(self, context: ScenarioContext) -> None:
        bodies, originals = self.paused_edit_queue(context)
        ids = [row["id"] for row in originals]
        before = self.queue_page(context)["queue"]
        context.check(before["message_ids"] == before["pending_message_ids"] == ids and before["can_reorder"]
                      and before["order_revision"] == 0, "legacy queue did not start in receipt order")
        ordered = [ids[index] for index in context.scenario["order"]]
        body = {"reorder_key": "first-order", "expected_version": before["version"], "message_ids": ordered}
        stranger = self.foreign_client()
        try:
            denied = stranger.http.post(f"/api/jobs/{context.job['id']}/messages/reorders", json=body)
            context.check(denied.status_code == 404, "cross-account queue order disclosed or changed source")
        finally:
            stranger.close()
        accepted = self.reorder_receipt(context, body, 201)
        context.check(accepted["queue"]["message_ids"] == ordered and accepted["reorder"]["order_revision"] == 1,
                      "accepted queue order differs from requested order")
        url = f"/api/jobs/{context.job['id']}/messages/reorders"
        context.check(context.client.http.post(url, json={**body, "reorder_key": "stale-new-key"}).status_code == 409,
                      "stale queue version accepted a new operation")
        context.check(context.client.http.post(url, json={**body, "message_ids": ids}).status_code == 409,
                      "same reorder identity accepted changed body")
        edited_id = ordered[0]
        edited_text = f"[[{context.scenario['id']}]] latest body at new queue head"
        self.edit_receipt(context, edited_id, {"edit_key": "edit-after-order", "expected_revision": 0,
                                             "payload": {"text": edited_text}}, 201)
        edited_queue = self.queue_page(context)["queue"]
        context.check(edited_queue["message_ids"] == ordered and edited_queue["order_revision"] == 1
                      and edited_queue["version"] != accepted["queue"]["version"], "editing failed to invalidate the old queue snapshot")
        self.stack.restart_idle_agent()
        replay = self.reorder_receipt(context, body, 200)
        context.check(replay["reorder"] == accepted["reorder"] and replay["queue"]["message_ids"] == ordered,
                      "restart or old reorder ACK reset current order")
        page = self.queue_page(context)
        context.check({row["id"]: (row["message_key"], row["created_at"]) for row in page["messages"] if row["type"] == "follow_up"}
                      == {row["id"]: (row["message_key"], row["created_at"]) for row in originals}, "reorder changed message identity")
        context.client.resume_job(context.job["id"])
        context.job = context.client.wait_job(context.job["id"])
        self.wait_terminal_events(context)
        texts = {row["id"]: original["payload"]["text"] for row, original in zip(originals, bodies)}
        texts[edited_id] = edited_text
        self.assert_reordered_execution(context, ordered, texts, {value: int(value == edited_id) for value in ids}, [1, 1, 1])
        final_replay = self.reorder_receipt(context, body, 200)
        context.check(final_replay["reorder"] == accepted["reorder"] and not final_replay["queue"]["can_reorder"],
                      "terminal replay lost original ACK or revived queue")

    def driver_reorder_created(self, context: ScenarioContext) -> None:
        bodies, originals = self.paused_edit_queue(context)
        ids = [row["id"] for row in originals]
        old = self.queue_page(context)["queue"]
        context.client.resume_job(context.job["id"])
        context.job = context.client.wait_job(context.job["id"])
        self.wait_terminal_events(context)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            page = self.queue_page(context)
            first = next(row for row in page["messages"] if row["id"] == ids[0])
            if first.get("follow_up_job_id"):
                break
            time.sleep(0.1)
        child_id = first["follow_up_job_id"]
        approval = context.client.wait_approval(child_id)
        child = context.client.get_job(child_id)
        queue = self.queue_page(context)["queue"]
        context.check(queue["pending_message_ids"] == ids[1:] and queue["can_reorder"], "created prefix did not leave reorderable tail")
        url = f"/api/jobs/{context.job['id']}/messages/reorders"
        for token, desired in ((old["version"], ids[::-1]), (queue["version"], ids[::-1])):
            refused = context.client.http.post(url, json={"reorder_key": "move-created", "expected_version": token, "message_ids": desired})
            context.check(refused.status_code == 409, "reorder moved a task already created")
        body = {"reorder_key": "tail-order", "expected_version": queue["version"], "message_ids": ids[:0:-1]}
        accepted = self.reorder_receipt(context, body, 201)
        desired = [ids[0], ids[2], ids[1]]
        context.check(accepted["queue"]["message_ids"] == desired, "reorder changed locked prefix")
        after = context.client.get_job(child_id)
        context.check(all(after.get(key) == child.get(key) for key in ("status", "cancel_requested", "prompt", "turn_id"))
                      and approval["id"] in {item["id"] for item in context.client.pending_approvals(child_id)},
                      "reordering implicitly approved or changed the prefix task")
        self.pump.watch(child_id)
        self.assert_reordered_execution(context, desired, {row["id"]: body["payload"]["text"] for row, body in zip(originals, bodies)},
                                        {value: 0 for value in ids}, [0, 1, 1])

    def driver_reorder_membership(self, context: ScenarioContext) -> None:
        from concurrent.futures import ThreadPoolExecutor

        bodies, originals = self.paused_edit_queue(context)
        ids = [row["id"] for row in originals]
        queue = self.queue_page(context)["queue"]
        initial_body = {"reorder_key": "lost-order-ack", "expected_version": queue["version"], "message_ids": ids[::-1]}
        initial = self.reorder_receipt(context, initial_body, 201)
        extra_body = {"message_key": "new-member", "type": "follow_up", "payload": {"text": f"[[{context.scenario['id']}]] appended D"}}
        extra = context.client.http.post(f"/api/jobs/{context.job['id']}/messages", json=extra_body)
        context.check(extra.status_code == 201, "new queue member was not accepted")
        extra_id = extra.json()["message"]["id"]
        queue = self.queue_page(context)["queue"]
        context.check(queue["message_ids"] == [*ids[::-1], extra_id] and queue["order_revision"] == 1
                      and queue["version"] != initial["queue"]["version"], "new message was not appended or snapshot stayed reusable")
        url = f"/api/jobs/{context.job['id']}/messages/reorders"
        stale = {"reorder_key": "stale-members", "expected_version": initial["queue"]["version"], "message_ids": ids}
        context.check(context.client.http.post(url, json=stale).status_code == 409, "reorder silently dropped a newly received message")
        withdrawn = self.withdraw_receipt(context, ids[1])
        queue = self.queue_page(context)["queue"]
        pending = [ids[2], ids[0], extra_id]
        context.check(queue["pending_message_ids"] == pending and queue["message_ids"][1] == withdrawn["id"],
                      "withdrawal lost its historical queue slot")
        competing = [{"reorder_key": f"client-{index}", "expected_version": queue["version"], "message_ids": order}
                     for index, order in enumerate((pending[::-1], [pending[1], pending[2], pending[0]]))]
        second_client = E2EClient(self.stack)
        second_client.http.headers["Authorization"] = f"Bearer {context.client.token}"
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(client.http.post, url, json=body)
                           for client, body in zip((context.client, second_client), competing)]
                replies = [future.result() for future in futures]
        finally:
            second_client.close()
        context.check(sorted(reply.status_code for reply in replies) == [201, 409], "two different queue orders won the same snapshot")
        winner = next(reply.json() for reply in replies if reply.status_code == 201)
        context.check(winner["reorder"]["order_revision"] == 2, "CAS winner did not append exactly one order revision")
        self.stack.restart_idle_agent()
        current = self.queue_page(context)["queue"]
        replay = self.reorder_receipt(context, initial_body, 200)
        context.check(replay["reorder"] == initial["reorder"] and replay["queue"] == current
                      and current["message_ids"][1] == withdrawn["id"], "old ACK revived withdrawn work or restored an old order")
        old_send = context.client.http.post(f"/api/jobs/{context.job['id']}/messages", json=bodies[1])
        context.check(old_send.status_code == 200 and old_send.json()["message"]["delivery_state"] == "withdrawn",
                      "original send replay revived the withdrawn member")
        context.client.resume_job(context.job["id"])
        context.job = context.client.wait_job(context.job["id"])
        self.wait_terminal_events(context)
        texts = {row["id"]: body["payload"]["text"] for row, body in zip(originals, bodies)}
        texts[extra_id] = extra_body["payload"]["text"]
        order = current["pending_message_ids"]
        self.assert_reordered_execution(context, order, texts, {value: 0 for value in texts}, [2, 2, 2])
        final = self.reorder_receipt(context, initial_body, 200)
        context.check(final["reorder"] == initial["reorder"] and final["queue"]["message_ids"] == current["message_ids"],
                      "terminal original ACK changed permanent queue history")

    def driver_reorder_blocked(self, context: ScenarioContext) -> None:
        bodies, originals = self.paused_edit_queue(context)
        ids = [row["id"] for row in originals]
        cancel_parent = context.scenario.get("cancel_parent", False)
        if not cancel_parent:
            self.edit_receipt(context, ids[2], {"edit_key": "failing-child", "expected_revision": 0,
                "payload": {"text": "[[34_followup_failure]] actual failed reordered head"}}, 201)
        queue = self.queue_page(context)["queue"]
        body = {"reorder_key": "accepted-before-block", "expected_version": queue["version"], "message_ids": ids[::-1]}
        accepted = self.reorder_receipt(context, body, 201)
        if cancel_parent:
            context.client.cancel_job(context.job["id"])
            context.job = context.client.wait_job(context.job["id"], until={"canceled"})
            expected_ids = {context.job["id"]}
            blocker = context.job
        else:
            context.client.resume_job(context.job["id"])
            context.job = context.client.wait_job(context.job["id"])
            deadline = time.monotonic() + 20
            child_id = None
            while time.monotonic() < deadline:
                rows = self.message_receipts(context)
                head = next(row for row in rows if row["id"] == ids[2])
                if head.get("follow_up_job_id"):
                    child_id = head["follow_up_job_id"]
                    break
                time.sleep(0.1)
            context.check(bool(child_id), "new queue head was never dispatched")
            blocker = context.client.wait_job(child_id)
            context.check(blocker["status"] == "failed", "reordered failure fixture did not fail")
            expected_ids = {context.job["id"], child_id}
        self.wait_terminal_events(context)
        page = self.queue_page(context)
        queue = page["queue"]
        context.check(queue["can_reorder"] is False and queue["message_ids"] == ids[::-1], "failed prefix still permits new reordering")
        for row in page["messages"]:
            if row["type"] == "follow_up" and row["id"] in queue["pending_message_ids"]:
                context.check(row["delivery_state"] == "blocked", "failed predecessor did not block remaining members")
                self.assert_blocking_target(context, row, blocker)
        refusal = context.client.http.post(f"/api/jobs/{context.job['id']}/messages/reorders", json={
            "reorder_key": "bypass-failure", "expected_version": queue["version"], "message_ids": queue["pending_message_ids"][::-1]})
        context.check(refusal.status_code == 409, "reorder bypassed the failed prefix")
        self.stack.restart_idle_agent()
        replay = self.reorder_receipt(context, body, 200)
        context.check(replay["reorder"] == accepted["reorder"] and replay["queue"]["can_reorder"] is False,
                      "historical confirmation automatically unblocked queued work")
        listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
        listing.raise_for_status()
        context.check({job["id"] for job in listing.json()["jobs"]} == expected_ids, "blocked reorder created recovery or tail tasks")
        context.check(context.workspace_file("app/src/test/edited-prompts.txt").splitlines()
                      == [f"[[{context.scenario['id']}]] {context.scenario['prompt']}"], "blocked queue executed ordinary tail instructions")

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
            lambda event: event.get("event_type") == "tool_result" and (
                not context.scenario.get("pause_after_tool")
                or (event.get("payload") or {}).get("name") == context.scenario["pause_after_tool"]
            ),
            timeout=30,
        )
        self.pause_and_resume(context, job_id)

    def pause_and_resume(self, context: ScenarioContext, job_id: str) -> None:
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
        if context.scenario.get("assert_pause_evidence"):
            turn_id = context.client.get_job(job_id)["turn_id"]
            response = context.client.http.get(f"/api/projects/{context.project_id}/checkpoints")
            response.raise_for_status()
            after = [item for item in response.json()["checkpoints"]
                     if item.get("turn_id") == turn_id and item.get("kind") == "after_turn"]
            context.check(not after, "paused task prematurely froze its final after_turn checkpoint")
            events = context.client.conversation_events(context.conversation_id)
            context.check(not any(event.get("turn_id") == turn_id and event.get("event_type") == "changes"
                                  for event in events), "pause prematurely published final canonical changes")
        context.client.resume_job(job_id)
        context.job = context.client.wait_job(job_id, timeout=90)
        self.wait_terminal_events(context)

    def driver_pause_after_steer(self, context: ScenarioContext) -> None:
        job = self.send_prompt(context)
        job_id = str(job["id"])
        context.client.wait_event(context.conversation_id, lambda event: event.get("event_type") == "tool_call")
        steer = str(context.scenario["steer"])
        body = {"message_key": "e2e-resume-steer", "type": "steer", "payload": {"text": steer}}
        first = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
        first.raise_for_status()
        repeat = context.client.http.post(f"/api/jobs/{job_id}/messages", json=body)
        repeat.raise_for_status()
        context.check(first.json()["message"]["id"] == repeat.json()["message"]["id"], "steer retry was not idempotent")
        context.client.wait_event(context.conversation_id, lambda event:
                                  event.get("event_type") == "user_message"
                                  and steer in payload_strings(event.get("payload")), timeout=20)
        self.pause_and_resume(context, job_id)
        recorded = [event for event in context.events if event.get("event_type") == "user_message"
                    and steer in payload_strings(event.get("payload"))]
        context.check(len(recorded) == 1, f"consumed steer persisted {len(recorded)} times instead of once")

    def driver_service_recovery(self, context: ScenarioContext) -> None:
        from concurrent.futures import ThreadPoolExecutor

        original = self.send_prompt(context)
        source_id = str(original["id"])
        context.client.wait_event(context.conversation_id, lambda event: event.get("event_type") == "tool_result")
        self.stack.restart_agent_after_crash(source_id)
        interrupted = context.client.wait_job(source_id, until={"failed", "interrupted"})
        context.check(interrupted.get("can_recover") is True, "restarted source is not explicitly recoverable")

        # Recovery must never reveal or act on another account's source job.
        stranger = self.foreign_client()
        try:
            forbidden = stranger.http.post(f"/api/jobs/{source_id}/recover")
            context.check(forbidden.status_code == 404, f"cross-account recovery returned {forbidden.status_code}")
        finally:
            stranger.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(context.client.http.post, f"/api/jobs/{source_id}/recover") for _ in range(2)]
            responses = [request.result() for request in requests]
        context.check(sorted(response.status_code for response in responses) == [200, 201],
                      f"concurrent recover statuses {[response.status_code for response in responses]}")
        recovered = [response.json()["job"] for response in responses]
        recovery_id = str(recovered[0]["id"])
        context.check(recovery_id != source_id and recovered[1]["id"] == recovery_id, "duplicate recovery task created")
        context.check(all(job["conversation_id"] == context.conversation_id and job["project_id"] == context.project_id
                          for job in recovered), "recovery changed conversation/project")
        context.extra["event_turn_id"] = recovered[0]["turn_id"]
        context.check(recovered[0]["turn_id"] != interrupted["turn_id"], "recovery did not create a new turn")
        self.pump.watch(recovery_id)
        context.job = context.client.wait_job(recovery_id)
        self.wait_terminal_events(context)
        retry = context.client.http.post(f"/api/jobs/{source_id}/recover")
        context.check(retry.status_code == 200 and retry.json()["job"]["id"] == recovery_id,
                      "retry after completion created a different recovery")
        source = context.client.get_job(source_id)
        context.check(source.get("can_recover") is False and source.get("recovery_job_id") == recovery_id,
                      "source recovery fields are stale")
        notes = context.payloads("recovery_note")
        context.check(len(notes) == 1 and notes[0].get("original_task_id") == source_id, "missing/duplicate recovery provenance")

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
        if "diff_files" in expect:
            response = context.client.http.get(f"/api/projects/{context.project_id}/diff", params={"turn_id": job["turn_id"]})
            response.raise_for_status()
            paths = {item["path"] for item in response.json().get("files", [])}
            context.check(paths == set(expect["diff_files"]), f"checkpoint diff files {sorted(paths)} != {expect['diff_files']}")
        for rel_path, needle in (expect.get("file_contains") or {}).items():
            content = context.workspace_file(rel_path)
            context.check(needle in content, f"{rel_path} does not contain {needle!r}: {content[:200]!r}")
        for rel_path, lines in (expect.get("file_lines") or {}).items():
            actual_lines = context.workspace_file(rel_path).splitlines()
            context.check(actual_lines == lines, f"{rel_path} lines {actual_lines!r} != {lines!r}")
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
        if "verification" in expect:
            verification = job.get("verification") or {}
            context.check(verification.get("schema_version") == 1 and verification.get("job_id") == job["id"]
                          and verification.get("scope") == "job", f"invalid verification binding: {verification}")
            for step, fields in expect["verification"].items():
                actual = verification.get(step) or {}
                for key, value in fields.items():
                    context.check(actual.get(key) == value,
                                  f"verification.{step}.{key}={actual.get(key)!r} != {value!r}")
                if actual.get("state") in {"passed", "failed", "no_tests", "skipped", "canceled"}:
                    context.check(bool(actual.get("run_id")) and (actual.get("evidence_time") or 0) > 0,
                                  f"verification.{step} missing execution identity/time")
            context.check(verification.get("installation", {}).get("state") == "unknown",
                          "build evidence claimed installation without a device receipt")
            feedback = context.client.http.get(f"/api/projects/{context.project_id}/feedback", params={"job_id": job["id"]})
            feedback.raise_for_status()
            context.check(feedback.json().get("verification") == verification, "feedback disagrees with exact job evidence")
            listing = context.client.http.get("/api/jobs", params={"project_id": context.project_id})
            listing.raise_for_status()
            listed = next(item for item in listing.json()["jobs"] if item["id"] == job["id"])
            context.check(listed.get("verification") == verification, "job list disagrees with job evidence")
            stranger = self.foreign_client()
            try:
                response = stranger.http.get(f"/api/projects/{context.project_id}/feedback", params={"job_id": job["id"]})
                context.check(response.status_code == 404, "verification leaked across accounts")
            finally:
                stranger.close()
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
        report_path = self.stack.tmp_root / "e2e-report.json"
        report_path.write_text(json.dumps({
            "total": len(self.results),
            "failed": failed,
            "results": self.results,
            "projects_per_account": list(self.account_project_counts.values()),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if self.stack.keep:
            print(f"  report: {report_path}", flush=True)


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
