from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAX_MODEL_OUTPUT_CHARS = 100_000
DEFAULT_TIMEOUT_SECONDS = 300.0
TRUNCATION_MARKER = "\n... (输出已截断，完整内容见任务日志)"


class ProcessRuntimeError(RuntimeError):
    """Base class for process runner errors."""


class CancellationRequested(ProcessRuntimeError):
    """Raised when a process is cancelled by the caller."""


class ProcessStartError(ProcessRuntimeError):
    """Raised when a process cannot be started."""


class ProcessTimeoutError(ProcessRuntimeError):
    """Raised when a process exceeds its timeout."""


@dataclass
class CancelToken:
    """Thread-safe token that can be used to cancel a running process."""

    _event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise CancellationRequested("进程已取消")


@dataclass
class ProcessResult:
    """Standardized result of a process execution."""

    ok: bool
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool
    full_output_path: Path | None
    error_type: str | None = None


DEFAULT_ALLOWED_ENV_VARS = (
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "JAVA_HOME",
    "ANDROID_HOME",
    "ANDROID_SDK_ROOT",
)

_SENSITIVE_ENV_KEYS = frozenset({
    "APIKEY",
    "APITOKEN",
    "AUTHORIZATION",
    "TOKEN",
    "ACCESSTOKEN",
    "REFRESHTOKEN",
    "SECRET",
    "CLIENTSECRET",
    "PASSWORD",
    "AGENTAPIKEY",
    "DEEPSEEKAPIKEY",
    "ANTHROPICAPIKEY",
    "TAVILYAPIKEY",
    "OPENAIAPIKEY",
    "OPENAIKEY",
    "GEMINIAPIKEY",
    "GOOGLEAPIKEY",
})

_active: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


def _normalize_env_key(key: str) -> str:
    return "".join(char for char in key.upper() if char.isalnum())


def _is_sensitive_env_key(key: str) -> bool:
    norm = _normalize_env_key(key)
    if norm in _SENSITIVE_ENV_KEYS:
        return True
    # Catch provider-specific *API_KEY / *SECRET / *TOKEN variants.
    if norm.endswith("APIKEY") or norm.endswith("SECRET"):
        return True
    if norm.endswith("TOKEN") and norm not in {"TERM"}:
        return True
    return False


def build_minimal_env(
    extra: dict[str, str] | None = None,
    allowed_vars: tuple[str, ...] = DEFAULT_ALLOWED_ENV_VARS,
) -> dict[str, str]:
    """Build a minimal environment that excludes API keys and secrets."""
    env: dict[str, str] = {}
    for key in allowed_vars:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if extra:
        for key, value in extra.items():
            if _is_sensitive_env_key(key):
                continue
            env[key] = value
    return env


def build_sandboxed_command(
    argv: list[str],
    workspace: Path,
    *,
    allow_network: bool = False,
    env: dict[str, str] | None = None,
    extra_read_paths: list[Path] | None = None,
) -> list[str]:
    """Wrap a command in the host OS sandbox when a supported backend exists."""
    sandbox_exec = shutil.which("sandbox-exec")
    if platform.system() != "Darwin" or not sandbox_exec:
        return list(argv)

    root = workspace.resolve()
    read_paths = {str(root)}
    for key in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = (env or {}).get(key)
        if value:
            read_paths.add(str(Path(value).resolve()))
    for item in extra_read_paths or []:
        if item.exists():
            read_paths.add(str(item.resolve()))

    def subpath_rule(action: str, path: str) -> str:
        escaped = path.replace("\\", "\\\\").replace('"', '\\"')
        return f'({action} (subpath "{escaped}"))'

    home = str(Path.home().resolve())
    profile_lines = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        subpath_rule("deny file-read*", home),
        subpath_rule("deny file-write*", home),
    ]
    profile_lines.extend(subpath_rule("allow file-read*", path) for path in sorted(read_paths))
    profile_lines.append(subpath_rule("allow file-write*", str(root)))
    if allow_network:
        profile_lines.append("(allow network*)")
    profile = "\n".join(profile_lines)
    return [sandbox_exec, "-p", profile, *argv]


