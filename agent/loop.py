from __future__ import annotations

import json
from typing import Any, Callable

from agent.config import Settings
from agent.model_fallback import should_try_next_model
from agent.prompts import get_system_prompt
from agent.tools import dispatch_tool, get_tool_definitions

EventCallback = Callable[[str, Any], None]


def run_agent(
    settings: Settings,
    workspace,
    user_id: str,
    project_id: str,
    user_prompt: str,
    on_event: EventCallback | None = None,
) -> str:
    provider_chain = [settings, *settings.provider_fallbacks]
    errors: list[str] = []

    for index, current_settings in enumerate(provider_chain):
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
            )
        except Exception as exc:
            errors.append(f"{current_settings.provider}: {exc}")
            if index == len(provider_chain) - 1:
                break
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

    raise RuntimeError("所有提供商均不可用:\n" + "\n".join(errors))


def _run_agent_with_provider(
    settings: Settings,
    workspace,
    user_id: str,
    project_id: str,
    user_prompt: str,
    on_event: EventCallback | None,
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
        )
    return _run_openai_compatible(
        settings,
        workspace,
        user_id,
        project_id,
        user_prompt,
        system_prompt,
        on_event,
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
    **kwargs: Any,
):
    errors: list[str] = []
    start_index = model_candidates.index(active_model) if active_model in model_candidates else 0
    for model_name in model_candidates[start_index:]:
        try:
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
    **kwargs: Any,
):
    errors: list[str] = []
    start_index = model_candidates.index(active_model) if active_model in model_candidates else 0
    for model_name in model_candidates[start_index:]:
        try:
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
) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("请先安装依赖: pip install -r requirements.txt") from e

    client_kwargs = {"api_key": settings.api_key}
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = OpenAI(**client_kwargs)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    final_text_parts: list[str] = []
    active_model = settings.model

    for turn in range(1, settings.max_turns + 1):
        turn_msg = f"\n--- Agent 轮次 {turn}/{settings.max_turns} ---"
        _emit(on_event, "turn", turn_msg, turn=turn, max_turns=settings.max_turns)

        response, active_model = _chat_completion_with_fallback(
            client,
            settings.model_candidates,
            active_model,
            on_event,
            messages=messages,
            tools=_openai_tools(settings),
            max_tokens=8096,
        )

        choice = response.choices[0]
        message = choice.message
        if message.content:
            text = message.content.strip()
            if text:
                final_text_parts.append(text)
                _emit(on_event, "text", message.content, content=text)

        tool_calls = message.tool_calls or []
        for tool_call in tool_calls:
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
            result = dispatch_tool(
                workspace,
                user_id,
                project_id,
                tool_call.function.name,
                tool_input,
            )
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
                preview=preview,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": _format_tool_output(result.output),
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
) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("请先安装依赖: pip install -r requirements.txt") from e

    client_kwargs = {"api_key": settings.api_key}
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = anthropic.Anthropic(**client_kwargs)

    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    final_text_parts: list[str] = []
    tool_definitions = get_tool_definitions(settings)
    active_model = settings.model

    for turn in range(1, settings.max_turns + 1):
        turn_msg = f"\n--- Agent 轮次 {turn}/{settings.max_turns} ---"
        _emit(on_event, "turn", turn_msg, turn=turn, max_turns=settings.max_turns)

        response, active_model = _anthropic_message_with_fallback(
            client,
            settings.model_candidates,
            active_model,
            on_event,
            max_tokens=8096,
            system=system_prompt,
            tools=tool_definitions,
            messages=messages,
        )

        assistant_content = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                text = block.text.strip()
                if text:
                    final_text_parts.append(text)
                    _emit(on_event, "text", block.text, content=text)
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
            break

        tool_results = []
        for tool_use in tool_uses:
            result = dispatch_tool(
                workspace,
                user_id,
                project_id,
                tool_use.name,
                tool_use.input,
            )
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
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": _format_tool_output(result.output),
                    "is_error": not result.ok,
                }
            )

        messages.append({"role": "user", "content": tool_results})
    else:
        final_text_parts.append("(已达最大轮次上限)")

    return "\n\n".join(final_text_parts) if final_text_parts else "(无文本回复)"
