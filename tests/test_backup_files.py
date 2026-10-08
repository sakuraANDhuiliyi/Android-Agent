"""Backup must distinguish SQLite storage from arbitrary tenant project files."""
from contextlib import closing
import sqlite3
import tarfile

from scripts.backup_data import create_backup


def test_backup_snapshots_sqlite_sidecars_but_preserves_ordinary_assets(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = {
        "notes-journal": b"user-authored journal",
        "drawing-wal": b"ordinary wall asset",
        "shared-shm": b"ordinary project file",
        "-wal": b"a filename that is only the sidecar suffix",
        "encrypted.db": b"synthetic encrypted asset, not SQLite",
        "custom.sqlite": b"ordinary asset with database suffix",
        "custom.sqlite3": b"another ordinary asset",
        "encrypted.db-wal": b"ordinary asset next to a non-SQLite file",
    }
    for name, body in files.items():
        (workspace / name).write_bytes(body)
    # SQLite recognition also works without one of the conventional extensions.
    source_path = workspace / "events.store"
    with closing(sqlite3.connect(source_path)) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE events (body TEXT)")
        source.commit()
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        source.execute("INSERT INTO events VALUES ('committed in live WAL')")
        source.commit()
        assert source_path.with_name("events.store-wal").stat().st_size > 0
        archive_path = tmp_path / "backup.tar.gz"
        manifest = create_backup(archive_path, {"workspaces": workspace})
        assert manifest["databases"] == ["workspaces/events.store"]
        with tarfile.open(archive_path) as archive:
            names = set(archive.getnames())
            assert "workspaces/events.store-wal" not in names
            assert "workspaces/events.store-shm" not in names
            for name, body in files.items():
                assert archive.extractfile(f"workspaces/{name}").read() == body
            restored = tmp_path / "restored.db"
            restored.write_bytes(archive.extractfile("workspaces/events.store").read())
        with closing(sqlite3.connect(restored)) as restored_db:
            assert restored_db.execute("SELECT body FROM events").fetchone()[0] == "committed in live WAL"


def test_backup_keeps_existing_archive_when_a_database_is_corrupt(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # It has a real SQLite signature but invalid pages: fail rather than publish
    # an archive advertised as a verified database snapshot.
    (workspace / "broken.db").write_bytes(b"SQLite format 3\x00" + b"bad pages" * 64)
    output = tmp_path / "backup.tar.gz"
    output.write_bytes(b"previous good backup")
    try:
        create_backup(output, {"workspaces": workspace})
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("Corrupt SQLite file should fail validation")
    assert output.read_bytes() == b"previous good backup"
    assert list(tmp_path.glob(".agent-backup-*")) == []
