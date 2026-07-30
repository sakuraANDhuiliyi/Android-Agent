from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.paths import validate_id
import agent.paths as paths


DEFAULT_RULE_MAX_CHARS = 8_000
DEFAULT_TOTAL_RULE_BUDGET = 24_000

# Patterns that attempt to override hard security — stripped / rejected.
_SECURITY_OVERRIDE_PATTERNS = (
    re.compile(r"(?i)bypass\s+(path|permission|approval|auth|sandbox|workspace)"),
    re.compile(r"(?i)ignore\s+(path|permission|approval|auth|security)\s+(rules?|checks?|restrictions?)"),
    re.compile(r"(?i)disable\s+(approval|auth|sandbox|permission)"),
    re.compile(r"(?i)grant\s+(root|sudo|unrestricted)\s+access"),
    re.compile(r"(?i)execute\s+arbitrary\s+(shell|code|script)"),
    re.compile(r"(?i)skip\s+user\s+(approval|confirmation)"),
)

RULE_SOURCE_BUILTIN = "builtin"
RULE_SOURCE_USER_GLOBAL = "user_global"
RULE_SOURCE_ROOT_AGENTS = "workspace_root_agents"
RULE_SOURCE_DOT_RULES = "dot_android_agent_rules"
RULE_SOURCE_SUBDIR_AGENTS = "subdir_agents"
RULE_SOURCE_USER_MESSAGE = "user_message_preference"

# Lower number = lower priority (loaded first, overridden by higher).
SOURCE_PRIORITY = {
    RULE_SOURCE_BUILTIN: 1,
    RULE_SOURCE_USER_GLOBAL: 2,
    RULE_SOURCE_ROOT_AGENTS: 3,
    RULE_SOURCE_DOT_RULES: 4,
    RULE_SOURCE_SUBDIR_AGENTS: 5,
    RULE_SOURCE_USER_MESSAGE: 6,
}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class RuleDocument:
    """A single rule file or fragment after frontmatter parsing."""

    id: str
    source: str
    path: str
    description: str = ""
    always: bool = False
    globs: list[str] = field(default_factory=list)
    exclude_globs: list[str] = field(default_factory=list)
    max_chars: int = DEFAULT_RULE_MAX_CHARS
    body: str = ""
    frontmatter_errors: list[str] = field(default_factory=list)
    security_stripped: bool = False

    @property
    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "path": self.path,
            "description": self.description,
            "always": self.always,
            "globs": list(self.globs),
            "exclude_globs": list(self.exclude_globs),
            "max_chars": self.max_chars,
            "body_chars": len(self.body),
            "priority": self.priority,
            "frontmatter_errors": list(self.frontmatter_errors),
            "security_stripped": self.security_stripped,
        }


@dataclass
class LoadedRule:
    """A rule selected for the current turn, with audit metadata."""

    rule: RuleDocument
    reason: str
    chars_used: int
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.rule.to_dict(),
            "reason": self.reason,
            "chars_used": self.chars_used,
            "truncated": self.truncated,
        }


@dataclass
class RulesBundle:
    """Result of loading rules for a turn."""

    builtin_prompt: str
    loaded: list[LoadedRule] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    total_chars: int = 0
    budget: int = DEFAULT_TOTAL_RULE_BUDGET
    audit_text: str = ""

    def composed_rules_text(self) -> str:
        parts: list[str] = []
        for item in self.loaded:
            header = f"### [{item.rule.source}] {item.rule.path}"
            if item.rule.description:
                header += f" — {item.rule.description}"
            parts.append(f"{header}\n{item.rule.body[:item.chars_used]}")
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "loaded": [item.to_dict() for item in self.loaded],
            "skipped": list(self.skipped),
            "total_chars": self.total_chars,
            "budget": self.budget,
            "audit_text": self.audit_text,
            "composed_chars": len(self.composed_rules_text()),
        }


def user_global_rules_dir(user_id: str) -> Path:
    return paths.DATA_DIR / "users" / validate_id(user_id, kind="user_id") / "rules"


def user_skills_dir(user_id: str) -> Path:
    return paths.DATA_DIR / "users" / validate_id(user_id, kind="user_id") / "skills"


