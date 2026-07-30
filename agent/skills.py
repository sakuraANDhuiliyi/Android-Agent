from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.rules import (
    parse_frontmatter,
    project_skills_dir,
    user_skills_dir,
    _coerce_bool,
    _coerce_str_list,
    _is_inside,
)


_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
DEFAULT_SKILL_MAX_CHARS = 12_000


@dataclass
class SkillMeta:
    name: str
    description: str = ""
    version: str = "0"
    allowed_tools: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    globs: list[str] = field(default_factory=list)
    manual_only: bool = False
    path: str = ""
    scope: str = "project"  # project | user
    frontmatter_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "allowed_tools": list(self.allowed_tools),
            "required_permissions": list(self.required_permissions),
            "globs": list(self.globs),
            "manual_only": self.manual_only,
            "path": self.path,
            "scope": self.scope,
            "frontmatter_errors": list(self.frontmatter_errors),
        }


@dataclass
class SkillContent:
    meta: SkillMeta
    body: str
    resources: list[str] = field(default_factory=list)
    chars: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.meta.to_dict(),
            "body": self.body,
            "resources": list(self.resources),
            "chars": self.chars,
            "truncated": self.truncated,
        }


def _validate_skill_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not _SKILL_NAME_RE.fullmatch(cleaned):
        raise ValueError(f"无效的 skill 名称: {name!r}")
    return cleaned


def _parse_skill_meta(
    skill_md: Path,
    *,
    name: str,
    scope: str,
    rel_path: str,
) -> tuple[SkillMeta, str]:
    text = skill_md.read_text(encoding="utf-8")
    meta, body, errors = parse_frontmatter(text)
    meta_name = str(meta.get("name") or name).strip() or name
    try:
        meta_name = _validate_skill_name(meta_name)
    except ValueError:
        errors.append(f"invalid name in frontmatter: {meta_name!r}; using directory name")
        meta_name = name
    return (
        SkillMeta(
            name=meta_name,
            description=str(meta.get("description") or "").strip(),
            version=str(meta.get("version") or "0").strip() or "0",
            allowed_tools=_coerce_str_list(meta.get("allowed_tools")),
            required_permissions=_coerce_str_list(meta.get("required_permissions")),
            globs=_coerce_str_list(meta.get("globs")),
            manual_only=_coerce_bool(meta.get("manual_only"), default=False),
            path=rel_path,
            scope=scope,
            frontmatter_errors=errors,
        ),
        body.strip(),
    )


def _scan_skills_root(
    root: Path,
    *,
    scope: str,
    confine: Path,
    rel_base: Path | None = None,
) -> list[SkillMeta]:
    results: list[SkillMeta] = []
    if not root.is_dir():
        return results
    confine_resolved = confine.resolve()
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            child_resolved = child.resolve()
        except Exception:
            continue
        if not (
            str(child_resolved) == str(confine_resolved)
            or str(child_resolved).startswith(str(confine_resolved) + "/")
        ):
            continue
        try:
            name = _validate_skill_name(child.name)
        except ValueError:
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            skill_resolved = skill_md.resolve()
        except Exception:
            continue
        if not str(skill_resolved).startswith(str(child_resolved) + "/"):
            if skill_resolved.parent != child_resolved:
                continue
        base = rel_base or confine_resolved
        try:
            rel = skill_resolved.relative_to(base).as_posix()
        except Exception:
            rel = f"{name}/SKILL.md"
        try:
            meta, _ = _parse_skill_meta(skill_md, name=name, scope=scope, rel_path=rel)
        except Exception:
            continue
        results.append(meta)
    return results


def list_skills(
    workspace: Path | None,
    user_id: str,
    *,
    include_user: bool = True,
) -> list[SkillMeta]:
    results: list[SkillMeta] = []
    seen: set[str] = set()

    if workspace is not None:
        project_root = project_skills_dir(workspace)
        for meta in _scan_skills_root(
            project_root,
            scope="project",
            confine=workspace.resolve(),
            rel_base=workspace.resolve(),
        ):
            key = f"project:{meta.name}"
            if key not in seen:
                seen.add(key)
                results.append(meta)

    if include_user:
        user_root = user_skills_dir(user_id)
        for meta in _scan_skills_root(
            user_root,
            scope="user",
            confine=user_root.resolve(),
            rel_base=user_root.resolve(),
        ):
            key = f"user:{meta.name}"
            # Project skill with same name wins for discovery listing uniqueness by name+scope.
            if key not in seen:
                seen.add(key)
                results.append(meta)

    return results


