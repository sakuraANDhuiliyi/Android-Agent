#!/usr/bin/env python3
"""Release gate runner for Stage 19. Offline; does not call paid models."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int | None = None, env: dict | None = None) -> dict:
    print("RUN", cmd, flush=True)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "elapsed_s": round(time.perf_counter() - started, 3),
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-2000:],
            "ok": proc.returncode == 0,
        }
    except OSError as exc:
        return {
            "cmd": cmd, "returncode": 127,
            "elapsed_s": round(time.perf_counter() - started, 3),
            "stdout_tail": "", "stderr_tail": str(exc), "ok": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "returncode": 124,
            "elapsed_s": round(time.perf_counter() - started, 3),
            "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": "timeout",
            "ok": False,
        }


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

    steps: list[dict] = []

    steps.append(run([sys.executable, str(ROOT / "scripts" / "scan_secrets.py")]))
    steps.append(run([sys.executable, str(ROOT / "scripts" / "check_api_contract.py")]))
    steps.append(run([sys.executable, str(ROOT / "scripts" / "export_creative_seed.py"), "--check"]))
    steps.append(run(["git", "diff", "--check"], timeout=60))
    steps.append(run([sys.executable, str(ROOT / "scripts" / "sync_aora_assets.py"), "--check"], timeout=60))
    steps.append(
        run(
            [sys.executable, "-m", "pytest", "tests", "-q", "--tb=line"],
            timeout=1800,
        )
    )
    steps.append(run([sys.executable, str(ROOT / "tests" / "e2e" / "run_e2e.py")], timeout=900))
    if not args.skip_desktop:
        steps.append(run(["npm", "run", "check"], cwd=ROOT / "desktop", timeout=120))
        steps.append(run(["npm", "run", "test:unit"], cwd=ROOT / "desktop", timeout=120))
        steps.append(run(["npm", "run", "test:emotion"], cwd=ROOT / "desktop", timeout=120))
        steps.append(run(["npm", "run", "test:recovery"], cwd=ROOT / "desktop", timeout=180))
        steps.append(run(["npm", "run", "test:studio"], cwd=ROOT / "desktop", timeout=120))
        steps.append(run(["npm", "run", "test:creative-live"], cwd=ROOT / "desktop", timeout=180,
                         env={**os.environ, "CREATIVE_TEST_PYTHON": sys.executable}))
        steps.append(run(["npm", "run", "test:creative-community"], cwd=ROOT / "desktop", timeout=180,
                         env={**os.environ, "CREATIVE_TEST_PYTHON": sys.executable}))
        steps.append(
            run(["npm", "audit"], cwd=ROOT / "desktop", timeout=120)
        )
        steps.append(
            run(["npm", "run", "test:screenshot"], cwd=ROOT / "desktop", timeout=300)
        )
        # Match the Python interpreter used for the backend suite; never depend
        # on an unrelated system python3 or the developer's old .venv.
        desktop_test_env = {
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        }
        for smoke in ("electron-smoke.test.js", "electron-scenario-smoke.test.js"):
            steps.append(run(["node", "tests/" + smoke], cwd=ROOT / "desktop", timeout=300,
                             env=desktop_test_env))
        steps.append(run(["npm", "run", "test:recovery-smoke"], cwd=ROOT / "desktop", timeout=300,
                         env=desktop_test_env))
    if not args.skip_android:
        android = ROOT / "android-app"
        if (android / "gradlew").is_file():
            steps.append(
                run(
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
