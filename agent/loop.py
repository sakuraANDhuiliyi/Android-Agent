from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from agent.compact import (
    compact_anthropic_messages,
    compact_openai_messages,
)
from agent.approvals import ApprovalEventPersistenceError
from agent.conversation_context import build_provider_messages
from agent.conversation_events import ConversationEventType as EventType
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
from agent.prompts import build_system_prompt
from agent.hooks import run_hooks
from agent.mcp_manager import get_mcp_manager
from agent.processes import CancellationRequested as ProcessCancellationRequested
from agent.stream import StreamedCompletion, stream_anthropic_message, stream_openai_chat
from agent.tools import ToolResult, dispatch_tool, get_tool_definitions

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
    check_pause: CancelCheck | None = None,
    get_steers: Callable[[], list[str]] | None = None,
    task_id: str | None = None,
    set_status: Callable[[str], None] | None = None,
    conversation_events: list[dict[str, Any]] | None = None,
    turn_id: str | None = None,
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
    allowed_tools: set[str] | frozenset[str] | None = None,
    extra_system_prompt: str | None = None,
    run_mode: str = "workspace",
) -> str:
    provider_chain = [settings, *settings.provider_fallbacks]
    errors: list[str] = []

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
                EventType.PROVIDER_SWITCH,
                switch_msg,
                from_provider=provider_chain[index - 1].provider,
                to_provider=current_settings.provider,
                model=current_settings.model,
            )

        try:
            if conversation_events is not None:
                prior_messages = build_provider_messages(
                    conversation_events,
                    current_settings.provider,
                    current_user_prompt=user_prompt,
                )
                append_user_prompt = False
            else:
                prior_messages = []
                append_user_prompt = True
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
                append_user_prompt=append_user_prompt,
                turn_id=turn_id,
                recovery_replays=recovery_replays,
                recovery_mode=recovery_mode,
                check_pause=check_pause,
                get_steers=get_steers,
                allowed_tools=allowed_tools,
                extra_system_prompt=extra_system_prompt,
                run_mode=run_mode,
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
                    EventType.PROVIDER_SWITCH,
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
    append_user_prompt: bool = True,
    turn_id: str | None = None,
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
    check_pause: CancelCheck | None = None,
    get_steers: Callable[[], list[str]] | None = None,
    allowed_tools: set[str] | frozenset[str] | None = None,
    extra_system_prompt: str | None = None,
    run_mode: str = "workspace",
) -> str:
    system_prompt, rules_bundle = build_system_prompt(
        settings,
        workspace=workspace,
        user_id=user_id,
    )
    if extra_system_prompt:
        system_prompt = f"{system_prompt}\n\n{extra_system_prompt.strip()}"
    if rules_bundle is not None:
        _emit(
            on_event,
            EventType.SYSTEM_NOTE,
            rules_bundle.audit_text,
            kind="rules_loaded",
            loaded=[item.to_dict() for item in rules_bundle.loaded],
            skipped=list(rules_bundle.skipped),
            total_chars=rules_bundle.total_chars,
            budget=rules_bundle.budget,
        )

    try:
        from pathlib import Path as _Path

        mcp_mgr = get_mcp_manager(
            user_id,
            project_id,
            _Path(workspace),
            on_event=on_event,
        )
        mcp_mgr.start_enabled()
    except Exception as exc:
        _emit(on_event, "mcp_status", f"MCP 启动跳过: {exc}", error=str(exc))

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
            append_user_prompt=append_user_prompt,
            turn_id=turn_id,
            recovery_replays=recovery_replays,
            recovery_mode=recovery_mode,
            check_pause=check_pause,
            get_steers=get_steers,
            allowed_tools=allowed_tools,
            run_mode=run_mode,
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
        append_user_prompt=append_user_prompt,
        turn_id=turn_id,
        recovery_replays=recovery_replays,
        recovery_mode=recovery_mode,
        check_pause=check_pause,
        get_steers=get_steers,
        allowed_tools=allowed_tools,
        run_mode=run_mode,
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