def discover_skills_for_context(
    workspace: Path,
    user_id: str,
    *,
    focus_paths: list[str] | None = None,
    query: str | None = None,
    limit: int = 8,
) -> list[SkillMeta]:
    """Return skill metadata for discovery (not full bodies)."""
    focus_paths = [p.replace("\\", "/") for p in (focus_paths or [])]
    query_l = (query or "").strip().lower()
    scored: list[tuple[int, SkillMeta]] = []
    for meta in list_skills(workspace, user_id):
        if meta.manual_only and not query_l:
            continue
        score = 0
        if query_l:
            hay = f"{meta.name} {meta.description}".lower()
            if query_l in hay:
                score += 10
            for token in query_l.split():
                if token and token in hay:
                    score += 2
            if score == 0:
                continue
        if meta.globs and focus_paths:
            if any(any(fnmatch.fnmatch(fp, g) for g in meta.globs) for fp in focus_paths):
                score += 5
            elif not query_l:
                continue
        elif meta.globs and not focus_paths and not query_l:
            continue
        scored.append((score, meta))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [meta for _, meta in scored[:limit]]


def resolve_skill_path(
    workspace: Path | None,
    user_id: str,
    name: str,
    *,
    prefer_project: bool = True,
) -> tuple[Path, str]:
    """Return (skill_dir, scope)."""
    name = _validate_skill_name(name)
    ordered: list[tuple[Path, str, Path]] = []
    if prefer_project and workspace is not None:
        ordered.append((project_skills_dir(workspace) / name, "project", workspace.resolve()))
    ordered.append((user_skills_dir(user_id) / name, "user", user_skills_dir(user_id).resolve()))
    if not prefer_project and workspace is not None:
        ordered.append((project_skills_dir(workspace) / name, "project", workspace.resolve()))

    seen: set[str] = set()
    for skill_dir, scope, confine in ordered:
        key = str(skill_dir)
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved = skill_dir.resolve()
        except Exception as exc:
            raise PermissionError(f"skill 路径无效: {name}") from exc
        confine_str = str(confine)
        if not (str(resolved) == confine_str or str(resolved).startswith(confine_str + "/")):
            raise PermissionError(f"skill 路径越界: {name}")
        if scope == "project" and workspace is not None and not _is_inside(workspace, resolved):
            raise PermissionError(f"skill 路径越界: {name}")
        if (resolved / "SKILL.md").is_file():
            return resolved, scope
    raise FileNotFoundError(f"skill 不存在: {name}")


def load_skill(
    workspace: Path | None,
    user_id: str,
    name: str,
    *,
    max_chars: int = DEFAULT_SKILL_MAX_CHARS,
    resource_path: str | None = None,
) -> SkillContent:
    """Load SKILL.md (and optionally a resource file confined to the skill dir).

    Scripts under the skill directory are returned as text only — never executed.
    """
    skill_dir, scope = resolve_skill_path(workspace, user_id, name)
    skill_md = skill_dir / "SKILL.md"
    try:
        if workspace is not None and scope == "project":
            rel = skill_md.resolve().relative_to(workspace.resolve()).as_posix()
        else:
            rel = f"users/{user_id}/skills/{name}/SKILL.md"
    except Exception:
        rel = skill_md.name

    meta, body = _parse_skill_meta(skill_md, name=name, scope=scope, rel_path=rel)

    resources: list[str] = []
    extra = ""
    if resource_path:
        resource = _resolve_skill_resource(skill_dir, resource_path)
        try:
            resources.append(resource.relative_to(skill_dir).as_posix())
        except Exception:
            resources.append(resource.name)
        try:
            extra = resource.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"无法读取 skill 资源: {resource_path}: {exc}") from exc

    combined = body if not extra else f"{body}\n\n---\n# Resource: {resource_path}\n{extra}"
    truncated = len(combined) > max_chars
    combined = combined[:max_chars]
    return SkillContent(
        meta=meta,
        body=combined,
        resources=resources,
        chars=len(combined),
        truncated=truncated,
    )


def _resolve_skill_resource(skill_dir: Path, resource_path: str) -> Path:
    raw = (resource_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        raise PermissionError(f"skill 资源路径越界: {resource_path}")
    target = (skill_dir / raw).resolve()
    skill_resolved = skill_dir.resolve()
    if not (str(target) == str(skill_resolved) or str(target).startswith(str(skill_resolved) + "/")):
        raise PermissionError(f"skill 资源路径越界: {resource_path}")
    if not target.is_file():
        raise FileNotFoundError(f"skill 资源不存在: {resource_path}")
    return target


def list_skill_resources(workspace: Path | None, user_id: str, name: str) -> list[str]:
    skill_dir, _ = resolve_skill_path(workspace, user_id, name)
    files: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        try:
            rel = path.relative_to(skill_dir).as_posix()
        except Exception:
            continue
        if ".." in rel.split("/"):
            continue
        files.append(rel)
    return files
