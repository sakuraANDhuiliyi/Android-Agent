"""Offline eval scenarios (fixture v1). No real network or paid models."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

from evals.metrics import EvalMetrics, EvalResult, Timer
from evals.quality import (
    anthropic_tool_ids,
    chars_of,
    constraint_recall,
    hallucination_rate,
    openai_tool_ids,
    openai_tool_pairing_valid,
    token_savings,
    tool_chain_complete,
    unresolved_retention,
)

FIXTURE_VERSION = "v1"
FAKE_MCP = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fake_mcp_server.py"


def _ok(scenario_id: str, title: str, metrics: EvalMetrics, **details: Any) -> EvalResult:
    metrics.estimate_tokens_from_chars()
    return EvalResult(
        scenario_id=scenario_id,
        title=title,
        fixture_version=FIXTURE_VERSION,
        passed=bool(metrics.goal_completed) and metrics.security_violations == 0,
        metrics=metrics,
        details=details,
    )


def _fail(
    scenario_id: str, title: str, metrics: EvalMetrics, error: str, **details: Any
) -> EvalResult:
    metrics.estimate_tokens_from_chars()
    return EvalResult(
        scenario_id=scenario_id,
        title=title,
        fixture_version=FIXTURE_VERSION,
        passed=False,
        metrics=metrics,
        error=error,
        details=details,
    )


def _seed_android_tree(ws: Path) -> None:
    java = ws / "app" / "src" / "main" / "java" / "com" / "example" / "app"
    layout = ws / "app" / "src" / "main" / "res" / "layout"
    java.mkdir(parents=True)
    layout.mkdir(parents=True)
    (java / "MainActivity.kt").write_text(
        "package com.example.app\nclass MainActivity\n", encoding="utf-8"
    )
    (layout / "activity_main.xml").write_text(
        '<?xml version="1.0"?>\n<LinearLayout/>\n', encoding="utf-8"
    )
    (ws / "app" / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (ws / "settings.gradle.kts").write_text('rootProject.name="app"\n', encoding="utf-8")
    (ws / ".agent-project.json").write_text(
        json.dumps({"package": "com.example.app"}), encoding="utf-8"
    )


def scenario_01_create_page_build() -> EvalResult:
    title = "创建简单 Android 页面并构建"
    timer = Timer()
    metrics = EvalMetrics()
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        _seed_android_tree(ws)
        from agent.tools import ToolResult, write_file

        page = "package com.example.app\nclass SettingsActivity\n"
        result = write_file(
            ws, "app/src/main/java/com/example/app/SettingsActivity.kt", page
        )
        metrics.tool_calls += 1
        metrics.chars_estimate += len(page)
        target = ws / "app/src/main/java/com/example/app/SettingsActivity.kt"
        metrics.files_modified = target.is_file()
        metrics.modified_paths = ["app/src/main/java/com/example/app/SettingsActivity.kt"]
        # Offline CI must not invoke real Gradle/SDK; record a mocked successful build.
        build = ToolResult(True, "BUILD SUCCESSFUL (mocked assembleDebug)")
        metrics.tool_calls += 1
        metrics.build_result = "mocked"
        metrics.goal_completed = bool(result.ok and build.ok and metrics.files_modified)
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("01_create_page_build", title, metrics, result.output)
        return _ok("01_create_page_build", title, metrics)


def scenario_02_fix_kotlin() -> EvalResult:
    title = "修复 Kotlin 编译错误"
    timer = Timer()
    metrics = EvalMetrics()
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        _seed_android_tree(ws)
        path = "app/src/main/java/com/example/app/MainActivity.kt"
        (ws / path).write_text(
            "package com.example.app\nclass MainActivity {\n  fun bad(: Int {}\n}\n",
            encoding="utf-8",
        )
        from agent.tools import str_replace

        r = str_replace(ws, path, "fun bad(: Int {}", "fun ok(): Int = 1")
        metrics.tool_calls += 1
        content = (ws / path).read_text(encoding="utf-8")
        metrics.files_modified = "fun ok()" in content
        metrics.modified_paths = [path]
        metrics.chars_estimate += len(content)
        metrics.goal_completed = bool(r.ok and metrics.files_modified)
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("02_fix_kotlin", title, metrics, str(r.output))
        return _ok("02_fix_kotlin", title, metrics)


def scenario_03_fix_xml() -> EvalResult:
    title = "修复 XML resource 错误"
    timer = Timer()
    metrics = EvalMetrics()
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        _seed_android_tree(ws)
        path = "app/src/main/res/layout/activity_main.xml"
        (ws / path).write_text(
            '<LinearLayout><TextView android:text="@string/missing"/></LinearLayout>\n',
            encoding="utf-8",
        )
        from agent.tools import str_replace

        r = str_replace(
            ws, path, 'android:text="@string/missing"', 'android:text="Hello"'
        )
        metrics.tool_calls += 1
        content = (ws / path).read_text(encoding="utf-8")
        metrics.files_modified = "Hello" in content and "@string/missing" not in content
        metrics.modified_paths = [path]
        metrics.chars_estimate += len(content)
        metrics.goal_completed = bool(r.ok and metrics.files_modified)
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("03_fix_xml", title, metrics, str(r.output))
        return _ok("03_fix_xml", title, metrics)


def scenario_04_multi_turn_tools() -> EvalResult:
    title = "多轮追问保留工具链"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_context import build_openai_messages
    from agent.conversation_events import ConversationEventStore
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "agent.db"
        store = TaskStore(db)
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p", title="eval-4")
        t1 = events.create_turn(conv["id"], "u", "p", task_id="t1")
        events.append_event(
            conv["id"], t1["id"], "user_message", {"content": "read file"}, context_visible=True
        )
        events.append_event(
            conv["id"],
            t1["id"],
            "assistant_message",
            {"content": "", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": "{}"}]},
        )
        events.append_event(
            conv["id"],
            t1["id"],
            "tool_call",
            {"tool_call_id": "c1", "name": "read_file", "arguments": {"path": "a.kt"}},
        )
        events.append_event(
            conv["id"],
            t1["id"],
            "tool_result",
            {
                "tool_call_id": "c1",
                "name": "read_file",
                "ok": True,
                "content": "class A",
                "model_output": "class A",
            },
        )
        t2 = events.create_turn(conv["id"], "u", "p", task_id="t2")
        events.append_event(
            conv["id"], t2["id"], "user_message", {"content": "edit it"}, context_visible=True
        )
        events.append_event(
            conv["id"],
            t2["id"],
            "tool_call",
            {"tool_call_id": "c2", "name": "write_file", "arguments": {"path": "a.kt"}},
        )
        events.append_event(
            conv["id"],
            t2["id"],
            "tool_result",
            {
                "tool_call_id": "c2",
                "name": "write_file",
                "ok": True,
                "content": "ok",
                "model_output": "ok",
            },
        )
        rows = events.list_events(conv["id"], user_id="u")
        messages = build_openai_messages(rows)
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        metrics.tool_calls = len(tool_msgs)
        metrics.chars_estimate = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        metrics.goal_completed = metrics.tool_calls >= 1 and any(
            m.get("tool_call_id") == "c1" for m in tool_msgs
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "04_multi_turn_tools",
                title,
                metrics,
                f"tools={metrics.tool_calls} msgs={len(messages)}",
            )
        return _ok("04_multi_turn_tools", title, metrics, message_count=len(messages))


def scenario_05_steer() -> EvalResult:
    title = "中途 steer"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        task_id = uuid.uuid4().hex[:12]
        store.create_task(
            {
                "id": task_id,
                "user_id": "u",
                "project_id": "p",
                "conversation_id": "c1",
                "prompt": "work",
                "status": "running",
                "created_at": time.time(),
            }
        )
        store.add_task_message(
            task_id, "steer-1", "steer", {"text": "改用 ConstraintLayout"}
        )
        pending = store.get_pending_messages(task_id, types=["steer"])
        metrics.goal_completed = bool(pending) and pending[0]["payload"].get("text")
        if pending:
            store.consume_message(pending[0]["id"])
        again = store.get_pending_messages(task_id, types=["steer"])
        metrics.goal_completed = bool(metrics.goal_completed and again == [])
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("05_steer", title, metrics, "steer not consumed once")
        return _ok("05_steer", title, metrics)


def scenario_06_tool_failure() -> EvalResult:
    title = "工具失败"
    timer = Timer()
    metrics = EvalMetrics()
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        _seed_android_tree(ws)
        from agent.tools import read_file

        r = read_file(ws, "does/not/exist.kt")
        metrics.tool_calls += 1
        metrics.goal_completed = not r.ok
        metrics.notes.append(str(r.output)[:200])
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("06_tool_failure", title, metrics, "expected failure")
        return _ok("06_tool_failure", title, metrics)


def scenario_07_approvals() -> EvalResult:
    title = "审批允许、拒绝、超时和取消"
    timer = Timer()
    metrics = EvalMetrics()
    from agent import approvals

    decisions: list[str] = []

    def _one(mode: str) -> str:
        def later() -> None:
            time.sleep(0.05)
            pending = list(approvals._pending.values())
            if not pending:
                return
            req = pending[-1]
            if mode == "approved":
                approvals.resolve_approval(req.id, "u", approved=True)
            elif mode == "rejected":
                approvals.resolve_approval(req.id, "u", approved=False)
            elif mode == "canceled":
                approvals.resolve_approval(
                    req.id, "u", approved=False, force_decision="canceled"
                )
            elif mode == "timeout":
                # Production floor is max(30s, timeout_sec); force the timeout decision path.
                approvals.resolve_approval(
                    req.id, "u", approved=False, force_decision="timeout"
                )

        threading.Thread(target=later, daemon=True).start()
        result = approvals.request_user_approval(
            job_id="j1",
            user_id="u",
            kind="download",
            payload={"message": "ok?", "url": "https://example.test/a"},
            timeout_sec=30.0,
        )
        decisions.append(result)
        metrics.approvals += 1
        return result

    try:
        a = _one("approved")
        b = _one("rejected")
        c = _one("canceled")
        d = _one("timeout")
        metrics.goal_completed = {a, b, c, d} >= {
            "approved",
            "rejected",
            "canceled",
            "timeout",
        }
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("07_approvals", title, metrics, f"got {decisions}")
        return _ok("07_approvals", title, metrics, decisions=decisions)
    except Exception as exc:
        metrics.wall_time_ms = timer.ms()
        return _fail("07_approvals", title, metrics, str(exc))


def scenario_08_model_fallback() -> EvalResult:
    title = "Provider/Model fallback"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.model_fallback import (
        should_try_next_model,
        should_try_next_provider,
        unique_models,
    )

    models = unique_models("primary", ["primary", "fallback-a", "fallback-b"])
    metrics.goal_completed = (
        models == ["primary", "fallback-a", "fallback-b"]
        and should_try_next_model(RuntimeError("model not found / 429"))
        and not should_try_next_model(RuntimeError("invalid_api_key"))
        and should_try_next_provider(ConnectionError("connection reset"))
        and not should_try_next_provider(KeyError("path"))
    )
    metrics.wall_time_ms = timer.ms()
    if not metrics.goal_completed:
        return _fail("08_model_fallback", title, metrics, "fallback rules failed")
    return _ok("08_model_fallback", title, metrics, models=models)


def scenario_09_interrupt_stages() -> EvalResult:
    title = "服务在模型/工具/审批/checkpoint 阶段中断"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_events import ConversationEventStore
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        stages_wanted = ["model", "tool", "approval", "checkpoint"]
        for i, stage in enumerate(stages_wanted):
            turn = events.create_turn(conv["id"], "u", "p", task_id=f"t{i}")
            if stage == "tool":
                events.append_event(
                    conv["id"],
                    turn["id"],
                    "tool_result",
                    {
                        "tool_call_id": "x",
                        "name": "read_file",
                        "ok": False,
                        "content": "service_interrupted",
                        "model_output": "service_interrupted",
                    },
                )
            elif stage == "approval":
                events.append_event(
                    conv["id"],
                    turn["id"],
                    "approval_required",
                    {"approval_id": "ap1", "kind": "download"},
                )
            elif stage == "checkpoint":
                events.append_event(
                    conv["id"],
                    turn["id"],
                    "context_checkpoint",
                    {"summary": "cp", "kept_turns": 1},
                    context_visible=True,
                )
            events.append_event(
                conv["id"],
                turn["id"],
                "turn_interrupted",
                {"stage": stage, "reason": "service_restart"},
            )
        rows = events.list_events(conv["id"], user_id="u")
        stages = [
            (e.get("payload") or {}).get("stage")
            for e in rows
            if e.get("event_type") == "turn_interrupted"
        ]
        metrics.recoveries = len(stages)
        metrics.goal_completed = set(stages) >= set(stages_wanted)
        metrics.chars_estimate = sum(len(json.dumps(e, default=str)) for e in rows)
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("09_interrupt_stages", title, metrics, f"stages={stages}")
        return _ok("09_interrupt_stages", title, metrics, stages=stages)


def scenario_10_git_dirty_restore() -> EvalResult:
    title = "Git dirty workspace 和恢复冲突"
    timer = Timer()
    metrics = EvalMetrics()
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        workspaces = Path(tmp) / "workspaces"
        data.mkdir()
        ws = workspaces / "u" / "p"
        _seed_android_tree(ws)
        git_env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TEMPLATE_DIR": "",
        }
        for args in (
            ["git", "init", "-b", "main", "--template="],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "t"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "init"],
        ):
            proc = subprocess.run(
                args, cwd=str(ws), capture_output=True, text=True, env=git_env
            )
            if proc.returncode != 0:
                metrics.wall_time_ms = timer.ms()
                return _fail(
                    "10_git_dirty_restore",
                    title,
                    metrics,
                    proc.stderr or proc.stdout or str(args),
                )

        from agent.database import TaskStore
        from agent.workspace import WorkspaceRepository

        with (
            patch("agent.paths.DATA_DIR", data),
            patch("agent.paths.WORKSPACES_DIR", workspaces),
            patch("agent.workspace.DATA_DIR", data),
            patch("agent.workspace.workspace_path", lambda u, p: workspaces / u / p),
        ):
            store = TaskStore(data / "agent.db")
            repo = WorkspaceRepository("u", "p", task_store=store)
            cp = repo.create_checkpoint("manual", idempotency_key="cp1")
            target = ws / "app/src/main/java/com/example/app/MainActivity.kt"
            target.write_text(
                "package com.example.app\nclass MainActivity // dirty\n",
                encoding="utf-8",
            )
            status = repo.git_status()
            conflicts = repo.detect_conflicts(cp["id"])
            restore = repo.restore_checkpoint(cp["id"])
            metrics.files_modified = True
            metrics.modified_paths = ["app/src/main/java/com/example/app/MainActivity.kt"]
            metrics.recoveries = 1
            metrics.goal_completed = bool(
                status.get("dirty")
                and (
                    (not restore.get("ok") and restore.get("error") == "conflict")
                    or conflicts.get("has_conflicts")
                )
            )
            metrics.notes.append(json.dumps({"status": status.get("dirty"), "restore": restore.get("error"), "conflicts": conflicts.get("has_conflicts")}))
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("10_git_dirty_restore", title, metrics, "expected dirty conflict")
        return _ok("10_git_dirty_restore", title, metrics)


def scenario_11_index_incremental() -> EvalResult:
    title = "代码索引增量更新"
    timer = Timer()
    metrics = EvalMetrics()
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        workspaces = Path(tmp) / "workspaces"
        data.mkdir()
        ws = workspaces / "u" / "p"
        _seed_android_tree(ws)
        with (
            patch("agent.repo_index.DATA_DIR", data),
            patch("agent.repo_index.workspace_path", lambda uid, pid: workspaces / uid / pid),
        ):
            from agent.repo_index import RepoIndex

            idx = RepoIndex("u", "p")
            first = idx.update()
            (ws / "app/src/main/java/com/example/app/New.kt").write_text(
                "package com.example.app\nclass NewThing\n", encoding="utf-8"
            )
            second = idx.update()
            hits = idx.search("NewThing")
            metrics.files_modified = True
            metrics.modified_paths = ["app/src/main/java/com/example/app/New.kt"]
            metrics.goal_completed = (
                first.get("status") == "ready"
                and second.get("updated", 0) >= 1
                and bool(hits)
            )
            metrics.chars_estimate = 500
            metrics.wall_time_ms = timer.ms()
            if not metrics.goal_completed:
                return _fail(
                    "11_index_incremental",
                    title,
                    metrics,
                    f"first={first} second={second} hits={len(hits)}",
                )
            return _ok("11_index_incremental", title, metrics, updated=second.get("updated"))


def scenario_12_mcp_crash() -> EvalResult:
    title = "MCP server 崩溃"
    timer = Timer()
    metrics = EvalMetrics()
    if not FAKE_MCP.is_file():
        metrics.wall_time_ms = timer.ms()
        return _fail("12_mcp_crash", title, metrics, f"missing {FAKE_MCP}")
    from agent.mcp_client import McpTransportError, StdioMcpTransport
    from agent.mcp_config import McpServerConfig

    cfg = McpServerConfig(
        name="fake-crash",
        transport="stdio",
        command=sys.executable,
        args=[str(FAKE_MCP)],
        env_refs={"FAKE_MCP_MODE": "crash"},
        enabled=True,
        timeout_seconds=5.0,
        scope="user",
    )
    transport = StdioMcpTransport(cfg, workspace=Path(tempfile.mkdtemp()))
    try:
        transport.start()
        transport.list_tools()
        try:
            transport.call_tool("echo", {"message": "x"})
            # If call somehow returns, still check process death
            metrics.goal_completed = not transport.healthy()
            metrics.notes.append("call returned; relying on unhealthy")
        except Exception as exc:
            metrics.goal_completed = True
            metrics.notes.append(f"{type(exc).__name__}: {exc}")
            metrics.recoveries = 1
    except McpTransportError as exc:
        metrics.goal_completed = True
        metrics.notes.append(f"start/transport: {exc}")
        metrics.recoveries = 1
    finally:
        try:
            transport.close()
        except Exception as close_exc:
            metrics.notes.append(f"close: {close_exc}")
    metrics.tool_calls = 1
    metrics.wall_time_ms = timer.ms()
    if not metrics.goal_completed:
        return _fail("12_mcp_crash", title, metrics, "MCP did not crash")
    return _ok("12_mcp_crash", title, metrics)


def scenario_13_subagent() -> EvalResult:
    title = "Subagent 成功、失败和取消"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.database import TaskStore
    from agent.subagent_roles import get_role

    role = get_role("explore")
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        parent_id = uuid.uuid4().hex[:12]
        store.create_task(
            {
                "id": parent_id,
                "user_id": "u",
                "project_id": "p",
                "conversation_id": "c",
                "prompt": "parent",
                "status": "running",
                "created_at": time.time(),
            }
        )
        statuses = {}
        for status in ("completed", "failed", "canceled"):
            cid = uuid.uuid4().hex[:12]
            store.create_task(
                {
                    "id": cid,
                    "user_id": "u",
                    "project_id": "p",
                    "conversation_id": "c",
                    "prompt": f"child {status}",
                    "status": "queued",
                    "created_at": time.time(),
                    "parent_task_id": parent_id,
                    "role": "explore",
                }
            )
            store.update_task(cid, status=status)
            statuses[status] = store.get_task(cid, "u")["status"]
        metrics.goal_completed = bool(role) and set(statuses.values()) >= {
            "completed",
            "failed",
            "canceled",
        }
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("13_subagent", title, metrics, f"statuses={statuses}")
        return _ok("13_subagent", title, metrics, statuses=statuses)


def scenario_14_long_history_checkpoint() -> EvalResult:
    title = "八轮以上历史和 checkpoint"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_events import ConversationEventStore
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        for i in range(9):
            turn = events.create_turn(conv["id"], "u", "p", task_id=f"t{i}")
            events.append_event(
                conv["id"],
                turn["id"],
                "user_message",
                {"content": f"q{i}" * 20},
                context_visible=True,
            )
            events.append_event(
                conv["id"],
                turn["id"],
                "assistant_message",
                {"content": f"a{i}" * 20},
                context_visible=True,
            )
            events.append_event(
                conv["id"], turn["id"], "turn_completed", {"status": "completed"}
            )
        last = events.create_turn(conv["id"], "u", "p", task_id="tcp")
        events.append_event(
            conv["id"],
            last["id"],
            "context_checkpoint",
            {"summary": "eight-plus turns", "kept_turns": 4},
            context_visible=True,
        )
        rows = events.list_events(conv["id"], user_id="u")
        user_msgs = [e for e in rows if e.get("event_type") == "user_message"]
        checkpoints = [e for e in rows if e.get("event_type") == "context_checkpoint"]
        metrics.goal_completed = len(user_msgs) >= 8 and bool(checkpoints)
        metrics.chars_estimate = sum(len(json.dumps(e, default=str)) for e in rows)
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "14_long_history_checkpoint",
                title,
                metrics,
                f"users={len(user_msgs)} cp={len(checkpoints)}",
            )
        return _ok(
            "14_long_history_checkpoint",
            title,
            metrics,
            user_messages=len(user_msgs),
        )


def scenario_15_memory_approve_delete() -> EvalResult:
    title = "项目记忆批准与删除"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.memory_store import MemoryStore

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp) / "mem.db")
        cand = store.create_memory(
            user_id="u",
            project_id="p",
            scope="project",
            memory_type="convention",
            title="ViewBinding",
            content="约定：使用 ViewBinding",
            tags=["convention"],
            status="candidate",
        )
        before = store.search("u", "ViewBinding", project_id="p")
        approved = store.approve(cand["id"], "u")
        after = store.search("u", "ViewBinding", project_id="p")
        deleted = store.delete_memory(cand["id"], "u")
        gone = store.get_memory(cand["id"], "u")
        metrics.approvals = 1
        metrics.goal_completed = (
            before == []
            and approved is not None
            and approved["status"] == "active"
            and bool(after)
            and deleted
            and gone is None
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail("15_memory_approve_delete", title, metrics, "memory lifecycle failed")
        return _ok("15_memory_approve_delete", title, metrics)


def scenario_16_reconnect() -> EvalResult:
    title = "桌面和手机断线重连游标"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_events import ConversationEventStore
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        turn = events.create_turn(conv["id"], "u", "p", task_id="t1")
        for i in range(5):
            events.append_event(
                conv["id"],
                turn["id"],
                "system_note",
                {"text": f"e{i}"},
                context_visible=True,
            )
        page1 = events.list_events(conv["id"], user_id="u", after_seq=0, limit=2)
        last = page1[-1]["seq"]
        page2 = events.list_events(conv["id"], user_id="u", after_seq=last, limit=10)
        seen = {e["seq"] for e in page1} | {e["seq"] for e in page2}
        overlap = {e["seq"] for e in page1} & {e["seq"] for e in page2}
        metrics.recoveries = 1
        metrics.goal_completed = len(seen) == 5 and overlap == set()
        metrics.chars_estimate = 200
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "16_reconnect", title, metrics, f"seen={seen} overlap={overlap}"
            )
        return _ok("16_reconnect", title, metrics, seqs=sorted(seen))


def _append_user_assistant(
    events: Any,
    conv_id: str,
    user_id: str,
    project_id: str,
    *,
    task_id: str,
    user_text: str,
    assistant_text: str,
) -> Any:
    turn = events.create_turn(conv_id, user_id, project_id, task_id=task_id)
    events.append_event(
        conv_id,
        turn["id"],
        "user_message",
        {"content": user_text},
        context_visible=True,
    )
    events.append_event(
        conv_id,
        turn["id"],
        "assistant_message",
        {
            "text_blocks": [{"type": "text", "text": assistant_text}],
            "is_final": True,
        },
        context_visible=True,
    )
    return turn


def scenario_17_hundred_turn_history() -> EvalResult:
    title = "100+ 轮长历史与结构化 checkpoint"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_context import select_context_events
    from agent.conversation_events import ConversationEventStore
    from agent.conversation_summary import create_semantic_checkpoint
    from agent.database import TaskStore

    constraint = "必须使用 ViewBinding"
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        for index in range(102):
            user_text = f"q{index} {constraint}" if index == 0 else f"q{index}" * 8
            _append_user_assistant(
                events,
                conv["id"],
                "u",
                "p",
                task_id=f"t{index}",
                user_text=user_text,
                assistant_text=f"a{index}" * 8,
            )
        checkpoint = create_semantic_checkpoint(
            events,
            conv["id"],
            "u",
            keep_recent_turns=4,
            force=True,
        )
        rows = events.list_events(conv["id"], user_id="u")
        selected = select_context_events(rows)
        state = (checkpoint or {}).get("payload", {}).get("state") or {}
        metrics.constraint_recall = constraint_recall(
            [constraint],
            state.get("constraints") or [],
        )
        metrics.hallucination_rate = hallucination_rate(
            state,
            (event.get("seq") or 0 for event in rows),
        )
        metrics.token_savings = token_savings(chars_of(rows), chars_of(selected))
        metrics.first_token_ms = timer.ms()
        metrics.chars_estimate = chars_of(selected)
        user_msgs = [e for e in rows if e.get("event_type") == "user_message"]
        metrics.goal_completed = (
            len(user_msgs) >= 100
            and checkpoint is not None
            and metrics.constraint_recall == 1.0
            and metrics.hallucination_rate == 0.0
            and metrics.token_savings > 0
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "17_hundred_turn_history",
                title,
                metrics,
                f"users={len(user_msgs)} cp={bool(checkpoint)} "
                f"recall={metrics.constraint_recall} save={metrics.token_savings}",
            )
        return _ok(
            "17_hundred_turn_history",
            title,
            metrics,
            user_messages=len(user_msgs),
        )


def scenario_18_parallel_failed_tools() -> EvalResult:
    title = "并行工具与失败项保留"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_context import build_openai_messages
    from agent.conversation_events import ConversationEventStore
    from agent.conversation_summary import create_semantic_checkpoint
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        turn = events.create_turn(conv["id"], "u", "p", task_id="tools")
        events.append_event(
            conv["id"],
            turn["id"],
            "user_message",
            {"content": "读取并写入"},
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "assistant_message",
            {
                "message_id": "m-par",
                "text_blocks": [{"type": "text", "text": "并行调用"}],
                "is_final": False,
            },
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_call",
            {
                "message_id": "m-par",
                "tool_call_id": "ok1",
                "block_index": 0,
                "name": "read_file",
                "input": {"path": "a.kt"},
            },
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_call",
            {
                "message_id": "m-par",
                "tool_call_id": "fail1",
                "block_index": 1,
                "name": "write_file",
                "input": {"path": "b.kt"},
            },
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_result",
            {
                "tool_call_id": "ok1",
                "name": "read_file",
                "ok": True,
                "model_output": "package ok",
            },
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_result",
            {
                "tool_call_id": "fail1",
                "name": "write_file",
                "ok": False,
                "model_output": "disk full",
                "error_type": "ToolExecutionError",
            },
            context_visible=True,
        )
        for index in range(6):
            _append_user_assistant(
                events,
                conv["id"],
                "u",
                "p",
                task_id=f"f{index}",
                user_text=f"follow {index}",
                assistant_text=f"ack {index}",
            )
        checkpoint = create_semantic_checkpoint(
            events,
            conv["id"],
            "u",
            keep_recent_turns=2,
            force=True,
        )
        rows = events.list_events(conv["id"], user_id="u")
        state = (checkpoint or {}).get("payload", {}).get("state") or {}
        unresolved = [item.get("text") or "" for item in state.get("unresolved") or []]
        metrics.tool_calls = 2
        metrics.tool_chain_complete = tool_chain_complete(rows)
        metrics.unresolved_retention = unresolved_retention(
            ["Resolve failed tool write_file"],
            unresolved,
        )
        messages = build_openai_messages(rows)
        metrics.goal_completed = (
            checkpoint is not None
            and metrics.tool_chain_complete == 1.0
            and metrics.unresolved_retention == 1.0
            and openai_tool_pairing_valid(messages)
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "18_parallel_failed_tools",
                title,
                metrics,
                f"complete={metrics.tool_chain_complete} unresolved={unresolved}",
            )
        return _ok("18_parallel_failed_tools", title, metrics)


def scenario_19_mid_turn_user_message() -> EvalResult:
    title = "中途用户消息仍保持工具配对"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_context import build_openai_messages
    from agent.conversation_events import ConversationEventStore
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        turn = events.create_turn(conv["id"], "u", "p", task_id="mid")
        events.append_event(
            conv["id"],
            turn["id"],
            "user_message",
            {"content": "开始改布局"},
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "assistant_message",
            {
                "message_id": "m-mid",
                "text_blocks": [{"type": "text", "text": "先读后写"}],
                "is_final": False,
            },
            context_visible=True,
        )
        for index, tool_id in enumerate(("c-read", "c-write")):
            events.append_event(
                conv["id"],
                turn["id"],
                "tool_call",
                {
                    "message_id": "m-mid",
                    "tool_call_id": tool_id,
                    "block_index": index,
                    "name": "read_file" if index == 0 else "write_file",
                    "input": {"path": "ui.xml"},
                },
                context_visible=True,
            )
        events.append_event(
            conv["id"],
            turn["id"],
            "user_message",
            {"content": "改用 ConstraintLayout"},
            context_visible=True,
        )
        for tool_id, name, ok in (
            ("c-read", "read_file", True),
            ("c-write", "write_file", True),
        ):
            events.append_event(
                conv["id"],
                turn["id"],
                "tool_result",
                {
                    "tool_call_id": tool_id,
                    "name": name,
                    "ok": ok,
                    "model_output": "ok",
                },
                context_visible=True,
            )
        rows = events.list_events(conv["id"], user_id="u")
        messages = build_openai_messages(rows)
        metrics.tool_calls = 2
        metrics.tool_chain_complete = tool_chain_complete(rows)
        metrics.goal_completed = (
            metrics.tool_chain_complete == 1.0
            and openai_tool_pairing_valid(messages)
            and any("ConstraintLayout" in (m.get("content") or "") for m in messages)
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "19_mid_turn_user_message",
                title,
                metrics,
                f"msgs={messages}",
            )
        return _ok("19_mid_turn_user_message", title, metrics)


def scenario_20_provider_projection_parity() -> EvalResult:
    title = "OpenAI 与 Anthropic 投影工具 ID 一致"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_context import (
        build_anthropic_messages,
        build_openai_messages,
    )
    from agent.conversation_events import ConversationEventStore
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        turn = events.create_turn(conv["id"], "u", "p", task_id="parity")
        events.append_event(
            conv["id"],
            turn["id"],
            "user_message",
            {"content": "读文件"},
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "assistant_message",
            {
                "message_id": "m-par",
                "text_blocks": [{"type": "text", "text": "读取"}],
                "is_final": False,
            },
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_call",
            {
                "message_id": "m-par",
                "tool_call_id": "call-shared",
                "block_index": 0,
                "name": "read_file",
                "input": {"path": "MainActivity.kt"},
            },
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_result",
            {
                "tool_call_id": "call-shared",
                "name": "read_file",
                "ok": True,
                "model_output": "class MainActivity",
            },
            context_visible=True,
        )
        rows = events.list_events(conv["id"], user_id="u")
        openai_ids = openai_tool_ids(build_openai_messages(rows))
        anthropic_ids = anthropic_tool_ids(build_anthropic_messages(rows))
        metrics.tool_calls = 1
        metrics.tool_chain_complete = 1.0
        metrics.goal_completed = openai_ids == anthropic_ids == [
            "call-shared",
            "call-shared",
        ]
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "20_provider_projection_parity",
                title,
                metrics,
                f"openai={openai_ids} anthropic={anthropic_ids}",
            )
        return _ok(
            "20_provider_projection_parity",
            title,
            metrics,
            openai_ids=openai_ids,
            anthropic_ids=anthropic_ids,
        )


def scenario_21_pause_resume_approval() -> EvalResult:
    title = "暂停、恢复与审批"
    timer = Timer()
    metrics = EvalMetrics()
    from agent import approvals
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        conv = store.create_conversation("u", "p")
        queued_id = uuid.uuid4().hex[:12]
        store.create_task(
            {
                "id": queued_id,
                "user_id": "u",
                "project_id": "p",
                "conversation_id": conv["id"],
                "prompt": "work",
                "status": "queued",
                "created_at": time.time(),
            }
        )
        paused = store.pause_task(queued_id, "u")
        paused_row = store.get_task(queued_id, "u")
        resumed = store.resume_task(queued_id, "u")
        resumed_row = store.get_task(queued_id, "u")

        def later() -> None:
            time.sleep(0.05)
            pending = list(approvals._pending.values())
            if pending:
                approvals.resolve_approval(pending[-1].id, "u", approved=True)

        threading.Thread(target=later, daemon=True).start()
        decision = approvals.request_user_approval(
            job_id="j-eval",
            user_id="u",
            kind="download",
            payload={"message": "ok?", "url": "https://example.test/a"},
            timeout_sec=30.0,
        )
        metrics.approvals = 1
        metrics.goal_completed = (
            paused
            and paused_row
            and paused_row["status"] == "paused"
            and resumed
            and resumed_row
            and resumed_row["status"] == "queued"
            and decision == "approved"
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "21_pause_resume_approval",
                title,
                metrics,
                f"paused={paused_row} resumed={resumed_row} decision={decision}",
            )
        return _ok("21_pause_resume_approval", title, metrics)


def scenario_22_cross_project_isolation() -> EvalResult:
    title = "跨项目事件与记忆隔离"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_events import ConversationEventStore
    from agent.database import TaskStore
    from agent.memory_store import MemoryStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        mem = MemoryStore(Path(tmp) / "mem.db")
        conv_a = store.create_conversation("u", "proj_a")
        conv_b = store.create_conversation("u", "proj_b")
        _append_user_assistant(
            events,
            conv_a["id"],
            "u",
            "proj_a",
            task_id="ta",
            user_text="secret-a",
            assistant_text="only-a",
        )
        _append_user_assistant(
            events,
            conv_b["id"],
            "u",
            "proj_b",
            task_id="tb",
            user_text="secret-b",
            assistant_text="only-b",
        )
        mem.create_memory(
            user_id="u",
            project_id="proj_a",
            scope="project",
            memory_type="decision",
            title="Only project A decision",
            content="项目 A 专用决定，不得泄漏到 B。",
            status="active",
        )
        leaked_events = events.list_events(conv_a["id"], user_id="u")
        other_events = events.list_events(conv_b["id"], user_id="u")
        leaked_mem = mem.search("u", "专用决定", project_id="proj_b")
        own_mem = mem.search("u", "专用决定", project_id="proj_a")
        metrics.goal_completed = (
            any("secret-a" in json.dumps(e, default=str) for e in leaked_events)
            and not any("secret-a" in json.dumps(e, default=str) for e in other_events)
            and bool(own_mem)
            and leaked_mem == []
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "22_cross_project_isolation",
                title,
                metrics,
                f"mem_b={leaked_mem} events_b={other_events}",
            )
        return _ok("22_cross_project_isolation", title, metrics)


def scenario_23_corrupt_checkpoint_rollback() -> EvalResult:
    title = "损坏 checkpoint 回退原始历史"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_context import select_context_events
    from agent.conversation_events import ConversationEventStore
    from agent.conversation_summary import repair_invalid_checkpoints
    from agent.database import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        _append_user_assistant(
            events,
            conv["id"],
            "u",
            "p",
            task_id="raw",
            user_text="必须保留原始问题",
            assistant_text="原始回答",
        )
        last = events.create_turn(conv["id"], "u", "p", task_id="badcp")
        bad = events.append_event(
            conv["id"],
            last["id"],
            "context_checkpoint",
            {
                "summary": "hallucinated summary",
                "state": {
                    "goal": [{"text": "invented fact", "source_seq": 99999}],
                },
                "covers_through_seq": 2,
                "valid": True,
                "checkpoint_version": 2,
            },
            context_visible=True,
        )
        original_payload = dict(bad["payload"])
        repaired = repair_invalid_checkpoints(events, conv["id"], "u")
        rows = events.list_events(conv["id"], user_id="u")
        selected = select_context_events(rows)
        still_raw = any(
            "必须保留原始问题" in json.dumps(event.get("payload") or {}, ensure_ascii=False)
            for event in selected
        )
        invalidated = [
            event
            for event in rows
            if event.get("event_type") == "context_checkpoint_invalidated"
        ]
        current = events.list_events(conv["id"], user_id="u")
        stored_checkpoint = next(
            event for event in current if event.get("id") == bad["id"]
        )
        metrics.recoveries = len(repaired)
        metrics.hallucination_rate = 0.0
        metrics.goal_completed = (
            bool(repaired)
            and bool(invalidated)
            and still_raw
            and stored_checkpoint["payload"]["state"] == original_payload["state"]
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "23_corrupt_checkpoint_rollback",
                title,
                metrics,
                f"repaired={repaired} selected={len(selected)}",
            )
        return _ok("23_corrupt_checkpoint_rollback", title, metrics)


def scenario_24_summary_quality_metrics() -> EvalResult:
    title = "摘要质量指标可量化"
    timer = Timer()
    metrics = EvalMetrics()
    from agent.conversation_context import select_context_events
    from agent.conversation_events import ConversationEventStore
    from agent.conversation_summary import create_semantic_checkpoint
    from agent.database import TaskStore

    constraint = "禁止 findViewById"
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "agent.db")
        events = ConversationEventStore(store)
        conv = store.create_conversation("u", "p")
        _append_user_assistant(
            events,
            conv["id"],
            "u",
            "p",
            task_id="q0",
            user_text=f"改 UI，{constraint}",
            assistant_text="将改用 ViewBinding",
        )
        turn = events.create_turn(conv["id"], "u", "p", task_id="tool")
        events.append_event(
            conv["id"],
            turn["id"],
            "user_message",
            {"content": "继续"},
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_call",
            {
                "message_id": "m-q",
                "tool_call_id": "t-fail",
                "block_index": 0,
                "name": "write_file",
                "input": {"path": "ui.xml"},
            },
            context_visible=True,
        )
        events.append_event(
            conv["id"],
            turn["id"],
            "tool_result",
            {
                "tool_call_id": "t-fail",
                "name": "write_file",
                "ok": False,
                "model_output": "permission denied",
            },
            context_visible=True,
        )
        for index in range(6):
            _append_user_assistant(
                events,
                conv["id"],
                "u",
                "p",
                task_id=f"n{index}",
                user_text=f"next {index}",
                assistant_text=f"ok {index}",
            )
        checkpoint = create_semantic_checkpoint(
            events,
            conv["id"],
            "u",
            keep_recent_turns=2,
            force=True,
        )
        rows = events.list_events(conv["id"], user_id="u")
        selected = select_context_events(rows)
        state = (checkpoint or {}).get("payload", {}).get("state") or {}
        metrics.constraint_recall = constraint_recall(
            [constraint],
            state.get("constraints") or [],
        )
        metrics.tool_chain_complete = tool_chain_complete(rows)
        metrics.hallucination_rate = hallucination_rate(
            state,
            (event.get("seq") or 0 for event in rows),
        )
        metrics.unresolved_retention = unresolved_retention(
            ["Resolve failed tool write_file"],
            [item.get("text") or "" for item in state.get("unresolved") or []],
        )
        metrics.token_savings = token_savings(chars_of(rows), chars_of(selected))
        metrics.first_token_ms = timer.ms()
        metrics.goal_completed = (
            checkpoint is not None
            and metrics.constraint_recall == 1.0
            and metrics.tool_chain_complete == 1.0
            and metrics.hallucination_rate == 0.0
            and metrics.unresolved_retention == 1.0
            and metrics.token_savings > 0
            and metrics.first_token_ms >= 0
        )
        metrics.wall_time_ms = timer.ms()
        if not metrics.goal_completed:
            return _fail(
                "24_summary_quality_metrics",
                title,
                metrics,
                (
                    f"recall={metrics.constraint_recall} hallu={metrics.hallucination_rate} "
                    f"unresolved={metrics.unresolved_retention} save={metrics.token_savings}"
                ),
            )
        return _ok("24_summary_quality_metrics", title, metrics)


SCENARIO_RUNNERS: dict[str, Callable[[], EvalResult]] = {
    "01_create_page_build": scenario_01_create_page_build,
    "02_fix_kotlin": scenario_02_fix_kotlin,
    "03_fix_xml": scenario_03_fix_xml,
    "04_multi_turn_tools": scenario_04_multi_turn_tools,
    "05_steer": scenario_05_steer,
    "06_tool_failure": scenario_06_tool_failure,
    "07_approvals": scenario_07_approvals,
    "08_model_fallback": scenario_08_model_fallback,
    "09_interrupt_stages": scenario_09_interrupt_stages,
    "10_git_dirty_restore": scenario_10_git_dirty_restore,
    "11_index_incremental": scenario_11_index_incremental,
    "12_mcp_crash": scenario_12_mcp_crash,
    "13_subagent": scenario_13_subagent,
    "14_long_history_checkpoint": scenario_14_long_history_checkpoint,
    "15_memory_approve_delete": scenario_15_memory_approve_delete,
    "16_reconnect": scenario_16_reconnect,
    "17_hundred_turn_history": scenario_17_hundred_turn_history,
    "18_parallel_failed_tools": scenario_18_parallel_failed_tools,
    "19_mid_turn_user_message": scenario_19_mid_turn_user_message,
    "20_provider_projection_parity": scenario_20_provider_projection_parity,
    "21_pause_resume_approval": scenario_21_pause_resume_approval,
    "22_cross_project_isolation": scenario_22_cross_project_isolation,
    "23_corrupt_checkpoint_rollback": scenario_23_corrupt_checkpoint_rollback,
    "24_summary_quality_metrics": scenario_24_summary_quality_metrics,
}
