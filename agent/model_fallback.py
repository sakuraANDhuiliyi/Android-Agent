from __future__ import annotations


def unique_models(primary: str, fallbacks: list[str] | None = None) -> list[str]:
    models: list[str] = []
    for name in [primary, *(fallbacks or [])]:
        cleaned = str(name).strip()
        if cleaned and cleaned not in models:
            models.append(cleaned)
    return models


def should_try_next_model(exc: Exception) -> bool:
    text = str(exc).lower()
    if any(
        token in text
        for token in (
            "api key",
            "authentication",
            "unauthorized",
            "invalid_api_key",
            "incorrect api key",
            "鉴权",
            "密钥",
        )
    ):
        return False
    if any(
        token in text
        for token in (
            "model",
            "not found",
            "does not exist",
            "invalid",
            "unsupported",
            "access denied",
            "permission",
            "arrearage",
            "quota",
            "rate limit",
            "timeout",
            "503",
            "429",
            "404",
            "400",
        )
    ):
        return True
    return False
