from __future__ import annotations

import json
import time
from typing import Any, Callable

from agent.compact import (
    build_session_prior_messages,
    compact_anthropic_messages,
    compact_openai_messages,
)
from agent.config import Settings
from agent.honesty import (
    EDIT_TOOLS,
    download_tool_nudge_message,
    honesty_nudge_message,
    prompt_expects_file_edit,
    text_asks_download_permission,
    text_awaits_user_action,
    text_claims_file_edit,
)
from agent.model_fallback import should_try_next_model, should_try_next_provider
from agent.prompts import get_system_prompt
from agent.stream import StreamedCompletion, stream_anthropic_message, stream_openai_chat
from agent.tools import dispatch_tool, get_tool_definitions

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], None]


class CancellationRequested(RuntimeError):
    pass


def _total_turns(settings: Settings) -> int:
    batches = max(1, 1 + settings.max_auto_continuations)
    return max(1, settings.max_turns) * batches


def _emit_continuation(on_event: EventCallback | None, turn: int, settings: Settings) -> None:
    if turn > 1 and (turn - 1) % settings.max_turns == 0:
        batch = (turn - 1) // settings.max_turns + 1
        _emit(
            on_event,
            "auto_continue",
            f"已用完一批 {settings.max_turns} 轮，自动继续第 {batch} 批",
            batch=batch,
            max_batches=1 + settings.max_auto_continuations,
        )


def run_agent(
    settings: Settings,
    workspace,
    user_id: str,
    project_id: str,
    user_prompt: str,
    on_event: EventCallback | None = None,
    cancel_check: CancelCheck | None = None,
    prior_turns: list[dict[str, Any]] | None = None,
    task_id: str | None = None,
    set_status: Callable[[str], None] | None = None,
) -> str:
    provider_chain = [settings, *settings.provider_fallbacks]
    errors: list[str] = []
    prior_messages = build_session_prior_messages(prior_turns or [])

    for index, current_settings in enumerate(provider_chain):
        if cancel_check:
            cancel_check()
        if not current_settings.api_key:
            errors.append(f"{current_settings.provider}: 未配置 API Key")
            continue

        if index > 0:
            switch_msg = (
                f"已切换提供商: {provider_chain[index - 1].provider}"
                f" → {current_settings.provider}"
            )
            _emit(
                on_event,
                "provider_switch",
                switch_msg,
                from_provider=provider_chain[index - 1].provider,
                to_provider=current_settings.provider,
                model=current_settings.model,
            )

        try:
            return _run_agent_with_provider(
                current_settings,
                workspace,
                user_id,
                project_id,
                user_prompt,
                on_event,
                cancel_check,
                prior_messages,
                task_id=task_id,
                set_status=set_status,
            )
        except CancellationRequested:
            raise
        except Exception as exc:
            errors.append(f"{current_settings.provider}: {exc}")
            can_fallback = should_try_next_provider(exc) and index < len(provider_chain) - 1
            if can_fallback:
                retry_msg = (
                    f"提供商 {current_settings.provider} 不可用，"
                    f"尝试 {provider_chain[index + 1].provider}..."
                )
                _emit(
                    on_event,
                    "provider_switch",
                    retry_msg,
                    from_provider=current_settings.provider,
                    error=str(exc),
                )
                continue
            # Tool/internal bugs (e.g. KeyError 'path') are not provider outages
            if not should_try_next_provider(exc):
                raise RuntimeError(f"{current_settings.provider} 任务失败: {exc}") from exc
            break

    raise RuntimeError("所有提供商均不可用:\n" + "\n".join(errors))


def _run_agent_with_provider(
    settings: Settings,
    workspace,
    user_id: str,
    project_id: str,
    user_prompt: str,
    on_event: EventCallback | None,
    cancel_check: CancelCheck | None,
    prior_messages: list[dict[str, Any]],
    *,
    task_id: str | None = None,
    set_status: Callable[[str], None] | None = None,
) -> str:
    system_prompt = get_system_prompt(settings)

    if settings.provider == "anthropic":
        return _run_anthropic(
            settings,
            workspace,
            user_id,
            project_id,
            user_prompt,
            system_prompt,
            on_event,
            cancel_check,
            prior_messages,
            task_id=task_id,
            set_status=set_status,
        )
    return _run_openai_compatible(
        settings,
        workspace,
        user_id,
        project_id,
        user_prompt,
        system_prompt,
        on_event,
        cancel_check,
        prior_messages,
        task_id=task_id,
        set_status=set_status,
    )


