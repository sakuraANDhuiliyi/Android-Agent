from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], None]


@dataclass
class _Fn:
    name: str = ""
    arguments: str = ""


@dataclass
class _ToolCall:
    id: str = ""
    type: str = "function"
    function: _Fn = field(default_factory=_Fn)


@dataclass
class _Message:
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str | None = None


@dataclass
class _Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    # Anthropic-compatible aliases
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class StreamedCompletion:
    choices: list[_Choice]
    usage: _Usage | None = None
    # Anthropic-shaped fields used by the anthropic loop path
    content: list[Any] = field(default_factory=list)
    stop_reason: str | None = None


class _DeltaFlusher:
    """Coalesce tiny token deltas so the event store/UI are not flooded."""

    def __init__(
        self,
        on_event: EventCallback | None,
        *,
        min_chars: int = 12,
        max_interval: float = 0.05,
    ) -> None:
        self._on_event = on_event
        self._min_chars = min_chars
        self._max_interval = max_interval
        self._buf: list[str] = []
        self._last = time.monotonic()

    def push(self, chunk: str) -> None:
        if not chunk:
            return
        self._buf.append(chunk)
        now = time.monotonic()
        if "".join(self._buf).__len__() >= self._min_chars or (
            now - self._last
        ) >= self._max_interval:
            self.flush()

    def flush(self) -> None:
        if not self._buf or not self._on_event:
            self._buf.clear()
            return
        text = "".join(self._buf)
        self._buf.clear()
        self._last = time.monotonic()
        self._on_event(
            "text_delta",
            {"content": text, "delta": text, "message": text},
        )


def stream_openai_chat(
    client,
    *,
    model: str,
    on_event: EventCallback | None = None,
    cancel_check: CancelCheck | None = None,
    **kwargs: Any,
) -> StreamedCompletion:
    """Stream an OpenAI-compatible chat completion and emit text_delta events."""
    request = dict(kwargs)
    request["model"] = model
    request["stream"] = True

    try:
        stream = client.chat.completions.create(
            **request,
            stream_options={"include_usage": True},
        )
    except TypeError:
        stream = client.chat.completions.create(**request)
    except Exception:
        # Some providers reject stream_options; retry without it.
        stream = client.chat.completions.create(**request)

    content_parts: list[str] = []
    tool_acc: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    usage: _Usage | None = None
    flusher = _DeltaFlusher(on_event)

    for event in stream:
        if cancel_check:
            cancel_check()
        if getattr(event, "usage", None):
            u = event.usage
            usage = _Usage(
                prompt_tokens=getattr(u, "prompt_tokens", None),
                completion_tokens=getattr(u, "completion_tokens", None),
                total_tokens=getattr(u, "total_tokens", None),
            )
        choices = getattr(event, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        piece = getattr(delta, "content", None)
        if piece:
            content_parts.append(piece)
            flusher.push(piece)
        for tc in getattr(delta, "tool_calls", None) or []:
            idx = getattr(tc, "index", 0) or 0
            slot = tool_acc.setdefault(
                idx,
                {"id": "", "name": "", "arguments": ""},
            )
            if getattr(tc, "id", None):
                slot["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"] = fn.name
                if getattr(fn, "arguments", None):
                    slot["arguments"] += fn.arguments

    flusher.flush()
    full_text = "".join(content_parts) or None
    tool_calls = None
    if tool_acc:
        tool_calls = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            tool_calls.append(
                _ToolCall(
                    id=slot["id"] or f"call_{idx}",
                    function=_Fn(name=slot["name"], arguments=slot["arguments"]),
                )
            )

    return StreamedCompletion(
        choices=[
            _Choice(
                message=_Message(content=full_text, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
        stop_reason=finish_reason,
    )


@dataclass
class _AnthropicText:
    type: str = "text"
    text: str = ""


@dataclass
class _AnthropicToolUse:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


def stream_anthropic_message(
    client,
    *,
    model: str,
    on_event: EventCallback | None = None,
    cancel_check: CancelCheck | None = None,
    **kwargs: Any,
) -> StreamedCompletion:
    """Stream an Anthropic messages response and emit text_delta events."""
    flusher = _DeltaFlusher(on_event)
    text_parts: list[str] = []
    tool_uses: list[_AnthropicToolUse] = []
    stop_reason: str | None = None
    usage: _Usage | None = None

    # Prefer the context-manager streaming API when available
    with client.messages.stream(model=model, **kwargs) as stream:
        current_tool: _AnthropicToolUse | None = None
        tool_json: list[str] = []
        for event in stream:
            if cancel_check:
                cancel_check()
            etype = getattr(event, "type", "")
            if etype == "content_block_start":
                block = getattr(event, "content_block", None)
                if block is not None and getattr(block, "type", "") == "tool_use":
                    current_tool = _AnthropicToolUse(
                        id=getattr(block, "id", "") or "",
                        name=getattr(block, "name", "") or "",
                        input={},
                    )
                    tool_json = []
            elif etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                dtype = getattr(delta, "type", "")
                if dtype == "text_delta":
                    piece = getattr(delta, "text", "") or ""
                    if piece:
                        text_parts.append(piece)
                        flusher.push(piece)
                elif dtype == "input_json_delta" and current_tool is not None:
                    tool_json.append(getattr(delta, "partial_json", "") or "")
            elif etype == "content_block_stop":
                if current_tool is not None:
                    raw = "".join(tool_json)
                    try:
                        current_tool.input = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        current_tool.input = {}
                    tool_uses.append(current_tool)
                    current_tool = None
                    tool_json = []
            elif etype == "message_delta":
                stop_reason = getattr(getattr(event, "delta", None), "stop_reason", None) or stop_reason
                u = getattr(event, "usage", None)
                if u is not None:
                    usage = _Usage(
                        input_tokens=getattr(u, "input_tokens", None),
                        output_tokens=getattr(u, "output_tokens", None),
                        prompt_tokens=getattr(u, "input_tokens", None),
                        completion_tokens=getattr(u, "output_tokens", None),
                        total_tokens=(
                            (getattr(u, "input_tokens", 0) or 0)
                            + (getattr(u, "output_tokens", 0) or 0)
                        )
                        or None,
                    )

        # Final message may carry usage on some SDK versions
        try:
            final = stream.get_final_message()
            if usage is None and getattr(final, "usage", None):
                u = final.usage
                usage = _Usage(
                    input_tokens=getattr(u, "input_tokens", None),
                    output_tokens=getattr(u, "output_tokens", None),
                    prompt_tokens=getattr(u, "input_tokens", None),
                    completion_tokens=getattr(u, "output_tokens", None),
                    total_tokens=(
                        (getattr(u, "input_tokens", 0) or 0)
                        + (getattr(u, "output_tokens", 0) or 0)
                    )
                    or None,
                )
            if not stop_reason:
                stop_reason = getattr(final, "stop_reason", None)
        except Exception:
            pass

    flusher.flush()
    content: list[Any] = []
    full_text = "".join(text_parts)
    if full_text:
        content.append(_AnthropicText(text=full_text))
    content.extend(tool_uses)

    return StreamedCompletion(
        choices=[
            _Choice(
                message=_Message(
                    content=full_text or None,
                    tool_calls=None,
                ),
                finish_reason=stop_reason,
            )
        ],
        usage=usage,
        content=content,
        stop_reason=stop_reason,
    )