def _apply_resource_limits(timeout_seconds: float) -> None:
    try:
        import resource

        cpu_limit = max(1, min(int(timeout_seconds) + 5, 1800))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 30, 1 << 30))
    except (ImportError, OSError, ValueError):
        return


def _resolve_cwd(cwd: str | Path | None, workspace: Path) -> Path:
    if cwd is None:
        return workspace
    cwd_path = Path(cwd) if isinstance(cwd, str) else cwd
    if not cwd_path.is_absolute():
        cwd_path = workspace / cwd_path
    resolved = cwd_path.resolve()
    workspace_resolved = workspace.resolve()
    workspace_str = str(workspace_resolved)
    resolved_str = str(resolved)
    if resolved_str != workspace_str and not resolved_str.startswith(workspace_str + os.sep):
        raise PermissionError(f"cwd 越界: {cwd}")
    return resolved


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        pass
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def cancel_process(process_key: str) -> bool:
    """Cancel a tracked process and its process group."""
    with _lock:
        proc = _active.get(process_key)
    if not proc or proc.poll() is not None:
        return False
    _terminate_process_group(proc)
    try:
        proc.wait(timeout=3)
    except Exception:
        _kill_process_group(proc)
    return True


class _StreamReader:
    """Reads a stream, writes to a log file, and keeps a capped model buffer."""

    def __init__(
        self,
        stream: Any,
        log_file: Any,
        max_model_chars: int,
    ):
        self.stream = stream
        self.log_file = log_file
        self.max_model_chars = max_model_chars
        self.buffer: list[str] = []
        self.length = 0
        self.truncated = False
        self._closed = threading.Event()

    def run(self) -> None:
        try:
            while True:
                line = self.stream.readline()
                if not line:
                    break
                if self.log_file is not None:
                    try:
                        self.log_file.write(line)
                        self.log_file.flush()
                    except Exception:
                        pass
                if not self.truncated:
                    if self.length + len(line) > self.max_model_chars:
                        self.truncated = True
                        remaining = self.max_model_chars - self.length
                        if remaining > 0:
                            self.buffer.append(line[:remaining])
                            self.length += remaining
                        self.buffer.append(TRUNCATION_MARKER)
                        self.length += len(TRUNCATION_MARKER)
                    else:
                        self.buffer.append(line)
                        self.length += len(line)
        finally:
            try:
                self.stream.close()
            except Exception:
                pass
            self._closed.set()

    def join(self) -> None:
        self._closed.wait()

    def value(self) -> str:
        return "".join(self.buffer)


def _wait_for_process(
    proc: subprocess.Popen,
    cancel_token: CancelToken | None,
    timeout_seconds: float,
    started_at: float,
) -> None:
    deadline = started_at + timeout_seconds
    while proc.poll() is None:
        now = time.monotonic()
        if cancel_token and cancel_token.is_cancelled():
            _terminate_process_group(proc)
            raise CancellationRequested("进程已取消")
        if now >= deadline:
            _terminate_process_group(proc)
            raise ProcessTimeoutError(f"进程超时 ({timeout_seconds} 秒)")
        time.sleep(0.05)