def _emit(
    on_event: EventCallback | None,
    event_type: str,
    message: str | None = None,
    **data: Any,
) -> None:
    if message:
        print(message)
    if on_event:
        payload = dict(data)
        if message is not None:
            payload["message"] = message
        on_event(event_type, payload)


def _format_tool_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False)


def _openai_tools(settings: Settings) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in get_tool_definitions(settings)
    ]


def _chat_completion_with_fallback(
    client,
    model_candidates: list[str],
    active_model: str,
    on_event: EventCallback | None,
    cancel_check: CancelCheck | None = None,
    **kwargs: Any,
):
    errors: list[str] = []
    start_index = model_candidates.index(active_model) if active_model in model_candidates else 0
    for model_name in model_candidates[start_index:]:
        try:
            try:
                response = stream_openai_chat(
                    client,
                    model=model_name,
                    on_event=on_event,
                    cancel_check=cancel_check,
                    **kwargs,
                )
            except CancellationRequested:
                raise
            except Exception as stream_exc:
                # Fall back to non-streaming if the provider rejects streams
                _emit(
                    on_event,
                    "session",
                    f"流式输出不可用，回退普通模式: {stream_exc}",
                )
                response = client.chat.completions.create(model=model_name, **kwargs)
            if model_name != active_model:
                switch_msg = f"已切换对话模型: {active_model} → {model_name}"
                _emit(
                    on_event,
                    "model_switch",
                    switch_msg,
                    from_model=active_model,
                    to_model=model_name,
                )
            return response, model_name
        except CancellationRequested:
            raise
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")
            if model_name == model_candidates[-1] or not should_try_next_model(exc):
                break
            retry_msg = f"模型 {model_name} 不可用，尝试备用模型..."
            _emit(
                on_event,
                "model_switch",
                retry_msg,
                from_model=model_name,
                error=str(exc),
            )
    raise RuntimeError("所有对话模型均不可用:\n" + "\n".join(errors))


def _anthropic_message_with_fallback(
    client,
    model_candidates: list[str],
    active_model: str,
    on_event: EventCallback | None,
    cancel_check: CancelCheck | None = None,
    **kwargs: Any,
):
    errors: list[str] = []
    start_index = model_candidates.index(active_model) if active_model in model_candidates else 0
    for model_name in model_candidates[start_index:]:
        try:
            try:
                response = stream_anthropic_message(
                    client,
                    model=model_name,
                    on_event=on_event,
                    cancel_check=cancel_check,
                    **kwargs,
                )
            except CancellationRequested:
                raise
            except Exception as stream_exc:
                _emit(
                    on_event,
                    "session",
                    f"流式输出不可用，回退普通模式: {stream_exc}",
                )
                response = client.messages.create(model=model_name, **kwargs)
            if model_name != active_model:
                switch_msg = f"已切换对话模型: {active_model} → {model_name}"
                _emit(
                    on_event,
                    "model_switch",
                    switch_msg,
                    from_model=active_model,
                    to_model=model_name,
                )
            return response, model_name
        except CancellationRequested:
            raise
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")
            if model_name == model_candidates[-1] or not should_try_next_model(exc):
                break
            retry_msg = f"模型 {model_name} 不可用，尝试备用模型..."
            _emit(
                on_event,
                "model_switch",
                retry_msg,
                from_model=model_name,
                error=str(exc),
            )
    raise RuntimeError("所有对话模型均不可用:\n" + "\n".join(errors))


