#!/usr/bin/env python3
"""Idempotent DB migration / schema ensure for Android Agent.

Re-runs the same startup migrations used by TaskStore / MemoryStore / ConversationEventStore.
Safe to execute multiple times. Does not call remote services.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ensure Android Agent SQLite schemas")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override AGENT_DATA_DIR / data directory",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Copy agent.db to agent.db.bak-<timestamp> before migrating",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    data_dir = args.data_dir or Path(
        __import__("os").environ.get("AGENT_DATA_DIR", root / "data")
    ).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "agent.db"

    if args.backup and db_path.is_file():
        bak = db_path.with_name(f"agent.db.bak-{int(time.time())}")
        shutil.copy2(db_path, bak)
        print(f"backup: {bak}")

    # Import after path setup so stores pick up DATA_DIR when needed.
    sys.path.insert(0, str(root))
    import agent.paths as paths

    paths.DATA_DIR = data_dir

    from agent.database import TaskStore
    from agent.conversation_events import ConversationEventStore
    from agent.memory_store import MemoryStore

    store = TaskStore(db_path)
    events = ConversationEventStore(store)
    memory = MemoryStore(db_path)
    print(f"migrated: {db_path}")
    print(f"tasks_ok: {store.db_path.exists()}")
    print(f"events_ok: {events.db_path.exists()}")
    print(f"memory_ok: {memory.db_path.exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
