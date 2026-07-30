#!/usr/bin/env python3
"""Generate checksums, dependency inventory, and local build provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
LOCK_MATERIALS = (
    ROOT / "requirements.lock",
    ROOT / "desktop/package-lock.json",
    ROOT / "android-app/gradle/verification-metadata.xml",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def python_components() -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\\\]+)")
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            components.append({"name": match.group(1), "version": match.group(2)})
    return components


def node_components() -> list[dict[str, str]]:
    lock = json.loads(
        (ROOT / "desktop/package-lock.json").read_text(encoding="utf-8")
    )
    components: list[dict[str, str]] = []
    for package_path, item in sorted(lock.get("packages", {}).items()):
        if not package_path.startswith("node_modules/") or not item.get("version"):
            continue
        components.append(
            {
                "name": package_path.removeprefix("node_modules/"),
                "version": str(item["version"]),
            }
        )
    return components


def gradle_components() -> list[dict[str, str]]:
    components: dict[tuple[str, str], dict[str, str]] = {}
    for lock_path in sorted((ROOT / "android-app").rglob("gradle.lockfile")):
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            coordinate = line.split("=", 1)[0]
            parts = coordinate.split(":")
            if len(parts) != 3:
                continue
            group, name, version = parts
            components[(group, name)] = {
                "group": group,
                "name": name,
                "version": version,
            }
    return sorted(components.values(), key=lambda item: (item["group"], item["name"]))


def build_manifest(artifacts: list[Path]) -> dict[str, Any]:
    subjects = []
    for artifact in artifacts:
        resolved = artifact.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"artifact is not a file: {artifact}")
        subjects.append(
            {
                "name": resolved.name,
                "size": resolved.stat().st_size,
                "digest": {"sha256": sha256_file(resolved)},
            }
        )

    materials = []
    for path in LOCK_MATERIALS:
        if path.is_file():
            materials.append(
                {
                    "name": str(path.relative_to(ROOT)),
                    "digest": {"sha256": sha256_file(path)},
                }
            )

    return {
        "schema": "android-agent-release-manifest/v1",
        "generated_at": time.time(),
        "source": {
            "repository": git_value("remote", "get-url", "origin"),
            "commit": git_value("rev-parse", "HEAD"),
            "dirty": bool(git_value("status", "--porcelain")),
        },
        "subjects": subjects,
        "materials": materials,
        "dependencies": {
            "python": python_components(),
            "node": node_components(),
            "gradle": gradle_components(),
        },
        "provenance": {
            "builder": "scripts/generate_release_manifest.py",
            "invocation": "local-release",
            "network_used_by_generator": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact",
        type=Path,
        action="append",
        required=True,
        help="Release artifact; may be repeated.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_manifest(args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output} for {len(manifest['subjects'])} artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
