"""Bounded, decoded and re-encoded cover images for the admin catalog."""
from __future__ import annotations

import hashlib
from io import BytesIO
import os
import time
import threading
import uuid
import warnings

from .store import CreativeError, CreativeStore

MAX_COVER_BYTES = 1536 * 1024
MAX_PIXELS = 16_000_000
_decoders = threading.BoundedSemaphore(2)


def encode_cover(raw: bytes):
    if not _decoders.acquire(blocking=False):
        raise CreativeError(429, "image_decoder_busy", "图片处理繁忙，请稍后重试原上传")
    try:
        return _encode_cover(raw)
    finally:
        _decoders.release()


def _encode_cover(raw: bytes):
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not raw or len(raw) > MAX_COVER_BYTES:
        raise CreativeError(413, "asset_too_large", "封面大小不能超过 1.5 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as candidate:
                if candidate.format not in {"JPEG", "PNG", "WEBP"} or candidate.width * candidate.height > MAX_PIXELS:
                    raise CreativeError(422, "invalid_image", "请上传不超过 1600 万像素的 JPEG、PNG 或 WebP 图片")
                candidate.load()
                frame = ImageOps.exif_transpose(candidate).convert("RGB")
                frame.thumbnail((1200, 1200))
                output = BytesIO()
                frame.save(output, format="JPEG", quality=85, optimize=True)
                encoded = output.getvalue()
                width, height = frame.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise CreativeError(422, "invalid_image", "图片无法安全解码，请更换文件") from exc
    return encoded, width, height


def save_cover(store: CreativeStore, raw: bytes) -> dict:
    encoded, width, height = encode_cover(raw)
    asset_id = uuid.uuid4().hex
    store.asset_root.mkdir(parents=True, exist_ok=True)
    destination = store.asset_root / f"{asset_id}.jpg"
    # Unique files are never overwritten; readers only see assets after DB commit.
    try:
        with destination.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        with store.connection(write=True) as db:
            db.execute("INSERT INTO creative_assets(id,digest,size,mime,width,height,created_at) VALUES (?,?,?,?,?,?,?)",
                       (asset_id, hashlib.sha256(encoded).hexdigest(), len(encoded), "image/jpeg", width, height, time.time()))
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"id": asset_id, "mime": "image/jpeg", "width": width, "height": height, "size": len(encoded)}


def asset_path(store: CreativeStore, asset_id: str, *, admin=False, owner=None):
    with store.connection() as db:
        asset = db.execute("SELECT * FROM creative_assets WHERE id=?", (asset_id,)).fetchone()
        if not asset:
            raise CreativeError(404, "asset_unavailable", "素材不存在")
        if owner is not None and asset["owner_user_id"] != owner:
            raise CreativeError(404, "asset_unavailable", "素材不存在")
        if not admin and owner is None:
            visible = db.execute("""SELECT 1 FROM creatives c JOIN creative_revisions r ON r.id=c.published_revision_id
                WHERE c.distribution_state='listed' AND r.state='approved'
                AND json_extract(r.content_json,'$.cover_asset_id')=? LIMIT 1""", (asset_id,)).fetchone()
            if not visible:
                raise CreativeError(404, "asset_unavailable", "素材不可访问")
        path = store.asset_root / f"{asset['id']}.jpg"
        if not path.is_file():
            raise CreativeError(404, "asset_unavailable", "素材文件缺失")
        return path


