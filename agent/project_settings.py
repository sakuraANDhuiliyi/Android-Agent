"""Project-level agent configuration stored in ``.android-agent/settings.json``.

Managed values:
- ``permission_profile``: safe | standard | full_access (see agent.permissions)
- ``disabled_rules``: rule ids (e.g. ``rules:code-style.md``) excluded from prompts
- ``disabled_skills``: ``scope:name`` keys excluded from skill discovery
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

PERMISSION_PROFILES = ("safe", "standard", "full_access")
DEFAULT_PERMISSION_PROFILE = "standard"

_SETTINGS_FILENAME = "settings.json"
# Rule ids look like ``rules:code-style.md`` / ``subdir:app/AGENTS.md`` and
# skill keys like ``project:android-build`` — hence ``:`` and ``/`` are allowed.
_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def project_settings_path(workspace: Path) -> Path:
    return workspace / ".android-agent" / _SETTINGS_FILENAME


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def normalize_profile(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in PERMISSION_PROFILES else DEFAULT_PERMISSION_PROFILE


def load_project_settings(workspace: Path) -> dict[str, Any]:
    path = project_settings_path(workspace)
    if not path.is_file():
        return _defaults()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _defaults()
    if not isinstance(data, dict):
        return _defaults()
    return merge_with_defaults(data)


def _defaults() -> dict[str, Any]:
    return {
        "permission_profile": DEFAULT_PERMISSION_PROFILE,
        "disabled_rules": [],
        "disabled_skills": [],
    }


def merge_with_defaults(data: dict[str, Any]) -> dict[str, Any]:
    profile = normalize_profile(data.get("permission_profile"))
    disabled_rules = _clean_key_list(data.get("disabled_rules"))
    disabled_skills = _clean_key_list(data.get("disabled_skills"))
    return {
        "permission_profile": profile,
        "disabled_rules": disabled_rules,
        "disabled_skills": disabled_skills,
    }


def _clean_key_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if (
            text
            and _KEY_RE.fullmatch(text)
            and ".." not in text.split("/")
            and text not in seen
        ):
            seen.add(text)
            result.append(text)
    return result


def save_project_settings(workspace: Path, data: dict[str, Any]) -> dict[str, Any]:
    merged = merge_with_defaults(data)
    path = project_settings_path(workspace)
    with _lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    return merged


def update_project_settings(workspace: Path, **changes: Any) -> dict[str, Any]:
    current = load_project_settings(workspace)
    current.update({key: value for key, value in changes.items() if key in _defaults()})
    return save_project_settings(workspace, current)


def is_rule_disabled(workspace: Path, rule_id: str) -> bool:
    return rule_id in load_project_settings(workspace).get("disabled_rules", [])


def set_rule_disabled(workspace: Path, rule_id: str, disabled: bool) -> dict[str, Any]:
    rule_id = str(rule_id or "").strip()
    if not rule_id or not _KEY_RE.fullmatch(rule_id):
        raise ValueError(f"无效的规则 ID: {rule_id!r}")
    current = load_project_settings(workspace)
    disabled_rules = [item for item in current["disabled_rules"] if item != rule_id]
    if disabled:
        disabled_rules.append(rule_id)
    return update_project_settings(workspace, disabled_rules=disabled_rules)


def is_skill_disabled(workspace: Path, skill_key: str) -> bool:
    return skill_key in load_project_settings(workspace).get("disabled_skills", [])


def set_skill_disabled(workspace: Path, skill_key: str, disabled: bool) -> dict[str, Any]:
    skill_key = str(skill_key or "").strip()
    if not skill_key or not _KEY_RE.fullmatch(skill_key):
        raise ValueError(f"无效的 skill 标识: {skill_key!r}")
    current = load_project_settings(workspace)
    disabled_skills = [item for item in current["disabled_skills"] if item != skill_key]
    if disabled:
        disabled_skills.append(skill_key)
    return update_project_settings(workspace, disabled_skills=disabled_skills)


def get_permission_profile(workspace: Path) -> str:
    return load_project_settings(workspace).get("permission_profile") or DEFAULT_PERMISSION_PROFILE