def _new_message_id(
    turn_id: str | None,
    provider: str,
    response_index: int,
) -> str:
    seed = f"android-agent:{turn_id or uuid.uuid4().hex}:{provider}:{response_index}"
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex


def _tool_call_id(
    provider_id: Any,
    message_id: str,
    block_index: int,
    name: str,
) -> str:
    if isinstance(provider_id, str) and provider_id.strip():
        return provider_id
    seed = f"android-agent:{message_id}:{block_index}:{name}"
    return f"call_{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:24]}"


def parse_tool_arguments(
    arguments: str | None,
) -> tuple[dict[str, Any], str | None]:
    raw = arguments or "{}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must decode to a JSON object")
        return parsed, None
    except (json.JSONDecodeError, ValueError) as exc:
        return {}, str(exc)


def _safe_response_id(response: Any) -> str | None:
    response_id = getattr(response, "id", None)
    return response_id if isinstance(response_id, str) and response_id else None


def _record_assistant_message(
    on_event: EventCallback | None,
    *,
    message_id: str,
    text_blocks: list[dict[str, Any]],
    finish_reason: str | None,
    is_final: bool,
    streamed: bool,
    provider: str,
    model: str,
    response_id: str | None,
) -> None:
    _emit(
        on_event,
        EventType.ASSISTANT_MESSAGE,
        None,
        message_id=message_id,
        text_blocks=text_blocks,
        finish_reason=finish_reason,
        is_final=is_final,
        streamed=streamed,
        provider=provider,
        model=model,
        response_id=response_id,
    )


def _tool_result_event_payload(
    *,
    tool_call_id: str,
    name: str,
    ok: bool,
    output: Any,
    duration_ms: int,
    interrupted: bool = False,
    error_type: str | None = None,
) -> dict[str, Any]:
    model_output = _format_tool_output(output)
    return {
        "tool_call_id": tool_call_id,
        "name": name,
        "ok": ok,
        "model_output": model_output,
        "structured_output": output if not isinstance(output, str) else None,
        "duration_ms": duration_ms,
        "error_type": error_type if error_type else None if ok else "ToolExecutionError",
        "interrupted": interrupted,
    }


def dispatch_agent_tool(
    workspace,
    user_id: str,
    project_id: str,
    name: str,
    tool_input: dict[str, Any],
    *,
    cancel_check: CancelCheck | None = None,
    settings: Settings | None = None,
    on_event: EventCallback | None = None,
    task_id: str | None = None,
    tool_call_id: str | None = None,
    set_status: Callable[[str], None] | None = None,
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
    allowed_tools: set[str] | frozenset[str] | None = None,
    run_mode: str = "workspace",
) -> ToolResult:
    """Dispatch a tool through the unified runtime, including recovery replay."""
    from agent.approvals import request_user_approval
    from agent.tool_registry import get_tool_spec

    try:
        if allowed_tools is not None and name not in allowed_tools:
            return ToolResult(False, f"当前 Agent 角色无权调用工具: {name}", "PermissionError")
        spec = get_tool_spec(name)
        replay = next(
            (
                item
                for item in recovery_replays or []
                if item.get("name") == name
                and (item.get("input") or {}) == (tool_input or {})
            ),
            None,
        )
        if (
            recovery_mode
            and spec is not None
            and spec.replay_policy == "requires_approval_on_recovery"
        ):
            if not task_id or not tool_call_id:
                return ToolResult(
                    False,
                    "恢复任务检测到中断前的有副作用工具调用，但缺少审批上下文。",
                )
            decision = request_user_approval(
                job_id=task_id,
                user_id=user_id,
                kind="recovery_tool_replay",
                tool_call_id=tool_call_id,
                payload={
                    "message": (
                        f"恢复任务准备重新执行中断前未完成的工具 {name}，"
                        "需要重新确认。"
                    ),
                    "name": name,
                    "input": tool_input,
                    "interrupted_tool_call_id": (
                        replay.get("tool_call_id") if replay else None
                    ),
                },
                on_event=on_event,
                set_status=set_status,
                cancel_check=cancel_check,
            )
            if decision != "approved":
                return ToolResult(
                    False,
                    f"恢复工具重放未获批准: {decision}",
                )
            if recovery_replays is not None and replay is not None:
                recovery_replays.remove(replay)

        return dispatch_tool(
            workspace,
            user_id,
            project_id,
            name,
            tool_input,
            cancel_check=cancel_check,
            settings=settings,
            on_event=on_event,
            task_id=task_id,
            tool_call_id=tool_call_id,
            set_status=set_status,
            recovery_replays=recovery_replays,
            recovery_mode=recovery_mode,
            run_mode=run_mode,
        )
    except ProcessCancellationRequested as exc:
        raise CancellationRequested(str(exc)) from exc


