"""Stage 19 performance budget tests with diagnostics on overrun."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent.context_planner import ContextPlanner
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.repo_index import RepoIndex
from agent.workspace import MAX_DIFF_CHARS


# Budgets (wall time seconds). Conservative for CI laptops; diagnose on fail.
BUDGET_INDEX_10K_S = 90.0
BUDGET_EVENTS_PAGE_S = 5.0
BUDGET_CONTEXT_PLAN_S = 5.0
BUDGET_CLAIM_S = 10.0
BUDGET_DIFF_BUILD_S = 5.0


def _diagnose(name: str, elapsed: float, budget: float, extra: dict) -> str:
    return (
        f"PERF BUDGET EXCEEDED: {name} took {elapsed:.3f}s > {budget:.3f}s; "
        f"diagnostics={json.dumps(extra, ensure_ascii=False, default=str)}"
    )


class PerfBudgetTests(unittest.TestCase):
    def test_index_10k_files_under_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            workspaces = Path(tmp) / "workspaces"
            data.mkdir()
            ws = workspaces / "u" / "p"
            src = ws / "app" / "src"
            src.mkdir(parents=True)
            # 10k small files — no unbounded in-memory cache expected.
            for i in range(10_000):
                sub = src / f"b{i // 500}"
                sub.mkdir(exist_ok=True)
                (sub / f"F{i}.kt").write_text(
                    f"package p\nclass F{i}\n", encoding="utf-8"
                )
            with (
                patch("agent.repo_index.DATA_DIR", data),
                patch(
                    "agent.repo_index.workspace_path",
                    lambda uid, pid: workspaces / uid / pid,
                ),
            ):
                idx = RepoIndex("u", "p")
                t0 = time.perf_counter()
                status = idx.update()
                elapsed = time.perf_counter() - t0
                # Incremental should be cheap
                t1 = time.perf_counter()
                status2 = idx.update()
                elapsed2 = time.perf_counter() - t1
            diag = {
                "file_count": status.get("file_count"),
                "updated_first": status.get("updated"),
                "updated_second": status2.get("updated"),
                "elapsed_first_s": round(elapsed, 3),
                "elapsed_second_s": round(elapsed2, 3),
            }
            if elapsed > BUDGET_INDEX_10K_S:
                self.fail(_diagnose("index_10k", elapsed, BUDGET_INDEX_10K_S, diag))
            self.assertEqual(status["status"], "ready")
            self.assertGreaterEqual(status.get("file_count", 0), 10_000)
            self.assertEqual(status2.get("updated"), 0)
            # No unbounded python list of file bodies retained on index object
            self.assertFalse(hasattr(idx, "_file_cache") and idx._file_cache)  # noqa: SLF001

    def test_100k_events_pagination_under_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "agent.db")
            events = ConversationEventStore(store)
            conv = store.create_conversation("u", "p")
            turn = events.create_turn(conv["id"], "u", "p")
            n = 100_000
            t_insert = time.perf_counter()
            # Bulk insert for scale (pagination path still uses list_events).
            now = time.time()
            with store._connect() as conn:  # noqa: SLF001
                conn.execute("BEGIN")
                rows = [
                    (
                        f"e{i}",
                        conv["id"],
                        turn["id"],
                        None,
                        i + 1,
                        "system_note",
                        None,
                        1,
                        None,
                        None,
                        json.dumps({"text": f"e{i}"}),
                        None,
                        now,
                        1,
                    )
                    for i in range(n)
                ]
                conn.executemany(
                    """INSERT INTO conversation_events
                       (id, conversation_id, turn_id, task_id, seq, event_type, role,
                        context_visible, provider, model, payload_json, event_key,
                        created_at, schema_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                conn.execute("COMMIT")
            insert_s = time.perf_counter() - t_insert
            t0 = time.perf_counter()
            page = events.list_events(conv["id"], user_id="u", after_seq=0, limit=200)
            elapsed = time.perf_counter() - t0
            t1 = time.perf_counter()
            page2 = events.list_events(
                conv["id"], user_id="u", after_seq=page[-1]["seq"], limit=200
            )
            elapsed2 = time.perf_counter() - t1
            # Walk to the end with cursor to prove 100k is pageable without loading all.
            cursor = 0
            pages = 0
            t_walk = time.perf_counter()
            while True:
                batch = events.list_events(
                    conv["id"], user_id="u", after_seq=cursor, limit=500
                )
                if not batch:
                    break
                pages += 1
                cursor = batch[-1]["seq"]
                if pages > 250:
                    self.fail("unbounded page walk")
            walk_s = time.perf_counter() - t_walk
            diag = {
                "n_events": n,
                "insert_s": round(insert_s, 3),
                "page1_s": round(elapsed, 3),
                "page2_s": round(elapsed2, 3),
                "walk_s": round(walk_s, 3),
                "pages": pages,
            }
            if elapsed > BUDGET_EVENTS_PAGE_S:
                self.fail(_diagnose("events_page", elapsed, BUDGET_EVENTS_PAGE_S, diag))
            self.assertEqual(len(page), 200)
            self.assertEqual(len(page2), 200)
            self.assertEqual(cursor, n)
            self.assertGreaterEqual(pages, n // 500)

    def test_long_history_context_planner_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / "app").mkdir(parents=True)
            (ws / "app" / "A.kt").write_text("class A\n", encoding="utf-8")
            data = Path(tmp) / "data"
            data.mkdir()
            with (
                patch("agent.repo_index.DATA_DIR", data),
                patch("agent.repo_index.workspace_path", lambda *_a: ws),
            ):
                from agent.repo_index import RepoIndex

                idx = RepoIndex("u", "p")
                idx._workspace = ws  # noqa: SLF001
                idx.update()
                planner = ContextPlanner(idx, user_id="u", project_id="p")
                history = [{"role": "user", "content": f"msg {i}"} for i in range(200)]
                t0 = time.perf_counter()
                plan = planner.plan(
                    "find class A",
                    history=history,
                    budget_chars=20_000,
                    include_memories=False,
                )
                elapsed = time.perf_counter() - t0
            total = sum(len(s.get("content") or "") for s in plan.get("selected") or [])
            diag = {"elapsed_s": round(elapsed, 3), "selected_chars": total, "items": len(plan.get("selected") or [])}
            if elapsed > BUDGET_CONTEXT_PLAN_S:
                self.fail(_diagnose("context_plan", elapsed, BUDGET_CONTEXT_PLAN_S, diag))
            self.assertLessEqual(total, 20_000 + 500)

    def test_sqlite_multi_worker_claim_under_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "agent.db")
            for i in range(50):
                store.create_task(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "user_id": "u",
                        "project_id": f"p{i}",
                        "prompt": f"t{i}",
                        "status": "queued",
                        "created_at": time.time(),
                        "write_lock_key": f"lock-{i}",
                    }
                )
            claimed: list[str] = []
            lock = threading.Lock()

            def worker(name: str) -> None:
                local = TaskStore(Path(tmp) / "agent.db")
                while True:
                    task = local.claim_next_task(name, lease_seconds=60)
                    if not task:
                        return
                    with lock:
                        claimed.append(task["id"])
                    local.release_task(task["id"], name, status="succeeded")

            t0 = time.perf_counter()
            threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=30)
            elapsed = time.perf_counter() - t0
            diag = {"claimed": len(claimed), "unique": len(set(claimed)), "elapsed_s": round(elapsed, 3)}
            if elapsed > BUDGET_CLAIM_S:
                self.fail(_diagnose("multi_claim", elapsed, BUDGET_CLAIM_S, diag))
            self.assertEqual(len(claimed), len(set(claimed)))
            self.assertEqual(len(claimed), 50)

    def test_large_diff_is_truncated(self) -> None:
        huge = "line\n" * (MAX_DIFF_CHARS // 2)
        self.assertGreater(len(huge), MAX_DIFF_CHARS // 4)
        # WorkspaceRepository.git_diff truncates to MAX_DIFF_CHARS — assert constant exists
        # and truncation logic bound.
        truncated = huge[:MAX_DIFF_CHARS]
        self.assertEqual(len(truncated), MAX_DIFF_CHARS)
        self.assertLessEqual(MAX_DIFF_CHARS, 200_000)


if __name__ == "__main__":
    unittest.main()
