from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "template"
WORKSPACES_DIR = ROOT / "workspaces"
BUILDS_DIR = ROOT / "builds"
DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR", ROOT / "data")).expanduser()

DEFAULT_PACKAGE = "com.example.template"
DEFAULT_USER_ID = os.environ.get("AGENT_USER_ID", "local").strip() or "local"

STUDIO_JBR = Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home")

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def validate_id(value: str, *, kind: str = "id") -> str:
    cleaned = (value or "").strip()
    if not cleaned or not _ID_RE.fullmatch(cleaned):
        raise ValueError(f"无效的{kind}: {value!r}（仅允许字母数字、_、-，最长 64）")
    if cleaned in {".", ".."}:
        raise ValueError(f"无效的{kind}: {value!r}")
    return cleaned


def user_workspaces_dir(user_id: str) -> Path:
    return WORKSPACES_DIR / validate_id(user_id, kind="user_id")


def user_builds_dir(user_id: str) -> Path:
    return BUILDS_DIR / validate_id(user_id, kind="user_id")


def workspace_path(user_id: str, project_id: str) -> Path:
    return user_workspaces_dir(user_id) / validate_id(project_id, kind="project_id")


def project_meta_path(user_id: str, project_id: str) -> Path:
    return workspace_path(user_id, project_id) / ".agent-project.json"


def build_log_path(user_id: str, project_id: str, build_id: str) -> Path:
    return user_builds_dir(user_id) / validate_id(project_id, kind="project_id") / f"{build_id}.log"


def latest_apk_path(user_id: str, project_id: str) -> Path:
    return user_builds_dir(user_id) / validate_id(project_id, kind="project_id") / "latest.apk"


def find_android_sdk() -> Path | None:
    candidates: list[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(key, "").strip()
        if value:
            candidates.append(Path(value))
    candidates.append(Path.home() / "Library/Android/sdk")

    seen: set[str] = set()
    for path in candidates:
        resolved = path.expanduser().resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_dir() and (resolved / "platforms").is_dir():
            return resolved
    return None


def ensure_local_properties(workspace: Path) -> Path | None:
    sdk = find_android_sdk()
    if not sdk:
        return None

    local_props = workspace / "local.properties"
    content = f"sdk.dir={sdk.as_posix()}\n"
    if not local_props.is_file() or local_props.read_text(encoding="utf-8") != content:
        local_props.write_text(content, encoding="utf-8")
    return sdk