def run_command(
    argv: list[str],
    *,
    cwd: str | Path | None,
    workspace: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    cancel_token: CancelToken | None = None,
    process_key: str | None = None,
    combine_output: bool = False,
    max_model_output_chars: int = MAX_MODEL_OUTPUT_CHARS,
    task_log_path: Path | None = None,
    allowed_env_vars: tuple[str, ...] = DEFAULT_ALLOWED_ENV_VARS,
) -> ProcessResult:
    """Run a non-interactive command with strict workspace and env isolation.

    - ``argv`` must be an argv array; shell=True is never used.
    - ``cwd`` is resolved under ``workspace``; escaping the workspace raises
      ``PermissionError``.
    - Environment variables are filtered to a minimal allowlist; API keys are
      never passed to the child process.
    - Output is capped for the model; the full stream may be written to
      ``task_log_path``.
    - Cancellation terminates the whole process group.
    """
    if not argv:
        raise ProcessStartError("argv 不能为空")

    resolved_cwd = _resolve_cwd(cwd, workspace)
    minimal_env = build_minimal_env(env, allowed_env_vars)
    executable = argv[0]
    executable_path = Path(executable)
    if executable_path.is_absolute():
        executable_exists = executable_path.is_file()
    elif os.sep in executable:
        executable_exists = (resolved_cwd / executable_path).is_file()
    else:
        executable_exists = shutil.which(
            executable,
            path=minimal_env.get("PATH"),
        ) is not None
    if not executable_exists:
        raise ProcessStartError(f"无法启动进程: 找不到可执行文件 {executable}")
    child_home = workspace.resolve() / ".agent-home"
    child_home.mkdir(parents=True, exist_ok=True)
    minimal_env["HOME"] = str(child_home)
    minimal_env.setdefault("GRADLE_USER_HOME", str(workspace.resolve() / ".gradle"))
    command = build_sandboxed_command(
        argv,
        workspace,
        allow_network=False,
        env=minimal_env,
    )

    popen_kwargs: dict[str, Any] = {
        "cwd": resolved_cwd,
        "env": minimal_env,
        "stdout": subprocess.PIPE,
        "text": True,
        "start_new_session": True,
        "preexec_fn": lambda: _apply_resource_limits(timeout_seconds),
    }
    if combine_output:
        popen_kwargs["stderr"] = subprocess.STDOUT
    else:
        popen_kwargs["stderr"] = subprocess.PIPE

    try:
        proc = subprocess.Popen(command, **popen_kwargs)
    except Exception as exc:
        raise ProcessStartError(f"无法启动进程: {exc}") from exc

    if process_key:
        with _lock:
            _active[process_key] = proc

    log_file = None
    if task_log_path is not None:
        task_log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_file = open(task_log_path, "w", encoding="utf-8")
        except Exception:
            log_file = None

    started_at = time.monotonic()
    stdout_reader: _StreamReader | None = None
    stderr_reader: _StreamReader | None = None
    error_type: str | None = None
    returncode = -1

    try:
        if proc.stdout is not None:
            stdout_reader = _StreamReader(proc.stdout, log_file, max_model_output_chars)
            threading.Thread(target=stdout_reader.run, daemon=True).start()
        if proc.stderr is not None and not combine_output:
            stderr_reader = _StreamReader(proc.stderr, log_file, max_model_output_chars)
            threading.Thread(target=stderr_reader.run, daemon=True).start()

        _wait_for_process(proc, cancel_token, timeout_seconds, started_at)

        if stdout_reader:
            stdout_reader.join()
        if stderr_reader:
            stderr_reader.join()

        try:
            returncode = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            returncode = proc.wait(timeout=5)

        if cancel_token and cancel_token.is_cancelled():
            error_type = "CancellationRequested"
        elif returncode != 0:
            error_type = "NonZeroExitCode"

    except CancellationRequested:
        error_type = "CancellationRequested"
        _kill_process_group(proc)
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    except ProcessTimeoutError:
        error_type = "Timeout"
        _kill_process_group(proc)
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    except Exception as exc:
        error_type = exc.__class__.__name__
        _kill_process_group(proc)
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    finally:
        try:
            if stdout_reader:
                stdout_reader.join(timeout=2)
            if stderr_reader:
                stderr_reader.join(timeout=2)
        except Exception:
            pass
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        if process_key:
            with _lock:
                active = _active.get(process_key)
                if active is proc or (active is not None and active.poll() is not None):
                    _active.pop(process_key, None)
        elif proc.poll() is None:
            _kill_process_group(proc)
            try:
                proc.wait(timeout=3)
            except Exception:
                pass

    duration_ms = round((time.monotonic() - started_at) * 1000)
    stdout = stdout_reader.value() if stdout_reader else ""
    stderr = stderr_reader.value() if stderr_reader else ""
    truncated = (stdout_reader.truncated if stdout_reader else False) or (
        stderr_reader.truncated if stderr_reader else False
    )

    ok = error_type is None and returncode == 0
    return ProcessResult(
        ok=ok,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        truncated=truncated,
        full_output_path=task_log_path,
        error_type=error_type,
    )
