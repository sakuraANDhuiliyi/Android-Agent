"""Create an atomic backup with consistent SQLite snapshots (including WAL)."""
from __future__ import annotations

import argparse
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
SQLITE_HEADER = b"SQLite format 3\x00"


def _is_sqlite_file(path: Path) -> bool:
    """Extensions are not a database format: project assets may also end in .db."""
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def _is_sqlite_sidecar(path: Path) -> bool:
    for suffix in ("-wal", "-shm", "-journal"):
        if path.name.endswith(suffix):
            database_name = path.name[:-len(suffix)]
            return bool(database_name) and _is_sqlite_file(path.with_name(database_name))
    return False


def create_backup(output: Path, roots: dict[str, Path]) -> dict:
    output = output.expanduser().resolve()
    roots = {name: path.expanduser().resolve() for name, path in roots.items()}
    for name, root in roots.items():
        if not root.is_dir():
            raise ValueError(f"Backup source does not exist: {name} ({root})")
        if output.is_relative_to(root):
            raise ValueError("Backup output must be outside the source directories")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "sources": {name: str(path) for name, path in roots.items()},
        "databases": [],
        "consistency": "SQLite databases are individually consistent; pause writes for a cross-file checkpoint.",
    }
    fd, temporary_name = tempfile.mkstemp(prefix=".agent-backup-", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with tempfile.TemporaryDirectory(prefix="agent-db-backup-") as temp:
            with tarfile.open(temporary, "w:gz", dereference=False) as archive:
                def add(path: Path, name: str) -> None:
                    if path.is_symlink():
                        archive.add(path, arcname=name, recursive=False)
                    elif path.is_dir():
                        archive.add(path, arcname=name, recursive=False)
                        for child in sorted(path.iterdir()):
                            add(child, f"{name}/{child.name}")
                    elif _is_sqlite_sidecar(path):
                        return  # Included through SQLite's snapshot API.
                    elif _is_sqlite_file(path):
                        snapshot = Path(temp) / f"{len(manifest['databases'])}.db"
                        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                            with closing(sqlite3.connect(snapshot)) as dest:
                                source.backup(dest, pages=256)
                                check = dest.execute("PRAGMA integrity_check").fetchall()
                                if check != [("ok",)]:
                                    raise RuntimeError(f"Backup database integrity check failed: {name}")
                        archive.add(snapshot, arcname=name, recursive=False)
                        manifest["databases"].append(name)
                    elif path.is_file():
                        archive.add(path, arcname=name, recursive=False)

                for name, root in roots.items():
                    add(root, name)
                body = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                info = tarfile.TarInfo("backup-manifest.json")
                info.size, info.mode = len(body), 0o600
                archive.addfile(info, io.BytesIO(body))
        # Verify every member before replacing an existing backup.
        with tarfile.open(temporary, "r:gz") as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        while stream.read(1024 * 1024):
                            pass
        temporary.replace(output)
        return manifest
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path,
                        default=ROOT / "backups" / f"agent-backup-{datetime.now():%Y%m%d-%H%M%S}.tar.gz")
    args = parser.parse_args()
    roots = {name: Path(os.environ.get(env, str(ROOT / name))) for name, env in (
        ("data", "AGENT_DATA_DIR"), ("workspaces", "AGENT_WORKSPACES_DIR"),
        ("builds", "AGENT_BUILDS_DIR"),
    )}
    manifest = create_backup(args.output, roots)
    print(f"OK: {args.output} ({len(manifest['databases'])} verified database snapshots)")


if __name__ == "__main__":
    main()
