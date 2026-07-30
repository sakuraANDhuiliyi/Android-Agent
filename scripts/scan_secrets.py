#!/usr/bin/env python3
"""Scan tracked text files for likely secrets. Offline, no network."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?i)(?:api[_-]?key|password)\s*[:=]\s*['\"]([A-Za-z0-9_\-+/=]{20,})['\"]"
    ),
]

SKIP_DIRS = {".git", "node_modules", "build", ".gradle", "__pycache__", "src", "backups"}
SKIP_SUFFIXES = {".png", ".jpg", ".apk", ".jar", ".so", ".dex", ".pyc", ".db"}


def _looks_like_code_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", value))


def iter_files(root: Path) -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=str(root), text=True
        )
        files = [root / line for line in out.splitlines() if line.strip()]
        if files:
            return files
    except Exception:
        pass
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    hits = []
    for path in iter_files(args.root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Allow intentionally fake keys in tests/evals/docs examples.
        if any(p in path.parts for p in ("tests", "evals", "docs")):
            continue
        if path.name in {"config.yaml"}:
            # Local config may contain real keys — warn but do not fail hard if redacted-looking.
            pass
        for pat in PATTERNS:
            for match in pat.finditer(text):
                snippet = match.group(0)[:60]
                value = match.group(1) if match.lastindex else snippet
                if _looks_like_code_identifier(value):
                    continue
                if "fake" in snippet.lower() or "example" in snippet.lower():
                    continue
                if "你的" in snippet or "YOUR" in snippet.upper():
                    continue
                if "getenv" in snippet.lower() or "resolve" in snippet.lower():
                    continue
                hits.append(f"{path}:{snippet}")
    if hits:
        print("SECRET SCAN HITS:")
        for h in hits[:50]:
            print(h)
        return 1
    print("secret scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
