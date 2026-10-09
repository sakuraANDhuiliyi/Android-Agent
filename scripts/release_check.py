#!/usr/bin/env python3
"""Release gate runner for Stage 19. Offline; does not call paid models."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int | None = None, env: dict | None = None, log_dir: Path | None = None) -> dict:
    # Some backend modules initialize their default store during import, before
    # individual tests can install fixtures. Isolate the process from its first
    # instruction, including Python helpers launched by desktop checks.
    with tempfile.TemporaryDirectory(prefix="android-agent-release-") as directory:
        isolated = Path(directory)
        process_env = {
            **os.environ, **(env or {}),
            "AGENT_DATA_DIR": str(isolated / "data"),
            "AGENT_WORKSPACES_DIR": str(isolated / "workspaces"),
            "AGENT_BUILDS_DIR": str(isolated / "builds"),
        }
        return _run_isolated(cmd, cwd=cwd, timeout=timeout, env=process_env, log_dir=log_dir)


def _run_isolated(cmd: list[str], *, cwd: Path | None, timeout: int | None, env: dict, log_dir: Path | None) -> dict:
    print("RUN", cmd, flush=True)
    started = time.perf_counter()

    def result(returncode: int, stdout: str | bytes | None, stderr: str | bytes | None) -> dict:
        # Keep the first assertion as well as the noisy service tail. Truncated
        # report summaries alone can otherwise hide the reason a gate failed.
        stdout = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else stdout or ""
        stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr or ""
        item = {"cmd": cmd, "returncode": returncode,
                "elapsed_s": round(time.perf_counter() - started, 3),
                "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:], "ok": returncode == 0}
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            identity = hashlib.sha256(json.dumps([str(cwd or ROOT), cmd]).encode()).hexdigest()[:16]
            for stream, value in (("stdout", stdout), ("stderr", stderr)):
                path = log_dir / f"{identity}.{stream}.log"
                path.write_text(value, encoding="utf-8")
                item[f"{stream}_path"] = str(path.resolve())
        return item

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result(proc.returncode, proc.stdout, proc.stderr)
    except OSError as exc:
        return result(127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        diagnostic = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        return result(124, exc.stdout, diagnostic + "\ntimeout")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-android", action="store_true")
    parser.add_argument("--skip-desktop", action="store_true")
    parser.add_argument("--skip-perf", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / ".artifacts" / "release_report.json",
    )
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    log_dir = Path(tempfile.mkdtemp(prefix=args.report.stem + "-logs-", dir=args.report.parent))

    def check(cmd: list[str], **kwargs) -> dict:
        return run(cmd, log_dir=log_dir, **kwargs)

    steps: list[dict] = []

    steps.append(check([sys.executable, str(ROOT / "scripts" / "scan_secrets.py")]))
    steps.append(check([sys.executable, str(ROOT / "scripts" / "check_api_contract.py")]))
    steps.append(check([sys.executable, str(ROOT / "scripts" / "export_creative_seed.py"), "--check"]))
    steps.append(check(["git", "diff", "--check"], timeout=60))
    steps.append(check([sys.executable, str(ROOT / "scripts" / "sync_aora_assets.py"), "--check"], timeout=60))
    steps.append(
        check(
            [sys.executable, "-m", "pytest", "tests", "-q", "--tb=line"],
            timeout=1800,
        )
    )
    steps.append(check([sys.executable, str(ROOT / "tests" / "e2e" / "run_e2e.py")], timeout=900))
    if not args.skip_desktop:
        steps.append(check(["npm", "run", "check"], cwd=ROOT / "desktop", timeout=120))
        steps.append(check(["npm", "run", "test:unit"], cwd=ROOT / "desktop", timeout=120))
        steps.append(check(["npm", "run", "test:emotion"], cwd=ROOT / "desktop", timeout=120))
        steps.append(check(["npm", "run", "test:recovery"], cwd=ROOT / "desktop", timeout=180))
        steps.append(check(["npm", "run", "test:delivery"], cwd=ROOT / "desktop", timeout=180))
        steps.append(check(["npm", "run", "test:messages"], cwd=ROOT / "desktop", timeout=180))
        steps.append(check(["npm", "run", "test:submissions"], cwd=ROOT / "desktop", timeout=180))
        steps.append(check(["npm", "run", "test:studio"], cwd=ROOT / "desktop", timeout=120))
        steps.append(check(["npm", "run", "test:creative-live"], cwd=ROOT / "desktop", timeout=180,
                         env={**os.environ, "CREATIVE_TEST_PYTHON": sys.executable}))
        steps.append(check(["npm", "run", "test:creative-community"], cwd=ROOT / "desktop", timeout=180,
                         env={**os.environ, "CREATIVE_TEST_PYTHON": sys.executable}))
        steps.append(
            check(["npm", "audit"], cwd=ROOT / "desktop", timeout=120)
        )
        steps.append(
            check(["npm", "run", "test:screenshot"], cwd=ROOT / "desktop", timeout=300)
        )
        # Match the Python interpreter used for the backend suite; never depend
        # on an unrelated system python3 or the developer's old .venv.
        desktop_test_env = {
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        }
        for smoke in ("electron-smoke.test.js", "electron-scenario-smoke.test.js"):
            steps.append(check(["node", "tests/" + smoke], cwd=ROOT / "desktop", timeout=300,
                             env=desktop_test_env))
        steps.append(check(["npm", "run", "test:recovery-smoke"], cwd=ROOT / "desktop", timeout=300,
                         env=desktop_test_env))
        steps.append(check(["npm", "run", "test:delivery-smoke"], cwd=ROOT / "desktop", timeout=300,
                         env=desktop_test_env))
        steps.append(check(["npm", "run", "test:messages-smoke"], cwd=ROOT / "desktop", timeout=300,
                         env=desktop_test_env))
        steps.append(check(["npm", "run", "test:submissions-smoke"], cwd=ROOT / "desktop", timeout=300,
                         env=desktop_test_env))
    if not args.skip_android:
        android = ROOT / "android-app"
        if (android / "gradlew").is_file():
            steps.append(
                check(
                    ["./gradlew", "testDebugUnitTest", "assembleDebug", "lintDebug", "--quiet"],
                    cwd=android,
                    timeout=1800,
                )
            )
        else:
            steps.append(
                {
                    "cmd": ["android"],
                    "ok": False,
                    "returncode": 1,
                    "elapsed_s": 0,
                    "stdout_tail": "",
                    "stderr_tail": "gradlew missing",
                }
            )

    report = {
        "ok": all(s["ok"] for s in steps),
        "passed": sum(1 for s in steps if s["ok"]),
        "failed": sum(1 for s in steps if not s["ok"]),
        "steps": steps,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failed": report["failed"], "ok": report["ok"]}, indent=2))
    for s in steps:
        status = "PASS" if s["ok"] else "FAIL"
        print(status, s["cmd"], f"({s['elapsed_s']}s)")
        if not s["ok"]:
            print(s["stderr_tail"] or s["stdout_tail"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
