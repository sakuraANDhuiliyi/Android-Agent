from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from agent.paths import (
    DEFAULT_PACKAGE,
    DEFAULT_USER_ID,
    TEMPLATE_DIR,
    ensure_local_properties,
    project_meta_path,
    user_workspaces_dir,
    validate_id,
    workspace_path,
)

WRITE_IGNORE = shutil.ignore_patterns(
    ".gradle",
    ".idea",
    "build",
    "local.properties",
    ".DS_Store",
)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "project"


def _validate_package(package: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", package):
        raise ValueError(f"无效包名: {package}")


def _replace_package_in_tree(root: Path, old_pkg: str, new_pkg: str) -> None:
    if old_pkg == new_pkg:
        return

    old_rel = Path(*old_pkg.split("."))
    new_rel = Path(*new_pkg.split("."))
    java_root = root / "app" / "src" / "main" / "java"
    old_dir = java_root / old_rel
    new_dir = java_root / new_rel

    if old_dir.is_dir():
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if new_dir.exists():
            shutil.rmtree(new_dir)
        shutil.move(str(old_dir), str(new_dir))
        parent = old_dir.parent
        while parent != java_root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    text_suffixes = {".kt", ".kts", ".xml", ".gradle", ".properties", ".toml"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in text_suffixes:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if old_pkg in content:
            path.write_text(content.replace(old_pkg, new_pkg), encoding="utf-8")


def init_project(
    name: str,
    package: str | None = None,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> str:
    if not TEMPLATE_DIR.is_dir():
        raise FileNotFoundError(f"模板不存在: {TEMPLATE_DIR}")

    user_id = validate_id(user_id, kind="user_id")
    package = package or f"com.androidagent.{_slugify(name).replace('-', '')}"
    _validate_package(package)

    user_dir = user_workspaces_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    base_id = _slugify(name)
    project_id = base_id
    suffix = 2
    while workspace_path(user_id, project_id).exists():
        project_id = f"{base_id}-{suffix}"
        suffix += 1

    dest = workspace_path(user_id, project_id)
    shutil.copytree(TEMPLATE_DIR, dest, ignore=WRITE_IGNORE)

    _replace_package_in_tree(dest, DEFAULT_PACKAGE, package)
    ensure_local_properties(dest)

    meta = {
        "id": project_id,
        "name": name,
        "package": package,
        "user_id": user_id,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    project_meta_path(user_id, project_id).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return project_id


def load_project_meta(user_id: str, project_id: str) -> dict:
    user_id = validate_id(user_id, kind="user_id")
    project_id = validate_id(project_id, kind="project_id")
    meta_file = project_meta_path(user_id, project_id)
    if not meta_file.is_file():
        raise FileNotFoundError(f"项目不存在: {user_id}/{project_id}")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta_user = str(meta.get("user_id") or user_id)
    if meta_user != user_id:
        raise FileNotFoundError(f"项目不存在: {user_id}/{project_id}")
    meta["user_id"] = user_id
    meta["id"] = project_id
    return meta


def list_projects(user_id: str) -> list[dict]:
    user_id = validate_id(user_id, kind="user_id")
    user_dir = user_workspaces_dir(user_id)
    if not user_dir.is_dir():
        return []
    projects = []
    for child in sorted(user_dir.iterdir()):
        meta_file = child / ".agent-project.json"
        if not child.is_dir() or not meta_file.is_file():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        meta["id"] = child.name
        meta["user_id"] = user_id
        projects.append(meta)
    return projects


def delete_project(user_id: str, project_id: str) -> None:
    load_project_meta(user_id, project_id)
    shutil.rmtree(workspace_path(user_id, project_id))


def new_build_id() -> str:
    return uuid.uuid4().hex[:8]
