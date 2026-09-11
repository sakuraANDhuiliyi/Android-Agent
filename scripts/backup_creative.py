#!/usr/bin/env python3
"""Snapshot or restore creative content, covers and accounts into a new local directory."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.paths import DATA_DIR
from agent.creative.backup import create_bundle, restore_bundle

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--database", type=Path, default=DATA_DIR / "agent.db")
    create.add_argument("--users-database", type=Path, default=DATA_DIR / "users.db")
    create.add_argument("--output", type=Path, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = create_bundle(args.database, args.users_database, args.output) if args.action == "create" else restore_bundle(args.input, args.output)
    print(f"{args.action}: {len(result['files'])} verified files in {args.output}")