def _run_openai_compatible(
    settings: Settings,
    workspace,
    user_id: str,
    project_id: str,
    user_prompt: str,
    system_prompt: str,
    on_event: EventCallback | None,
    cancel_check: CancelCheck | None,
    prior_messages: list[dict[str, Any]],
    *,
    task_id: str | None = None,
    set_status: Callable[[str], None] | None = None,
) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("请先安装依赖: pip install -r requirements.txt") from e

    client_kwargs = {"api_key": settings.api_key}
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = OpenAI(**client_kwargs)

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for item in prior_messages:
        if item.get("role") in {"user", "assistant"} and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": user_prompt})

    final_text_parts: list[str] = []
    active_model = settings.model
    gradle_failures = 0
    max_gradle_retries = max(0, int(getattr(settings, "max_gradle_retries", 3)))
    compact_max = int(getattr(settings, "compact_max_chars", 2_500_000))
    successful_edits = 0
    honesty_nudges = 0
    max_honesty_nudges = 2

    max_output = max(1024, int(getattr(settings, "max_output_tokens", 65_536)))

    total_turns = _total_turns(settings)
    for turn in range(1, total_turns + 1):
        _emit_continuation(on_event, turn, settings)
        if cancel_check:
            cancel_check()
        turn_msg = f"\n--- Agent 轮次 {turn}/{total_turns} ---"
        _emit(on_event, "turn", turn_msg, turn=turn, max_turns=total_turns)

        messages, did_compact = compact_openai_messages(messages, max_chars=compact_max)
        if did_compact:
            _emit(on_event, "compact", "已压缩早期上下文以控制 Token")

        response, active_model = _chat_completion_with_fallback(
            client,
            settings.model_candidates,
            active_model,
            on_event,
            cancel_check,
            messages=messages,
            tools=_openai_tools(settings),
            max_tokens=max_output,
        )

        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "completion_tokens", None)
            _emit(on_event, "usage", provider=settings.provider, model=active_model, usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": getattr(usage, "total_tokens", None),
            })
        message = choice.message
        turn_text = ""
        streamed = isinstance(response, StreamedCompletion)
        if message.content:
            text = message.content.strip()
            if text:
                turn_text = text
                final_text_parts.append(text)
                if streamed:
                    _emit(on_event, "text", None, content=text, streamed=True)
                    print(text)
                else:
                    _emit(on_event, "text", message.content, content=text, streamed=False)

        tool_calls = message.tool_calls or []
        for tool_call in tool_calls:
            if cancel_check:
                cancel_check()
            args = tool_call.function.arguments
            try:
                tool_input = json.loads(args) if args else {}
            except json.JSONDecodeError:
                tool_input = {}
            tool_msg = f"🔧 {tool_call.function.name}({tool_input})"
            _emit(
                on_event,
                "tool_call",
                tool_msg,
                name=tool_call.function.name,
                input=tool_input,
            )

        if not tool_calls:
            if (
                text_asks_download_permission(turn_text)
                and honesty_nudges < max_honesty_nudges
                and turn < total_turns
            ):
                honesty_nudges += 1
                if final_text_parts and final_text_parts[-1] == turn_text:
                    final_text_parts.pop()
                nudge = download_tool_nudge_message()
                _emit(on_event, "honesty_nudge", nudge, kind="download_permission")
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                    }
                )
                messages.append({"role": "user", "content": nudge})
                continue
            needs_real_edit = (
                successful_edits == 0
                and not text_awaits_user_action(turn_text)
                and (
                    prompt_expects_file_edit(user_prompt)
                    or text_claims_file_edit(turn_text)
                )
            )
            if needs_real_edit and honesty_nudges < max_honesty_nudges and turn < total_turns:
                honesty_nudges += 1
                if final_text_parts and final_text_parts[-1] == turn_text:
                    final_text_parts.pop()
                nudge = honesty_nudge_message(successful_edits=successful_edits)
                _emit(on_event, "honesty_nudge", nudge, successful_edits=successful_edits)
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                    }
                )
                messages.append({"role": "user", "content": nudge})
                continue
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in tool_calls
                ],
            }
        )

        for tool_call in tool_calls:
            try:
                tool_input = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                tool_input = {}

            # Enforce gradle retry budget before executing another failed assembleDebug cycle
            if (
                tool_call.function.name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and gradle_failures >= max_gradle_retries
            ):
                result_output = (
                    f"已达到 assembleDebug 失败重试上限 ({max_gradle_retries})，请停止继续构建，"
                    "总结当前错误并结束本任务。"
                )
                _emit(
                    on_event,
                    "tool_result",
                    f"   → FAIL: {result_output[:120]}",
                    name="run_gradle",
                    ok=False,
                    preview=result_output,
                    duration_ms=0,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_output,
                    }
                )
                continue

            started = time.monotonic()
            result = dispatch_tool(
                workspace,
                user_id,
                project_id,
                tool_call.function.name,
                tool_input,
                cancel_check=cancel_check,
                settings=settings,
                on_event=on_event,
                task_id=task_id,
                set_status=set_status,
            )
            if tool_call.function.name in EDIT_TOOLS and result.ok:
                successful_edits += 1
            preview = _format_tool_output(result.output)
            if len(preview) > 2000:
                preview = preview[:2000] + "\n... (输出已截断)"
            result_msg = (
                f"   → {'OK' if result.ok else 'FAIL'}: {preview.splitlines()[0][:120]}"
            )
            _emit(
                on_event,
                "tool_result",
                result_msg,
                name=tool_call.function.name,
                ok=result.ok,
                input=tool_input,
                preview=preview,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            if (
                tool_call.function.name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and not result.ok
            ):
                gradle_failures += 1

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": _format_tool_output(result.output),
                }
            )

            if settings.auto_build_after_edit and tool_call.function.name in {"write_file", "str_replace"} and result.ok:
                auto_result = dispatch_tool(
                    workspace,
                    user_id,
                    project_id,
                    "run_gradle",
                    {"task": "assembleDebug"},
                    cancel_check=cancel_check,
                    settings=settings,
                    on_event=on_event,
                    task_id=task_id,
                    set_status=set_status,
                )
                auto_preview = _format_tool_output(auto_result.output)
                if len(auto_preview) > 2000:
                    auto_preview = auto_preview[:2000] + "\n... (输出已截断)"
                _emit(
                    on_event,
                    "tool_call",
                    "🔧 run_gradle(auto_build_after_edit)",
                    name="run_gradle",
                    input={"task": "assembleDebug", "auto": True},
                )
                _emit(
                    on_event,
                    "tool_result",
                    f"   → {'OK' if auto_result.ok else 'FAIL'}: {auto_preview.splitlines()[0][:120]}",
                    name="run_gradle",
                    ok=auto_result.ok,
                    preview=auto_preview,
                    duration_ms=0,
                    auto=True,
                )
                if not auto_result.ok:
                    gradle_failures += 1
                messages.append(
                    {
                        "role": "user",
                        "content": f"[auto_build_after_edit]\n{_format_tool_output(auto_result.output)}",
                    }
                )
    else:
        final_text_parts.append("(已达最大轮次上限)")

    return "\n\n".join(final_text_parts) if final_text_parts else "(无文本回复)"


