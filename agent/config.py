from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from agent.model_fallback import unique_models
from agent.paths import DEFAULT_USER_ID, ROOT, validate_id

CONFIG_PATH = ROOT / "config.yaml"

PROVIDER_DEFAULTS = {
    "anthropic": {
        "model": "claude-sonnet-4-20250514",
        "model_fallbacks": [],
        "base_url": None,
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url_env": "ANTHROPIC_BASE_URL",
    },
    "deepseek": {
        "model": "deepseek-v4-pro",
        "model_fallbacks": ["deepseek-v4-flash"],
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
    },
}

DEFAULT_PROVIDER_FALLBACKS: dict[str, list[str]] = {
    "deepseek": [],
    "anthropic": [],
}


@dataclass
class UserAccount:
    id: str
    token: str


@dataclass
class Settings:
    provider: str
    api_key: str
    model: str
    model_candidates: list[str]
    max_turns: int
    max_auto_continuations: int
    max_gradle_retries: int
    compact_max_chars: int
    max_output_tokens: int
    base_url: str | None
    auto_build_after_edit: bool
    server_host: str
    server_port: int
    api_token: str
    tavily_api_key: str = ""
    users: list[UserAccount] = field(default_factory=list)
    provider_fallbacks: list["Settings"] = field(default_factory=list)


def _resolve_api_key(provider: str, file_data: dict[str, Any], *, is_primary: bool) -> str:
    defaults = PROVIDER_DEFAULTS[provider]
    candidates: list[str | None] = []

    if is_primary:
        candidates.extend(
            [
                os.environ.get("AGENT_API_KEY"),
                os.environ.get(defaults["api_key_env"]),
                file_data.get("api_key"),
            ]
        )

    candidates.extend(
        [
            file_data.get(f"{provider}_api_key"),
            file_data.get("deepseek_api_key") if provider == "deepseek" else None,
            file_data.get("anthropic_api_key") if provider == "anthropic" else None,
        ]
    )

    for value in candidates:
        if value:
            return str(value).strip()
    return ""


def _resolve_base_url(provider: str, file_data: dict[str, Any], *, is_primary: bool) -> str | None:
    defaults = PROVIDER_DEFAULTS[provider]
    candidates: list[str | None] = []

    if is_primary:
        candidates.extend(
            [
                os.environ.get("AGENT_BASE_URL"),
                os.environ.get(defaults["base_url_env"]),
                file_data.get("base_url"),
            ]
        )

    candidates.extend(
        [
            file_data.get(f"{provider}_base_url"),
            file_data.get("deepseek_base_url") if provider == "deepseek" else None,
            file_data.get("anthropic_base_url") if provider == "anthropic" else None,
            defaults["base_url"],
        ]
    )

    for value in candidates:
        if value:
            cleaned = str(value).strip()
            if cleaned:
                return cleaned
    return None


def _build_settings(
    provider: str,
    file_data: dict[str, Any],
    *,
    is_primary: bool,
    shared: dict[str, Any],
) -> Settings:
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"不支持的 provider: {provider}")

    defaults = PROVIDER_DEFAULTS[provider]
    model_key = "model" if is_primary else f"{provider}_model"
    primary_model = str(file_data.get(model_key, defaults["model"]))

    if is_primary:
        model_fallbacks = file_data.get("model_fallbacks", defaults.get("model_fallbacks", []))
    else:
        model_fallbacks = file_data.get(
            f"{provider}_model_fallbacks",
            defaults.get("model_fallbacks", []),
        )

    model_candidates = unique_models(primary_model, list(model_fallbacks or []))

    return Settings(
        provider=provider,
        api_key=_resolve_api_key(provider, file_data, is_primary=is_primary),
        model=model_candidates[0],
        model_candidates=model_candidates,
        max_turns=int(shared["max_turns"]),
        max_auto_continuations=int(shared["max_auto_continuations"]),
        max_gradle_retries=int(shared["max_gradle_retries"]),
        compact_max_chars=int(shared["compact_max_chars"]),
        max_output_tokens=int(shared["max_output_tokens"]),
        base_url=_resolve_base_url(provider, file_data, is_primary=is_primary),
        auto_build_after_edit=bool(shared["auto_build_after_edit"]),
        server_host=str(shared["server_host"]),
        server_port=int(shared["server_port"]),
        api_token=str(shared["api_token"]),
        tavily_api_key=str(shared.get("tavily_api_key", "") or ""),
        users=list(shared["users"]),
        provider_fallbacks=[],
    )


