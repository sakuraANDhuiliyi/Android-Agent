from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.config import Settings, load_settings
from agent.loop import run_agent
from agent.paths import workspace_path
from agent.project import load_project_meta


@dataclass
class Job:
    id: str
    user_id: str
    project_id: str
    prompt: str
    status: str = "pending"
    events: list[dict[str, Any]] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None


_lock = threading.Lock()
_jobs: dict[str, Job] = {}


def _append_event(job: Job, event_type: str, **data: Any) -> dict[str, Any]:
    event = {"type": event_type, "ts": time.time(), **data}
    with _lock:
        job.events.append(event)
    return event


def get_job(job_id: str, *, user_id: str | None = None) -> Job | None:
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return None
    if user_id is not None and job.user_id != user_id:
        return None
    return job


def list_jobs(
    user_id: str,
    project_id: str | None = None,
) -> list[Job]:
    with _lock:
        jobs = [job for job in _jobs.values() if job.user_id == user_id]
    if project_id:
        jobs = [job for job in jobs if job.project_id == project_id]
    jobs.sort(key=lambda job: job.created_at, reverse=True)
    return jobs


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "user_id": job.user_id,
        "project_id": job.project_id,
        "prompt": job.prompt,
        "status": job.status,
        "events": job.events,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def start_ask_job(
    user_id: str,
    project_id: str,
    prompt: str,
    settings: Settings | None = None,
) -> Job:
    load_project_meta(user_id, project_id)
    settings = settings or load_settings()

    job = Job(
        id=uuid.uuid4().hex[:12],
        user_id=user_id,
        project_id=project_id,
        prompt=prompt,
    )
    with _lock:
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job.id, user_id, project_id, prompt, settings),
        daemon=True,
    )
    thread.start()
    return job


def _run_job(
    job_id: str,
    user_id: str,
    project_id: str,
    prompt: str,
    settings: Settings,
) -> None:
    job = get_job(job_id)
    if not job:
        return

    with _lock:
        job.status = "running"
    _append_event(
        job,
        "started",
        user_id=user_id,
        project_id=project_id,
        prompt=prompt,
    )

    def on_event(event_type: str, data: Any) -> None:
        payload = data if isinstance(data, dict) else {"message": str(data)}
        _append_event(job, event_type, **payload)

    workspace = workspace_path(user_id, project_id)
    try:
        answer = run_agent(
            settings,
            workspace,
            user_id,
            project_id,
            prompt,
            on_event=on_event,
        )
        with _lock:
            job.status = "completed"
            job.result = answer
            job.finished_at = time.time()
        _append_event(job, "completed", result=answer)
    except Exception as e:
        with _lock:
            job.status = "failed"
            job.error = str(e)
            job.finished_at = time.time()
        _append_event(job, "failed", error=str(e))


def wait_for_job(job_id: str, timeout: float | None = None) -> Job | None:
    deadline = None if timeout is None else time.time() + timeout
    while True:
        job = get_job(job_id)
        if not job:
            return None
        if job.status in {"completed", "failed"}:
            return job
        if deadline is not None and time.time() >= deadline:
            return job
        time.sleep(0.2)
