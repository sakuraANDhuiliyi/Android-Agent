from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import hmac
import json
import shutil
import smtplib
import socket
from contextlib import asynccontextmanager
from email.message import EmailMessage
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
from agent.conversation_events import (
    ConversationEventError,
    ConversationEventStore,
    EVENT_SCHEMA_VERSION,
)
from agent.explicit_context import build_context_bundle
from agent.feedback import FeedbackStore, feedback_summary
from agent.explorer import search_files
from agent.history import history, restore_preview, restore_snapshot, branch_snapshot
from agent.workspace import WorkspaceRepository
from agent.project_lifecycle import project_operation
import hashlib
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
    revert_hunk,
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
from agent.rules import (
    create_project_rule,
    delete_project_rule,
    diagnose_rules,
    discover_rules,
    load_rules_for_turn,
    update_project_rule,
)
from agent.skills import discover_skills_for_context, list_skills, load_skill
from agent.project_settings import (
    PERMISSION_PROFILES,
    load_project_settings,
    set_rule_disabled,
    set_skill_disabled,
    update_project_settings,
)
from agent.permissions import profile_summary
from agent.usage_inspector import conversation_usage, usage_summary
from agent.turn_trace import build_turn_trace
from agent.mcp_manager import get_mcp_manager
from agent.mcp_manager import reset_mcp_managers
from agent.mcp_config import (
    is_project_mcp_trusted,
    project_mcp_config_path,
    sanitize_mcp_config_for_edit,
    save_project_mcp_config,
    validate_mcp_config_payload,
)
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
    purge_user_terminals,
)
from agent.tools import is_writable_path, list_dir_entries, read_file_meta, write_file
from agent.users import AuthIdentity, UserStore, UserStoreError
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
    feedback_requested: bool = False
    prompt: str = Field(..., min_length=1, max_length=100_000)
    provider: Optional[str] = None
    auto_fallback: bool = False
    continue_session: bool = True
    reset_session: bool = False
    conversation_id: Optional[str] = None
    run_mode: Optional[RunMode] = None
    contexts: list["ContextAttachmentRequest"] = Field(default_factory=list, max_length=20)


class ApprovalDecisionRequest(StrictRequest):
    approved: bool


class CreateConversationRequest(StrictRequest):
    title: Optional[str] = Field(default=None, max_length=500)


class UpdateConversationRequest(StrictRequest):
    title: Optional[str] = Field(default=None, max_length=500)
    status: Optional[str] = None


class ConversationAskRequest(StrictRequest):
    feedback_requested: bool = False
    prompt: str = Field(..., min_length=1, max_length=100_000)
    provider: Optional[str] = None
    auto_fallback: bool = False
    run_mode: Optional[RunMode] = None
    contexts: list["ContextAttachmentRequest"] = Field(default_factory=list, max_length=20)


class ContextAttachmentRequest(StrictRequest):
    kind: str = Field(..., pattern="^(file|folder|selection|diff|build_log|terminal|error|screenshot|conversation|symbol)$")
    label: str = Field(..., min_length=1, max_length=240)
    path: Optional[str] = Field(default=None, max_length=1000)
    text: Optional[str] = Field(default=None, max_length=24_000)
    symbol: Optional[str] = Field(default=None, max_length=500)
    line_start: Optional[int] = Field(default=None, ge=1)
    line_end: Optional[int] = Field(default=None, ge=1)
    ref_id: Optional[str] = Field(default=None, max_length=128)


class ContextPreviewRequest(StrictRequest):
    prompt: str = Field(default="", max_length=100_000)
    contexts: list[ContextAttachmentRequest] = Field(default_factory=list, max_length=20)


class WriteFileRequest(StrictRequest):
    path: str = Field(..., min_length=1)
    content: str = Field(default="", max_length=2_000_000)
    expected_revision: Optional[str] = Field(default=None, pattern="^[a-f0-9]{64}$")


class FeedbackSettingsRequest(StrictRequest):
    build_after_changes: bool = False
    run_tests: bool = False
    fix_failures: bool = False


class RuntimeDiagnosticRequest(StrictRequest):
    message: str = Field(..., min_length=1, max_length=24_000)


class RestoreCheckpointRequest(StrictRequest):
    path: Optional[str] = None
    preview: bool = False


class RestoreSnapshotRequest(StrictRequest):
    expected_revision: str = Field(..., min_length=64, max_length=64)


class SnapshotBranchRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=160)


class RevertHunkRequest(StrictRequest):
    path: str = Field(..., min_length=1)
    hunk: str = Field(..., min_length=1, max_length=200_000)


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

class McpConfigRequest(StrictRequest):
    config: dict[str, Any] = Field(default_factory=dict)


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


class RuleCreateRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=67)
    description: str = Field(default="", max_length=200)
    content: str = Field(..., min_length=1, max_length=32_000)
    always: bool = True
    globs: list[str] = Field(default_factory=list, max_length=16)


class RuleUpdateRequest(StrictRequest):
    description: Optional[str] = Field(default=None, max_length=200)
    content: Optional[str] = Field(default=None, max_length=32_000)
    always: Optional[bool] = None
    globs: Optional[list[str]] = Field(default=None, max_length=16)


class SkillToggleRequest(StrictRequest):
    scope: str = Field(..., pattern="^(project|user)$")
    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True


class ProjectSettingsPatchRequest(StrictRequest):
    permission_profile: Optional[str] = Field(default=None, min_length=1, max_length=32)


class WebSocketTicketRequest(StrictRequest):
    resource_type: str = Field(..., pattern="^(job|terminal)$")
    resource_id: str = Field(..., min_length=1, max_length=128)


class DeviceRequest(StrictRequest):
    device_id: str = Field(..., min_length=1, max_length=128)
    device_name: str = Field(..., min_length=1, max_length=120)
    device_type: str = Field(default="android", max_length=32)
    platform: str = Field(default="Android", max_length=80)
    app_version: str = Field(default="", max_length=40)


class AccountRegisterRequest(StrictRequest):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)
    device: DeviceRequest


class AccountLoginRequest(StrictRequest):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)
    device: DeviceRequest


