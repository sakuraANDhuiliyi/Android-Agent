"""Persist selection intent, never credentials, across durable task boundaries."""
from __future__ import annotations

import copy
from typing import Any

from agent.config import Settings

_LIMITS = ("max_turns", "max_auto_continuations", "max_gradle_retries", "max_output_tokens")


def model_selection(settings: Settings) -> dict[str, Any]:
    return {
        "model_candidates": list(settings.model_candidates),
        "fallback_providers": [item.provider for item in settings.provider_fallbacks],
        "limits": {name: getattr(settings, name) for name in _LIMITS},
        "auto_build_after_edit": settings.auto_build_after_edit,
    }


def resolve_task_settings(base: Settings, task: dict[str, Any]) -> Settings:
    available = {item.provider: item for item in [base, *base.provider_fallbacks]}
    provider = task.get("provider") or base.provider
    if provider not in available:
        raise ValueError(f"任务使用的提供商尚未配置: {provider}")
    selected = copy.copy(available[provider])
    selected.model = task.get("model") or selected.model
    selection = (task.get("context") or {}).get("model_selection") or {}
    candidates = selection.get("model_candidates", selected.model_candidates)
    selected.model_candidates = list(dict.fromkeys([selected.model, *candidates]))
    # Legacy rows did not persist fallback consent. Do not infer permission
    # to send their prompts to additional providers from the worker catalog.
    fallback_names = selection.get("fallback_providers", [])
    selected.provider_fallbacks = []
    for name in fallback_names:
        if name == provider:
            continue
        if name not in available:
            raise ValueError(f"任务的备用提供商尚未配置: {name}")
        fallback = copy.copy(available[name])
        fallback.provider_fallbacks = []
        selected.provider_fallbacks.append(fallback)
    # In particular, guest tasks must retain their tighter per-request limits.
    for item in [selected, *selected.provider_fallbacks]:
        for name, value in selection.get("limits", {}).items():
            if name in _LIMITS and type(value) is int and value >= 0:
                setattr(item, name, min(getattr(item, name), value))
        if selection.get("auto_build_after_edit") is False:
            item.auto_build_after_edit = False
    return selected


def inherited_execution_context(context: dict[str, Any]) -> dict[str, Any]:
    """Only durable execution choices survive; progress/replay state does not."""
    return copy.deepcopy({key: context[key] for key in (
        "run_mode", "permission_profile", "attachments", "model_selection", "feedback_options"
    ) if key in context})