def project_rules_dir(workspace: Path) -> Path:
    return workspace / ".android-agent" / "rules"


def project_skills_dir(workspace: Path) -> Path:
    return workspace / ".android-agent" / "skills"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    """Parse optional YAML frontmatter. Malicious/invalid keys are recorded, not applied blindly."""
    errors: list[str] = []
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, errors
    raw_meta, body = match.group(1), match.group(2)
    try:
        meta = yaml.safe_load(raw_meta) or {}
    except Exception as exc:
        return {}, body, [f"frontmatter parse error: {exc}"]
    if not isinstance(meta, dict):
        return {}, body, ["frontmatter must be a mapping"]
    # Reject unexpected executable-looking keys.
    forbidden = {"script", "command", "exec", "shell", "run", "hooks", "env"}
    cleaned: dict[str, Any] = {}
    for key, value in meta.items():
        key_str = str(key)
        if key_str.lower() in forbidden:
            errors.append(f"ignored forbidden frontmatter key: {key_str}")
            continue
        cleaned[key_str] = value
    return cleaned, body, errors


def _strip_security_overrides(body: str) -> tuple[str, bool]:
    stripped = False
    lines: list[str] = []
    for line in body.splitlines():
        if any(pat.search(line) for pat in _SECURITY_OVERRIDE_PATTERNS):
            stripped = True
            lines.append(f"[已移除：试图覆盖硬安全规则] {line[:80]}")
        else:
            lines.append(line)
    return "\n".join(lines).strip(), stripped


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _coerce_max_chars(value: Any, default: int = DEFAULT_RULE_MAX_CHARS) -> int:
    try:
        n = int(value)
    except Exception:
        return default
    return max(200, min(n, DEFAULT_RULE_MAX_CHARS * 2))


def _is_inside(workspace: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(workspace.resolve())
        return True
    except Exception:
        return False


def _read_rule_file(
    path: Path,
    *,
    workspace: Path | None,
    source: str,
    rule_id: str,
) -> RuleDocument | None:
    if workspace is not None and not _is_inside(workspace, path):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    meta, body, errors = parse_frontmatter(text)
    body, security_stripped = _strip_security_overrides(body)
    if not body.strip() and not meta:
        return None
    rel = str(path)
    if workspace is not None:
        try:
            rel = str(path.resolve().relative_to(workspace.resolve()))
        except Exception:
            rel = str(path)
    return RuleDocument(
        id=rule_id,
        source=source,
        path=rel,
        description=str(meta.get("description") or "").strip(),
        always=_coerce_bool(meta.get("always"), default=(source in {
            RULE_SOURCE_ROOT_AGENTS,
            RULE_SOURCE_USER_GLOBAL,
        })),
        globs=_coerce_str_list(meta.get("globs")),
        exclude_globs=_coerce_str_list(meta.get("exclude_globs")),
        max_chars=_coerce_max_chars(meta.get("max_chars")),
        body=body.strip(),
        frontmatter_errors=errors,
        security_stripped=security_stripped,
    )


def _match_globs(path: str, globs: list[str]) -> bool:
    if not globs:
        return True
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in globs)


def _excluded(path: str, exclude_globs: list[str]) -> bool:
    if not exclude_globs:
        return False
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in exclude_globs)


