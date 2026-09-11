from __future__ import annotations

import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from agent.api_errors import build_error_body
from agent.governance import QuotaExceededError, ensure_disk_capacity

from .assets import MAX_COVER_BYTES, asset_path, save_cover
from .models import (AdminItem, CatalogPage, Category, CreativeContent, CreativeDetail,
                     Model, Placement, SaveCategory, SaveDraft, VersionAction)
from .store import CreativeError, CreativeStore, dump
from .community import CommunityStore
from .models import CreateSubmission, SubmitRevision, ReviewDecision, UploadRequest, ReadNotifications
from .models import AuthorItem, CreatedAuthorItem, AuthorList, CreatorIdentity, UploadSession, NotificationPage
from .models import ReportRequest, ReportReceipt, ReportDecision, FavoriteList, CreativeMetrics
from .assets import begin_upload, get_upload, complete_upload, cancel_upload


class ImportRequest(Model):
    publish: bool = False


class CatalogCapabilities(Model):
    schema_version: int = 1
    catalog_enabled: bool = True
    submissions_enabled: bool = False
    applications_enabled: bool = False
    favorites_enabled: bool = True
    reports_enabled: bool = True
    application_unavailable_reason: str = "暂不支持直接应用，可将源码复制到项目"
    max_cover_bytes: int = MAX_COVER_BYTES
    max_source_bytes: int = 1024 * 1024
    max_source_files: int = 30
    max_file_bytes: int = 256 * 1024
    max_cover_pixels: int = 16000000
    max_pending_uploads: int = 2
    max_pending_submissions: int = 3
    max_daily_submissions: int = 5
    max_favorites: int = 500
    max_daily_reports: int = 10
    preview_kinds: list[str] = Field(default_factory=lambda: ["image", "native_preset"])


