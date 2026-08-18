#!/usr/bin/env python3
"""SQLite → PostgreSQL migration helper.

Default is dry-run: count rows, hash ids, and write a verification manifest.
--apply writes Postgres INSERT SQL (does not require a live server).
--rollback restores a --backup copy.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate Android Agent SQLite data toward PostgreSQL")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write manifests and SQL (default: data-dir/pg-migration)",
    )
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true", help="Write postgres SQL dump")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--backup", action="store_true", help="Copy DBs before apply")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from agent.paths import DATA_DIR
    from agent.stores.migrate_pg import (
        compare_summaries,
        render_postgres_sql,
        summarize_sqlite,
        write_manifest,
    )

    data_dir = (args.data_dir or DATA_DIR).expanduser()
    agent_db = data_dir / "agent.db"
    users_db = data_dir / "users.db"
    out_dir = args.output_dir or (data_dir / "pg-migration")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    backup_dir = out_dir / "backup"

    if args.rollback:
        if not backup_dir.is_dir():
            print("rollback failed: no backup directory", file=sys.stderr)
            return 1
        for name in ("agent.db", "users.db"):
            src = backup_dir / name
            if src.is_file():
                shutil.copy2(src, data_dir / name)
                print(f"restored: {name}")
        return 0

    if not agent_db.is_file():
        print(f"missing {agent_db}", file=sys.stderr)
        return 1

    summary = summarize_sqlite(agent_db, extra=[users_db] if users_db.is_file() else [])
    write_manifest(manifest_path, summary)
    print(f"rows: {summary['row_total']}")
    print(f"hash: {summary['aggregate_hash']}")
    print(f"manifest: {manifest_path}")

    if args.apply:
        if args.backup:
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = int(time.time())
            for path in (agent_db, users_db):
                if path.is_file():
                    shutil.copy2(path, backup_dir / path.name)
            print(f"backup: {backup_dir} ({stamp})")
        sql_path = out_dir / "android_agent.pg.sql"
        chunks = [render_postgres_sql(agent_db)]
        if users_db.is_file():
            chunks.append(render_postgres_sql(users_db))
        sql_path.write_text("\n".join(chunks), encoding="utf-8")
        print(f"sql: {sql_path}")
        verify = summarize_sqlite(agent_db, extra=[users_db] if users_db.is_file() else [])
        errors = compare_summaries(summary, verify)
        if errors:
            print("verify failed:", file=sys.stderr)
            for err in errors:
                print(f"  {err}", file=sys.stderr)
            return 2
        print("verify: ok")
        print("Apply the SQL on PostgreSQL, then set deployment_mode=postgres and database_url.")
        print("Live psycopg import is optional; this dump is the supported apply path.")
    else:
        print("dry-run only (pass --apply to write SQL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
