"""E2E harness: real agent service + scenario stub model + real HTTP/WS client.

Everything runs against throwaway directories: a temp AGENT_DATA_DIR,
AGENT_WORKSPACES_DIR and AGENT_BUILDS_DIR, plus a fake ANDROID_HOME so the
build scenarios can run the real build pipeline against a scripted gradlew.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
STUB_SCRIPT = Path(__file__).resolve().parent / "stub_scenario_model.py"

TERMINAL_STATUSES = {"succeeded", "failed", "canceled", "interrupted"}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_tcp(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"port {port} never became reachable")


def load_scenarios(directory: Path = SCENARIO_DIR) -> dict[str, dict[str, Any]]:
    scenarios: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        scenarios[str(data.get("id") or path.stem)] = data
    return scenarios


class E2EStack:
    """Spawns the scenario stub model and the real agent service."""

    def __init__(self, python_bin: str = ".venv/bin/python", keep: bool = False) -> None:
        self.python_bin = str(REPO_ROOT / python_bin) if not Path(python_bin).is_absolute() else python_bin
        self.keep = keep
        self.tmp_root = Path(tempfile.mkdtemp(prefix="agent-e2e-"))
        self.data_dir = self.tmp_root / "data"
        self.workspaces_dir = self.tmp_root / "workspaces"
        self.builds_dir = self.tmp_root / "builds"
        self.fake_sdk = self.tmp_root / "fake-android-sdk"
        for directory in (self.data_dir, self.workspaces_dir, self.builds_dir, self.fake_sdk):
            directory.mkdir(parents=True, exist_ok=True)
        self.stub_port = free_port()
        self.agent_port = free_port()
        self.stub_process: subprocess.Popen | None = None
        self.agent_process: subprocess.Popen | None = None
        self.agent_log_path = self.tmp_root / "agent-service.log"
        self.stub_log_path = self.tmp_root / "stub-model.log"

    def start(self) -> None:
        env = {
            **os.environ,
            "NO_PROXY": "*",
            "no_proxy": "*",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
            "PYTHONUNBUFFERED": "1",
        }
        self.stub_process = subprocess.Popen(
            [self.python_bin, str(STUB_SCRIPT)],
            env={**env, "AGENT_E2E_STUB_PORT": str(self.stub_port), "AGENT_E2E_SCENARIO_DIR": str(SCENARIO_DIR)},
            stdout=open(self.stub_log_path, "wb"),
            stderr=subprocess.STDOUT,
        )
        wait_tcp(self.stub_port)

        self.agent_process = subprocess.Popen(
            [
                self.python_bin,
                "-m",
                "agent",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.agent_port),
            ],
            cwd=str(REPO_ROOT),
            env={
                **env,
                "AGENT_DATA_DIR": str(self.data_dir),
                "AGENT_WORKSPACES_DIR": str(self.workspaces_dir),
                "AGENT_BUILDS_DIR": str(self.builds_dir),
                "AGENT_BASE_URL": f"http://127.0.0.1:{self.stub_port}",
                "AGENT_API_KEY": "sk-e2e-stub",
                "AGENT_CMD_SANDBOX": "0",
                "AGENT_GUEST_MESSAGE_LIMIT": "1000",
                "AGENT_MAX_REQUESTS_PER_MINUTE": "100000",
                "ANDROID_HOME": str(self.fake_sdk),
            },
            stdout=open(self.agent_log_path, "wb"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 180
        base = f"http://127.0.0.1:{self.agent_port}"
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{base}/healthz", timeout=1.0, trust_env=False)
                if response.status_code == 200:
                    return
            except httpx.HTTPError as exc:
                if os.environ.get("E2E_DEBUG"):
                    print(f"[e2e-debug] healthz: {type(exc).__name__}: {exc}", flush=True)
            if self.agent_process.poll() is not None:
                raise RuntimeError(
                    f"agent service exited early:\n{self.agent_log_path.read_text(encoding='utf-8', errors='replace')[-4000:]}"
                )
            time.sleep(0.2)
        raise TimeoutError(
            "agent service did not become healthy within 180s; log tail:\n"
            + self.agent_log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        )

    def stop(self) -> None:
        for process in (self.agent_process, self.stub_process):
            if process is None:
                continue
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if not self.keep:
            import shutil

            shutil.rmtree(self.tmp_root, ignore_errors=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.agent_port}"


class ApprovalPump:
    """Simulates a user clicking allow: polls pending approvals and approves."""

    def __init__(self, client: "E2EClient", interval: float = 0.25) -> None:
        self.client = client
        self.interval = interval
        self.jobs: set[str] = set()
        self.decisions: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, job_id: str) -> None:
        self.jobs.add(job_id)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.is_set():
            for job_id in list(self.jobs):
                try:
                    for approval in self.client.pending_approvals(job_id):
                        result = self.client.decide_approval(job_id, approval["id"], approved=True)
                        if result:
                            self.decisions.append(result)
                except Exception:
                    pass
            self._stop.wait(self.interval)


class WsCollector:
    """Collects job WS events on a background thread."""

    def __init__(self, client: "E2EClient", job_id: str, after_event_id: int | None = None) -> None:
        self.client = client
        self.job_id = job_id
        self.after_event_id = after_event_id
        self.events: list[dict[str, Any]] = []
        self.done: dict[str, Any] | None = None
        self.errors: list[str] = []
        self.connected = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "WsCollector":
        self._thread.start()
        self.connected.wait(timeout=10)
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def _run(self) -> None:
        from websockets.sync.client import connect

        uri = f"ws://127.0.0.1:{self.client.stack.agent_port}/api/ws/jobs/{self.job_id}"
        if self.after_event_id is not None:
            uri += f"?after_event_id={self.after_event_id}"
        try:
            connection = connect(
                uri,
                additional_headers={"Authorization": f"Bearer {self.client.token}"},
                open_timeout=10,
                close_timeout=2,
                proxy=None,  # never route ws://127.0.0.1 through env proxies
            )
        except Exception as exc:  # noqa: BLE001 - record and surface in assertions
            self.errors.append(f"connect failed: {exc}")
            return
        self.connected.set()
        try:
            while not self._stop.is_set():
                try:
                    raw = connection.recv(timeout=0.5)
                except TimeoutError:
                    continue
                if raw is None:
                    break
                message = json.loads(raw)
                if message.get("type") == "done":
                    self.done = message
                    break
                self.events.append(message)
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"recv failed: {exc}")
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


class E2EClient:
    """Real HTTP/WS client driving the agent service."""

    def __init__(self, stack: E2EStack) -> None:
        self.stack = stack
        self.token = ""
        self.user_id = ""
        self.http = httpx.Client(base_url=stack.base_url, timeout=30.0, trust_env=False)

    def close(self) -> None:
        self.http.close()

    # —— auth / projects ——

    def guest_login(self) -> None:
        response = self.http.post(
            "/api/auth/guest",
            json={
                "device": {
                    "device_id": "e2e-device",
                    "device_name": "E2E Harness",
                    "device_type": "desktop",
                    "platform": "E2E",
                }
            },
        )
        response.raise_for_status()
        payload = response.json()
        self.token = payload["token"]
        self.user_id = payload["user_id"]
        self.http.headers["Authorization"] = f"Bearer {self.token}"

    def create_project(self, name: str) -> dict[str, Any]:
        response = self.http.post("/api/projects", json={"name": name})
        response.raise_for_status()
        return response.json()

    def workspace_path(self, project_id: str) -> Path:
        return self.stack.workspaces_dir / self.user_id / project_id

    def install_setup_files(self, project_id: str, setup_files: dict[str, Any] | None) -> None:
        if not setup_files:
            return
        workspace = self.workspace_path(project_id)
        for rel_path, spec in setup_files.items():
            if isinstance(spec, str):
                spec = {"content": spec}
            target = workspace / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(spec.get("content") or ""), encoding="utf-8")
            if spec.get("executable"):
                target.chmod(0o755)

    # —— ask / job lifecycle ——

    def ask(
        self,
        project_id: str,
        prompt: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt}
        if conversation_id:
            body["conversation_id"] = conversation_id
        response = self.http.post(f"/api/projects/{project_id}/ask", json=body)
        response.raise_for_status()
        return response.json()["job"]

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self.http.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        return response.json()["job"]

    def wait_job(
        self,
        job_id: str,
        timeout: float = 90.0,
        until: set[str] | None = None,
    ) -> dict[str, Any]:
        wanted = until or TERMINAL_STATUSES
        deadline = time.monotonic() + timeout
        job: dict[str, Any] = {}
        while time.monotonic() < deadline:
            job = self.get_job(job_id)
            if job.get("status") in wanted:
                return job
            time.sleep(0.2)
        raise TimeoutError(
            f"job {job_id} did not reach {sorted(wanted)} within {timeout}s (status={job.get('status')})"
        )

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        response = self.http.post(f"/api/jobs/{job_id}/cancel")
        response.raise_for_status()
        return response.json()["job"]

    def pause_job(self, job_id: str) -> dict[str, Any]:
        response = self.http.post(f"/api/jobs/{job_id}/pause")
        response.raise_for_status()
        return response.json()["job"]

    def resume_job(self, job_id: str) -> dict[str, Any]:
        response = self.http.post(f"/api/jobs/{job_id}/resume")
        response.raise_for_status()
        return response.json()["job"]

    # —— approvals ——

    def pending_approvals(self, job_id: str) -> list[dict[str, Any]]:
        response = self.http.get(f"/api/jobs/{job_id}/approvals")
        response.raise_for_status()
        return response.json().get("approvals") or []

    def decide_approval(self, job_id: str, approval_id: str, approved: bool) -> dict[str, Any] | None:
        response = self.http.post(
            f"/api/jobs/{job_id}/approvals/{approval_id}",
            json={"approved": approved},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json().get("approval")

    def wait_approval(self, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            approvals = self.pending_approvals(job_id)
            if approvals:
                return approvals[0]
            time.sleep(0.15)
        raise TimeoutError(f"no approval appeared for job {job_id}")

    # —— conversation events ——

    def conversation_events(self, conversation_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        cursor = after_seq
        while True:
            response = self.http.get(
                f"/api/conversations/{conversation_id}/events",
                params={"after_seq": cursor, "limit": 500},
            )
            response.raise_for_status()
            payload = response.json()
            page = payload.get("events") or []
            events.extend(page)
            if not payload.get("has_more"):
                return events
            cursor = payload.get("next_after_seq") or cursor

    def wait_event(
        self,
        conversation_id: str,
        predicate,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            events = self.conversation_events(conversation_id)
            for event in events[seen:]:
                if predicate(event):
                    return event
            seen = len(events)
            time.sleep(0.2)
        raise TimeoutError("expected conversation event did not appear")

    # —— build log ——

    def build_log_page(self, job_id: str, offset: int, limit: int) -> dict[str, Any]:
        response = self.http.get(
            f"/api/jobs/{job_id}/log",
            params={"offset": offset, "limit": limit},
        )
        if response.status_code == 404:
            return {"content": "", "has_more": False, "total_size": 0}
        response.raise_for_status()
        return response.json()

    def job_log_full(self, job_id: str) -> str:
        payload = self.build_log_page(job_id, 0, 512_000)
        if not payload.get("has_more"):
            return str(payload.get("content") or "")
        parts = [str(payload.get("content") or "")]
        offset = len(parts[0])
        while True:
            page = self.build_log_page(job_id, offset, 512_000)
            content = str(page.get("content") or "")
            if not content:
                break
            parts.append(content)
            offset += len(content)
            if not page.get("has_more"):
                break
        return "".join(parts)