def install_creative_routes(app: FastAPI, settings, current_admin, current_identity) -> None:
    store = CommunityStore(app.state.task_store.db_path)
    app.state.creative_store = store

    async def creative_error(_request: Request, exc: CreativeError):
        return JSONResponse(status_code=exc.status, content=build_error_body(exc.status, exc.message, code=exc.code))

    app.add_exception_handler(CreativeError, creative_error)

    def enabled():
        if not settings.creative_catalog_enabled:
            raise HTTPException(404, "服务端创意目录未启用")

    def writable():
        enabled()
        try:
            ensure_disk_capacity(store.db_path.parent, minimum_free_bytes=settings.minimum_free_disk_bytes)
        except QuotaExceededError as exc:
            raise HTTPException(507, str(exc)) from exc

    # This identifies a credential, not a human operator. No raw token enters audit.
    actor = "admin:" + hashlib.sha256(settings.admin_token.encode()).hexdigest()[:12]
    public = APIRouter(prefix="/api/creative", tags=["creative"], dependencies=[Depends(enabled)])
    admin = APIRouter(prefix="/api/admin/creative", tags=["creative-admin"], dependencies=[Depends(current_admin), Depends(enabled)])

    submissions_enabled = settings.creative_submissions_enabled and settings.admin_ui_enabled and bool(settings.admin_token)

    def creator(identity=Depends(current_identity)):
        enabled()
        if not identity.session_id:
            raise HTTPException(403, "请使用正式注册账号投稿")
        try:
            account = app.state.user_store.get_account(identity.user_id)
        except ValueError as exc:
            raise HTTPException(403, "账号不可用") from exc
        if account["is_guest"] or account["disabled"] or not account["email"]:
            raise HTTPException(403, "请使用有效的正式账号投稿")
        return account

    def accepting_submissions():
        if not submissions_enabled:
            raise CreativeError(409, "submissions_paused", "新投稿暂未开放；草稿、撤回和已有审核仍可处理")

    author = APIRouter(prefix="/api/me/creative", tags=["creative-author"], dependencies=[Depends(enabled)])

    @public.get("/capabilities", response_model=CatalogCapabilities)
    def capabilities():
        return CatalogCapabilities(submissions_enabled=submissions_enabled)

    @public.get("/categories", response_model=list[Category])
    def categories():
        return store.categories()

    @public.get("/items", response_model=CatalogPage)
    def items(request: Request, response: Response, q: str = Query("", max_length=100),
              category: str = Query("", max_length=48), origin: Literal["", "official", "community"] = "",
              cursor: str | None = Query(None, max_length=512), limit: int = Query(36, ge=1, le=100)):
        result = store.catalog(query=q.strip(), category=category, origin=origin, cursor=cursor, limit=limit)
        etag = '"' + hashlib.sha256(dump(result).encode()).hexdigest() + '"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        response.headers.update(headers)
        return result

    @public.get("/items/{item_id}", response_model=CreativeDetail)
    def detail(item_id: str, response: Response):
        result = store.public_detail(item_id)
        response.headers["ETag"] = '"' + result["content_hash"] + '"'
        response.headers["Cache-Control"] = "no-cache"
        return result

    @public.post("/items/{item_id}/reports", response_model=ReportReceipt, status_code=201, dependencies=[Depends(writable)])
    def report(item_id: str, body: ReportRequest, account=Depends(creator)):
        return store.create_report(account["user_id"], item_id, body)

    @public.get("/assets/{asset_id}", response_class=FileResponse)
    def cover(asset_id: str):
        return FileResponse(asset_path(store, asset_id), media_type="image/jpeg", headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    @admin.get("/items")
    def admin_items(q: str = Query("", max_length=100), state: str = Query("", max_length=32),
                    offset: int = Query(0, ge=0, le=1000000), limit: int = Query(40, ge=1, le=100)):
        return store.admin_list(query=q.strip(), state=state, offset=offset, limit=limit)

    @admin.post("/items", response_model=AdminItem, status_code=201, dependencies=[Depends(writable)])
    def create(content: CreativeContent):
        return store.admin_detail(store.create(content, actor)["id"])

    @admin.get("/items/{item_id}", response_model=AdminItem)
    def admin_detail(item_id: str, revision_id: str | None = None):
        return store.admin_detail(item_id, revision_id)

    @admin.put("/items/{item_id}/draft", response_model=AdminItem, dependencies=[Depends(writable)])
    def save_draft(item_id: str, body: SaveDraft):
        return store.save_draft(item_id, body.expected_version, body.content, actor)

    @admin.post("/items/{item_id}/publish", response_model=AdminItem, dependencies=[Depends(writable)])
    def publish(item_id: str, body: VersionAction):
        if not body.revision_id:
            raise CreativeError(422, "revision_required", "请选择要发布的版本")
        return store.publish(item_id, body.expected_version, body.revision_id, actor, body.reason)

    @admin.post("/items/{item_id}/visibility/{action}", response_model=AdminItem, dependencies=[Depends(writable)])
    def visibility(item_id: str, action: Literal["unpublish", "restore", "archive"], body: VersionAction):
        return store.change_visibility(item_id, body.expected_version, action, actor, body.reason)

    @admin.patch("/items/{item_id}/placement", response_model=AdminItem, dependencies=[Depends(writable)])
    def placement(item_id: str, body: Placement):
        return store.placement(item_id, body.expected_version, body.featured, body.sort_order, actor)

    @admin.get("/categories", response_model=list[Category])
    def admin_categories():
        return store.categories(all_categories=True)

    @admin.put("/categories", response_model=list[Category], dependencies=[Depends(writable)])
    def category(body: SaveCategory):
        return store.save_category(body, actor)

    @admin.get("/audit")
    def audit(item_id: str | None = None, limit: int = Query(100, ge=1, le=500)):
        return {"items": store.audit(item_id, limit=limit)}

    @admin.post("/covers", status_code=201, dependencies=[Depends(writable)])
    async def upload_cover(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_COVER_BYTES:
                raise CreativeError(413, "asset_too_large", "封面不能超过 1.5 MiB")
            raw.extend(chunk)
        # Image decoding is bounded but CPU-heavy; keep it off the ASGI event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(save_cover, store, bytes(raw))

    @admin.get("/assets/{asset_id}", response_class=FileResponse)
    def admin_cover(asset_id: str):
        return FileResponse(asset_path(store, asset_id, admin=True), media_type="image/jpeg",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @admin.post("/import-builtins", dependencies=[Depends(writable)])
    def import_builtins(body: ImportRequest):
        from .seed import import_builtins
        return import_builtins(store, actor, publish=body.publish)

    @admin.get("/reviews")
    def reviews(state: Literal["", "submitted", "in_review"] = "", offset: int = Query(0, ge=0, le=100000)):
        return store.reviews(state, offset)

    @admin.get("/reviews/{item_id}")
    def review_detail(item_id: str, revision_id: str):
        return store.review_detail(item_id, revision_id)

    @admin.post("/reviews/{item_id}", dependencies=[Depends(writable)])
    def decide(item_id: str, body: ReviewDecision):
        if body.decision == "approve":
            owner = store.admin_detail(item_id)["owner_user_id"]
            if not owner:
                raise CreativeError(409, "review_stale", "此条目不是用户投稿")
            try:
                account = app.state.user_store.get_account(owner)
            except ValueError as exc:
                raise CreativeError(409, "author_unavailable", "作者账号已不可用") from exc
            if account["disabled"] or account["is_guest"]:
                raise CreativeError(409, "author_unavailable", "作者账号已不可用，请先处理账号状态")
        return store.decide(item_id, body, actor)

    @admin.get("/reports")
    def reports(state: Literal["", "open", "reviewing", "resolved", "dismissed"] = "",
                offset: int = Query(0, ge=0, le=100000)):
        return store.reports(state, offset)

    @admin.get("/reports/{report_id}")
    def report_detail(report_id: str):
        return store.report_detail(report_id)

    @admin.post("/reports/{report_id}", dependencies=[Depends(writable)])
    def decide_report(report_id: str, body: ReportDecision):
        return store.decide_report(report_id, body, actor)

    @admin.get("/metrics", response_model=CreativeMetrics)
    def metrics():
        return store.metrics()

    @author.get("/identity", response_model=CreatorIdentity)
    def creator_identity(account=Depends(creator)):
        return {"user_id": account["user_id"], "display_name": account["display_name"], "schema_version": 1, "categories": store.categories(), "submissions_enabled": submissions_enabled}

    @author.get("/items", response_model=AuthorList)
    def mine(account=Depends(creator)):
        return store.owner_list(account["user_id"])

    @author.post("/items", response_model=CreatedAuthorItem, status_code=201, dependencies=[Depends(writable)])
    def create_owned(body: CreateSubmission, account=Depends(creator)):
        return store.create_owned(account["user_id"], account["display_name"], body.client_id, body.content)

    @author.get("/items/{item_id}", response_model=AuthorItem)
    def owned_detail(item_id: str, account=Depends(creator)):
        return store.owner_detail(item_id, account["user_id"])

    @author.put("/items/{item_id}/draft", response_model=AuthorItem, dependencies=[Depends(writable)])
    def save_owned(item_id: str, body: SaveDraft, account=Depends(creator)):
        return store.save_owned(item_id, account["user_id"], body.expected_version, body.content)

    @author.post("/items/{item_id}/submit", response_model=AuthorItem, dependencies=[Depends(writable)])
    def submit(item_id: str, body: SubmitRevision, account=Depends(creator)):
        accepting_submissions()
        return store.submit(item_id, account["user_id"], body)

    @author.post("/items/{item_id}/withdraw", response_model=AuthorItem, dependencies=[Depends(writable)])
    def withdraw(item_id: str, body: VersionAction, account=Depends(creator)):
        return store.withdraw(item_id, account["user_id"], body.expected_version, body.revision_id)

    @author.post("/items/{item_id}/unpublish", response_model=AuthorItem, dependencies=[Depends(writable)])
    def hide(item_id: str, body: VersionAction, account=Depends(creator)):
        return store.hide(item_id, account["user_id"], body.expected_version)

    @author.delete("/items/{item_id}", dependencies=[Depends(writable)])
    def delete_unpublished(item_id: str, expected_version: int = Query(ge=1), account=Depends(creator)):
        return store.delete_unpublished(item_id, account["user_id"], expected_version)

    @author.post("/uploads", response_model=UploadSession, status_code=201, dependencies=[Depends(writable)])
    def upload_session(body: UploadRequest, account=Depends(creator)):
        return begin_upload(store, account["user_id"], body)

    @author.get("/uploads/{upload_id}", response_model=UploadSession)
    def upload_status(upload_id: str, account=Depends(creator)):
        return get_upload(store, account["user_id"], upload_id)

    @author.put("/uploads/{upload_id}/content", response_model=UploadSession, dependencies=[Depends(writable)])
    async def upload_content(upload_id: str, request: Request, account=Depends(creator)):
        info = get_upload(store, account["user_id"], upload_id)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > info["size"]:
                raise CreativeError(413, "asset_too_large", "上传超过声明的文件大小")
            raw.extend(chunk)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(complete_upload, store, account["user_id"], upload_id, bytes(raw))

    @author.delete("/uploads/{upload_id}", dependencies=[Depends(writable)])
    def remove_upload(upload_id: str, account=Depends(creator)):
        return cancel_upload(store, account["user_id"], upload_id)

    @author.get("/assets/{asset_id}", response_class=FileResponse)
    def owned_cover(asset_id: str, account=Depends(creator)):
        return FileResponse(asset_path(store, asset_id, owner=account["user_id"]), media_type="image/jpeg",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @author.get("/notifications", response_model=NotificationPage)
    def notifications(after: int = Query(0, ge=0), account=Depends(creator)):
        return store.notifications(account["user_id"], after)

    @author.post("/notifications/read", dependencies=[Depends(writable)])
    def read_notifications(body: ReadNotifications, account=Depends(creator)):
        return store.read_notifications(account["user_id"], body.through_id)

    @author.get("/favorites", response_model=FavoriteList)
    def favorites(account=Depends(creator)):
        return store.favorites(account["user_id"])

    @author.put("/favorites/{item_id}", dependencies=[Depends(writable)])
    def favorite(item_id: str, account=Depends(creator)):
        return store.favorite(account["user_id"], item_id, True)

    @author.delete("/favorites/{item_id}", dependencies=[Depends(writable)])
    def unfavorite(item_id: str, account=Depends(creator)):
        return store.favorite(account["user_id"], item_id, False)

    @author.get("/reports")
    def own_reports(account=Depends(creator)):
        return store.own_reports(account["user_id"])

    app.include_router(public)
    app.include_router(admin)
    app.include_router(author)
