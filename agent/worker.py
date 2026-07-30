from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import replace
from typing import Any, Callable

from agent.config import Settings
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore

logger = logging.getLogger(__name__)

LEASE_SECONDS = 300.0
POLL_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 30.0

RunJobFn = Callable[..., None]
ProjectLockReleaseFn = Callable[[str, str], None]


class PauseRequested(RuntimeError):
    """Raised when a running task is asked to pause."""


class TaskLeaseLost(RuntimeError):
    """Raised when a worker no longer owns the task execution lease."""


class _FollowUpRequested(RuntimeError):
    """Internal signal used by the worker to create a follow-up turn."""

    def __init__(self, prompt: str, payload: dict[str, Any]) -> None:
        self.prompt = prompt
        self.payload = payload


class TaskWorker:
    """Persistent worker that claims tasks from SQLite and executes them."""

    def __init__(
        self,
        store: TaskStore,
        run_fn: RunJobFn,
        settings: Settings,
        *,
        lease_seconds: float = LEASE_SECONDS,
        poll_interval: float = POLL_INTERVAL,
        project_lock_release: ProjectLockReleaseFn | None = None,
    ):
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self.store = store
        self.run_fn = run_fn
        self.settings = settings
        self.lease_seconds = lease_seconds
        self.poll_interval = poll_interval
        self.project_lock_release = project_lock_release
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running_task: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(), daemon=True)
        self._thread.start()

    def stop(self, wait: bool = False, timeout: float | None = None) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            try:
                self._thread.join(timeout=timeout)
            except AttributeError:
                # Tests may patch threading.Thread to a synchronous class
                # without a join() method; in that case the thread body has
                # already executed and there is nothing to wait for.
                pass

    def run_once(self) -> dict[str, Any] | None:
        """Claim and execute a single task, then return. For tests."""
        task = self.store.claim_next_task(self.worker_id, self.lease_seconds)
        if task is None:
            return None
        with self._lock:
            self._running_task = task
        try:
            self._execute(task)
        finally:
            with self._lock:
                self._running_task = None
        return task

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self.store.claim_next_task(self.worker_id, self.lease_seconds)
                if task is None:
                    time.sleep(self.poll_interval)
                    continue
                with self._lock:
                    self._running_task = task
                try:
                    self._execute(task)
                finally:
                    with self._lock:
                        self._running_task = None
            except sqlite3.OperationalError as exc:
                if (
                    "unable to open database file" in str(exc).lower()
                    and not self.store.db_path.parent.exists()
                ):
                    logger.info("Worker store disappeared; stopping %s", self.worker_id)
                    return
                logger.exception("Worker loop database error")
                time.sleep(self.poll_interval)
            except Exception:
                logger.exception("Worker loop error")
                time.sleep(self.poll_interval)

    def _execute(self, task: dict[str, Any]) -> None:
        try:
            self._execute_claimed(task)
        finally:
            if self.project_lock_release is not None:
                self.project_lock_release(task["user_id"], task["project_id"])

    def _execute_claimed(self, task: dict[str, Any]) -> None:
        claim_token = str(task.get("claim_token") or "")
        if not claim_token:
            self.store.release_task(
                task["id"],
                self.worker_id,
                "failed",
                error_message="任务租约缺少 fencing token",
            )
            return
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat_interval = max(
            0.01,
            min(HEARTBEAT_INTERVAL, self.lease_seconds / 3),
        )

        def heartbeat_loop() -> None:
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    renewed = self.store.heartbeat_task(
                        task["id"],
                        self.worker_id,
                        self.lease_seconds,
                        claim_token=claim_token,
                    )
                except Exception:
                    logger.exception("Heartbeat failed for task %s", task["id"])
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name=f"task-heartbeat-{task['id'][:8]}",
            daemon=True,
        )
        heartbeat_thread.start()

        def check_lease() -> None:
            if lease_lost.is_set() or not self.store.claim_is_valid(
                task["id"],
                self.worker_id,
                claim_token,
            ):
                lease_lost.set()
                raise TaskLeaseLost("任务租约已失效，停止旧执行器写入")

        event_store = ConversationEventStore(self.store)
        try:
            turn = event_store.get_turn_by_task(task["id"], user_id=task["user_id"])
            if not turn:
                logger.error("Task %s has no turn; releasing as failed", task["id"])
                self.store.release_task(
                    task["id"],
                    self.worker_id,
                    "failed",
                    claim_token=claim_token,
                    error_message="找不到对应的 turn",
                )
                return
            turn_id = turn["id"]
            conversation_id = task["conversation_id"]
            if not conversation_id:
                logger.error("Task %s has no conversation_id; releasing as failed", task["id"])
                self.store.release_task(
                    task["id"],
                    self.worker_id,
                    "failed",
                    claim_token=claim_token,
                    error_message="缺少 conversation_id",
                )
                return

            settings = self._task_settings(task)
            history_events = event_store.list_events(
                conversation_id,
                user_id=task["user_id"],
            )
            prior_turn_count = len(
                {
                    event["turn_id"]
                    for event in history_events
                    if event["turn_id"] != turn_id
                }
            )
            context = task.get("context") or {}
            recovery_replays = context.get("recovery_replays")
            recovery_mode = bool(
                context.get("recovery_mode") or task.get("recovery_of_task_id")
            )
            check_lease()
            self.run_fn(
                task["id"],
                task["user_id"],
                task["project_id"],
                conversation_id,
                turn_id,
                task["prompt"],
                settings,
                history_events,
                prior_turn_count,
                recovery_replays,
                recovery_mode,
                check_lease,
                self.project_lock_release is not None,
            )
        except PauseRequested:
            self.store.release_task(
                task["id"],
                self.worker_id,
                "paused",
                claim_token=claim_token,
            )
            try:
                event_store.update_turn_status(
                    turn_id,
                    "paused",
                    user_id=task["user_id"],
                    finished_at=time.time(),
                )
            except Exception:
                logger.exception("Failed to set turn status to paused for task %s", task["id"])
            return
        except TaskLeaseLost:
            logger.warning("Task %s stopped after losing its lease", task["id"])
            return
        except Exception as exc:
            logger.exception("Task %s execution failed", task["id"])
            try:
                self.store.release_task(
                    task["id"],
                    self.worker_id,
                    "failed",
                    claim_token=claim_token,
                    error_message=str(exc),
                )
            except Exception:
                logger.exception("Failed to release task %s after error", task["id"])
            return
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(1.0, heartbeat_interval * 2))

        fresh = self.store.get_task(task["id"], task["user_id"])
        if (
            fresh
            and fresh.get("claim_owner") == self.worker_id
            and fresh.get("claim_token") == claim_token
        ):
            final_status = (
                "succeeded" if fresh["status"] == "running" else fresh["status"]
            )
            released = self.store.release_task(
                task["id"],
                self.worker_id,
                final_status,
                claim_token=claim_token,
            )
            if released:
                self._create_follow_ups(task)

    def _create_follow_ups(self, task: dict[str, Any]) -> None:
        messages = self.store.get_pending_messages(task["id"], types=["follow_up"])
        for msg in messages:
            self.store.consume_message(msg["id"])
            payload = msg.get("payload") or {}
            prompt = (
                payload.get("prompt")
                or payload.get("text")
                or payload.get("content")
                or ""
            )
            if not prompt:
                continue
            try:
                from agent.jobs import start_ask_job

                start_ask_job(
                    task["user_id"],
                    task["project_id"],
                    str(prompt),
                    self._task_settings(task),
                    conversation_id=task["conversation_id"],
                    continue_session=True,
                    reset_session=False,
                )
            except Exception:
                logger.exception("Failed to create follow-up for task %s", task["id"])

    def _task_settings(self, task: dict[str, Any]) -> Settings:
        provider = task.get("provider") or self.settings.provider
        model = task.get("model") or self.settings.model
        if provider != self.settings.provider or model != self.settings.model:
            return replace(self.settings, provider=provider, model=model)
        return self.settings


def start_default_worker(
    store: TaskStore,
    settings: Settings,
) -> TaskWorker:
    """Create and start the default persistent worker."""
    from agent.jobs import _release_project_lock, _run_job

    worker = TaskWorker(
        store,
        _run_job,
        settings,
        project_lock_release=_release_project_lock,
    )
    worker.start()
    return worker
