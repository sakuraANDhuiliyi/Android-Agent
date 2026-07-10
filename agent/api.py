from __future__ import annotations

import asyncio
import socket
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.config import Settings, load_settings, models_catalog, resolve_job_settings, resolve_user_id
from agent.jobs import get_job, job_to_dict, list_jobs, start_ask_job
from agent.paths import (
    build_log_path,
    latest_apk_path,
    user_builds_dir,
    user_workspaces_dir,
    workspace_path,
)
from agent.project import init_project, list_projects, load_project_meta
from agent.tools import is_writable_path, list_dir_entries, read_file_meta, write_file
from agent.users import UserStore


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1)
    package: Optional[str] = None


class AskRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    provider: Optional[str] = None
    auto_fallback: bool = False


class WriteFileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = Field(default="")


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
    return {
        **meta,
        "user_id": user_id,
        "workspace": str(workspace_path(user_id, project_id)),
        "has_apk": apk.is_file(),
        "apk_path": str(apk) if apk.is_file() else None,
        "build_logs": build_logs[:20],
    }


def create_app(
    settings: Settings | None = None,
    user_store: UserStore | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="Android Agent API",
        version="0.3.0",
        description="本地 Android AI Agent HTTP 服务，按 user_id 隔离项目",
    )
    app.state.settings = settings
    app.state.user_store = user_store or UserStore()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def authenticated_user(authorization: str | None) -> str:
        token = _bearer_token(authorization)
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

        job = start_ask_job(
            user_id,
            project_id,
            body.prompt,
            job_settings,
        )
        return {"job": job_to_dict(job)}

    @app.get("/api/jobs")
    def get_jobs(
        project_id: Optional[str] = None,
        user_id: str = Depends(current_user),
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "jobs": [job_to_dict(job) for job in list_jobs(user_id, project_id)],
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
    async def ws_job(job_id: str, websocket: WebSocket) -> None:
        try:
            user_id = authenticated_user(websocket.headers.get("authorization"))
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

                while sent < len(job.events):
                    await websocket.send_json(job.events[sent])
                    sent += 1

                if job.status in {"completed", "failed"}:
                    await websocket.send_json(
                        {
                            "type": "done",
                            "ts": job.finished_at,
                            "status": job.status,
                            "result": job.result,
                            "error": job.error,
                        }
                    )
                    break

                await asyncio.sleep(0.3)
        except WebSocketDisconnect:
            return

    return app


def _guess_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None