def discover_rules(
    workspace: Path,
    user_id: str,
    *,
    focus_paths: list[str] | None = None,
) -> list[RuleDocument]:
    """Discover all candidate rule documents (not yet filtered by budget)."""
    rules: list[RuleDocument] = []
    focus_paths = focus_paths or []

    # User global rules.
    global_dir = user_global_rules_dir(user_id)
    if global_dir.is_dir():
        for path in sorted(global_dir.glob("*.md")):
            doc = _read_rule_file(
                path,
                workspace=None,
                source=RULE_SOURCE_USER_GLOBAL,
                rule_id=f"user:{path.name}",
            )
            if doc:
                # Ensure global rules stay under user data dir.
                if not str(path.resolve()).startswith(str(global_dir.resolve())):
                    continue
                rules.append(doc)

    # Workspace root AGENTS.md
    root_agents = workspace / "AGENTS.md"
    if root_agents.is_file():
        doc = _read_rule_file(
            root_agents,
            workspace=workspace,
            source=RULE_SOURCE_ROOT_AGENTS,
            rule_id="workspace:AGENTS.md",
        )
        if doc:
            doc.always = True if not doc.globs else doc.always
            rules.append(doc)

    # .android-agent/rules/*.md
    rules_dir = project_rules_dir(workspace)
    if rules_dir.is_dir():
        for path in sorted(rules_dir.glob("*.md")):
            if not _is_inside(workspace, path):
                continue
            doc = _read_rule_file(
                path,
                workspace=workspace,
                source=RULE_SOURCE_DOT_RULES,
                rule_id=f"rules:{path.name}",
            )
            if doc:
                rules.append(doc)

    # Subdirectory AGENTS.md near focus paths.
    seen_subdir: set[str] = set()
    for focus in focus_paths:
        focus_path = (workspace / focus).resolve() if not Path(focus).is_absolute() else Path(focus).resolve()
        if not _is_inside(workspace, focus_path):
            continue
        current = focus_path if focus_path.is_dir() else focus_path.parent
        workspace_resolved = workspace.resolve()
        while True:
            try:
                current.relative_to(workspace_resolved)
            except Exception:
                break
            if current == workspace_resolved:
                break
            agents = current / "AGENTS.md"
            key = str(agents)
            if key not in seen_subdir and agents.is_file():
                seen_subdir.add(key)
                doc = _read_rule_file(
                    agents,
                    workspace=workspace,
                    source=RULE_SOURCE_SUBDIR_AGENTS,
                    rule_id=f"subdir:{agents.relative_to(workspace_resolved).as_posix()}",
                )
                if doc:
                    rules.append(doc)
            if current.parent == current:
                break
            current = current.parent

    rules.sort(key=lambda r: (r.priority, r.path))
    return rules


