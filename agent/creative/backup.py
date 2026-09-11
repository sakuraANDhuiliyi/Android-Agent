"""Consistent SQLite + immutable cover snapshots for the creative domain.

This is an operator-local bundle, not a public download and not a workspace/APK
backup. Restore only into a new directory; never overwrite a running service.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def sqlite_snapshot(source: Path, target: Path):
    if not source.is_file():
        raise ValueError(f"数据库不存在：{source}")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(target)) as snapshot:
        original.backup(snapshot)
        snapshot.execute("PRAGMA journal_mode=DELETE")
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("数据库快照完整性检查失败")


def validate_bundle(bundle: Path):
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("scope") != "creative-and-accounts":
        raise ValueError("不支持的创意备份格式")
    files = manifest.get("files", {})
    if not {"agent.db", "users.db"}.issubset(files):
        raise ValueError("备份缺少必要数据库")
    for relative, expected in files.items():
        if relative not in {"agent.db", "users.db"} and not re.fullmatch(r"creative-assets/[a-f0-9]{32}\.jpg", relative):
            raise ValueError("备份包含非法路径")
        file = bundle / relative
        if file.is_symlink() or not file.is_file() or digest(file) != expected:
            raise ValueError(f"备份文件缺失或摘要不一致：{relative}")
    for name in ("agent.db", "users.db"):
        with closing(sqlite3.connect((bundle / name).as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("数据库完整性检查失败")
    with closing(sqlite3.connect(bundle / "agent.db")) as db, closing(sqlite3.connect(bundle / "users.db")) as users:
        for asset_id, expected in db.execute("SELECT id,digest FROM creative_assets"):
            relative = f"creative-assets/{asset_id}.jpg"
            if relative not in files or files[relative] != expected:
                raise ValueError("素材索引与文件摘要不一致")
        owners = {r[0] for r in db.execute("SELECT DISTINCT owner_user_id FROM creatives WHERE owner_user_id IS NOT NULL")}
        owners.update(r[0] for r in db.execute("SELECT DISTINCT owner_user_id FROM creative_favorites"))
        owners.update(r[0] for r in db.execute("SELECT DISTINCT reporter_user_id FROM creative_reports"))
        accounts = {r[0] for r in users.execute("SELECT user_id FROM users")}
        if not owners.issubset(accounts):
            raise ValueError("账号与创意快照不一致，请在停止账号变更后重试")
    return manifest


def create_bundle(database: Path, users_database: Path, destination: Path):
    database, users_database, destination = database.resolve(), users_database.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError("备份目标已存在，请使用新目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".creative-backup-", dir=destination.parent))
    try:
        sqlite_snapshot(database, staging / "agent.db")
        sqlite_snapshot(users_database, staging / "users.db")
        assets = staging / "creative-assets"; assets.mkdir()
        with closing(sqlite3.connect(staging / "agent.db")) as db:
            for asset_id, expected in db.execute("SELECT id,digest FROM creative_assets"):
                if not re.fullmatch("[a-f0-9]{32}", asset_id):
                    raise ValueError("非法素材 ID")
                source = database.parent / "creative-assets" / f"{asset_id}.jpg"
                if source.is_symlink() or digest(source) != expected:
                    raise ValueError("素材缺失或已变化，不能生成完整备份")
                shutil.copyfile(source, assets / source.name)
        manifest = {"schema_version": 1, "scope": "creative-and-accounts", "created_at": time.time(),
                    "files": {str(p.relative_to(staging)): digest(p) for p in staging.rglob("*") if p.is_file()}}
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        validate_bundle(staging)
        staging.rename(destination)
        return manifest
    except Exception:
        shutil.rmtree(staging)
        raise


def restore_bundle(bundle: Path, destination: Path):
    bundle, destination = bundle.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError("只允许恢复到不存在的新目录")
    manifest = validate_bundle(bundle)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".creative-restore-", dir=destination.parent))
    try:
        for relative in [*manifest["files"], "manifest.json"]:
            target = staging / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle / relative, target)
        validate_bundle(staging)
        staging.rename(destination)
        return manifest
    except Exception:
        shutil.rmtree(staging)
        raise