def _run_anthropic(
    settings: Settings,
    workspace,
    user_id: str,
    project_id: str,
    user_prompt: str,
    system_prompt: str,
    on_event: EventCallback | None,
    cancel_check: CancelCheck | None,
    prior_messages: list[dict[str, Any]],
    *,
    task_id: str | None = None,
    set_status: Callable[[str], None] | None = None,
) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("请先安装依赖: pip install -r requirements.txt") from e

    client_kwargs = {"api_key": settings.api_key}
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = anthropic.Anthropic(**client_kwargs)

    messages: list[dict] = []
    for item in prior_messages:
        if item.get("role") in {"user", "assistant"} and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": user_prompt})

    final_text_parts: list[str] = []
    tool_definitions = get_tool_definitions(settings)
    active_model = settings.model
    gradle_failures = 0
    max_gradle_retries = max(0, int(getattr(settings, "max_gradle_retries", 3)))
    compact_max = int(getattr(settings, "compact_max_chars", 2_500_000))
    max_output = max(1024, int(getattr(settings, "max_output_tokens", 65_536)))
    successful_edits = 0
    honesty_nudges = 0
    max_honesty_nudges = 2

    total_turns = _total_turns(settings)
    for turn in range(1, total_turns + 1):
        _emit_continuation(on_event, turn, settings)
        if cancel_check:
            cancel_check()
        turn_msg = f"\n--- Agent 轮次 {turn}/{total_turns} ---"
        _emit(on_event, "turn", turn_msg, turn=turn, max_turns=total_turns)

        messages, did_compact = compact_anthropic_messages(messages, max_chars=compact_max)
        if did_compact:
            _emit(on_event, "compact", "已压缩早期上下文以控制 Token")

        response, active_model = _anthropic_message_with_fallback(
            client,
            settings.model_candidates,
            active_model,
            on_event,
            cancel_check,
            max_tokens=max_output,
            system=system_prompt,
            tools=tool_definitions,
            messages=messages,
        )
        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
            _emit(on_event, "usage", provider=settings.provider, model=active_model, usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": (input_tokens + output_tokens) if input_tokens is not None and output_tokens is not None else None,
            })

        assistant_content = []
        tool_uses = []
        turn_text = ""
        streamed = isinstance(response, StreamedCompletion)
        for block in response.content:
            if block.type == "text":
                text = block.text.strip()
                if text:
                    turn_text = f"{turn_text}\n{text}".strip() if turn_text else text
                    final_text_parts.append(text)
                    if streamed:
                        _emit(on_event, "text", None, content=text, streamed=True)
                        print(text)
                    else:
                        _emit(on_event, "text", block.text, content=text, streamed=False)
                assistant_content.append(block)
            elif block.type == "tool_use":
                tool_uses.append(block)
                assistant_content.append(block)
                tool_msg = f"🔧 {block.name}({block.input})"
                _emit(
                    on_event,
                    "tool_call",
                    tool_msg,
                    name=block.name,
                    input=block.input,
                )

        messages.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason != "tool_use":
            if (
                text_asks_download_permission(turn_text)
                and honesty_nudges < max_honesty_nudges
                and turn < total_turns
            ):
                honesty_nudges += 1
                text_blocks = sum(
                    1 for b in response.content if b.type == "text" and (b.text or "").strip()
                )
                for _ in range(text_blocks):
                    if final_text_parts:
                        final_text_parts.pop()
                nudge = download_tool_nudge_message()
                _emit(on_event, "honesty_nudge", nudge, kind="download_permission")
                messages.append({"role": "user", "content": nudge})
                continue
            needs_real_edit = (
                successful_edits == 0
                and not text_awaits_user_action(turn_text)
                and (
                    prompt_expects_file_edit(user_prompt)
                    or text_claims_file_edit(turn_text)
                )
            )
            if needs_real_edit and honesty_nudges < max_honesty_nudges and turn < total_turns:
                honesty_nudges += 1
                text_blocks = sum(
                    1 for b in response.content if b.type == "text" and (b.text or "").strip()
                )
                for _ in range(text_blocks):
                    if final_text_parts:
                        final_text_parts.pop()
                nudge = honesty_nudge_message(successful_edits=successful_edits)
                _emit(on_event, "honesty_nudge", nudge, successful_edits=successful_edits)
                messages.append({"role": "user", "content": nudge})
                continue
            break

        tool_results = []
        for tool_use in tool_uses:
            if cancel_check:
                cancel_check()
            tool_input = tool_use.input if isinstance(tool_use.input, dict) else {}
            if (
                tool_use.name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and gradle_failures >= max_gradle_retries
            ):
                result_output = (
                    f"已达到 assembleDebug 失败重试上限 ({max_gradle_retries})，请停止继续构建，"
                    "总结当前错误并结束本任务。"
                )
                _emit(
                    on_event,
                    "tool_result",
                    f"   → FAIL: {result_output[:120]}",
                    name="run_gradle",
                    ok=False,
                    preview=result_output,
                    duration_ms=0,
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result_output,
                        "is_error": True,
                    }
                )
                continue

            started = time.monotonic()
            result = dispatch_tool(
                workspace,
                user_id,
                project_id,
                tool_use.name,
                tool_input,
                cancel_check=cancel_check,
                settings=settings,
                on_event=on_event,
                task_id=task_id,
                set_status=set_status,
            )
            if tool_use.name in EDIT_TOOLS and result.ok:
                successful_edits += 1
            preview = _format_tool_output(result.output)
            if len(preview) > 2000:
                preview = preview[:2000] + "\n... (输出已截断)"
            result_msg = (
                f"   → {'OK' if result.ok else 'FAIL'}: {preview.splitlines()[0][:120]}"
            )
            _emit(
                on_event,
                "tool_result",
                result_msg,
                name=tool_use.name,
                ok=result.ok,
                preview=preview,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            if (
                tool_use.name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and not result.ok
            ):
                gradle_failures += 1
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": _format_tool_output(result.output),
                    "is_error": not result.ok,
                }
            )

            if settings.auto_build_after_edit and tool_use.name in {"write_file", "str_replace"} and result.ok:
                auto_result = dispatch_tool(
                    workspace,
                    user_id,
                    project_id,
                    "run_gradle",
                    {"task": "assembleDebug"},
                    cancel_check=cancel_check,
                    settings=settings,
                    on_event=on_event,
                    task_id=task_id,
                    set_status=set_status,
                )
                auto_preview = _format_tool_output(auto_result.output)
                _emit(
                    on_event,
                    "tool_call",
                    "🔧 run_gradle(auto_build_after_edit)",
                    name="run_gradle",
                    input={"task": "assembleDebug", "auto": True},
                )
                _emit(
                    on_event,
                    "tool_result",
                    f"   → {'OK' if auto_result.ok else 'FAIL'}",
                    name="run_gradle",
                    ok=auto_result.ok,
                    preview=auto_preview[:2000],
                    duration_ms=0,
                    auto=True,
                )
                if not auto_result.ok:
                    gradle_failures += 1
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": f"{tool_use.id}_auto_build",
                        "content": _format_tool_output(auto_result.output),
                        "is_error": not auto_result.ok,
                    }
                )

        messages.append({"role": "user", "content": tool_results})
    else:
        final_text_parts.append("(已达最大轮次上限)")

    return "\n\n".join(final_text_parts) if final_text_parts else "(无文本回复)"