def begin_upload(store, owner, body):
    with store.connection(write=True) as db:
        db.execute("DELETE FROM creative_uploads WHERE owner_user_id=? AND state='pending' AND created_at<?", (owner, time.time()-86400))
        existing = db.execute("SELECT * FROM creative_uploads WHERE owner_user_id=? AND client_id=?", (owner, body.client_id)).fetchone()
        if existing:
            if existing["sha256"] != body.sha256 or existing["size"] != body.size:
                raise CreativeError(409, "idempotency_conflict", "此上传编号已用于其他文件")
            return dict(existing)
        count, used = db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM creative_uploads WHERE owner_user_id=?", (owner,)).fetchone()
        if count >= 100 or used + body.size > 50 * 1024 * 1024:
            raise CreativeError(429, "upload_quota", "创意封面已达到账号额度（100 张或 50 MiB）")
        pending = db.execute("SELECT COUNT(*) FROM creative_uploads WHERE owner_user_id=? AND state='pending'", (owner,)).fetchone()[0]
        if pending >= 2:
            raise CreativeError(429, "pending_upload_quota", "最多同时保留 2 个未完成上传，请完成或取消后再试")
        upload_id = uuid.uuid4().hex
        db.execute("INSERT INTO creative_uploads(id,owner_user_id,client_id,sha256,size,created_at) VALUES (?,?,?,?,?,?)",
                   (upload_id, owner, body.client_id, body.sha256, body.size, time.time()))
        return dict(db.execute("SELECT * FROM creative_uploads WHERE id=?", (upload_id,)).fetchone())


def get_upload(store, owner, upload_id):
    with store.connection() as db:
        row = db.execute("SELECT * FROM creative_uploads WHERE id=? AND owner_user_id=?", (upload_id, owner)).fetchone()
        if not row:
            raise CreativeError(404, "upload_unavailable", "上传会话不存在或已过期")
        return dict(row)


def complete_upload(store, owner, upload_id, raw):
    info = get_upload(store, owner, upload_id)
    if len(raw) != info["size"] or hashlib.sha256(raw).hexdigest() != info["sha256"]:
        raise CreativeError(422, "upload_digest_mismatch", "上传大小或摘要不一致，请重试原文件")
    if info["state"] == "complete":
        return info
    encoded, width, height = encode_cover(raw)
    asset_id = uuid.uuid4().hex
    destination = store.asset_root / f"{asset_id}.jpg"
    store.asset_root.mkdir(parents=True, exist_ok=True)
    try:
        with store.connection(write=True) as db:
            current = db.execute("SELECT * FROM creative_uploads WHERE id=? AND owner_user_id=?", (upload_id, owner)).fetchone()
            if not current or current["created_at"] < time.time()-86400:
                raise CreativeError(404, "upload_unavailable", "上传会话已过期，请重新发起上传")
            if current["state"] == "complete":
                return dict(current)
            with destination.open("xb") as handle:
                handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
            db.execute("INSERT INTO creative_assets(id,digest,size,mime,width,height,created_at,owner_user_id) VALUES (?,?,?,?,?,?,?,?)",
                       (asset_id, hashlib.sha256(encoded).hexdigest(), len(encoded), "image/jpeg", width, height, time.time(), owner))
            db.execute("UPDATE creative_uploads SET state='complete',asset_id=? WHERE id=?", (asset_id, upload_id))
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return get_upload(store, owner, upload_id)


def cancel_upload(store, owner, upload_id):
    with store.connection(write=True) as db:
        row = db.execute("SELECT * FROM creative_uploads WHERE id=? AND owner_user_id=?", (upload_id, owner)).fetchone()
        if not row:
            raise CreativeError(404, "upload_unavailable", "上传会话不存在")
        asset_id = row["asset_id"]
        if asset_id and db.execute("SELECT 1 FROM creative_revisions WHERE json_extract(content_json,'$.cover_asset_id')=?", (asset_id,)).fetchone():
            raise CreativeError(409, "asset_referenced", "已有创意版本引用此封面，不能删除")
        db.execute("DELETE FROM creative_uploads WHERE id=?", (upload_id,))
        if asset_id:
            db.execute("DELETE FROM creative_assets WHERE id=? AND owner_user_id=?", (asset_id, owner))
    if asset_id:
        (store.asset_root / f"{asset_id}.jpg").unlink(missing_ok=True)
    return {"ok": True}