class GuestSessionRequest(StrictRequest):
    device: DeviceRequest


class EmailCodeLoginRequest(StrictRequest):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=6, max_length=12)
    device: DeviceRequest


class VerifyEmailRequest(StrictRequest):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=6, max_length=12)
    device: DeviceRequest


class EmailRequest(StrictRequest):
    email: str = Field(..., min_length=3, max_length=254)


class ResetPasswordRequest(StrictRequest):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=6, max_length=12)
    new_password: str = Field(..., min_length=8, max_length=128)


class AccountUpdateRequest(StrictRequest):
    display_name: str = Field(..., min_length=1, max_length=80)


class ChangePasswordRequest(StrictRequest):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)
    revoke_other_sessions: bool = True


class DeleteAccountRequest(StrictRequest):
    password: str = Field(default="", max_length=128)


class AdminCreateAccountRequest(StrictRequest):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)
    email_verified: bool = True


class AdminUpdateAccountRequest(StrictRequest):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    email_verified: Optional[bool] = None
    disabled: Optional[bool] = None


class AdminIssueTokenRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=120)
    device_id: Optional[str] = Field(default=None, max_length=128)


class AdminResetPasswordRequest(StrictRequest):
    new_password: str = Field(..., min_length=8, max_length=128)


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


def _device_dict(device: DeviceRequest) -> dict[str, str]:
    return {
        "device_id": device.device_id,
        "device_name": device.device_name,
        "device_type": device.device_type,
        "platform": device.platform,
        "app_version": device.app_version,
    }


def _auth_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "account": result["account"],
        "user_id": result["account"]["user_id"],
        "token": result.get("token"),
        "token_type": "Bearer",
        "session_id": result.get("session_id"),
    }