def select_rules(
    candidates: list[RuleDocument],
    *,
    focus_paths: list[str] | None = None,
    budget: int = DEFAULT_TOTAL_RULE_BUDGET,
    user_preferences: str | None = None,
) -> RulesBundle:
    """Select rules for this turn by always/glob match and budget."""
    focus_paths = [p.replace("\\", "/") for p in (focus_paths or [])]
    loaded: list[LoadedRule] = []
    skipped: list[dict[str, Any]] = []
    remaining = budget

    # Higher priority last so they appear later (stronger) in the composed text,
    # but we allocate budget from high priority first.
    ordered = sorted(candidates, key=lambda r: (-r.priority, r.path))

    for rule in ordered:
        if remaining <= 0:
            skipped.append({**rule.to_dict(), "reason": "budget_exhausted"})
            continue

        reason: str | None = None
        if rule.always or (not rule.globs and rule.source in {
            RULE_SOURCE_ROOT_AGENTS,
            RULE_SOURCE_USER_GLOBAL,
            RULE_SOURCE_DOT_RULES,
        } and not rule.exclude_globs):
            # Dot rules without globs default to always for discoverability,
            # but still respect exclude_globs against focus paths.
            if focus_paths and any(_excluded(fp, rule.exclude_globs) for fp in focus_paths):
                skipped.append({**rule.to_dict(), "reason": "excluded_by_glob"})
                continue
            reason = "always" if rule.always or not rule.globs else "matched_glob"
        if reason is None:
            matched = False
            for fp in focus_paths:
                if _excluded(fp, rule.exclude_globs):
                    continue
                if _match_globs(fp, rule.globs):
                    matched = True
                    break
            if matched:
                reason = "matched_glob"
            elif not focus_paths and not rule.globs:
                reason = "always"
            else:
                skipped.append({**rule.to_dict(), "reason": "glob_not_matched"})
                continue

        if rule.frontmatter_errors:
            # Still load body, but keep errors in audit.
            pass

        allowed = min(rule.max_chars, remaining, len(rule.body))
        truncated = allowed < len(rule.body)
        body_slice = rule.body[:allowed]
        loaded.append(
            LoadedRule(
                rule=RuleDocument(
                    id=rule.id,
                    source=rule.source,
                    path=rule.path,
                    description=rule.description,
                    always=rule.always,
                    globs=rule.globs,
                    exclude_globs=rule.exclude_globs,
                    max_chars=rule.max_chars,
                    body=body_slice,
                    frontmatter_errors=rule.frontmatter_errors,
                    security_stripped=rule.security_stripped,
                ),
                reason=reason or "always",
                chars_used=len(body_slice),
                truncated=truncated,
            )
        )
        remaining -= len(body_slice)

    # User message non-security preferences (lowest of model-visible additions,
    # but highest among optional prefs — still below hard security).
    if user_preferences and user_preferences.strip() and remaining > 0:
        pref_body, stripped = _strip_security_overrides(user_preferences.strip())
        allowed = min(DEFAULT_RULE_MAX_CHARS // 2, remaining, len(pref_body))
        if allowed > 0 and pref_body:
            loaded.append(
                LoadedRule(
                    rule=RuleDocument(
                        id="user_message_preference",
                        source=RULE_SOURCE_USER_MESSAGE,
                        path="(user message)",
                        description="当前用户消息中的非安全偏好",
                        always=True,
                        body=pref_body[:allowed],
                        security_stripped=stripped,
                    ),
                    reason="user_message",
                    chars_used=allowed,
                    truncated=allowed < len(pref_body),
                )
            )
            remaining -= allowed

    # Restore ascending priority order for composition (builtin first).
    loaded.sort(key=lambda item: (item.rule.priority, item.rule.path))
    total_chars = sum(item.chars_used for item in loaded)
    audit_lines = [
        f"- {item.rule.source}/{item.rule.path}: reason={item.reason}, "
        f"chars={item.chars_used}"
        + (", truncated" if item.truncated else "")
        + (", security_stripped" if item.rule.security_stripped else "")
        + (f", frontmatter_errors={item.rule.frontmatter_errors}" if item.rule.frontmatter_errors else "")
        for item in loaded
    ]
    for item in skipped:
        audit_lines.append(
            f"- SKIP {item.get('source')}/{item.get('path')}: {item.get('reason')}"
        )
    audit_text = (
        f"Rules loaded ({len(loaded)}), skipped ({len(skipped)}), "
        f"chars={total_chars}/{budget}\n" + "\n".join(audit_lines)
    )
    return RulesBundle(
        builtin_prompt="",
        loaded=loaded,
        skipped=skipped,
        total_chars=total_chars,
        budget=budget,
        audit_text=audit_text,
    )


def load_rules_for_turn(
    workspace: Path,
    user_id: str,
    *,
    focus_paths: list[str] | None = None,
    budget: int = DEFAULT_TOTAL_RULE_BUDGET,
    user_preferences: str | None = None,
    builtin_prompt: str = "",
) -> RulesBundle:
    candidates = discover_rules(workspace, user_id, focus_paths=focus_paths)
    bundle = select_rules(
        candidates,
        focus_paths=focus_paths,
        budget=budget,
        user_preferences=user_preferences,
    )
    bundle.builtin_prompt = builtin_prompt
    return bundle


def diagnose_rules(
    workspace: Path,
    user_id: str,
    *,
    focus_paths: list[str] | None = None,
    budget: int = DEFAULT_TOTAL_RULE_BUDGET,
) -> dict[str, Any]:
    candidates = discover_rules(workspace, user_id, focus_paths=focus_paths)
    bundle = select_rules(candidates, focus_paths=focus_paths, budget=budget)
    return {
        "candidates": [c.to_dict() for c in candidates],
        **bundle.to_dict(),
        "priority_order": [
            RULE_SOURCE_BUILTIN,
            RULE_SOURCE_USER_GLOBAL,
            RULE_SOURCE_ROOT_AGENTS,
            RULE_SOURCE_DOT_RULES,
            RULE_SOURCE_SUBDIR_AGENTS,
            RULE_SOURCE_USER_MESSAGE,
        ],
        "hard_security_note": (
            "Rules only affect model instructions. Path, permission, auth, "
            "and approval hard rules cannot be overridden."
        ),
    }
