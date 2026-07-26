from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.config import Settings, load_settings, models_catalog, resolve_job_settings, resolve_user_id
from agent.conversation_events import ConversationEventError
from agent.database import TaskStore
from agent.jobs import (
    clear_project_session,
    configure_task_store,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_job,
    get_project_session,
    job_to_dict,
    list_conversations,
    list_conversation_events,
    list_job_approvals,
    list_jobs,
    request_cancel,
    resolve_job_approval,
    start_ask_job,
    update_conversation,
)
from agent.paths import (
    DEFAULT_USER_ID,
    build_log_path,
    latest_apk_path,
    user_builds_dir,
    user_workspaces_dir,
    workspace_path,
)
from agent.project import delete_project, init_project, list_projects, load_project_meta
from agent.redaction import redact_sensitive_text
from agent.tools import is_writable_path, list_dir_entries, read_file_meta, write_file
from agent.users import UserStore


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1)
    package: Optional[str] = None


class AskRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    provider: Optional[str] = None
    auto_fallback: bool = False
    continue_session: bool = True
    reset_session: bool = False
    conversation_id: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None


class ConversationAskRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    provider: Optional[str] = None
    auto_fallback: bool = False


class WriteFileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = Field(default="")


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
    return value[7:].strip() if value.lower().startswith("bearer ") else value


def _project_status(user_id: str, project_id: str) -> dict[str, Any]:
    meta = load_project_meta(user_id, project_id)
    apk = latest_apk_path(user_id, project_id)
    builds_dir = user_builds_dir(user_id) / project_id
    build_logs = []
    if builds_dir.is_dir():
        for log_file in sorted(builds_dir.glob("*.log"), reverse=True):
            build_logs.append(
                {
                    "id": log_file.stem,
                    "path": str(log_file),
                }
            )
    recent_tasks = list_jobs(user_id, project_id)
    latest_task = recent_tasks[0] if recent_tasks else None
    return {
        **meta,
        "user_id": user_id,
        "workspace": str(workspace_path(user_id, project_id)),
        "has_apk": apk.is_file(),
        "apk_path": str(apk) if apk.is_file() else None,
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
    app = FastAPI(
        title="Android Agent API",
        version="1.0.0-mvp",
        description="本地 Android AI Agent HTTP 服务，按 user_id 隔离项目",
    )
    app.state.settings = settings
    app.state.user_store = user_store or UserStore()
    configure_task_store(task_store, settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def authenticated_user(authorization: str | None) -> str:
        token = _bearer_token(authorization)
        if not token:
            # 单机自用：无 Token 时默认 local，与 CLI 一致，无需注册
            return DEFAULT_USER_ID
        registered_user = app.state.user_store.authenticate(token)
        if registered_user:
            return registered_user
        try:
            return resolve_user_id(app.state.settings, authorization)
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

    def current_user(authorization: Optional[str] = Header(default=None)) -> str:
        return authenticated_user(authorization)

    @app.post("/api/register", status_code=201)
    def register() -> dict[str, str]:
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
            )
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"job": job_to_dict(job)}

    @app.get("/api/projects/{project_id}/conversations")
    def get_conversations(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
        try:
            items = list_conversations(user_id, project_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"user_id": user_id, "project_id": project_id, "conversations": items}

    @app.post("/api/projects/{project_id}/conversations", status_code=201)
    def post_conversation(
        project_id: str,
        body: CreateConversationRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
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
        limit: int = Query(default=200, ge=1, le=500),
        context_only: bool = False,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            events = list_conversation_events(
                conversation_id,
                user_id,
                after_seq=after_seq,
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
        has_more = len(events) > limit
        page = events[:limit]
        next_after_seq = page[-1]["seq"] if page else after_seq
        return {
            "conversation_id": conversation_id,
            "events": [_public_event_value(event) for event in page],
            "next_after_seq": next_after_seq,
            "has_more": has_more,
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

    @app.post("/api/conversations/{conversation_id}/ask")
    def ask_conversation(
        conversation_id: str,
        body: ConversationAskRequest,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
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
        if job["status"] in {"succeeded", "failed", "canceled"}:
            return {"job": job_to_dict(job)}
        request_cancel(job_id, user_id)
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
        return FileResponse(apk_path, media_type="application/vnd.android.package-archive", filename=f"{job['project_id']}-{job_id}.apk")

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

    @app.delete("/api/projects/{project_id}", status_code=204)
    def remove_project(project_id: str, user_id: str = Depends(current_user)) -> None:
        active = [item for item in list_jobs(user_id, project_id) if item["status"] in {"queued", "running"}]
        if active:
            raise HTTPException(status_code=409, detail="项目有正在运行的任务")
        try:
            delete_project(user_id, project_id)
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

        return FileResponse(
            path=apk,
            media_type="application/vnd.android.package-archive",
            filename=f"{project_id}.apk",
        )

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

    @app.websocket("/api/ws/jobs/{job_id}")
    async def ws_job(
        job_id: str,
        websocket: WebSocket,
        token: Optional[str] = Query(default=None),
    ) -> None:
        auth_header = websocket.headers.get("authorization")
        if not auth_header and token:
            auth_header = f"Bearer {token}"
        try:
            user_id = authenticated_user(auth_header)
        except HTTPException:
            await websocket.close(code=4401)
            return

        job = get_job(job_id, user_id=user_id)
        if not job:
            await websocket.close(code=4404)
            return

        await websocket.accept()
        sent = 0
        try:
            while True:
                job = get_job(job_id, user_id=user_id)
                if not job:
                    break

                while sent < len(job.get("events", [])):
                    await websocket.send_json(job["events"][sent])
                    sent += 1

                if job["status"] in {"succeeded", "failed", "canceled"}:
                    await websocket.send_json(
                        {
                            "type": "done",
                            "ts": job["finished_at"],
                            "status": job["status"],
                            "result": job.get("final_message"),
                            "error": job.get("error_message"),
                        }
                    )
                    break

                await asyncio.sleep(0.05)
        except WebSocketDisconnect:
            return

    web_dir = Path(__file__).resolve().parent / "web"
    if web_dir.is_dir():
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