def _openai_tools(
    settings: Settings,
    allowed_tools: set[str] | frozenset[str] | None = None,
) -> list[dict]:
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
        if allowed_tools is None or tool["name"] in allowed_tools
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
                    EventType.MODEL_SWITCH,
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
                EventType.MODEL_SWITCH,
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
                    EventType.MODEL_SWITCH,
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
                EventType.MODEL_SWITCH,
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
    append_user_prompt: bool = True,
    turn_id: str | None = None,
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
    check_pause: CancelCheck | None = None,
    get_steers: Callable[[], list[str]] | None = None,
    allowed_tools: set[str] | frozenset[str] | None = None,
    run_mode: str = "workspace",
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
    messages.extend(dict(item) for item in prior_messages)
    if append_user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    final_text_parts: list[str] = []
    active_model = settings.model
    gradle_failures = 0
    max_gradle_retries = max(0, int(getattr(settings, "max_gradle_retries", 3)))
    compact_max = int(getattr(settings, "compact_max_chars", 2_500_000))
    successful_edits = 0
    honesty_nudges = 0
    max_honesty_nudges = 2
    auto_build_count = 0

    max_output = max(1024, int(getattr(settings, "max_output_tokens", 65_536)))

    total_turns = _total_turns(settings)
    for turn in range(1, total_turns + 1):
        _emit_continuation(on_event, turn, settings)
        if cancel_check:
            cancel_check()
        if check_pause:
            check_pause()
        if turn > 1 and get_steers:
            for steer in get_steers():
                messages.append({"role": "user", "content": steer})
                _emit(on_event, "steer", steer, source="task_message")
        turn_msg = f"\n--- Agent 轮次 {turn}/{total_turns} ---"
        _emit(on_event, "turn", turn_msg, turn=turn, max_turns=total_turns)

        messages, did_compact = compact_openai_messages(messages, max_chars=compact_max)
        if did_compact:
            _emit(on_event, "compact", "已压缩早期上下文以控制 Token")

        run_hooks(
            "BeforeModel",
            user_id=user_id,
            workspace=workspace,
            on_event=on_event,
        )
        response, active_model = _chat_completion_with_fallback(
            client,
            settings.model_candidates,
            active_model,
            on_event,
            cancel_check,
            messages=messages,
            tools=_openai_tools(settings, allowed_tools),
            max_tokens=max_output,
        )
        run_hooks(
            "AfterModel",
            user_id=user_id,
            workspace=workspace,
            on_event=on_event,
        )

        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "completion_tokens", None)
            _emit(on_event, EventType.USAGE, provider=settings.provider, model=active_model, usage={
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

        message_id = _new_message_id(turn_id, settings.provider, turn)
        normalized_tool_calls: list[dict[str, Any]] = []
        for block_index, tool_call in enumerate(message.tool_calls or [], start=1):
            arguments = tool_call.function.arguments or "{}"
            tool_input, input_error = parse_tool_arguments(arguments)
            name = tool_call.function.name
            normalized_tool_calls.append(
                {
                    "message_id": message_id,
                    "tool_call_id": _tool_call_id(
                        getattr(tool_call, "id", None),
                        message_id,
                        block_index,
                        name,
                    ),
                    "block_index": block_index,
                    "name": name,
                    "input": tool_input,
                    "input_error": input_error,
                    "raw_arguments": arguments[:4000] if input_error else None,
                }
            )

        def record_assistant(is_final: bool) -> None:
            _record_assistant_message(
                on_event,
                message_id=message_id,
                text_blocks=(
                    [{"block_index": 0, "type": "text", "text": turn_text}]
                    if turn_text
                    else []
                ),
                finish_reason=getattr(choice, "finish_reason", None),
                is_final=is_final,
                streamed=streamed,
                provider=settings.provider,
                model=active_model,
                response_id=_safe_response_id(response),
            )

        if not normalized_tool_calls:
            if (
                text_asks_download_permission(turn_text)
                and honesty_nudges < max_honesty_nudges
                and turn < total_turns
            ):
                record_assistant(False)
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
                record_assistant(False)
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
            record_assistant(True)
            break

        record_assistant(False)
        for tool_call in normalized_tool_calls:
            if cancel_check:
                cancel_check()
            if tool_call.get("input_error"):
                continue
            tool_msg = f"🔧 {tool_call['name']}({tool_call['input']})"
            _emit(
                on_event,
                EventType.TOOL_CALL,
                tool_msg,
                **tool_call,
            )

        assistant_runtime_message = {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tool_call["tool_call_id"],
                    "type": "function",
                    "function": {
                        "name": tool_call["name"],
                        "arguments": json.dumps(
                            tool_call["input"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                }
                for tool_call in normalized_tool_calls
            ],
        }
        messages.append(assistant_runtime_message)

        for tool_call in normalized_tool_calls:
            tool_input = tool_call["input"]
            tool_name = tool_call["name"]
            tool_call_id = tool_call["tool_call_id"]
            if tool_call.get("input_error"):
                error = (
                    "工具参数不是合法 JSON 对象，调用已拒绝且未执行: "
                    f"{tool_call['input_error']}"
                )
                _emit(
                    on_event,
                    EventType.MALFORMED_TOOL_CALL,
                    error,
                    message_id=tool_call["message_id"],
                    tool_call_id=tool_call_id,
                    block_index=tool_call["block_index"],
                    name=tool_name,
                    raw_arguments=tool_call.get("raw_arguments"),
                    error_type="MalformedToolArguments",
                )
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    error,
                    **_tool_result_event_payload(
                        tool_call_id=tool_call_id,
                        name=tool_name,
                        ok=False,
                        output=error,
                        duration_ms=0,
                        error_type="MalformedToolArguments",
                    ),
                    input=None,
                    preview=error,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": error,
                    }
                )
                continue
            # Enforce gradle retry budget before executing another failed assembleDebug cycle
            if (
                tool_name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and gradle_failures >= max_gradle_retries
            ):
                result_output = (
                    f"已达到 assembleDebug 失败重试上限 ({max_gradle_retries})，请停止继续构建，"
                    "总结当前错误并结束本任务。"
                )
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    f"   → FAIL: {result_output[:120]}",
                    **_tool_result_event_payload(
                        tool_call_id=tool_call_id,
                        name="run_gradle",
                        ok=False,
                        output=result_output,
                        duration_ms=0,
                        error_type="RetryLimitExceeded",
                    ),
                    input=tool_input,
                    preview=result_output,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_output,
                    }
                )
                continue

            started = time.monotonic()
            try:
                result = dispatch_agent_tool(
                    workspace,
                    user_id,
                    project_id,
                    tool_name,
                    tool_input,
                    cancel_check=cancel_check,
                    settings=settings,
                    on_event=on_event,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    set_status=set_status,
                    recovery_replays=recovery_replays,
                    recovery_mode=recovery_mode,
                    allowed_tools=allowed_tools,
                    run_mode=run_mode,
                )
            except CancellationRequested as exc:
                duration_ms = round((time.monotonic() - started) * 1000)
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    f"   → INTERRUPTED: {exc}",
                    **_tool_result_event_payload(
                        tool_call_id=tool_call_id,
                        name=tool_name,
                        ok=False,
                        output=str(exc),
                        duration_ms=duration_ms,
                        interrupted=True,
                        error_type=exc.__class__.__name__,
                    ),
                    input=tool_input,
                    preview=str(exc),
                )
                raise
            except ApprovalEventPersistenceError:
                raise
            except Exception as exc:
                result = ToolResult(
                    False,
                    f"工具 {tool_name} 执行异常: {exc}",
                    error_type=exc.__class__.__name__,
                )
            if tool_name in EDIT_TOOLS and result.ok:
                successful_edits += 1
            model_output = _format_tool_output(result.output)
            preview = model_output
            if len(preview) > 2000:
                preview = preview[:2000] + "\n... (输出已截断)"
            first_line = preview.splitlines()[0][:120] if preview else "(空输出)"
            result_msg = (
                f"   → {'OK' if result.ok else 'FAIL'}: {first_line}"
            )
            duration_ms = round((time.monotonic() - started) * 1000)
            _emit(
                on_event,
                EventType.TOOL_RESULT,
                result_msg,
                **_tool_result_event_payload(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    ok=result.ok,
                    output=result.output,
                    duration_ms=duration_ms,
                    error_type=result.error_type,
                ),
                input=tool_input,
                preview=preview,
            )
            if (
                tool_name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and not result.ok
            ):
                gradle_failures += 1

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": model_output,
                }
            )

            if (
                settings.auto_build_after_edit
                and (allowed_tools is None or "run_gradle" in allowed_tools)
                and tool_name in {"write_file", "str_replace"}
                and result.ok
            ):
                auto_build_count += 1
                auto_block_index = (
                    max(
                        call["block_index"]
                        for call in normalized_tool_calls
                    )
                    + auto_build_count
                )
                auto_call_id = _tool_call_id(
                    None,
                    message_id,
                    auto_block_index,
                    "run_gradle",
                )
                _emit(
                    on_event,
                    EventType.TOOL_CALL,
                    "🔧 run_gradle(auto_build_after_edit)",
                    message_id=message_id,
                    tool_call_id=auto_call_id,
                    block_index=auto_block_index,
                    name="run_gradle",
                    input={"task": "assembleDebug", "auto": True},
                )
                assistant_runtime_message["tool_calls"].append(
                    {
                        "id": auto_call_id,
                        "type": "function",
                        "function": {
                            "name": "run_gradle",
                            "arguments": '{"task":"assembleDebug","auto":true}',
                        },
                    }
                )
                auto_started = time.monotonic()
                try:
                    auto_result = dispatch_agent_tool(
                        workspace,
                        user_id,
                        project_id,
                        "run_gradle",
                        {"task": "assembleDebug"},
                        cancel_check=cancel_check,
                        settings=settings,
                        on_event=on_event,
                        task_id=task_id,
                        tool_call_id=auto_call_id,
                        set_status=set_status,
                        recovery_replays=recovery_replays,
                        recovery_mode=recovery_mode,
                        allowed_tools=allowed_tools,
                        run_mode=run_mode,
                    )
                except CancellationRequested as exc:
                    _emit(
                        on_event,
                        EventType.TOOL_RESULT,
                        f"   → INTERRUPTED: {exc}",
                        **_tool_result_event_payload(
                            tool_call_id=auto_call_id,
                            name="run_gradle",
                            ok=False,
                            output=str(exc),
                            duration_ms=round(
                                (time.monotonic() - auto_started) * 1000
                            ),
                            interrupted=True,
                            error_type=exc.__class__.__name__,
                        ),
                        input={"task": "assembleDebug", "auto": True},
                        preview=str(exc),
                        auto=True,
                    )
                    raise
                except ApprovalEventPersistenceError:
                    raise
                except Exception as exc:
                    auto_result = ToolResult(
                        False,
                        f"工具 run_gradle 执行异常: {exc}",
                        error_type=exc.__class__.__name__,
                    )
                auto_preview = _format_tool_output(auto_result.output)
                if len(auto_preview) > 2000:
                    auto_preview = auto_preview[:2000] + "\n... (输出已截断)"
                auto_first_line = (
                    auto_preview.splitlines()[0][:120]
                    if auto_preview
                    else "(空输出)"
                )
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    f"   → {'OK' if auto_result.ok else 'FAIL'}: {auto_first_line}",
                    **_tool_result_event_payload(
                        tool_call_id=auto_call_id,
                        name="run_gradle",
                        ok=auto_result.ok,
                        output=auto_result.output,
                        duration_ms=round(
                            (time.monotonic() - auto_started) * 1000
                        ),
                        error_type=auto_result.error_type,
                    ),
                    input={"task": "assembleDebug", "auto": True},
                    preview=auto_preview,
                    auto=True,
                )
                if not auto_result.ok:
                    gradle_failures += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": auto_call_id,
                        "content": _format_tool_output(auto_result.output),
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
    append_user_prompt: bool = True,
    turn_id: str | None = None,
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
    check_pause: CancelCheck | None = None,
    get_steers: Callable[[], list[str]] | None = None,
    allowed_tools: set[str] | frozenset[str] | None = None,
    run_mode: str = "workspace",
) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("请先安装依赖: pip install -r requirements.txt") from e

    client_kwargs = {"api_key": settings.api_key}
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = anthropic.Anthropic(**client_kwargs)

    messages: list[dict] = [dict(item) for item in prior_messages]
    if append_user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    final_text_parts: list[str] = []
    tool_definitions = [
        tool
        for tool in get_tool_definitions(settings)
        if allowed_tools is None or tool["name"] in allowed_tools
    ]
    active_model = settings.model
    gradle_failures = 0
    max_gradle_retries = max(0, int(getattr(settings, "max_gradle_retries", 3)))
    compact_max = int(getattr(settings, "compact_max_chars", 2_500_000))
    max_output = max(1024, int(getattr(settings, "max_output_tokens", 65_536)))
    successful_edits = 0
    honesty_nudges = 0
    max_honesty_nudges = 2
    auto_build_count = 0

    total_turns = _total_turns(settings)
    for turn in range(1, total_turns + 1):
        _emit_continuation(on_event, turn, settings)
        if cancel_check:
            cancel_check()
        if check_pause:
            check_pause()
        if turn > 1 and get_steers:
            for steer in get_steers():
                messages.append({"role": "user", "content": steer})
                _emit(on_event, "steer", steer, source="task_message")
        turn_msg = f"\n--- Agent 轮次 {turn}/{total_turns} ---"
        _emit(on_event, "turn", turn_msg, turn=turn, max_turns=total_turns)

        messages, did_compact = compact_anthropic_messages(messages, max_chars=compact_max)
        if did_compact:
            _emit(on_event, "compact", "已压缩早期上下文以控制 Token")

        run_hooks(
            "BeforeModel",
            user_id=user_id,
            workspace=workspace,
            on_event=on_event,
        )
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
        run_hooks(
            "AfterModel",
            user_id=user_id,
            workspace=workspace,
            on_event=on_event,
        )
        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
            _emit(on_event, EventType.USAGE, provider=settings.provider, model=active_model, usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": (input_tokens + output_tokens) if input_tokens is not None and output_tokens is not None else None,
            })

        assistant_content: list[dict[str, Any]] = []
        text_blocks: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        turn_text = ""
        streamed = isinstance(response, StreamedCompletion)
        message_id = _new_message_id(turn_id, settings.provider, turn)
        for block_index, block in enumerate(response.content):
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
                    text_blocks.append(
                        {
                            "block_index": block_index,
                            "type": "text",
                            "text": text,
                        }
                    )
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_input = block.input if isinstance(block.input, dict) else {}
                input_error = getattr(block, "input_error", None)
                if not isinstance(block.input, dict) and not input_error:
                    input_error = "tool input must be a JSON object"
                tool_call_id = _tool_call_id(
                    getattr(block, "id", None),
                    message_id,
                    block_index,
                    block.name,
                )
                tool_use = {
                    "message_id": message_id,
                    "tool_call_id": tool_call_id,
                    "block_index": block_index,
                    "name": block.name,
                    "input": tool_input,
                    "input_error": input_error,
                    "raw_arguments": getattr(block, "raw_input", None),
                }
                tool_uses.append(tool_use)
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call_id,
                        "name": block.name,
                        "input": tool_input,
                    }
                )

        messages.append({"role": "assistant", "content": assistant_content})

        def record_assistant(is_final: bool) -> None:
            _record_assistant_message(
                on_event,
                message_id=message_id,
                text_blocks=text_blocks,
                finish_reason=getattr(response, "stop_reason", None),
                is_final=is_final,
                streamed=streamed,
                provider=settings.provider,
                model=active_model,
                response_id=_safe_response_id(response),
            )

        if not tool_uses:
            if (
                text_asks_download_permission(turn_text)
                and honesty_nudges < max_honesty_nudges
                and turn < total_turns
            ):
                record_assistant(False)
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
                record_assistant(False)
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
            record_assistant(True)
            break

        record_assistant(False)
        for tool_use in tool_uses:
            if cancel_check:
                cancel_check()
            if tool_use.get("input_error"):
                continue
            tool_msg = f"🔧 {tool_use['name']}({tool_use['input']})"
            _emit(on_event, EventType.TOOL_CALL, tool_msg, **tool_use)

        tool_results = []
        for tool_use in tool_uses:
            if cancel_check:
                cancel_check()
            tool_input = tool_use["input"]
            tool_name = tool_use["name"]
            tool_call_id = tool_use["tool_call_id"]
            if tool_use.get("input_error"):
                error = (
                    "工具参数不是合法 JSON 对象，调用已拒绝且未执行: "
                    f"{tool_use['input_error']}"
                )
                _emit(
                    on_event,
                    EventType.MALFORMED_TOOL_CALL,
                    error,
                    message_id=tool_use["message_id"],
                    tool_call_id=tool_call_id,
                    block_index=tool_use["block_index"],
                    name=tool_name,
                    raw_arguments=tool_use.get("raw_arguments"),
                    error_type="MalformedToolArguments",
                )
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    error,
                    **_tool_result_event_payload(
                        tool_call_id=tool_call_id,
                        name=tool_name,
                        ok=False,
                        output=error,
                        duration_ms=0,
                        error_type="MalformedToolArguments",
                    ),
                    input=None,
                    preview=error,
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": error,
                        "is_error": True,
                    }
                )
                continue
            if (
                tool_name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and gradle_failures >= max_gradle_retries
            ):
                result_output = (
                    f"已达到 assembleDebug 失败重试上限 ({max_gradle_retries})，请停止继续构建，"
                    "总结当前错误并结束本任务。"
                )
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    f"   → FAIL: {result_output[:120]}",
                    **_tool_result_event_payload(
                        tool_call_id=tool_call_id,
                        name="run_gradle",
                        ok=False,
                        output=result_output,
                        duration_ms=0,
                        error_type="RetryLimitExceeded",
                    ),
                    input=tool_input,
                    preview=result_output,
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": result_output,
                        "is_error": True,
                    }
                )
                continue

            started = time.monotonic()
            try:
                result = dispatch_agent_tool(
                    workspace,
                    user_id,
                    project_id,
                    tool_name,
                    tool_input,
                    cancel_check=cancel_check,
                    settings=settings,
                    on_event=on_event,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    set_status=set_status,
                    recovery_replays=recovery_replays,
                    recovery_mode=recovery_mode,
                    allowed_tools=allowed_tools,
                    run_mode=run_mode,
                )
            except CancellationRequested as exc:
                duration_ms = round((time.monotonic() - started) * 1000)
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    f"   → INTERRUPTED: {exc}",
                    **_tool_result_event_payload(
                        tool_call_id=tool_call_id,
                        name=tool_name,
                        ok=False,
                        output=str(exc),
                        duration_ms=duration_ms,
                        interrupted=True,
                        error_type=exc.__class__.__name__,
                    ),
                    preview=str(exc),
                )
                raise
            except ApprovalEventPersistenceError:
                raise
            except Exception as exc:
                result = ToolResult(
                    False,
                    f"工具 {tool_name} 执行异常: {exc}",
                    error_type=exc.__class__.__name__,
                )
            if tool_name in EDIT_TOOLS and result.ok:
                successful_edits += 1
            model_output = _format_tool_output(result.output)
            preview = model_output
            if len(preview) > 2000:
                preview = preview[:2000] + "\n... (输出已截断)"
            first_line = preview.splitlines()[0][:120] if preview else "(空输出)"
            result_msg = (
                f"   → {'OK' if result.ok else 'FAIL'}: {first_line}"
            )
            duration_ms = round((time.monotonic() - started) * 1000)
            _emit(
                on_event,
                EventType.TOOL_RESULT,
                result_msg,
                **_tool_result_event_payload(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    ok=result.ok,
                    output=result.output,
                    duration_ms=duration_ms,
                    error_type=result.error_type,
                ),
                input=tool_input,
                preview=preview,
            )
            if (
                tool_name == "run_gradle"
                and tool_input.get("task", "assembleDebug") == "assembleDebug"
                and not result.ok
            ):
                gradle_failures += 1
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": model_output,
                    "is_error": not result.ok,
                }
            )

            if (
                settings.auto_build_after_edit
                and (allowed_tools is None or "run_gradle" in allowed_tools)
                and tool_name in {"write_file", "str_replace"}
                and result.ok
            ):
                auto_build_count += 1
                auto_block_index = len(messages[-1]["content"]) + auto_build_count
                auto_call_id = _tool_call_id(
                    None,
                    message_id,
                    auto_block_index,
                    "run_gradle",
                )
                _emit(
                    on_event,
                    EventType.TOOL_CALL,
                    "🔧 run_gradle(auto_build_after_edit)",
                    message_id=message_id,
                    tool_call_id=auto_call_id,
                    block_index=auto_block_index,
                    name="run_gradle",
                    input={"task": "assembleDebug", "auto": True},
                )
                messages[-1]["content"].append(
                    {
                        "type": "tool_use",
                        "id": auto_call_id,
                        "name": "run_gradle",
                        "input": {"task": "assembleDebug", "auto": True},
                    }
                )
                auto_started = time.monotonic()
                try:
                    auto_result = dispatch_agent_tool(
                        workspace,
                        user_id,
                        project_id,
                        "run_gradle",
                        {"task": "assembleDebug"},
                        cancel_check=cancel_check,
                        settings=settings,
                        on_event=on_event,
                        task_id=task_id,
                        tool_call_id=auto_call_id,
                        set_status=set_status,
                        recovery_replays=recovery_replays,
                        recovery_mode=recovery_mode,
                        allowed_tools=allowed_tools,
                        run_mode=run_mode,
                    )
                except CancellationRequested as exc:
                    _emit(
                        on_event,
                        EventType.TOOL_RESULT,
                        f"   → INTERRUPTED: {exc}",
                        **_tool_result_event_payload(
                            tool_call_id=auto_call_id,
                            name="run_gradle",
                            ok=False,
                            output=str(exc),
                            duration_ms=round(
                                (time.monotonic() - auto_started) * 1000
                            ),
                            interrupted=True,
                            error_type=exc.__class__.__name__,
                        ),
                        input={"task": "assembleDebug", "auto": True},
                        preview=str(exc),
                        auto=True,
                    )
                    raise
                except ApprovalEventPersistenceError:
                    raise
                except Exception as exc:
                    auto_result = ToolResult(
                        False,
                        f"工具 run_gradle 执行异常: {exc}",
                        error_type=exc.__class__.__name__,
                    )
                auto_preview = _format_tool_output(auto_result.output)
                _emit(
                    on_event,
                    EventType.TOOL_RESULT,
                    f"   → {'OK' if auto_result.ok else 'FAIL'}",
                    **_tool_result_event_payload(
                        tool_call_id=auto_call_id,
                        name="run_gradle",
                        ok=auto_result.ok,
                        output=auto_result.output,
                        duration_ms=round(
                            (time.monotonic() - auto_started) * 1000
                        ),
                        error_type=auto_result.error_type,
                    ),
                    input={"task": "assembleDebug", "auto": True},
                    preview=auto_preview[:2000],
                    auto=True,
                )
                if not auto_result.ok:
                    gradle_failures += 1
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": auto_call_id,
                        "content": _format_tool_output(auto_result.output),
                        "is_error": not auto_result.ok,
                    }
                )

        messages.append({"role": "user", "content": tool_results})
    else:
        final_text_parts.append("(已达最大轮次上限)")

    return "\n\n".join(final_text_parts) if final_text_parts else "(无文本回复)"
