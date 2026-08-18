from __future__ import annotations

import asyncio
import hashlib
import hmac
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agent.api_contract import (
    public_job_ws_done,
    public_job_ws_event,
    public_terminal_ws_chunk,
    public_terminal_ws_done,
)
from agent.api_errors import build_error_body
from agent.config import Settings, load_settings, models_catalog, resolve_job_settings, resolve_user_id
from agent.conversation_events import ConversationEventError, EVENT_SCHEMA_VERSION
from agent.database import TaskStore
from agent.jobs import (
    add_job_message,
    clear_project_session,
    configure_task_store,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_job,
    get_project_session,
    job_to_dict,
    detect_checkpoint_conflicts,
    list_checkpoints,
    list_conversation_events,
    list_conversations,
    conversation_list_previews,
    list_job_approvals,
    list_job_messages,
    list_jobs,
    pause_job,
    request_cancel,
    recover_job_explicitly,
    restore_checkpoint,
    restore_conversation,
    restore_file,
    resolve_job_approval,
    resume_job,
    start_ask_job,
    stop_worker,
    update_conversation,
    workspace_diff,
    workspace_diff_file,
    workspace_status,
)
from agent.paths import (
    build_log_path,
    latest_apk_path,
    user_builds_dir,
    user_workspaces_dir,
    workspace_path,
)
from agent.project import delete_project, init_project, list_projects, load_project_meta
from agent.project_lifecycle import ProjectDeletingError, project_deletion
from agent.redaction import redact_sensitive_text
from agent.repo_index import get_repo_index
from agent.rules import diagnose_rules, discover_rules, load_rules_for_turn
from agent.skills import discover_skills_for_context, list_skills, load_skill
from agent.mcp_manager import get_mcp_manager
from agent.mcp_manager import reset_mcp_managers
from agent.mcp_config import is_project_mcp_trusted
from agent.memory_store import get_memory_store
from agent.memory_retrieve import retrieve_memories_for_task
from agent.terminal import (
    create_terminal,
    get_terminal,
    list_terminals,
    mark_interrupted_terminals,
    resize_terminal,
    terminate_terminal,
    terminal_outputs,
    write_terminal_input,
    shutdown_terminals,
)
from agent.tools import is_writable_path, list_dir_entries, read_file_meta, write_file
from agent.users import UserStore
from agent.worktrees import list_worktrees
from agent.diagnostics import get_diagnostic_store
from agent.governance import (
    QuotaExceededError,
    ensure_disk_capacity,
)
from agent.stores import build_runtime_stores


class RequestBodyTooLargeError(RuntimeError):
    pass