def _send_account_code(settings: Settings, email: str, code: str, purpose: str) -> None:
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("服务端尚未配置邮箱发送服务")
    subject = {
        "verify_email": "验证你的 Android Agent 邮箱",
        "login_email": "登录 Android Agent",
        "reset_password": "重置 Android Agent 密码",
    }.get(purpose, "Android Agent 验证码")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = email
    message.set_content(
        f"{subject}\n\n验证码：{code}\n\n验证码 15 分钟内有效。如非本人操作，请忽略本邮件。"
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


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
        # Restart recovery belongs to server startup, not app construction.
        # Schema generation and read-only contract checks must not invalidate live PTYs.
        mark_interrupted_terminals()
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Registration-Token"],
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=settings.max_request_bytes,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        error_code = None
        if isinstance(exc.detail, dict):
            error_code = str(exc.detail.get("code") or "") or None
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error_body(exc.status_code, exc.detail, code=error_code),
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
        response = await call_next(request)
        if request.url.path.startswith("/api/me/creative/") or (request.method == "POST" and request.url.path.startswith("/api/creative/items/") and request.url.path.endswith("/reports")):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Authorization"
        if request.url.path == "/admin" or request.url.path.startswith(("/admin/", "/api/admin/")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            )
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def authenticated_identity(authorization: str | None) -> AuthIdentity:
        token = _bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="未提供 API Token")
        identity = app.state.user_store.authenticate_identity(token)
        if identity:
            return identity
        try:
            return AuthIdentity(resolve_user_id(app.state.settings, authorization), None)
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

    def current_identity(authorization: Optional[str] = Header(default=None)) -> AuthIdentity:
        return authenticated_identity(authorization)

    def current_user(request: Request, identity: AuthIdentity = Depends(current_identity)) -> str:
        if app.state.user_store.is_guest(identity.user_id):
            if not settings.guest_sessions_enabled:
                raise HTTPException(status_code=403, detail="游客体验未启用，请登录账号")
            path = request.scope["path"]
            if "/mcp/" in path or "/terminals" in path or path.endswith("/settings") or path.endswith("/recover"):
                raise HTTPException(status_code=403, detail="此操作需要登录账号")
            route = getattr(request.scope.get("route"), "path", "")
            guest_writes = {
                ("POST", "/api/projects"),
                ("POST", "/api/projects/{project_id}/ask"),
                ("POST", "/api/projects/{project_id}/conversations"),
                ("POST", "/api/conversations/{conversation_id}/ask"),
                ("POST", "/api/jobs/{job_id}/cancel"),
                ("POST", "/api/ws/tickets"),
                ("DELETE", "/api/projects/{project_id}"),
                ("DELETE", "/api/projects/{project_id}/session"),
                ("DELETE", "/api/conversations/{conversation_id}"),
            }
            if request.method not in {"GET", "HEAD", "OPTIONS"} and (request.method, route) not in guest_writes:
                raise HTTPException(status_code=403, detail="游客体验仅支持只读操作，请登录账号")
        return identity.user_id

    def current_admin(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        if not settings.admin_ui_enabled:
            raise HTTPException(status_code=404, detail="管理后台未启用")
        token = _bearer_token(authorization)
        if not token or not hmac.compare_digest(token, settings.admin_token):
            client_host = request.client.host if request.client else "unknown"
            try:
                http_limiter.check(
                    f"admin-auth-failed:{client_host}",
                    limit=30,
                    window_seconds=15 * 60,
                )
            except QuotaExceededError as exc:
                raise HTTPException(
                    status_code=429,
                    detail="管理员鉴权尝试过于频繁，请稍后再试",
                    headers={"Retry-After": "900"},
                ) from exc
            raise HTTPException(status_code=401, detail="无效的管理员 Token")

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

    def reserve_guest_turn(user_id: str) -> int | None:
        try:
            return app.state.user_store.consume_guest_message(
                user_id, limit=settings.guest_message_limit, daily_limit=settings.max_guest_turns_per_day
            )
        except UserStoreError as exc:
            status = 429 if exc.code == "guest_global_quota_exhausted" else 403 if exc.code == "guest_quota_exhausted" else 400
            raise account_error(exc, status) from exc

    @app.get("/healthz", include_in_schema=False)
    def deployment_health() -> dict[str, str]:
        """Unauthenticated liveness probe for the hosting platform."""
        return {"status": "ok"}

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

    def account_error(exc: UserStoreError, status_code: int = 400) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={"message": str(exc), "code": exc.code},
        )

    @app.post("/api/auth/register", status_code=201)
    def register_account(body: AccountRegisterRequest, request: Request) -> dict[str, Any]:
        if not settings.registration_enabled:
            raise HTTPException(status_code=404, detail="网络注册未启用")
        client_host = request.client.host if request.client else "unknown"
        try:
            reg_limiter.check(
                f"account-register:{client_host}",
                limit=settings.max_registration_per_hour,
                window_seconds=3600,
            )
        except QuotaExceededError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": "3600"},
            ) from exc
        if settings.email_verification_required and (
            not settings.smtp_host or not settings.smtp_from
        ):
            raise HTTPException(status_code=503, detail="服务端尚未配置邮箱发送服务")
        ensure_write_budget()
        try:
            result = app.state.user_store.register_account(
                body.email,
                body.password,
                display_name=body.display_name,
                email_verified=not settings.email_verification_required,
                device=_device_dict(body.device),
            )
            user_id = result["account"]["user_id"]
            user_workspaces_dir(user_id).mkdir(parents=True, exist_ok=True)
            user_builds_dir(user_id).mkdir(parents=True, exist_ok=True)
            if settings.email_verification_required:
                code = app.state.user_store.create_code(user_id, "verify_email")
                _send_account_code(settings, body.email, code, "verify_email")
            payload = _auth_payload(result)
            payload["requires_verification"] = settings.email_verification_required
            return payload
        except UserStoreError as exc:
            raise account_error(exc, 409 if exc.code == "email_exists" else 400) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise HTTPException(status_code=503, detail="验证邮件发送失败，请稍后重试") from exc

    @app.post("/api/auth/login")
    def login_account(body: AccountLoginRequest, request: Request) -> dict[str, Any]:
        client_host = request.client.host if request.client else "unknown"
        email_key = hashlib.sha256(body.email.strip().lower().encode("utf-8")).hexdigest()[:16]
        try:
            http_limiter.check(
                f"account-login:{client_host}:{email_key}",
                limit=20,
                window_seconds=15 * 60,
            )
        except QuotaExceededError as exc:
            raise HTTPException(
                status_code=429,
                detail="登录尝试过于频繁，请稍后再试",
                headers={"Retry-After": "900"},
            ) from exc
        try:
            return _auth_payload(
                app.state.user_store.login(
                    body.email,
                    body.password,
                    device=_device_dict(body.device),
                )
            )
        except UserStoreError as exc:
            raise account_error(exc, 401) from exc

    @app.post("/api/auth/guest", status_code=201)
    def create_guest_session(body: GuestSessionRequest, request: Request,
                             authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
        if not settings.guest_sessions_enabled or not settings.registration_enabled or settings.email_verification_required:
            raise HTTPException(status_code=404, detail="游客体验未启用，请登录账号")
        try:
            host = request.client.host if request.client else "unknown"
            reg_limiter.check(f"guest-register:{host}", limit=settings.max_registration_per_hour, window_seconds=3600)
            reg_limiter.check("guest-register:global", limit=settings.max_registration_per_hour, window_seconds=3600)
        except QuotaExceededError as exc:
            raise HTTPException(status_code=429, detail="游客会话创建过于频繁，请稍后再试") from exc
        ensure_write_budget()
        try:
            result = app.state.user_store.guest_session(device=_device_dict(body.device), token=_bearer_token(authorization))
            user_id = result["account"]["user_id"]
            user_workspaces_dir(user_id).mkdir(parents=True, exist_ok=True)
            user_builds_dir(user_id).mkdir(parents=True, exist_ok=True)
            return _auth_payload(result)
        except UserStoreError as exc:
            raise account_error(exc, 401 if exc.code == "guest_auth_required" else 400) from exc

    def limit_email_code(request: Request, email: str, *, sending: bool = False) -> None:
        host = request.client.host if request.client else "unknown"
        digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        kind = "send" if sending else "verify"
        try:
            http_limiter.check(f"code:{kind}:email:{digest}", limit=1 if sending else 15,
                               window_seconds=60 if sending else 900)
            http_limiter.check(f"code:{kind}:ip:{host}", limit=10 if sending else 60, window_seconds=900)
        except QuotaExceededError as exc:
            raise HTTPException(status_code=429, detail="验证码操作过于频繁，请稍后再试") from exc

    @app.post("/api/auth/email-code/request", status_code=202)
    def request_email_login_code(body: EmailRequest, request: Request) -> dict[str, bool]:
        limit_email_code(request, body.email, sending=True)
        if not settings.smtp_host or not settings.smtp_from:
            raise HTTPException(status_code=503, detail="服务端尚未配置邮箱发送服务")
        try:
            account = app.state.user_store.account_for_email(body.email)
        except UserStoreError as exc:
            raise account_error(exc, 400) from exc
        # 始终返回 accepted，避免通过接口枚举已注册邮箱。
        if account and not account.get("is_guest"):
            try:
                code = app.state.user_store.create_code(account["user_id"], "login_email")
                _send_account_code(settings, body.email, code, "login_email")
            except (OSError, smtplib.SMTPException, RuntimeError, UserStoreError):
                pass
        return {"accepted": True}

    @app.post("/api/auth/email-code/login")
    def login_with_email_code(body: EmailCodeLoginRequest, request: Request) -> dict[str, Any]:
        limit_email_code(request, body.email, sending=False)
        try:
            return _auth_payload(
                app.state.user_store.login_with_email_code(
                    body.email,
                    body.code,
                    device=_device_dict(body.device),
                )
            )
        except UserStoreError as exc:
            raise account_error(exc, 401) from exc

    @app.post("/api/auth/verify-email")
    def verify_email(body: VerifyEmailRequest, request: Request) -> dict[str, Any]:
        limit_email_code(request, body.email, sending=False)
        try:
            result = app.state.user_store.verify_email_and_login(
                body.email,
                body.code,
                device=_device_dict(body.device),
            )
            return _auth_payload(result)
        except UserStoreError as exc:
            raise account_error(exc, 400) from exc

    @app.post("/api/auth/resend-verification", status_code=202)
    def resend_verification(body: EmailRequest, request: Request) -> dict[str, bool]:
        limit_email_code(request, body.email, sending=True)
        # Do not reveal whether an address exists.
        try:
            account = app.state.user_store.account_for_email(body.email)
        except UserStoreError:
            account = None
        if account and not account["email_verified"] and settings.email_verification_required:
            try:
                code = app.state.user_store.create_code(account["user_id"], "verify_email")
                _send_account_code(settings, body.email, code, "verify_email")
            except (OSError, smtplib.SMTPException, RuntimeError, UserStoreError):
                pass
        return {"accepted": True}

    @app.post("/api/auth/forgot-password", status_code=202)
    def forgot_password(body: EmailRequest, request: Request) -> dict[str, bool]:
        limit_email_code(request, body.email, sending=True)
        try:
            account = app.state.user_store.account_for_email(body.email)
        except UserStoreError:
            account = None
        if account and settings.smtp_host and settings.smtp_from:
            try:
                code = app.state.user_store.create_code(account["user_id"], "reset_password")
                _send_account_code(settings, body.email, code, "reset_password")
            except (OSError, smtplib.SMTPException, RuntimeError, UserStoreError):
                pass
        return {"accepted": True}

    @app.post("/api/auth/reset-password", status_code=204)
    def reset_password(body: ResetPasswordRequest, request: Request) -> None:
        limit_email_code(request, body.email, sending=False)
        try:
            app.state.user_store.reset_password(body.email, body.code, body.new_password)
        except UserStoreError as exc:
            raise account_error(exc, 400) from exc

    @app.post("/api/auth/logout", status_code=204)
    def logout(identity: AuthIdentity = Depends(current_identity)) -> None:
        if identity.session_id:
            app.state.user_store.revoke_session(identity.user_id, identity.session_id)

    @app.get("/api/account")
    def get_account(identity: AuthIdentity = Depends(current_identity)) -> dict[str, Any]:
        try:
            return app.state.user_store.get_account(identity.user_id)
        except UserStoreError as exc:
            raise account_error(exc, 404) from exc

    @app.patch("/api/account")
    def patch_account(
        body: AccountUpdateRequest,
        identity: AuthIdentity = Depends(current_identity),
    ) -> dict[str, Any]:
        try:
            return app.state.user_store.update_account(
                identity.user_id,
                display_name=body.display_name,
            )
        except UserStoreError as exc:
            raise account_error(exc, 400) from exc

    @app.post("/api/account/change-password", status_code=204)
    def change_account_password(
        body: ChangePasswordRequest,
        identity: AuthIdentity = Depends(current_identity),
    ) -> None:
        try:
            app.state.user_store.change_password(
                identity.user_id,
                body.old_password,
                body.new_password,
                current_session_id=identity.session_id,
                revoke_other_sessions=body.revoke_other_sessions,
            )
        except UserStoreError as exc:
            raise account_error(exc, 400) from exc

    @app.delete("/api/account", status_code=204)
    def delete_account_route(
        body: DeleteAccountRequest,
        identity: AuthIdentity = Depends(current_identity),
    ) -> None:
        try:
            app.state.user_store.delete_account(identity.user_id, body.password)
        except UserStoreError as exc:
            raise account_error(exc, 401 if exc.code == "invalid_password" else 400) from exc
        app.state.task_store.purge_user_data(identity.user_id)
        purge_user_terminals(identity.user_id)
        shutil.rmtree(user_workspaces_dir(identity.user_id), ignore_errors=True)
        shutil.rmtree(user_builds_dir(identity.user_id), ignore_errors=True)

    @app.get("/api/devices")
    def get_devices(identity: AuthIdentity = Depends(current_identity)) -> dict[str, Any]:
        return {
            "devices": app.state.user_store.list_sessions(
                identity.user_id,
                current_session_id=identity.session_id,
            )
        }

    @app.delete("/api/devices/{session_id}", status_code=204)
    def revoke_device(
        session_id: str,
        identity: AuthIdentity = Depends(current_identity),
    ) -> None:
        try:
            revoked = app.state.user_store.revoke_session(identity.user_id, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="设备会话不存在") from exc
        if not revoked:
            raise HTTPException(status_code=404, detail="设备会话不存在")

    @app.post("/api/devices/logout-others")
    def logout_other_devices(identity: AuthIdentity = Depends(current_identity)) -> dict[str, int]:
        return {
            "revoked": app.state.user_store.revoke_other_sessions(
                identity.user_id,
                identity.session_id,
            )
        }

    @app.get("/api/admin/overview", include_in_schema=False)
    def admin_overview(_admin: None = Depends(current_admin)) -> dict[str, int]:
        return app.state.user_store.admin_overview()

    @app.get("/api/admin/accounts", include_in_schema=False)
    def admin_accounts(
        query: str = Query(default="", max_length=200),
        _admin: None = Depends(current_admin),
    ) -> dict[str, Any]:
        accounts = app.state.user_store.admin_list_accounts(query)
        return {"accounts": accounts, "count": len(accounts)}

    @app.post("/api/admin/accounts", status_code=201, include_in_schema=False)
    def admin_create_account(
        body: AdminCreateAccountRequest,
        _admin: None = Depends(current_admin),
    ) -> dict[str, Any]:
        ensure_write_budget()
        try:
            result = app.state.user_store.register_account(
                body.email,
                body.password,
                display_name=body.display_name,
                email_verified=body.email_verified,
                issue_session=False,
            )
        except UserStoreError as exc:
            raise account_error(exc, 409 if exc.code == "email_exists" else 400) from exc
        user_id = result["account"]["user_id"]
        user_workspaces_dir(user_id).mkdir(parents=True, exist_ok=True)
        user_builds_dir(user_id).mkdir(parents=True, exist_ok=True)
        return result["account"]

    @app.get("/api/admin/accounts/{user_id}", include_in_schema=False)
    def admin_account(
        user_id: str,
        _admin: None = Depends(current_admin),
    ) -> dict[str, Any]:
        try:
            return app.state.user_store.admin_get_account(user_id)
        except (UserStoreError, ValueError) as exc:
            raise account_error(exc, 404) if isinstance(exc, UserStoreError) else HTTPException(status_code=404, detail="账号不存在")

    @app.patch("/api/admin/accounts/{user_id}", include_in_schema=False)
    def admin_update_account(
        user_id: str,
        body: AdminUpdateAccountRequest,
        _admin: None = Depends(current_admin),
    ) -> dict[str, Any]:
        try:
            return app.state.user_store.admin_update_account(
                user_id,
                display_name=body.display_name,
                email_verified=body.email_verified,
                disabled=body.disabled,
            )
        except (UserStoreError, ValueError) as exc:
            if isinstance(exc, UserStoreError):
                raise account_error(exc, 404 if exc.code == "account_not_found" else 400) from exc
            raise HTTPException(status_code=404, detail="账号不存在") from exc

    @app.post("/api/admin/accounts/{user_id}/tokens", status_code=201, include_in_schema=False)
    def admin_issue_token(
        user_id: str,
        body: AdminIssueTokenRequest,
        _admin: None = Depends(current_admin),
    ) -> dict[str, str]:
        try:
            result = app.state.user_store.admin_issue_token(
                user_id,
                device_id=body.device_id or "",
                device_name=body.name,
                device_type="api_token",
                platform="管理后台",
                app_version="",
            )
        except (UserStoreError, ValueError) as exc:
            if isinstance(exc, UserStoreError):
                raise account_error(exc, 409) from exc
            raise HTTPException(status_code=404, detail="账号不存在") from exc
        return {**result, "token_type": "Bearer"}

    @app.delete("/api/admin/accounts/{user_id}/tokens/{session_id}", status_code=204, include_in_schema=False)
    def admin_revoke_token(
        user_id: str,
        session_id: str,
        _admin: None = Depends(current_admin),
    ) -> None:
        try:
            revoked = app.state.user_store.revoke_session(user_id, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Token 不存在") from exc
        if not revoked:
            raise HTTPException(status_code=404, detail="Token 不存在或已撤销")

    @app.post("/api/admin/accounts/{user_id}/revoke-all", include_in_schema=False)
    def admin_revoke_all(
        user_id: str,
        _admin: None = Depends(current_admin),
    ) -> dict[str, int]:
        try:
            app.state.user_store.get_account(user_id)
            return {"revoked": app.state.user_store.revoke_other_sessions(user_id, None)}
        except (UserStoreError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="账号不存在") from exc

    @app.post("/api/admin/accounts/{user_id}/reset-password", include_in_schema=False)
    def admin_reset_password(
        user_id: str,
        body: AdminResetPasswordRequest,
        _admin: None = Depends(current_admin),
    ) -> dict[str, int]:
        try:
            return {"revoked": app.state.user_store.admin_set_password(user_id, body.new_password)}
        except (UserStoreError, ValueError) as exc:
            if isinstance(exc, UserStoreError):
                raise account_error(exc, 404 if exc.code == "account_not_found" else 400) from exc
            raise HTTPException(status_code=404, detail="账号不存在") from exc

    @app.delete("/api/admin/accounts/{user_id}", status_code=204, include_in_schema=False)
    def admin_delete_account(
        user_id: str,
        _admin: None = Depends(current_admin),
    ) -> None:
        try:
            app.state.user_store.admin_delete_account(user_id)
        except (UserStoreError, ValueError) as exc:
            if isinstance(exc, UserStoreError):
                raise account_error(exc, 404) from exc
            raise HTTPException(status_code=404, detail="账号不存在") from exc
        app.state.task_store.purge_user_data(user_id)
        purge_user_terminals(user_id)
        shutil.rmtree(user_workspaces_dir(user_id), ignore_errors=True)
        shutil.rmtree(user_builds_dir(user_id), ignore_errors=True)

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
            if app.state.user_store.is_guest(user_id):
                raise HTTPException(status_code=403, detail="终端需要登录账号")
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
        project_limit = min(settings.max_projects_per_user, 3) if app.state.user_store.is_guest(user_id) else settings.max_projects_per_user
        if len(list_projects(user_id)) >= project_limit:
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

        guest_remaining = reserve_guest_turn(user_id)
        if guest_remaining is not None:
            body.run_mode = "read_only"
            body.feedback_requested = False
            job_settings = replace(job_settings, max_auto_continuations=0, auto_build_after_edit=False, max_turns=min(job_settings.max_turns, 3))
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
                contexts=[item.model_dump(exclude_none=True) for item in body.contexts],
                feedback_requested=body.feedback_requested,
            )
        except RuntimeError as e:
            if guest_remaining is not None:
                app.state.user_store.refund_guest_message(user_id)
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"job": job_to_dict(job), "guest_remaining": guest_remaining}

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

    @app.get("/api/conversations/{conversation_id}/turns/{turn_id}/trace")
    def get_turn_trace(
        conversation_id: str,
        turn_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        trace = build_turn_trace(
            ConversationEventStore(app.state.task_store),
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        if trace is None:
            raise HTTPException(
                status_code=404,
                detail=f"Turn 不存在或不属于该对话: {turn_id}",
            )
        return trace

    @app.get("/api/conversations/{conversation_id}/turns")
    def list_conversation_turns(
        conversation_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        turns = ConversationEventStore(app.state.task_store).list_turns(
            conversation_id,
            user_id=user_id,
        )
        if turns is None:
            raise HTTPException(
                status_code=404,
                detail=f"对话不存在: {conversation_id}",
            )
        return {
            "conversation_id": conversation_id,
            "schema_version": 1,
            "turns": turns,
        }

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
        guest_remaining = reserve_guest_turn(user_id)
        if guest_remaining is not None:
            body.run_mode = "read_only"
            body.feedback_requested = False
            job_settings = replace(job_settings, max_auto_continuations=0, auto_build_after_edit=False, max_turns=min(job_settings.max_turns, 3))
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
                contexts=[item.model_dump(exclude_none=True) for item in body.contexts],
                feedback_requested=body.feedback_requested,
            )
        except RuntimeError as e:
            if guest_remaining is not None:
                app.state.user_store.refund_guest_message(user_id)
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {
            "job": job_to_dict(job),
            "conversation_id": conversation_id,
            "guest_remaining": guest_remaining,
        }

    @app.get("/api/conversations/{conversation_id}/context")
    def get_conversation_context(
        conversation_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        conv = get_conversation(conversation_id, user_id)
        if not conv:
            raise HTTPException(status_code=404, detail=f"对话不存在: {conversation_id}")
        jobs = list_jobs(user_id, conv["project_id"], conversation_id)
        latest = max(jobs, key=lambda row: row.get("created_at") or 0) if jobs else None
        context = latest.get("context") if latest and isinstance(latest.get("context"), dict) else {}
        summary = context.get("summary") if isinstance(context.get("summary"), dict) else None
        if summary is None:
            bundle = build_context_bundle(
                user_id,
                conv["project_id"],
                str(latest.get("prompt") or "") if latest else "",
                context.get("attachments") or [],
                effective_task_store,
                include_automatic=True,
                budget_chars=settings.max_prompt_chars,
            )
            bundle.pop("model_context", None)
            summary = bundle
        return {
            "user_id": user_id,
            "project_id": conv["project_id"],
            "conversation_id": conversation_id,
            **summary,
        }

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
        guest_remaining = (
            reserve_guest_turn(user_id)
            if body.type in {"steer", "follow_up"}
            else None
        )
        msg = add_job_message(
            job_id,
            user_id,
            message_key=body.message_key,
            type=body.type,
            payload=body.payload,
        )
        if not msg:
            if guest_remaining is not None:
                app.state.user_store.refund_guest_message(user_id)
            raise HTTPException(status_code=409, detail="无法添加消息")
        return {
            "job_id": job_id,
            "message": JobMessageResponse(**msg).model_dump(),
            "guest_remaining": guest_remaining,
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
    def get_task_log(
        job_id: str,
        offset: int = 0,
        limit: int | None = None,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        job = get_job(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        log_path = job.get("build_log_path")
        path = __import__("pathlib").Path(log_path) if log_path else None
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="该任务没有构建日志")
        content = path.read_text(encoding="utf-8", errors="replace")
        total = len(content)
        # 分页读取：客户端传 offset/limit，10MB 级日志不再一次性下发
        if limit is not None:
            page_limit = max(1, min(limit, 524_288))
            page_offset = max(0, offset)
            chunk = content[page_offset : page_offset + page_limit]
            return {
                "job_id": job_id,
                "content": chunk,
                "offset": page_offset,
                "limit": page_limit,
                "total_size": total,
                "has_more": page_offset + len(chunk) < total,
            }
        return {
            "job_id": job_id,
            "content": content,
            "offset": 0,
            "limit": total,
            "total_size": total,
            "has_more": False,
        }

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

    @app.get("/api/projects/{project_id}/feedback")
    def get_project_feedback(project_id: str, job_id: Optional[str] = None, user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if job_id:
            job = effective_task_store.get_task(job_id, user_id)
            if not job or job["project_id"] != project_id:
                raise HTTPException(status_code=404, detail="任务不存在")
        result = feedback_summary(effective_task_store, user_id, project_id, job_id)
        result["settings"] = FeedbackStore(effective_task_store.db_path).settings(user_id, project_id, settings.auto_build_after_edit)
        return result

    @app.put("/api/projects/{project_id}/feedback/settings")
    def put_feedback_settings(project_id: str, body: FeedbackSettingsRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return FeedbackStore(effective_task_store.db_path).save_settings(user_id, project_id, body.model_dump())

    @app.post("/api/projects/{project_id}/feedback/runtime", status_code=201)
    def add_runtime_diagnostic(project_id: str, body: RuntimeDiagnosticRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        current = feedback_summary(effective_task_store, user_id, project_id)
        return app.state.diagnostics.record("runtime", "user_report", body.message, severity="error", user_id=user_id, project_id=project_id, task_id=current.get("job_id"))

    @app.get("/api/projects/{project_id}/files/search")
    def search_project_files(project_id: str, q: str = Query(default="", max_length=200), kind: str = Query(default="all", pattern="^(all|code|resources)$"), modified_only: bool = False, user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
            modified = None
            if modified_only:
                modified = {f["path"] for f in workspace_diff(user_id, project_id).get("files", [])}
            return search_files(workspace_path(user_id, project_id), q, kind, modified)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

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
            "revision": hashlib.sha256(result.output["content"].encode("utf-8")).hexdigest(),
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
        with project_operation(user_id, project_id):
            if any(job["status"] in {"queued", "running", "paused", "awaiting_approval", "cancel_requested"} for job in effective_task_store.list_tasks(user_id, project_id)):
                raise HTTPException(status_code=409, detail="Agent 正在操作项目，请等待任务结束后保存")
            if body.expected_revision:
                current = read_file_meta(workspace, path)
                if not current.ok or current.output.get("truncated") or hashlib.sha256(current.output["content"].encode("utf-8")).hexdigest() != body.expected_revision:
                    raise HTTPException(status_code=409, detail="文件已发生变化。请保留草稿并重新加载文件后合并修改")
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

    def history_repo(project_id: str, user_id: str, *, write: bool = False) -> WorkspaceRepository:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if write:
            active = {"queued", "running", "paused", "awaiting_approval", "cancel_requested"}
            if any(t["status"] in active for t in effective_task_store.list_tasks(user_id, project_id)):
                raise HTTPException(status_code=409, detail="Wait for project tasks to finish before restoring")
            if any(t.get("status") in {"starting", "running"} for t in list_terminals(user_id, project_id)):
                raise HTTPException(status_code=409, detail="Close project terminals before restoring")
        return WorkspaceRepository(user_id, project_id, task_store=effective_task_store)

    @app.get("/api/projects/{project_id}/history")
    def get_project_history(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        return {"entries": history(history_repo(project_id, user_id))}

    @app.get("/api/projects/{project_id}/checkpoints/{checkpoint_id}/preview")
    def preview_snapshot(project_id: str, checkpoint_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            with project_operation(user_id, project_id):
                return restore_preview(history_repo(project_id, user_id), checkpoint_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/api/projects/{project_id}/checkpoints/{checkpoint_id}/restore-snapshot")
    def restore_history_snapshot(project_id: str, checkpoint_id: str, body: RestoreSnapshotRequest,
                                 user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            with project_operation(user_id, project_id):
                result = restore_snapshot(history_repo(project_id, user_id, write=True), checkpoint_id, body.expected_revision)
                if not result["ok"]:
                    raise HTTPException(status_code=409, detail=result)
                return result
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/api/projects/{project_id}/checkpoints/{checkpoint_id}/branch")
    def create_snapshot_branch(project_id: str, checkpoint_id: str, body: SnapshotBranchRequest,
                               user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            with project_operation(user_id, project_id):
                return branch_snapshot(history_repo(project_id, user_id, write=True), checkpoint_id, body.name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

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
            with project_operation(user_id, project_id):
                repo = history_repo(project_id, user_id, write=not body.preview)
                if body.preview:
                    result = detect_checkpoint_conflicts(user_id, project_id, checkpoint_id)
                else:
                    # Legacy conflict-safe undo also retains a durable recovery point.
                    check = repo.detect_conflicts(checkpoint_id)
                    if check.get("has_conflicts") or not check.get("ok"):
                        raise HTTPException(status_code=409, detail=check)
                    backup = repo.create_checkpoint("manual")
                    result = repo.restore_file(checkpoint_id, body.path) if body.path else repo.restore_checkpoint(checkpoint_id)
                    result["backup_checkpoint_id"] = backup["id"]
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

    @app.post("/api/projects/{project_id}/diff/revert-hunk")
    def post_revert_hunk(
        project_id: str,
        body: RevertHunkRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            result = revert_hunk(user_id, project_id, body.path, body.hunk)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result)
        return {"user_id": user_id, "project_id": project_id, **result}

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

    @app.get("/api/projects/{project_id}/context/suggestions")
    def get_context_suggestions(
        project_id: str,
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=40, ge=1, le=100),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        index = get_repo_index(user_id, project_id)
        if index.status()["status"] != "ready":
            index.rebuild()
        repo_map = index.repo_map(max_files=500)
        query = q.strip().lower().lstrip("@")
        files = [
            row for row in repo_map.get("files", [])
            if not query or query in str(row.get("rel_path") or "").lower()
        ][:limit]
        folders = sorted(
            {
                str(Path(str(row.get("rel_path") or "")).parent.as_posix())
                for row in repo_map.get("files", [])
                if "/" in str(row.get("rel_path") or "")
            }
        )
        folders = [path for path in folders if path != "." and (not query or query in path.lower())][:limit]
        symbols = index.find_symbol(limit=200)
        symbols = [
            row for row in symbols
            if not query
            or query in str(row.get("name") or "").lower()
            or query in str(row.get("qualified_name") or "").lower()
        ][:limit]
        return {
            "user_id": user_id,
            "project_id": project_id,
            "files": files,
            "folders": folders,
            "symbols": symbols,
        }

    @app.post("/api/projects/{project_id}/context/preview")
    def post_context_preview(
        project_id: str,
        body: ContextPreviewRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
            bundle = build_context_bundle(
                user_id,
                project_id,
                body.prompt,
                [item.model_dump(exclude_none=True) for item in body.contexts],
                effective_task_store,
                include_automatic=True,
                budget_chars=settings.max_prompt_chars,
            )
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        bundle.pop("model_context", None)
        return {"user_id": user_id, "project_id": project_id, **bundle}

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
        settings_data = load_project_settings(workspace)
        disabled_rules = set(settings_data.get("disabled_rules") or [])
        candidates = discover_rules(workspace, user_id, focus_paths=focus_paths)
        bundle = load_rules_for_turn(workspace, user_id, focus_paths=focus_paths)
        candidate_dicts = [
            {**c.to_dict(), "enabled": c.id not in disabled_rules}
            for c in candidates
        ]
        return {
            "user_id": user_id,
            "project_id": project_id,
            "focus_paths": focus_paths,
            "candidates": candidate_dicts,
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
        disabled_skills = set(
            load_project_settings(workspace).get("disabled_skills") or []
        )
        if q or focus_paths:
            skills = discover_skills_for_context(
                workspace,
                user_id,
                focus_paths=focus_paths,
                query=q,
            )
        else:
            skills = list_skills(workspace, user_id, include_disabled=True)
        skill_dicts = [
            {
                **s.to_dict(),
                "enabled": f"{s.scope}:{s.name}" not in disabled_skills,
            }
            for s in skills
        ]
        return {
            "user_id": user_id,
            "project_id": project_id,
            "query": q,
            "focus_paths": focus_paths,
            "skills": skill_dicts,
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

    @app.post("/api/projects/{project_id}/rules", status_code=201)
    def create_project_rule_route(
        project_id: str,
        body: RuleCreateRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        try:
            rule = create_project_rule(
                workspace,
                body.name,
                description=body.description,
                body=body.content,
                always=body.always,
                globs=body.globs,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileExistsError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "rule": {**rule.to_dict(), "enabled": True},
        }

    @app.patch("/api/projects/{project_id}/rules/{rule_id}")
    def update_project_rule_route(
        project_id: str,
        rule_id: str,
        body: RuleUpdateRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        try:
            rule = update_project_rule(
                workspace,
                rule_id,
                description=body.description,
                body=body.content,
                always=body.always,
                globs=body.globs,
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        disabled_rules = set(
            load_project_settings(workspace).get("disabled_rules") or []
        )
        return {
            "user_id": user_id,
            "project_id": project_id,
            "rule": {**rule.to_dict(), "enabled": rule_id not in disabled_rules},
        }

    @app.delete("/api/projects/{project_id}/rules/{rule_id}", status_code=204)
    def delete_project_rule_route(
        project_id: str,
        rule_id: str,
        user_id: str = Depends(current_user),
    ) -> None:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        try:
            delete_project_rule(workspace, rule_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        current = load_project_settings(workspace)
        if rule_id in current.get("disabled_rules") or []:
            update_project_settings(
                workspace,
                disabled_rules=[
                    item for item in current["disabled_rules"] if item != rule_id
                ],
            )

    @app.post("/api/projects/{project_id}/rules/{rule_id}/toggle")
    def toggle_project_rule_route(
        project_id: str,
        rule_id: str,
        body: McpEnableRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        known_ids = {rule.id for rule in discover_rules(workspace, user_id)}
        if rule_id not in known_ids:
            raise HTTPException(status_code=404, detail=f"规则不存在: {rule_id}")
        try:
            settings_data = set_rule_disabled(workspace, rule_id, not body.enabled)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "rule_id": rule_id,
            "enabled": body.enabled,
            "settings": settings_data,
        }

    @app.post("/api/projects/{project_id}/skills/toggle")
    def toggle_project_skill_route(
        project_id: str,
        body: SkillToggleRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        known = {
            f"{s.scope}:{s.name}"
            for s in list_skills(workspace, user_id, include_disabled=True)
        }
        skill_key = f"{body.scope}:{body.name}"
        if skill_key not in known:
            raise HTTPException(status_code=404, detail=f"Skill 不存在: {skill_key}")
        try:
            settings_data = set_skill_disabled(workspace, skill_key, not body.enabled)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "skill_key": skill_key,
            "enabled": body.enabled,
            "settings": settings_data,
        }

    @app.get("/api/projects/{project_id}/settings")
    def get_project_settings_route(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        return {
            "user_id": user_id,
            "project_id": project_id,
            "settings": load_project_settings(workspace),
            "permission_profiles": profile_summary(),
        }

    @app.patch("/api/projects/{project_id}/settings")
    def patch_project_settings_route(
        project_id: str,
        body: ProjectSettingsPatchRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        if body.permission_profile is not None:
            if body.permission_profile not in PERMISSION_PROFILES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "无效的权限档位: "
                        f"{body.permission_profile}（可选: {', '.join(PERMISSION_PROFILES)}）"
                    ),
                )
            update_project_settings(
                workspace, permission_profile=body.permission_profile
            )
        return {
            "user_id": user_id,
            "project_id": project_id,
            "settings": load_project_settings(workspace),
            "permission_profiles": profile_summary(),
        }

    @app.get("/api/conversations/{conversation_id}/usage")
    def get_conversation_usage_route(
        conversation_id: str,
        turn_limit: int = Query(default=100, ge=1, le=500),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        result = conversation_usage(
            effective_task_store,
            user_id,
            conversation_id,
            turn_limit=turn_limit,
        )
        if result is None:
            raise HTTPException(
                status_code=404, detail=f"对话不存在: {conversation_id}"
            )
        return result

    @app.get("/api/usage/summary")
    def get_usage_summary_route(
        project_id: Optional[str] = Query(default=None),
        days: int = Query(default=30, ge=0, le=365),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        if project_id is not None:
            try:
                load_project_meta(user_id, project_id)
            except (FileNotFoundError, ValueError) as e:
                raise HTTPException(status_code=404, detail=str(e)) from e
        return usage_summary(
            effective_task_store,
            user_id,
            project_id=project_id,
            days=days,
        )

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
        task_id: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=200),
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        rows = _memory_store().list_usage(
            user_id,
            memory_id=memory_id,
            project_id=project_id,
            task_id=task_id,
            limit=limit,
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

    @app.get("/api/projects/{project_id}/mcp/config")
    def get_mcp_config(
        project_id: str,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        path = project_mcp_config_path(workspace)
        if not path.is_file():
            return {
                "user_id": user_id,
                "project_id": project_id,
                "exists": False,
                "config": {"mcpServers": {}},
                "project_trusted": is_project_mcp_trusted(user_id, project_id, workspace),
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"mcp.json 解析失败: {e}"
            ) from e
        return {
            "user_id": user_id,
            "project_id": project_id,
            "exists": True,
            "config": sanitize_mcp_config_for_edit(data),
            "project_trusted": is_project_mcp_trusted(user_id, project_id, workspace),
        }

    @app.put("/api/projects/{project_id}/mcp/config")
    def put_mcp_config(
        project_id: str,
        body: McpConfigRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            load_project_meta(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        workspace = workspace_path(user_id, project_id)
        try:
            normalized = validate_mcp_config_payload(body.config)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        save_project_mcp_config(workspace, normalized)
        mgr = get_mcp_manager(user_id, project_id, workspace)
        mgr.reload_configs()
        return {
            "user_id": user_id,
            "project_id": project_id,
            "config": sanitize_mcp_config_for_edit(normalized),
            "servers": mgr.list_servers(),
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
            with project_operation(user_id, project_id):
                load_project_meta(user_id, project_id)
                if sum(t.get("status") in {"starting", "running"} for t in list_terminals(user_id, project_id)) >= settings.max_terminals_per_project:
                    raise RuntimeError("项目活动终端达到上限")
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

    @app.get("/api/terminals/{terminal_id}/output")
    def get_terminal_output(terminal_id: str, after_seq: int = Query(default=0, ge=0),
                            user_id: str = Depends(current_user)) -> dict[str, Any]:
        require_terminal_enabled()
        info = get_terminal(terminal_id, user_id)
        if not info:
            raise HTTPException(status_code=404, detail="终端不存在")
        chunks = terminal_outputs(terminal_id, after_seq=after_seq, limit=200)
        return {"terminal": info, "chunks": chunks,
                "next_seq": chunks[-1]["seq"] if chunks else after_seq}

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
                user_id = authenticated_identity(auth_header).user_id
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

        if app.state.user_store.is_guest(user_id):
            await websocket.close(code=4403)
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
                user_id = authenticated_identity(auth_header).user_id
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

    from agent.creative.api import install_creative_routes
    install_creative_routes(app, settings, current_admin, current_identity)

    web_dir = Path(__file__).resolve().parent / "web"
    admin_dir = Path(__file__).resolve().parent / "admin"
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

    if admin_dir.is_dir() and settings.admin_ui_enabled and settings.admin_token:
        @app.get("/admin", include_in_schema=False)
        def admin_redirect() -> RedirectResponse:
            return RedirectResponse(url="/admin/")

        app.mount(
            "/admin",
            StaticFiles(directory=str(admin_dir), html=True),
            name="admin",
        )

    return app


def _guess_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None