def _load_users(file_data: dict[str, Any], legacy_api_token: str) -> list[UserAccount]:
    raw_users = file_data.get("users")
    users: list[UserAccount] = []
    seen: set[str] = set()

    if isinstance(raw_users, list):
        for item in raw_users:
            if not isinstance(item, dict):
                continue
            try:
                user_id = validate_id(str(item.get("id", "")), kind="user_id")
            except ValueError:
                continue
            if user_id in seen:
                continue
            token = str(item.get("token", "") or "").strip()
            if not token:
                continue
            seen.add(user_id)
            users.append(
                UserAccount(
                    id=user_id,
                    token=token,
                )
            )

    if not users and legacy_api_token:
        users.append(
            UserAccount(
                id=DEFAULT_USER_ID,
                token=legacy_api_token,
            )
        )
    return users


def resolve_user_id(settings: Settings, authorization: str | None) -> str:
    """Map Bearer token to user_id. Empty Authorization matches a user with empty token."""
    token = ""
    if authorization:
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        else:
            token = auth

    matches = [user for user in settings.users if user.token == token]
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        raise PermissionError("多个用户使用了相同 Token，请检查 config.yaml")
    if not token:
        raise PermissionError("未提供 API Token")
    raise PermissionError("无效的 API Token")


def load_settings() -> Settings:
    file_data: dict[str, Any] = {}
    if CONFIG_PATH.is_file():
        with CONFIG_PATH.open(encoding="utf-8") as f:
            file_data = yaml.safe_load(f) or {}

    provider = (
        os.environ.get("AGENT_PROVIDER")
        or file_data.get("provider")
        or "deepseek"
    ).strip().lower()

    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"不支持的 provider: {provider}")

    legacy_api_token = str(file_data.get("api_token", "") or "").strip()
    users = _load_users(file_data, legacy_api_token)

    shared = {
        "max_turns": int(file_data.get("max_turns", 15)),
        "max_auto_continuations": int(file_data.get("max_auto_continuations", 2)),
        "max_gradle_retries": int(file_data.get("max_gradle_retries", 3)),
        "compact_max_chars": int(file_data.get("compact_max_chars", 2_500_000)),
        # DeepSeek V4 max output is 384K; agent default leaves headroom for thinking + tools
        "max_output_tokens": max(
            1024,
            min(int(file_data.get("max_output_tokens", 65_536)), 384_000),
        ),
        "auto_build_after_edit": bool(file_data.get("auto_build_after_edit", False)),
        "server_host": str(file_data.get("server_host", "0.0.0.0")),
        "server_port": int(file_data.get("server_port", 8000)),
        "api_token": legacy_api_token,
        "tavily_api_key": (
            os.environ.get("TAVILY_API_KEY")
            or file_data.get("tavily_api_key")
            or ""
        ).strip(),
        "users": users,
    }

    primary = _build_settings(provider, file_data, is_primary=True, shared=shared)

    configured_fallbacks = file_data.get("provider_fallbacks")
    if configured_fallbacks is None:
        configured_fallbacks = DEFAULT_PROVIDER_FALLBACKS.get(provider, [])

    fallback_settings: list[Settings] = []
    for fallback_provider in configured_fallbacks:
        name = str(fallback_provider).strip().lower()
        if not name or name == provider or name not in PROVIDER_DEFAULTS:
            continue
        fallback_settings.append(
            _build_settings(name, file_data, is_primary=False, shared=shared)
        )

    primary.provider_fallbacks = fallback_settings
    return primary