class RequestBodyLimitMiddleware:
    """Enforce body size while receiving, including chunked requests."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max(1, int(max_bytes))

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(
                    status_code=400,
                    content=build_error_body(400, "无效的 Content-Length"),
                )
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    raise RequestBodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLargeError:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send) -> None:
        response = JSONResponse(
            status_code=413,
            content=build_error_body(413, "请求体超过服务端大小限制"),
        )
        await response(scope, receive, send)


def _apk_file_response(path: Path, filename: str) -> FileResponse:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=filename,
        headers={"X-APK-SHA256": digest.hexdigest()},
    )


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=200)
    package: Optional[str] = Field(default=None, max_length=255)


RunMode = Literal["read_only", "workspace", "ask"]


class AskRequest(StrictRequest):
    prompt: str = Field(..., min_length=1, max_length=100_000)
    provider: Optional[str] = None
    auto_fallback: bool = False
    continue_session: bool = True
    reset_session: bool = False
    conversation_id: Optional[str] = None
    run_mode: Optional[RunMode] = None


class ApprovalDecisionRequest(StrictRequest):
    approved: bool


class CreateConversationRequest(StrictRequest):
    title: Optional[str] = Field(default=None, max_length=500)


class UpdateConversationRequest(StrictRequest):
    title: Optional[str] = Field(default=None, max_length=500)
    status: Optional[str] = None


class ConversationAskRequest(StrictRequest):
    prompt: str = Field(..., min_length=1, max_length=100_000)
    provider: Optional[str] = None
    auto_fallback: bool = False
    run_mode: Optional[RunMode] = None


class WriteFileRequest(StrictRequest):
    path: str = Field(..., min_length=1)
    content: str = Field(default="", max_length=2_000_000)


class RestoreCheckpointRequest(StrictRequest):
    path: Optional[str] = None
    preview: bool = False


class JobMessageRequest(StrictRequest):
    message_key: str = Field(..., min_length=1)
    type: str = Field(..., pattern="^(steer|follow_up|cancel|pause|resume)$")
    payload: dict[str, Any] = Field(default_factory=dict)


class JobMessageResponse(BaseModel):
    id: int
    task_id: str
    message_key: str
    type: str
    payload: dict[str, Any]
    created_at: float


class CreateTerminalRequest(StrictRequest):
    cwd: Optional[str] = "."
    argv: Optional[list[str]] = None
    shell: Optional[str] = None
    cols: int = 80
    rows: int = 24
    env: Optional[dict[str, str]] = None


class TerminalInputRequest(StrictRequest):
    data: str = Field(..., max_length=65_536)


class TerminalResizeRequest(StrictRequest):
    cols: int
    rows: int


class McpEnableRequest(StrictRequest):
    enabled: bool = True


class MemoryEditRequest(StrictRequest):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    memory_type: Optional[str] = None


class MemoryCreateRequest(StrictRequest):
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    memory_type: str = Field(..., min_length=1)
    scope: str = "project"
    tags: list[str] = Field(default_factory=list)
    status: str = "candidate"


class WebSocketTicketRequest(StrictRequest):
    resource_type: str = Field(..., pattern="^(job|terminal)$")
    resource_id: str = Field(..., min_length=1, max_length=128)


_PRIVATE_EVENT_FIELDS = frozenset(
    {
        "apikey",
        "apitoken",
        "authorization",
        "proxyauthorization",
        "xapikey",
        "token",
        "accesstoken",
        "refreshtoken",
        "secret",
        "clientsecret",
        "password",
        "deepseekapikey",
        "anthropicapikey",
        "tavilyapikey",
    }
)


def _public_event_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _public_event_value(nested)
            for key, nested in value.items()
            if "".join(
                char for char in str(key).lower() if char.isalnum()
            )
            not in _PRIVATE_EVENT_FIELDS
        }
    if isinstance(value, list):
        return [_public_event_value(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    value = authorization.strip()
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


def _project_status(user_id: str, project_id: str) -> dict[str, Any]:
    meta = load_project_meta(user_id, project_id)
    public_meta = {
        key: value
        for key, value in meta.items()
        if key
        not in {
            "repo_root",
            "workspace",
            "path",
            "source_url",
        }
    }
    apk = latest_apk_path(user_id, project_id)
    builds_dir = user_builds_dir(user_id) / project_id
    build_logs = []
    if builds_dir.is_dir():
        for log_file in sorted(builds_dir.glob("*.log"), reverse=True):
            build_logs.append(
                {
                    "id": log_file.stem,
                    "url": (
                        f"/api/projects/{project_id}/builds/{log_file.stem}"
                    ),
                }
            )
    recent_tasks = list_jobs(user_id, project_id)
    latest_task = recent_tasks[0] if recent_tasks else None
    return {
        **public_meta,
        "user_id": user_id,
        "has_apk": apk.is_file(),
        "apk_url": f"/api/projects/{project_id}/apk" if apk.is_file() else None,
        "build_logs": build_logs[:20],
        "latest_status": latest_task.get("status") if latest_task else None,
        "latest_task_id": latest_task.get("id") if latest_task else None,
    }


def create_app(
    settings: Settings | None = None,
    user_store: UserStore | None = None,
    task_store: TaskStore | None = None,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            stop_worker(wait=True, timeout=10.0)
            shutdown_terminals()
            reset_mcp_managers()

    app = FastAPI(
        title="Android Agent API",
        version="1.0.0-mvp",
        description="本地 Android AI Agent HTTP 服务，按 user_id 隔离项目",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.user_store = user_store or UserStore()
    effective_task_store = task_store or TaskStore()
    app.state.task_store = effective_task_store
    runtime = build_runtime_stores(settings, db_path=effective_task_store.db_path)
    app.state.runtime = runtime
    app.state.ws_tickets = runtime.tickets
    app.state.artifacts = runtime.artifacts
    app.state.outbox = runtime.outbox
    http_limiter = runtime.rate_limiter
    reg_limiter = runtime.registration_limiter
    app.state.diagnostics = get_diagnostic_store(effective_task_store.db_path)
    configure_task_store(effective_task_store, settings)
    # Mark pre-restart PTY sessions as interrupted; we cannot recover their
    # underlying processes.
    mark_interrupted_terminals()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Registration-Token"],
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=settings.max_request_bytes,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error_body(exc.status_code, exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=build_error_body(422, exc.errors(), code="validation_error"),
        )

    @app.middleware("http")
    async def enforce_request_budgets(
        request: Request,
        call_next,
    ):
        client_host = request.client.host if request.client else "unknown"
        try:
            http_limiter.check(
                f"http:{client_host}",
                limit=settings.max_requests_per_minute,
                window_seconds=60,
            )
        except QuotaExceededError as exc:
            return JSONResponse(
                status_code=429,
                content=build_error_body(429, str(exc), code="rate_limited"),
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    def authenticated_user(authorization: str | None) -> str:
        token = _bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="未提供 API Token")
        registered_user = app.state.user_store.authenticate(token)
        if registered_user:
            return registered_user
        try:
            return resolve_user_id(app.state.settings, authorization)
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

    def current_user(authorization: Optional[str] = Header(default=None)) -> str:
        return authenticated_user(authorization)

    def require_terminal_enabled() -> None:
        if not settings.terminal_enabled:
            raise HTTPException(status_code=404, detail="终端功能未启用")

    def ensure_write_budget() -> None:
        try:
            ensure_disk_capacity(
                user_workspaces_dir("quota-check").parent,
                settings.minimum_free_disk_bytes,
            )
        except QuotaExceededError as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc

    def ensure_prompt_budget(prompt: str, user_id: str) -> None:
        if len(prompt) > settings.max_prompt_chars:
            raise HTTPException(
                status_code=413,
                detail=f"Prompt 超过 {settings.max_prompt_chars} 字符限制",
            )
        active = [
            task
            for task in list_jobs(user_id)
            if task.get("status")
            in {"queued", "running", "awaiting_approval", "paused"}
        ]
        if len(active) >= settings.max_active_tasks_per_user:
            raise HTTPException(
                status_code=429,
                detail=(
                    "用户活动任务达到上限 "
                    f"({settings.max_active_tasks_per_user})"
                ),
            )
        ensure_write_budget()

    @app.post("/api/pair", status_code=201)
    @app.post("/api/register", status_code=201)
    def register(
        request: Request,
        registration_token: Optional[str] = Header(
            default=None,
            alias="X-Registration-Token",
        ),
    ) -> dict[str, str]:
        client_host = request.client.host if request.client else "unknown"
        try:
            reg_limiter.check(
                f"register:{client_host}",
                limit=settings.max_registration_per_hour,
                window_seconds=3600,
            )
        except QuotaExceededError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": "3600"},
            ) from exc
        if not settings.registration_enabled:
            raise HTTPException(status_code=404, detail="网络注册未启用")
        if not settings.registration_token:
            raise HTTPException(status_code=503, detail="服务端未配置注册密钥")
        if not registration_token or not hmac.compare_digest(
            registration_token,
            settings.registration_token,
        ):
            raise HTTPException(status_code=401, detail="无效的注册密钥")
        ensure_write_budget()
        user_id, token = app.state.user_store.register()
        user_workspaces_dir(user_id).mkdir(parents=True, exist_ok=True)
        user_builds_dir(user_id).mkdir(parents=True, exist_ok=True)
        return {
            "user_id": user_id,
            "token": token,
            "token_type": "Bearer",
        }

    @app.get("/api/health")
    def health(user_id: str = Depends(current_user)) -> dict[str, Any]:
        return {
            "status": "ok",
            "user_id": user_id,
            "provider": settings.provider,
            "model": settings.model,
            "model_candidates": settings.model_candidates,
            "provider_fallbacks": [item.provider for item in settings.provider_fallbacks],
            "api_key_configured": bool(settings.api_key),
            "tavily_configured": bool(settings.tavily_api_key),
            "lan_ip": _guess_lan_ip(),
            "port": settings.server_port,
        }

    @app.get("/api/models")
    def get_models(user_id: str = Depends(current_user)) -> dict[str, Any]:
        _ = user_id
        return models_catalog(settings)

    @app.post("/api/ws/tickets", status_code=201)
    def issue_websocket_ticket(
        body: WebSocketTicketRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        if body.resource_type == "job":
            if not get_job(body.resource_id, user_id=user_id):
                raise HTTPException(status_code=404, detail="任务不存在")
        else:
            require_terminal_enabled()
            if not get_terminal(body.resource_id, user_id):
                raise HTTPException(status_code=404, detail="终端不存在")
        ticket, expires_at = app.state.ws_tickets.issue(
            user_id,
            body.resource_type,
            body.resource_id,
            ttl_seconds=settings.ws_ticket_ttl_seconds,
        )
        return {
            "ticket": ticket,
            "expires_at": expires_at,
            "resource_type": body.resource_type,
            "resource_id": body.resource_id,
        }

    @app.get("/api/projects")
    def get_projects(user_id: str = Depends(current_user)) -> dict[str, Any]:
        projects = []
        for meta in list_projects(user_id):
            projects.append(_project_status(user_id, meta["id"]))
        return {"user_id": user_id, "projects": projects}

    @app.post("/api/projects", status_code=201)
    def create_project(
        body: CreateProjectRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        if len(list_projects(user_id)) >= settings.max_projects_per_user:
            raise HTTPException(
                status_code=429,
                detail=f"项目数量达到上限 ({settings.max_projects_per_user})",
            )
        ensure_write_budget()
        try:
            project_id = init_project(
                name=body.name,
                package=body.package,
                user_id=user_id,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _project_status(user_id, project_id)

    @app.get("/api/projects/{project_id}")
    def get_project(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            return _project_status(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/api/projects/{project_id}/ask")
    def ask_project(
        project_id: str,
        body: AskRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        ensure_prompt_budget(body.prompt, user_id)
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        try:
            job_settings = resolve_job_settings(
                settings,
                body.provider,
                auto_fallback=body.auto_fallback
                or (body.provider in {None, "", "auto"}),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        if not job_settings.api_key:
            raise HTTPException(status_code=503, detail="未配置 LLM API Key")

        try:
            job = start_ask_job(
                user_id,
                project_id,
                body.prompt,
                job_settings,
                conversation_id=body.conversation_id,
                continue_session=body.continue_session and not body.reset_session,
                reset_session=body.reset_session,
                run_mode=body.run_mode,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"job": job_to_dict(job)}

    @app.get("/api/projects/{project_id}/conversations")
    def get_conversations(
        project_id: str,
        archived: bool = False,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            items = list_conversations(user_id, project_id, include_archived=archived)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        try:
            previews = conversation_list_previews(
                user_id, project_id, [item["id"] for item in items]
            )
            for item in items:
                preview = previews.get(item["id"], {})
                item["summary"] = preview.get("summary", "")
                item["last_turn_status"] = preview.get("last_turn_status", "")
        except Exception:  # pragma: no cover - 摘要失败不阻塞列表
            for item in items:
                item.setdefault("summary", "")
                item.setdefault("last_turn_status", "")
        return {"user_id": user_id, "project_id": project_id, "conversations": items}

    @app.post("/api/projects/{project_id}/conversations", status_code=201)
    def post_conversation(
        project_id: str,
        body: CreateConversationRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        if (
            len(list_conversations(user_id, project_id))
            >= settings.max_conversations_per_project
        ):
            raise HTTPException(
                status_code=429,
                detail=(
                    "项目 Conversation 数量达到上限 "
                    f"({settings.max_conversations_per_project})"
                ),
            )
        try:
            conv = create_conversation(user_id, project_id, title=body.title or "新对话")
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return conv

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation_detail(
        conversation_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        conv = get_conversation(conversation_id, user_id)
        if not conv:
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")
        return conv

    @app.get("/api/conversations/{conversation_id}/events")
    def get_conversation_events(
        conversation_id: str,
        after_seq: Optional[int] = None,
        before_seq: Optional[int] = None,
        limit: int = Query(default=200, ge=1, le=500),
        context_only: bool = False,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            events = list_conversation_events(
                conversation_id,
                user_id,
                after_seq=after_seq,
                before_seq=before_seq,
                limit=limit + 1,
                context_only=context_only,
            )
        except ConversationEventError as exc:
            raise HTTPException(
                status_code=500,
                detail="Conversation Event 数据读取失败",
            ) from exc
        if events is None:
            raise HTTPException(
                status_code=404,
                detail=f"对话不存在: {conversation_id}",
            )
        backward = before_seq is not None and after_seq is None
        if backward:
            # events are the newest `limit+1` rows below before_seq, ascending.
            has_more = len(events) > limit
            page = events[-limit:] if has_more else events
            next_before_seq = page[0]["seq"] if page else before_seq
            return {
                "conversation_id": conversation_id,
                "schema_version": EVENT_SCHEMA_VERSION,
                "events": [_public_event_value(event) for event in page],
                "next_before_seq": next_before_seq,
                "has_more": has_more,
                "direction": "backward",
            }
        has_more = len(events) > limit
        page = events[:limit]
        next_after_seq = page[-1]["seq"] if page else after_seq
        return {
            "conversation_id": conversation_id,
            "schema_version": EVENT_SCHEMA_VERSION,
            "events": [_public_event_value(event) for event in page],
            "next_after_seq": next_after_seq,
            "has_more": has_more,
            "direction": "forward",
        }

    @app.patch("/api/conversations/{conversation_id}")
    def patch_conversation(
        conversation_id: str,
        body: UpdateConversationRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        conv = update_conversation(conversation_id, user_id, **payload)
        if not conv:
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")
        return conv

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    def remove_conversation(
        conversation_id: str,
        user_id: str = Depends(current_user),
    ) -> None:
        if not delete_conversation(conversation_id, user_id):
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")

    @app.post("/api/conversations/{conversation_id}/restore")
    def restore_conversation_endpoint(
        conversation_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        conv = restore_conversation(conversation_id, user_id)
        if not conv:
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")
        return conv

    @app.post("/api/conversations/{conversation_id}/ask")
    def ask_conversation(
        conversation_id: str,
        body: ConversationAskRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        ensure_prompt_budget(body.prompt, user_id)
        conv = get_conversation(conversation_id, user_id)
        if not conv:
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")
        try:
            job_settings = resolve_job_settings(
                settings,
                body.provider,
                auto_fallback=body.auto_fallback
                or (body.provider in {None, "", "auto"}),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not job_settings.api_key:
            raise HTTPException(status_code=503, detail="未配置 LLM API Key")
        try:
            job = start_ask_job(
                user_id,
                conv["project_id"],
                body.prompt,
                job_settings,
                conversation_id=conversation_id,
                continue_session=True,
                reset_session=False,
                run_mode=body.run_mode,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"job": job_to_dict(job), "conversation_id": conversation_id}

    @app.get("/api/projects/{project_id}/session")
    def get_session(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            return get_project_session(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.delete("/api/projects/{project_id}/session", status_code=204)
    def delete_session(project_id: str, user_id: str = Depends(current_user)) -> None:
        try:
            clear_project_session(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/jobs")
    def get_jobs(
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "jobs": [
                job_to_dict(job)
                for job in list_jobs(user_id, project_id, conversation_id)
            ],
        }

    @app.get("/api/diagnostics")
    def get_diagnostics(
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        after: Optional[float] = None,
        limit: int = Query(default=100, ge=1, le=500),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        if project_id:
            try:
                load_project_meta(user_id, project_id)
            except (FileNotFoundError, ValueError) as exc:
                raise HTTPException(status_code=404, detail="项目不存在") from exc
        if task_id and not get_job(task_id, user_id=user_id):
            raise HTTPException(status_code=404, detail="任务不存在")
        return {
            "diagnostics": app.state.diagnostics.list(
                user_id,
                project_id=project_id,
                task_id=task_id,
                after=after,
                limit=limit,
            )
        }

    @app.get("/api/jobs/{job_id}")
    def get_job_detail(
        job_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        return {"job": job_to_dict(job)}

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    def cancel_job(job_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        if job["status"] in {"succeeded", "failed", "canceled", "interrupted"}:
            return {"job": job_to_dict(job)}
        request_cancel(job_id, user_id)
        return {"job": job_to_dict(get_job(job_id, user_id=user_id) or job)}

    @app.post("/api/jobs/{job_id}/messages", status_code=201)
    def post_job_message(
        job_id: str,
        body: JobMessageRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        if job["status"] in {"succeeded", "failed", "canceled"}:
            raise HTTPException(status_code=409, detail="任务已结束")
        msg = add_job_message(
            job_id,
            user_id,
            message_key=body.message_key,
            type=body.type,
            payload=body.payload,
        )
        if not msg:
            raise HTTPException(status_code=409, detail="无法添加消息")
        return {
            "job_id": job_id,
            "message": JobMessageResponse(**msg).model_dump(),
        }

    @app.get("/api/jobs/{job_id}/messages")
    def get_job_messages(
        job_id: str,
        include_consumed: bool = False,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        messages = list_job_messages(job_id, user_id, include_consumed=include_consumed)
        if messages is None:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        return {
            "job_id": job_id,
            "messages": [
                JobMessageResponse(**msg).model_dump() for msg in messages
            ],
        }

    @app.post("/api/jobs/{job_id}/pause", status_code=202)
    def pause_job_endpoint(job_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        if not pause_job(job_id, user_id):
            raise HTTPException(status_code=409, detail="任务无法暂停")
        return {"job": job_to_dict(get_job(job_id, user_id=user_id) or job)}

    @app.post("/api/jobs/{job_id}/resume", status_code=202)
    def resume_job_endpoint(job_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        if not resume_job(job_id, user_id):
            raise HTTPException(status_code=409, detail="任务无法恢复")
        return {"job": job_to_dict(get_job(job_id, user_id=user_id) or job)}

    @app.get("/api/jobs/{job_id}/approvals")
    def get_job_approvals(job_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        pending = list_job_approvals(job_id, user_id)
        if pending is None:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        return {"job_id": job_id, "approvals": pending}

    @app.post("/api/jobs/{job_id}/approvals/{approval_id}")
    def decide_job_approval(
        job_id: str,
        approval_id: str,
        body: ApprovalDecisionRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        result = resolve_job_approval(job_id, approval_id, user_id, approved=body.approved)
        if not result:
            raise HTTPException(status_code=404, detail="待确认请求不存在或已处理")
        return {"approval": result}

    @app.get("/api/jobs/{job_id}/apk")
    def download_task_apk(job_id: str, user_id: str = Depends(current_user)) -> FileResponse:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        apk_path = job.get("apk_path")
        if not apk_path or not __import__("pathlib").Path(apk_path).is_file():
            raise HTTPException(status_code=404, detail="该任务没有 APK")
        return _apk_file_response(
            Path(apk_path),
            f"{job['project_id']}-{job_id}.apk",
        )

    @app.get("/api/jobs/{job_id}/log")
    def get_task_log(job_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        log_path = job.get("build_log_path")
        path = __import__("pathlib").Path(log_path) if log_path else None
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="该任务没有构建日志")
        return {"job_id": job_id, "content": path.read_text(encoding="utf-8", errors="replace")}

    @app.post("/api/jobs/{job_id}/recover", status_code=201)
    def recover_interrupted_job(
        job_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            recovered = recover_job_explicitly(job_id, user_id, settings)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not recovered:
            raise HTTPException(
                status_code=409,
                detail="任务不是可显式恢复的中断任务",
            )
        return {"job": job_to_dict(recovered)}

    @app.delete("/api/projects/{project_id}", status_code=204)
    def remove_project(project_id: str, user_id: str = Depends(current_user)) -> None:
        try:
            with project_deletion(user_id, project_id):
                load_project_meta(user_id, project_id)
                active_tasks = [
                    item
                    for item in list_jobs(user_id, project_id)
                    if item["status"]
                    in {"queued", "running", "awaiting_approval", "paused"}
                ]
                active_terminals = [
                    item
                    for item in list_terminals(user_id, project_id)
                    if item["status"] in {"starting", "running"}
                ]
                active_worktrees = [
                    item.public_dict()
                    for item in list_worktrees(user_id, project_id)
                    if item.status in {"active", "kept"}
                ]
                get_mcp_manager(
                    user_id,
                    project_id,
                    workspace_path(user_id, project_id),
                ).stop_all()
                if active_tasks or active_terminals or active_worktrees:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "项目仍有活跃资源，停止或处理后再删除",
                            "task_ids": [item["id"] for item in active_tasks],
                            "terminal_ids": [item["id"] for item in active_terminals],
                            "worktree_ids": [item["id"] for item in active_worktrees],
                        },
                    )
                delete_project(user_id, project_id)
        except ProjectDeletingError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/projects/{project_id}/apk")
    def download_apk(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> FileResponse:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        apk = latest_apk_path(user_id, project_id)
        if not apk.is_file():
            workspace_apk = (
                workspace_path(user_id, project_id)
                / "app/build/outputs/apk/debug/app-debug.apk"
            )
            if workspace_apk.is_file():
                apk = workspace_apk
            else:
                raise HTTPException(status_code=404, detail="APK 尚未生成")

        return _apk_file_response(apk, f"{project_id}.apk")

    @app.get("/api/projects/{project_id}/builds/{build_id}")
    def get_build_log(
        project_id: str,
        build_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        log_file = build_log_path(user_id, project_id, build_id)
        if not log_file.is_file():
            raise HTTPException(status_code=404, detail="构建日志不存在")
        return {
            "user_id": user_id,
            "project_id": project_id,
            "build_id": build_id,
            "content": log_file.read_text(encoding="utf-8", errors="replace"),
        }

    @app.get("/api/projects/{project_id}/files")
    def list_project_files(
        project_id: str,
        path: str = ".",
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        workspace = workspace_path(user_id, project_id)
        result = list_dir_entries(workspace, path)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.output)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "path": path,
            "entries": result.output,
        }

    @app.get("/api/projects/{project_id}/files/content")
    def read_project_file(
        project_id: str,
        path: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        if not path.strip():
            raise HTTPException(status_code=400, detail="缺少 path 参数")

        workspace = workspace_path(user_id, project_id)
        result = read_file_meta(workspace, path)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.output)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "writable": is_writable_path(path),
            **result.output,
        }

    @app.put("/api/projects/{project_id}/files/content")
    def write_project_file(
        project_id: str,
        body: WriteFileRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        path = body.path.strip()
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path 参数")

        workspace = workspace_path(user_id, project_id)
        result = write_file(workspace, path, body.content)
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.output)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "path": path,
            "size": len(body.content.encode("utf-8")),
            "message": result.output,
        }

    @app.get("/api/projects/{project_id}/workspace/status")
    def get_workspace_status(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            return workspace_status(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/projects/{project_id}/diff")
    def get_workspace_diff(
        project_id: str,
        turn_id: Optional[str] = None,
        checkpoint_id: Optional[str] = None,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            return workspace_diff(
                user_id,
                project_id,
                turn_id=turn_id,
                checkpoint_id=checkpoint_id,
            )
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/projects/{project_id}/diff/file")
    def get_workspace_diff_file(
        project_id: str,
        turn_id: str,
        path: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            return workspace_diff_file(
                user_id,
                project_id,
                turn_id=turn_id,
                path=path,
            )
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/projects/{project_id}/checkpoints")
    def get_checkpoints(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            checkpoints = list_checkpoints(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "checkpoints": checkpoints,
        }

    @app.post("/api/projects/{project_id}/checkpoints/{checkpoint_id}/restore")
    def post_restore_checkpoint(
        project_id: str,
        checkpoint_id: str,
        body: RestoreCheckpointRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            if body.preview:
                result = detect_checkpoint_conflicts(
                    user_id, project_id, checkpoint_id
                )
            elif body.path:
                result = restore_file(
                    user_id, project_id, checkpoint_id, body.path
                )
            else:
                result = restore_checkpoint(user_id, project_id, checkpoint_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if body.preview:
            return {
                "user_id": user_id,
                "project_id": project_id,
                "checkpoint_id": checkpoint_id,
                "preview": True,
                **result,
            }
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "checkpoint_id": checkpoint_id,
            **result,
        }

    @app.get("/api/projects/{project_id}/index/status")
    def get_index_status(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        index = get_repo_index(user_id, project_id)
        return {
            "user_id": user_id,
            "project_id": project_id,
            **index.status(),
        }

    @app.post("/api/projects/{project_id}/index/rebuild")
    def post_index_rebuild(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        index = get_repo_index(user_id, project_id)
        result = index.rebuild()
        return {
            "user_id": user_id,
            "project_id": project_id,
            **result,
        }

    @app.get("/api/projects/{project_id}/search")
    def get_project_search(
        project_id: str,
        q: str = Query(..., min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        index = get_repo_index(user_id, project_id)
        if index.status()["status"] != "ready":
            index.rebuild()
        hits = index.search(q, limit=limit)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "query": q,
            "hits": hits,
        }

    @app.get("/api/projects/{project_id}/symbols")
    def get_project_symbols(
        project_id: str,
        name: Optional[str] = None,
        symbol_type: Optional[str] = None,
        rel_path: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=200),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        index = get_repo_index(user_id, project_id)
        if index.status()["status"] != "ready":
            index.rebuild()
        symbols = index.find_symbol(
            name=name,
            symbol_type=symbol_type,
            rel_path=rel_path,
            limit=limit,
        )
        return {
            "user_id": user_id,
            "project_id": project_id,
            "symbols": symbols,
        }

    @app.get("/api/projects/{project_id}/rules")
    def get_project_rules(
        project_id: str,
        focus: Optional[str] = Query(default=None),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        focus_paths = [p.strip() for p in (focus or "").split(",") if p.strip()]
        candidates = discover_rules(workspace, user_id, focus_paths=focus_paths)
        bundle = load_rules_for_turn(workspace, user_id, focus_paths=focus_paths)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "focus_paths": focus_paths,
            "candidates": [c.to_dict() for c in candidates],
            "loaded": [item.to_dict() for item in bundle.loaded],
            "skipped": list(bundle.skipped),
            "total_chars": bundle.total_chars,
            "budget": bundle.budget,
            "audit_text": bundle.audit_text,
        }

    @app.get("/api/projects/{project_id}/rules/diagnose")
    def get_project_rules_diagnose(
        project_id: str,
        focus: Optional[str] = Query(default=None),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        focus_paths = [p.strip() for p in (focus or "").split(",") if p.strip()]
        return {
            "user_id": user_id,
            "project_id": project_id,
            "focus_paths": focus_paths,
            **diagnose_rules(workspace, user_id, focus_paths=focus_paths),
        }

    @app.get("/api/projects/{project_id}/skills")
    def get_project_skills(
        project_id: str,
        q: Optional[str] = Query(default=None),
        focus: Optional[str] = Query(default=None),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        focus_paths = [p.strip() for p in (focus or "").split(",") if p.strip()]
        if q or focus_paths:
            skills = discover_skills_for_context(
                workspace,
                user_id,
                focus_paths=focus_paths,
                query=q,
            )
        else:
            skills = list_skills(workspace, user_id)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "query": q,
            "focus_paths": focus_paths,
            "skills": [s.to_dict() for s in skills],
            "note": "Metadata only; full bodies require load_skill / skills/{name}.",
        }

    @app.get("/api/projects/{project_id}/skills/{skill_name}")
    def get_project_skill(
        project_id: str,
        skill_name: str,
        resource: Optional[str] = Query(default=None),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        try:
            content = load_skill(
                workspace,
                user_id,
                skill_name,
                resource_path=resource,
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "executed": False,
            "skill": content.to_dict(),
        }

    def _memory_store():
        from agent.paths import DATA_DIR

        return get_memory_store(DATA_DIR / "agent.db")

    @app.get("/api/projects/{project_id}/memories")
    def list_project_memories(
        project_id: str,
        status: Optional[str] = Query(default="active"),
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=200),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        store = _memory_store()
        items = store.list_memories(
            user_id,
            project_id=project_id,
            status=status,
            scope=scope,
            memory_type=memory_type,
            limit=limit,
        )
        return {
            "user_id": user_id,
            "project_id": project_id,
            "memories": items,
        }

    @app.get("/api/projects/{project_id}/memories/candidates")
    def list_memory_candidates(
        project_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        items = _memory_store().list_candidates(user_id, project_id, limit=limit)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "candidates": items,
        }

    @app.get("/api/projects/{project_id}/memories/search")
    def search_project_memories(
        project_id: str,
        q: str = Query(..., min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        hits = _memory_store().search(
            user_id, q, project_id=project_id, status="active", limit=limit
        )
        return {
            "user_id": user_id,
            "project_id": project_id,
            "query": q,
            "hits": hits,
        }

    @app.get("/api/projects/{project_id}/memories/usage")
    def list_memory_usage(
        project_id: str,
        memory_id: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=200),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        rows = _memory_store().list_usage(
            user_id, memory_id=memory_id, project_id=project_id, limit=limit
        )
        return {"user_id": user_id, "project_id": project_id, "usage": rows}

    @app.post("/api/projects/{project_id}/memories", status_code=201)
    def create_project_memory(
        project_id: str,
        body: MemoryCreateRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if (
            _memory_store().count_memories(user_id, project_id=project_id)
            >= settings.max_memories_per_project
        ):
            raise HTTPException(
                status_code=429,
                detail=(
                    "项目可见 Memory 数量达到上限 "
                    f"({settings.max_memories_per_project})"
                ),
            )
        try:
            item = _memory_store().create_memory(
                user_id=user_id,
                project_id=project_id,
                scope=body.scope,
                memory_type=body.memory_type,
                title=body.title,
                content=body.content,
                tags=body.tags,
                status=body.status if body.status in {"candidate", "active"} else "candidate",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"user_id": user_id, "project_id": project_id, "memory": item}

    @app.post("/api/memories/{memory_id}/approve")
    def approve_memory(
        memory_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        item = _memory_store().approve(memory_id, user_id)
        if not item:
            raise HTTPException(status_code=404, detail="记忆不存在")
        return {"memory": item}

    @app.post("/api/memories/{memory_id}/reject")
    def reject_memory(
        memory_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        item = _memory_store().reject(memory_id, user_id)
        if not item:
            raise HTTPException(status_code=404, detail="记忆不存在")
        return {"memory": item}

    @app.post("/api/memories/{memory_id}/archive")
    def archive_memory(
        memory_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        item = _memory_store().archive(memory_id, user_id)
        if not item:
            raise HTTPException(status_code=404, detail="记忆不存在")
        return {"memory": item}

    @app.patch("/api/memories/{memory_id}")
    def edit_memory(
        memory_id: str,
        body: MemoryEditRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            item = _memory_store().update_memory(
                memory_id,
                user_id,
                title=body.title,
                content=body.content,
                tags=body.tags,
                memory_type=body.memory_type,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not item:
            raise HTTPException(status_code=404, detail="记忆不存在")
        return {"memory": item}

    @app.delete("/api/memories/{memory_id}", status_code=204)
    def delete_memory(
        memory_id: str,
        user_id: str = Depends(current_user),
    ) -> None:
        if not _memory_store().delete_memory(memory_id, user_id):
            raise HTTPException(status_code=404, detail="记忆不存在")

    @app.post("/api/projects/{project_id}/memories/retrieve")
    def retrieve_memories(
        project_id: str,
        q: str = Query(..., min_length=1),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        plan = retrieve_memories_for_task(
            user_id=user_id,
            project_id=project_id,
            prompt=q,
            store=_memory_store(),
        )
        return {"user_id": user_id, "project_id": project_id, **plan}

    @app.get("/api/projects/{project_id}/mcp/servers")
    def get_mcp_servers(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        mgr = get_mcp_manager(user_id, project_id, workspace)
        servers = mgr.list_servers()
        # Never return secrets.
        return {
            "user_id": user_id,
            "project_id": project_id,
            "project_trusted": is_project_mcp_trusted(user_id, project_id, workspace),
            "servers": servers,
        }

    @app.get("/api/projects/{project_id}/mcp/tools")
    def get_mcp_tools(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        mgr = get_mcp_manager(user_id, project_id, workspace)
        tools = []
        for server in mgr.list_servers():
            for tool in server.get("tools") or []:
                tools.append(
                    {
                        "server": server["name"],
                        "tool": tool.get("name"),
                        "namespaced": f"mcp__{server['name']}__{tool.get('name')}",
                        "description": tool.get("description"),
                        "input_schema": tool.get("input_schema"),
                        "status": server.get("status"),
                    }
                )
        return {
            "user_id": user_id,
            "project_id": project_id,
            "tools": tools,
        }

    @app.post("/api/projects/{project_id}/mcp/trust")
    def post_mcp_trust(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        mgr = get_mcp_manager(user_id, project_id, workspace)
        result = mgr.trust_project()
        return {
            "user_id": user_id,
            "project_id": project_id,
            **result,
        }

    @app.post("/api/projects/{project_id}/mcp/servers/{server_name}/enable")
    def post_mcp_enable(
        project_id: str,
        server_name: str,
        body: McpEnableRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        mgr = get_mcp_manager(user_id, project_id, workspace)
        if body.enabled:
            enabled = [
                item
                for item in mgr.list_servers()
                if item.get("enabled") and item.get("name") != server_name
            ]
            if len(enabled) >= settings.max_mcp_servers_per_project:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "项目 MCP Server 数量达到上限 "
                        f"({settings.max_mcp_servers_per_project})"
                    ),
                )
        try:
            server = mgr.set_enabled(server_name, body.enabled)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if body.enabled and server.get("status") == "stopped":
            try:
                server = mgr.start_server(server_name)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "server": server,
        }

    @app.post("/api/projects/{project_id}/mcp/servers/{server_name}/reconnect")
    def post_mcp_reconnect(
        project_id: str,
        server_name: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        mgr = get_mcp_manager(user_id, project_id, workspace)
        try:
            server = mgr.reconnect(server_name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "server": server,
        }

    @app.post("/api/projects/{project_id}/mcp/servers/{server_name}/refresh")
    def post_mcp_refresh(
        project_id: str,
        server_name: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        mgr = get_mcp_manager(user_id, project_id, workspace)
        refreshed = mgr.refresh_tools(server_name)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "servers": refreshed,
        }

    @app.post("/api/projects/{project_id}/terminals", status_code=201)
    def create_project_terminal(
        project_id: str,
        body: CreateTerminalRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        require_terminal_enabled()
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        active_terminals = [
            item
            for item in list_terminals(user_id, project_id)
            if item.get("status") in {"starting", "running"}
        ]
        if len(active_terminals) >= settings.max_terminals_per_project:
            raise HTTPException(
                status_code=429,
                detail=(
                    "项目活动终端达到上限 "
                    f"({settings.max_terminals_per_project})"
                ),
            )
        ensure_write_budget()
        try:
            return create_terminal(
                user_id,
                project_id,
                cwd=body.cwd or ".",
                argv=body.argv,
                shell=body.shell,
                cols=body.cols,
                rows=body.rows,
                env=body.env,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/projects/{project_id}/terminals")
    def get_project_terminals(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        require_terminal_enabled()
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "terminals": list_terminals(user_id, project_id),
        }

    @app.get("/api/terminals/{terminal_id}")
    def get_terminal_info(
        terminal_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        require_terminal_enabled()
        info = get_terminal(terminal_id, user_id)
        if not info:
            raise HTTPException(status_code=404, detail="终端不存在")
        return info

    @app.post("/api/terminals/{terminal_id}/input")
    def post_terminal_input(
        terminal_id: str,
        body: TerminalInputRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        require_terminal_enabled()
        result = write_terminal_input(terminal_id, user_id, body.data)
        if result is None:
            raise HTTPException(status_code=404, detail="终端不存在")
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result

    @app.post("/api/terminals/{terminal_id}/resize")
    def post_terminal_resize(
        terminal_id: str,
        body: TerminalResizeRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        require_terminal_enabled()
        result = resize_terminal(terminal_id, user_id, body.cols, body.rows)
        if result is None:
            raise HTTPException(status_code=404, detail="终端不存在")
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result

    @app.delete("/api/terminals/{terminal_id}")
    def delete_terminal(
        terminal_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        require_terminal_enabled()
        result = terminate_terminal(terminal_id, user_id)
        if result is None:
            raise HTTPException(status_code=404, detail="终端不存在")
        return result

    @app.websocket("/api/ws/terminals/{terminal_id}")
    async def ws_terminal(
        terminal_id: str,
        websocket: WebSocket,
        ticket: Optional[str] = Query(default=None),
        after_seq: Optional[int] = Query(default=None),
    ) -> None:
        if not settings.terminal_enabled:
            await websocket.close(code=4404)
            return
        auth_header = websocket.headers.get("authorization")
        if auth_header:
            try:
                user_id = authenticated_user(auth_header)
            except HTTPException:
                await websocket.close(code=4401)
                return
        else:
            user_id = app.state.ws_tickets.consume(
                ticket or "",
                "terminal",
                terminal_id,
            )
            if not user_id:
                await websocket.close(code=4401)
                return

        info = get_terminal(terminal_id, user_id)
        if not info:
            await websocket.close(code=4404)
            return

        await websocket.accept()
        cursor = after_seq or 0
        try:
            while True:
                info = get_terminal(terminal_id, user_id)
                if not info:
                    break
                for chunk in terminal_outputs(terminal_id, after_seq=cursor, limit=100):
                    seq = chunk["seq"]
                    if seq > cursor:
                        await websocket.send_json(public_terminal_ws_chunk(chunk))
                        cursor = seq
                if info["status"] in {"exited", "failed", "terminated", "interrupted"}:
                    await websocket.send_json(public_terminal_ws_done(info))
                    break
                await asyncio.sleep(0.05)
        except WebSocketDisconnect:
            return

    @app.websocket("/api/ws/jobs/{job_id}")
    async def ws_job(
        job_id: str,
        websocket: WebSocket,
        ticket: Optional[str] = Query(default=None),
        after_event_id: Optional[int] = Query(default=None),
    ) -> None:
        auth_header = websocket.headers.get("authorization")
        if auth_header:
            try:
                user_id = authenticated_user(auth_header)
            except HTTPException:
                await websocket.close(code=4401)
                return
        else:
            user_id = app.state.ws_tickets.consume(
                ticket or "",
                "job",
                job_id,
            )
            if not user_id:
                await websocket.close(code=4401)
                return

        job = get_job(job_id, user_id=user_id)
        if not job:
            await websocket.close(code=4404)
            return

        await websocket.accept()
        cursor_id = after_event_id
        done_sent = False
        try:
            while True:
                job = get_job(job_id, user_id=user_id)
                if not job:
                    break

                for event in job.get("events", []):
                    event_id = event.get("id")
                    if cursor_id is None or (
                        isinstance(event_id, int) and event_id > cursor_id
                    ):
                        await websocket.send_json(public_job_ws_event(event))
                        if isinstance(event_id, int):
                            cursor_id = event_id

                if not done_sent and job["status"] in {"succeeded", "failed", "canceled", "interrupted"}:
                    await websocket.send_json(public_job_ws_done(job))
                    done_sent = True
                    break

                await asyncio.sleep(0.05)
        except WebSocketDisconnect:
            return

    web_dir = Path(__file__).resolve().parent / "web"
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if (
        web_dir.is_dir()
        and settings.debug_web_ui_enabled
        and settings.server_host in loopback_hosts
    ):
        @app.get("/", include_in_schema=False)
        def ui_root() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

        @app.get("/ui", include_in_schema=False)
        def ui_redirect() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

        app.mount(
            "/ui",
            StaticFiles(directory=str(web_dir), html=True),
            name="ui",
        )

    return app


def _guess_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None
