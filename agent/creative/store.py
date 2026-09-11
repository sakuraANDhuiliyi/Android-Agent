from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Iterator
import uuid

from .models import CreativeContent, SaveCategory, source_hash


class CreativeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


DEFAULT_CATEGORIES = [
    ("component", "组件"), ("motion", "动效"), ("feedback", "反馈"), ("data", "数据"),
    ("layout", "布局"), ("navigation", "导航"), ("input", "输入"), ("commerce", "电商"),
    ("social", "社交"), ("media", "媒体"), ("lifestyle", "生活"),
]


class CreativeStore:
    """One DB transaction for publication, catalog invalidation and audit.

    Content snapshots are immutable after publication. Administrative edits create
    a draft without changing what readers see. No code is executed by this store.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_root = self.db_path.parent / "creative-assets"
        with self.connection(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS creative_meta (id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL);
                INSERT OR IGNORE INTO creative_meta VALUES (1, 1);
                CREATE TABLE IF NOT EXISTS creative_categories (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1, row_version INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS creatives (
                    id TEXT PRIMARY KEY, legacy_id TEXT UNIQUE, owner_user_id TEXT,
                    origin TEXT NOT NULL CHECK(origin IN ('official','community')),
                    author_name TEXT NOT NULL, distribution_state TEXT NOT NULL DEFAULT 'unpublished',
                    published_revision_id TEXT, row_version INTEGER NOT NULL DEFAULT 1,
                    featured INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS creative_revisions (
                    id TEXT PRIMARY KEY, creative_id TEXT NOT NULL REFERENCES creatives(id),
                    version_no INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'draft',
                    content_json TEXT NOT NULL, content_hash TEXT NOT NULL,
                    created_at REAL NOT NULL, published_at REAL, UNIQUE(creative_id, version_no));
                CREATE UNIQUE INDEX IF NOT EXISTS creative_one_draft ON creative_revisions(creative_id) WHERE state='draft';
                CREATE INDEX IF NOT EXISTS creative_published ON creatives(distribution_state,sort_order,id);
                CREATE TABLE IF NOT EXISTS creative_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, creative_id TEXT, actor TEXT NOT NULL,
                    action TEXT NOT NULL, revision_id TEXT, reason TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS creative_assets (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS creative_submission_keys (
                    owner_user_id TEXT NOT NULL, client_id TEXT NOT NULL, creative_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL, PRIMARY KEY(owner_user_id,client_id));
                CREATE INDEX IF NOT EXISTS creative_owner ON creatives(owner_user_id,updated_at);
                CREATE TABLE IF NOT EXISTS creative_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, creative_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    decision TEXT NOT NULL, feedback TEXT NOT NULL, private_note TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS creative_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id TEXT NOT NULL, creative_id TEXT NOT NULL,
                    revision_id TEXT, kind TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL,
                    read_at REAL);
                CREATE INDEX IF NOT EXISTS creative_notice_owner ON creative_notifications(owner_user_id,id);
                CREATE TABLE IF NOT EXISTS creative_uploads (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, client_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL, size INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    asset_id TEXT, created_at REAL NOT NULL, UNIQUE(owner_user_id,client_id));
                CREATE TABLE IF NOT EXISTS creative_favorites (
                    owner_user_id TEXT NOT NULL, creative_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    created_at REAL NOT NULL, PRIMARY KEY(owner_user_id,creative_id));
                CREATE INDEX IF NOT EXISTS creative_favorite_item ON creative_favorites(creative_id);
                CREATE TABLE IF NOT EXISTS creative_reports (
                    id TEXT PRIMARY KEY, creative_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    reporter_user_id TEXT NOT NULL, reason TEXT NOT NULL, details TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'open', row_version INTEGER NOT NULL DEFAULT 1,
                    resolution TEXT NOT NULL DEFAULT '', private_note TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS creative_report_queue ON creative_reports(state,created_at,id);
                CREATE INDEX IF NOT EXISTS creative_report_owner ON creative_reports(reporter_user_id,created_at,id);
                CREATE TABLE IF NOT EXISTS creative_report_keys (
                    reporter_user_id TEXT NOT NULL, client_id TEXT NOT NULL, report_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL, PRIMARY KEY(reporter_user_id,client_id));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(creative_assets)")}
            if "owner_user_id" not in columns:
                db.execute("ALTER TABLE creative_assets ADD COLUMN owner_user_id TEXT")
            db.executemany("INSERT OR IGNORE INTO creative_categories(id,label,sort_order) VALUES (?,?,?)",
                           [(key, label, i) for i, (key, label) in enumerate(DEFAULT_CATEGORIES)])

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def has_items(self) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM creatives LIMIT 1").fetchone() is not None

    @staticmethod
    def _item(db, item_id, expected=None):
        item = db.execute("SELECT * FROM creatives WHERE id=?", (item_id,)).fetchone()
        if not item:
            raise CreativeError(404, "creative_unavailable", "创意不存在")
        if expected is not None and item["row_version"] != expected:
            raise CreativeError(409, "revision_conflict", "创意已被修改，请刷新后重试")
        return item

    @staticmethod
    def _revision(db, item_id, revision_id):
        row = db.execute("SELECT * FROM creative_revisions WHERE creative_id=? AND id=?",
                         (item_id, revision_id)).fetchone()
        if not row:
            raise CreativeError(404, "revision_unavailable", "版本不存在")
        return row

    @staticmethod
    def _audit(db, item_id, actor, action, revision=None, reason=""):
        db.execute("INSERT INTO creative_audit(creative_id,actor,action,revision_id,reason,created_at) VALUES (?,?,?,?,?,?)",
                   (item_id, actor, action, revision, reason[:1000], time.time()))

    @staticmethod
    def _bump(db):
        db.execute("UPDATE creative_meta SET generation=generation+1 WHERE id=1")

    @staticmethod
    def _validate_content(db, content, *, publishing=False):
        category = db.execute("SELECT enabled FROM creative_categories WHERE id=?", (content.category_id,)).fetchone()
        if not category or (publishing and not category["enabled"]):
            raise CreativeError(422, "category_unavailable", "请选择有效分类")
        if content.cover_asset_id and not db.execute("SELECT 1 FROM creative_assets WHERE id=?", (content.cover_asset_id,)).fetchone():
            raise CreativeError(422, "asset_not_ready", "封面不存在或尚未处理完成")
        if publishing and (not content.files or not any(file.content.strip() for file in content.files)):
            raise CreativeError(422, "source_required", "发布前请添加实际源码")

    def create(self, content: CreativeContent, actor: str, *, legacy_id=None) -> dict:
        with self.connection(write=True) as db:
            if legacy_id:
                existing = db.execute("SELECT id FROM creatives WHERE legacy_id=?", (legacy_id,)).fetchone()
                if existing:
                    return {"id": existing["id"], "created": False}
            self._validate_content(db, content)
            item_id, revision_id, now = uuid.uuid4().hex, uuid.uuid4().hex, time.time()
            encoded = dump(content.model_dump())
            db.execute("INSERT INTO creatives(id,legacy_id,origin,author_name,created_at,updated_at) VALUES (?,?,'official','Android Agent',?,?)",
                       (item_id, legacy_id, now, now))
            db.execute("INSERT INTO creative_revisions(id,creative_id,version_no,content_json,content_hash,created_at) VALUES (?,?,1,?,?,?)",
                       (revision_id, item_id, encoded, hashlib.sha256(encoded.encode()).hexdigest(), now))
            self._audit(db, item_id, actor, "create", revision_id)
        return {"id": item_id, "created": True}

    def admin_detail(self, item_id: str, revision_id=None) -> dict:
        with self.connection() as db:
            item = self._item(db, item_id)
            revisions = db.execute("SELECT * FROM creative_revisions WHERE creative_id=? ORDER BY version_no DESC", (item_id,)).fetchall()
            selected = self._revision(db, item_id, revision_id) if revision_id else revisions[0]
            return {key: item[key] for key in ("id", "legacy_id", "origin", "distribution_state", "row_version", "featured", "sort_order", "published_revision_id", "author_name", "owner_user_id")} | {
                "revision_id": selected["id"], "content": json.loads(selected["content_json"]),
                "revisions": [{key: row[key] for key in ("id", "version_no", "state", "content_hash", "created_at", "published_at")} for row in revisions],
            }

    def save_draft(self, item_id, expected, content: CreativeContent, actor):
        with self.connection(write=True) as db:
            item = self._item(db, item_id, expected)
            if item["origin"] != "official":
                raise CreativeError(409, "review_required", "社区稿件请通过审核队列退修或发布，不能直接覆盖作者稿件")
            self._validate_content(db, content)
            draft = db.execute("SELECT id FROM creative_revisions WHERE creative_id=? AND state='draft'", (item_id,)).fetchone()
            encoded = dump(content.model_dump())
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            revision_id = draft["id"] if draft else uuid.uuid4().hex
            if draft:
                db.execute("UPDATE creative_revisions SET content_json=?,content_hash=? WHERE id=?", (encoded, digest, revision_id))
            else:
                version = db.execute("SELECT MAX(version_no)+1 FROM creative_revisions WHERE creative_id=?", (item_id,)).fetchone()[0]
                db.execute("INSERT INTO creative_revisions(id,creative_id,version_no,content_json,content_hash,created_at) VALUES (?,?,?,?,?,?)",
                           (revision_id, item_id, version, encoded, digest, time.time()))
            db.execute("UPDATE creatives SET row_version=row_version+1,updated_at=? WHERE id=?", (time.time(), item_id))
            self._audit(db, item_id, actor, "save_draft", revision_id)
        return self.admin_detail(item_id)

    def publish(self, item_id, expected, revision_id, actor, reason=""):
        with self.connection(write=True) as db:
            item = self._item(db, item_id, expected)
            if item["distribution_state"] in {"admin_blocked", "archived"}:
                raise CreativeError(409, "creative_blocked", "请先显式恢复已下架或归档的创意")
            revision = self._revision(db, item_id, revision_id)
            if item["origin"] == "community" and revision["state"] != "approved":
                raise CreativeError(409, "review_required", "社区版本必须通过审核后才能发布")
            if revision["state"] not in {"draft", "approved"}:
                raise CreativeError(409, "revision_unavailable", "此版本不能发布")
            self._validate_content(db, CreativeContent.model_validate_json(revision["content_json"]), publishing=True)
            db.execute("UPDATE creative_revisions SET state='approved',published_at=COALESCE(published_at,?) WHERE id=?", (time.time(), revision_id))
            db.execute("UPDATE creatives SET published_revision_id=?,distribution_state='listed',row_version=row_version+1,updated_at=? WHERE id=?",
                       (revision_id, time.time(), item_id))
            self._bump(db)
            self._audit(db, item_id, actor, "publish", revision_id, reason)
        return self.admin_detail(item_id, revision_id)

    def change_visibility(self, item_id, expected, action, actor, reason):
        states = {"unpublish": "admin_blocked", "archive": "archived", "restore": "unpublished"}
        if action not in states:
            raise CreativeError(422, "invalid_action", "未知状态操作")
        if not reason.strip():
            raise CreativeError(422, "reason_required", "请填写操作原因")
        with self.connection(write=True) as db:
            item = self._item(db, item_id, expected)
            # Restore makes publication possible, but deliberately does not auto-publish.
            db.execute("UPDATE creatives SET distribution_state=?,row_version=row_version+1,updated_at=? WHERE id=?", (states[action], time.time(), item_id))
            self._bump(db)
            self._audit(db, item_id, actor, action, reason=reason)
            if item["owner_user_id"]:
                self._notice(db, item, None, action, {"unpublish": "你的创意已被管理员下架", "restore": "你的创意已恢复为未发布状态", "archive": "你的创意已被管理员归档"}[action])
        return self.admin_detail(item_id)

    @staticmethod
    def _notice(db, item, revision_id, kind, message):
        db.execute("INSERT INTO creative_notifications(owner_user_id,creative_id,revision_id,kind,message,created_at) VALUES (?,?,?,?,?,?)",
                   (item["owner_user_id"], item["id"], revision_id, kind, message, time.time()))

    def placement(self, item_id, expected, featured, sort_order, actor):
        with self.connection(write=True) as db:
            self._item(db, item_id, expected)
            db.execute("UPDATE creatives SET featured=?,sort_order=?,row_version=row_version+1,updated_at=? WHERE id=?",
                       (int(featured), sort_order, time.time(), item_id))
            self._bump(db)
            self._audit(db, item_id, actor, "placement", reason=dump({"featured": featured, "sort_order": sort_order}))
        return self.admin_detail(item_id)

    def categories(self, *, all_categories=False):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM creative_categories " + ("" if all_categories else "WHERE enabled=1 ") + "ORDER BY sort_order,id")]

    def save_category(self, category: SaveCategory, actor):
        with self.connection(write=True) as db:
            existing = db.execute("SELECT * FROM creative_categories WHERE id=?", (category.id,)).fetchone()
            if existing and existing["row_version"] != category.expected_version:
                raise CreativeError(409, "revision_conflict", "分类已修改，请刷新")
            if not existing and category.expected_version is not None:
                raise CreativeError(409, "revision_conflict", "分类不存在")
            db.execute("INSERT INTO creative_categories(id,label,sort_order,enabled) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,sort_order=excluded.sort_order,enabled=excluded.enabled,row_version=creative_categories.row_version+1",
                       (category.id, category.label.strip(), category.sort_order, int(category.enabled)))
            self._bump(db)
            self._audit(db, None, actor, "category", reason=dump(category.model_dump()))
        return self.categories(all_categories=True)

    @staticmethod
    def _card(row, content, category_label):
        return {"id": row["id"], "legacy_id": row["legacy_id"], "revision_id": row["revision_id"],
                "version_no": row["version_no"], "title": content.title, "summary": content.summary,
                "category_id": content.category_id, "category_label": category_label,
                "tags": content.tags, "origin": row["origin"], "author_name": row["author_name"],
                "ui_stack": content.ui_stack, "min_sdk": content.min_sdk,
                "cover_asset_id": content.cover_asset_id, "native_preview_id": content.native_preview_id,
                "source_hash": source_hash(content), "content_hash": row["content_hash"],
                "featured": bool(row["featured"]), "sort_order": row["sort_order"],
                "published_at": row["published_at"], "verification": "not_verified"}

    PUBLIC_SELECT = """SELECT c.*,r.id AS revision_id,r.version_no,r.content_json,r.content_hash,r.published_at,
        COALESCE(cat.label,json_extract(r.content_json,'$.category_id')) AS category_label
        FROM creatives c JOIN creative_revisions r ON r.id=c.published_revision_id
        LEFT JOIN creative_categories cat ON cat.id=json_extract(r.content_json,'$.category_id')
        WHERE c.distribution_state='listed' AND r.state='approved'"""

    def catalog(self, *, query="", category="", origin="", cursor=None, limit=36):
        fingerprint = hashlib.sha256(dump([query, category, origin, limit]).encode()).hexdigest()[:16]
        with self.connection() as db:
            generation = db.execute("SELECT generation FROM creative_meta WHERE id=1").fetchone()[0]
            offset = 0
            if cursor:
                try:
                    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                    offset = decoded["offset"]
                    if (type(offset) is not int or not 0 <= offset <= 1000000 or decoded["filter"] != fingerprint):
                        raise ValueError()
                    if decoded["generation"] != generation:
                        raise CreativeError(409, "catalog_changed", "目录已更新，请重新载入")
                except (ValueError, KeyError, TypeError) as exc:
                    raise CreativeError(422, "invalid_cursor", "无效的分页游标") from exc
            sql, args = self.PUBLIC_SELECT, []
            if query:
                sql += " AND instr(lower(json_extract(r.content_json,'$.title')||' '||json_extract(r.content_json,'$.summary')||' '||json_extract(r.content_json,'$.tags')),lower(?))>0"
                args.append(query)
            if category:
                sql += " AND json_extract(r.content_json,'$.category_id')=?"
                args.append(category)
            if origin:
                sql += " AND c.origin=?"
                args.append(origin)
            sql += " ORDER BY c.featured DESC,c.sort_order DESC,r.published_at DESC,c.id LIMIT ? OFFSET ?"
            rows = db.execute(sql, (*args, limit + 1, offset)).fetchall()
            cards = [self._card(row, CreativeContent.model_validate_json(row["content_json"]), row["category_label"]) for row in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                next_cursor = base64.urlsafe_b64encode(dump({"generation": generation, "filter": fingerprint, "offset": offset + limit}).encode()).decode().rstrip("=")
            categories = [dict(row) for row in db.execute("SELECT * FROM creative_categories WHERE enabled=1 ORDER BY sort_order,id")]
        return {"items": cards, "categories": categories, "next_cursor": next_cursor, "generation": generation}

    def public_detail(self, item_id):
        with self.connection() as db:
            row = db.execute(self.PUBLIC_SELECT + " AND c.id=?", (item_id,)).fetchone()
            if not row:
                raise CreativeError(404, "creative_unavailable", "创意未发布或已下架")
            content = CreativeContent.model_validate_json(row["content_json"])
            return self._card(row, content, row["category_label"]) | {"content": content.model_dump()}

    def admin_list(self, *, query="", state="", offset=0, limit=40):
        with self.connection() as db:
            sql = """SELECT c.*,r.id AS revision_id,r.content_json,r.state AS review_state,r.version_no FROM creatives c
                JOIN creative_revisions r ON r.creative_id=c.id AND r.version_no=(SELECT MAX(version_no) FROM creative_revisions WHERE creative_id=c.id) WHERE 1=1"""
            args = []
            if state:
                sql += " AND c.distribution_state=?"
                args.append(state)
            if query:
                sql += " AND instr(lower(json_extract(r.content_json,'$.title')||' '||COALESCE(c.legacy_id,'')||' '||c.id),lower(?))>0"
                args.append(query)
            rows = db.execute(sql + " ORDER BY c.updated_at DESC,c.id LIMIT ? OFFSET ?", (*args, limit + 1, offset)).fetchall()
            items = [{key: row[key] for key in ("id", "legacy_id", "origin", "distribution_state", "row_version", "revision_id", "version_no", "review_state", "featured", "sort_order")} |
                     {"title": json.loads(row["content_json"])["title"]} for row in rows[:limit]]
            return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}

    def audit(self, item_id=None, *, limit=100):
        with self.connection() as db:
            sql = "SELECT * FROM creative_audit"
            args = []
            if item_id:
                sql += " WHERE creative_id=?"
                args.append(item_id)
            return [dict(row) for row in db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]