def list_configured_providers(settings: Settings) -> list[Settings]:
    seen: set[str] = set()
    providers: list[Settings] = []
    for item in [settings, *settings.provider_fallbacks]:
        if item.api_key and item.provider not in seen:
            seen.add(item.provider)
            providers.append(item)
    return providers


def provider_option_dict(item: Settings, *, is_default: bool = False) -> dict[str, Any]:
    return {
        "id": item.provider,
        "provider": item.provider,
        "model": item.model,
        "model_candidates": item.model_candidates,
        "label": f"{item.provider} / {item.model}",
        "configured": bool(item.api_key),
        "is_default": is_default,
    }


def _model_option(
    item: Settings,
    model_name: str,
    *,
    is_default: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"{item.provider}/{model_name}",
        "provider": item.provider,
        "model": model_name,
        "model_candidates": [model_name],
        "label": f"{item.provider} / {model_name}",
        "configured": bool(item.api_key),
        "is_default": is_default,
    }


def models_catalog(settings: Settings) -> dict[str, Any]:
    models: list[dict[str, Any]] = [
        {
            "id": "auto",
            "provider": "auto",
            "model": settings.model,
            "model_candidates": settings.model_candidates,
            "label": "自动（含备用切换）",
            "configured": True,
            "is_default": False,
        }
    ]
    for item in list_configured_providers(settings):
        candidates = item.model_candidates or [item.model]
        if len(candidates) == 1:
            models.append(
                _model_option(
                    item,
                    candidates[0],
                    is_default=item.provider == settings.provider
                    and candidates[0] == settings.model,
                )
            )
        else:
            for model_name in candidates:
                models.append(
                    _model_option(
                        item,
                        model_name,
                        is_default=item.provider == settings.provider
                        and model_name == settings.model,
                    )
                )
    return {
        "default_provider": settings.provider,
        "default_model": settings.model,
        "models": models,
    }


def resolve_job_settings(
    base: Settings,
    provider: str | None,
    *,
    auto_fallback: bool = False,
    model: str | None = None,
) -> Settings:
    """Resolve provider/model for a job.

    ``provider`` may be:
    - None / \"auto\"
    - provider name (e.g. \"deepseek\")
    - catalog id \"provider/model\" (e.g. \"deepseek/deepseek-v4-pro\")
    """
    selected_model = (model or "").strip() or None
    provider_name = (provider or "").strip() or None

    if provider_name and "/" in provider_name and provider_name != "auto":
        maybe_provider, maybe_model = provider_name.split("/", 1)
        provider_name = maybe_provider.strip() or None
        if not selected_model:
            selected_model = maybe_model.strip() or None

    if not provider_name or provider_name == "auto":
        selected = base if auto_fallback else replace(base, provider_fallbacks=[])
        if selected_model:
            candidates = unique_models(selected_model, selected.model_candidates)
            selected = replace(
                selected,
                model=selected_model,
                model_candidates=candidates,
            )
        return selected

    for item in list_configured_providers(base):
        if item.provider != provider_name:
            continue
        selected = replace(item, provider_fallbacks=[])
        if selected_model:
            candidates = unique_models(selected_model, item.model_candidates)
            selected = replace(
                selected,
                model=selected_model,
                model_candidates=candidates,
            )
        if auto_fallback:
            fallbacks = [
                replace(fallback, provider_fallbacks=[])
                for fallback in base.provider_fallbacks
                if fallback.provider != provider_name and fallback.api_key
            ]
            selected = replace(selected, provider_fallbacks=fallbacks)
        return selected

    raise ValueError(f"未配置的提供商: {provider_name}")
