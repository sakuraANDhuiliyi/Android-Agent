#!/usr/bin/env python3
"""Fail when OpenAPI or shared API fixtures drift without client tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests" / "fixtures" / "api_contract"
MANIFEST = FIXTURES / "manifest.json"
LOCK = FIXTURES / ".contract-lock.json"
OPENAPI_SNAPSHOT = FIXTURES / "openapi.json"
OPENAPI_PATHS = FIXTURES / "openapi.paths.json"


def fixture_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(FIXTURES.rglob("*.json")):
        if path.name == ".contract-lock.json":
            continue
        digest.update(path.relative_to(FIXTURES).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def live_openapi() -> dict:
    from agent.api import create_app
    from agent.api_contract import dump_openapi, openapi_path_index
    from agent.config import Settings
    from agent.database import TaskStore
    from agent.users import UserStore

    settings = Settings(
        provider="deepseek",
        api_key="fake-model-key",
        model="fake-model",
        model_candidates=["fake-model"],
        max_turns=4,
        max_auto_continuations=0,
        max_gradle_retries=2,
        compact_max_chars=100_000,
        max_output_tokens=4096,
        base_url="https://example.test",
        auto_build_after_edit=False,
        server_host="127.0.0.1",
        server_port=8000,
        api_token="",
        tavily_api_key="",
        users=[],
        provider_fallbacks=[],
        debug_web_ui_enabled=False,
        terminal_enabled=True,
    )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        app = create_app(
            settings=settings,
            user_store=UserStore(root / "users.db"),
            task_store=TaskStore(root / "tasks.db"),
        )
        spec = dump_openapi(app)
        spec["x-android-agent-paths"] = openapi_path_index(spec)
        return spec


def write_openapi_snapshot(spec: dict) -> None:
    OPENAPI_SNAPSHOT.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    OPENAPI_PATHS.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paths": spec.get("x-android-agent-paths") or [],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def compare_openapi(live: dict, snapshot: dict) -> list[str]:
    diffs: list[str] = []
    live_paths = set(live.get("x-android-agent-paths") or [])
    snap_paths = set(snapshot.get("x-android-agent-paths") or [])
    added = sorted(live_paths - snap_paths)
    removed = sorted(snap_paths - live_paths)
    if added:
        diffs.append("added paths: " + ", ".join(added[:20]))
    if removed:
        diffs.append("removed paths: " + ", ".join(removed[:20]))
    if live != snapshot and not diffs:
        diffs.append("OpenAPI schema body changed")
    return diffs


def check_clients(manifest: dict) -> list[str]:
    return [
        relative
        for relative in manifest.get("client_tests", [])
        if not (ROOT / relative).is_file()
    ]


def main(write_lock: bool = False) -> int:
    if not MANIFEST.is_file():
        print("missing manifest", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    missing_clients = check_clients(manifest)
    if missing_clients:
        print("missing client contract tests:", ", ".join(missing_clients), file=sys.stderr)
        return 1

    live = live_openapi()
    if write_lock or not OPENAPI_SNAPSHOT.is_file():
        write_openapi_snapshot(live)
        if not write_lock:
            print(f"initialized OpenAPI snapshot ({len(live.get('x-android-agent-paths') or [])} paths)")
    else:
        snapshot = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        diffs = compare_openapi(live, snapshot)
        if diffs:
            print("OpenAPI contract drift detected.", file=sys.stderr)
            for item in diffs:
                print(f"  {item}", file=sys.stderr)
            print("Refresh with: python3 scripts/check_api_contract.py --write-lock", file=sys.stderr)
            return 1

    current = fixture_hash()
    if write_lock or not LOCK.is_file():
        LOCK.write_text(json.dumps({"fixture_hash": current}, indent=2) + "\n", encoding="utf-8")
        print(f"{'updated' if write_lock else 'initialized'} contract lock: {current}")
        return 0

    expected = json.loads(LOCK.read_text(encoding="utf-8")).get("fixture_hash", "")
    if expected != current:
        print("API contract fixture drift detected.", file=sys.stderr)
        print(f"  expected: {expected}", file=sys.stderr)
        print(f"  current:  {current}", file=sys.stderr)
        print("Update shared fixtures and client contract tests, then refresh:", file=sys.stderr)
        print("  python3 scripts/check_api_contract.py --write-lock", file=sys.stderr)
        return 1

    print(f"api contract ok ({current[:12]}..., {len(live.get('x-android-agent-paths') or [])} paths)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-lock", action="store_true")
    args = parser.parse_args()
    sys.exit(main(write_lock=args.write_lock))
