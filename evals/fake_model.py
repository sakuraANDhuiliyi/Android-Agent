"""Deterministic fake model script runner — no network, no paid APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable


@dataclass
class FakeTurn:
    """One model response step."""

    text: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    raise_error: Exception | None = None
    model: str = "fake-model"


@dataclass
class FakeModelScript:
    turns: list[FakeTurn]
    _idx: int = 0

    def next_response(self) -> tuple[Any, str]:
        if self._idx >= len(self.turns):
            turn = FakeTurn(text="(script exhausted)", finish_reason="stop")
        else:
            turn = self.turns[self._idx]
            self._idx += 1
        if turn.raise_error is not None:
            raise turn.raise_error
        tool_calls = []
        for i, tc in enumerate(turn.tool_calls):
            tool_calls.append(
                SimpleNamespace(
                    id=tc.get("id", f"call_{i}"),
                    function=SimpleNamespace(
                        name=tc["name"],
                        arguments=tc.get("arguments", "{}"),
                    ),
                )
            )
        response = SimpleNamespace(
            id=f"resp_{self._idx}",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=turn.text,
                        tool_calls=tool_calls,
                    ),
                    finish_reason=turn.finish_reason
                    if not tool_calls
                    else "tool_calls",
                )
            ],
            usage=None,
        )
        return response, turn.model

    def as_side_effect(self) -> Callable[..., tuple[Any, str]]:
        def _call(*_a: Any, **_k: Any) -> tuple[Any, str]:
            return self.next_response()

        return _call


def tool_call(name: str, arguments: dict[str, Any] | str, call_id: str | None = None) -> dict[str, Any]:
    import json

    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    payload: dict[str, Any] = {"name": name, "arguments": args}
    if call_id:
        payload["id"] = call_id
    return payload
