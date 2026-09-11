"""Author-owned drafts and immutable moderation snapshots. No submitted code runs here."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from .models import CreativeContent, ReportDecision, ReviewDecision, SubmitRevision
from .store import CreativeError, CreativeStore, dump


def scan_submission(content: CreativeContent) -> None:
    if not content.license.strip():
        raise CreativeError(422, "license_required", "请声明源码许可证或分享授权范围")
    patterns = [r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}",
                r'''(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*["'][^"'\n]{12,}["']''']
    for source in content.files:
        if any(re.search(pattern, source.content) for pattern in patterns):
            raise CreativeError(422, "sensitive_content", f"{source.path} 含疑似凭据，请移除后重新提交")
    if any(re.search(pattern, dump(content.model_dump(exclude={"files"}))) for pattern in patterns):
        raise CreativeError(422, "sensitive_content", "说明中含疑似凭据，请移除后重新提交")


class CommunityStore(CreativeStore):
    REPORT_REASONS = {
        "copyright": "版权或授权问题", "privacy": "隐私或个人信息", "malware": "恶意或危险内容",
        "misleading": "误导或描述不实", "other": "其他问题",
    }

    @staticmethod
    def _report_receipt(row):
        return {key: row[key] for key in ("id", "creative_id", "revision_id", "reason", "state", "row_version", "resolution", "created_at", "updated_at")}

    @staticmethod
    def _owned(db, item_id, owner, expected=None):
        item = CreativeStore._item(db, item_id)
        if item["owner_user_id"] != owner or item["origin"] != "community":
            raise CreativeError(404, "creative_unavailable", "创意不存在")
        if expected is not None and item["row_version"] != expected:
            raise CreativeError(409, "revision_conflict", "云端已有更新，请重新读取；本机草稿已保留")
        return item

    def _author_content(self, db, owner, content, *, submitting=False):
        self._validate_content(db, content, publishing=submitting)
        if content.native_preview_id:
            raise CreativeError(422, "native_preview_forbidden", "用户投稿请上传封面，不能指定内置预览")
        if content.cover_asset_id:
            asset = db.execute("SELECT owner_user_id FROM creative_assets WHERE id=?", (content.cover_asset_id,)).fetchone()
            if not asset or asset[0] != owner:
                raise CreativeError(422, "asset_not_ready", "封面不属于当前账号")
            if not (self.asset_root / f"{content.cover_asset_id}.jpg").is_file():
                raise CreativeError(422, "asset_not_ready", "封面文件尚未就绪")
        if submitting:
            scan_submission(content)

    @staticmethod
    def _quota(db, owner, encoded, replacing_bytes=0):
        used = db.execute("SELECT COALESCE(SUM(length(CAST(r.content_json AS BLOB))),0) FROM creative_revisions r JOIN creatives c ON c.id=r.creative_id WHERE c.owner_user_id=?", (owner,)).fetchone()[0]
        if used - replacing_bytes + len(encoded.encode()) > 100 * 1024 * 1024:
            raise CreativeError(429, "creative_quota", "账号创意源码存储已达到 100 MiB 上限")

    def create_owned(self, owner, author_name, client_id, content):
        encoded = dump(content.model_dump())
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connection(write=True) as db:
            existing = db.execute("SELECT * FROM creative_submission_keys WHERE owner_user_id=? AND client_id=?", (owner, client_id)).fetchone()
            if existing:
                # The original create may have succeeded even if its response was lost.
                item_id = existing["creative_id"]
                if existing["content_hash"] != digest:
                    raise CreativeError(409, "idempotency_conflict", "此草稿已同步，请先读取云端版本再保存修改")
            else:
                count = db.execute("SELECT COUNT(*) FROM creatives WHERE owner_user_id=?", (owner,)).fetchone()[0]
                if count >= 50:
                    raise CreativeError(429, "creative_quota", "每个账号最多保留 50 项创意")
                self._author_content(db, owner, content)
                self._quota(db, owner, encoded)
                item_id, revision_id, now = uuid.uuid4().hex, uuid.uuid4().hex, time.time()
                db.execute("INSERT INTO creatives(id,owner_user_id,origin,author_name,created_at,updated_at) VALUES (?,?,'community',?,?,?)",
                           (item_id, owner, author_name[:80] or "创作者", now, now))
                db.execute("INSERT INTO creative_revisions(id,creative_id,version_no,content_json,content_hash,created_at) VALUES (?,?,1,?,?,?)",
                           (revision_id, item_id, encoded, digest, now))
                db.execute("INSERT INTO creative_submission_keys VALUES (?,?,?,?)", (owner, client_id, item_id, digest))
                self._audit(db, item_id, "user:" + owner, "create_draft", revision_id)
        return self.owner_detail(item_id, owner) | {"create_content_hash": digest}

    def owner_detail(self, item_id, owner):
        with self.connection() as db:
            self._owned(db, item_id, owner)
        item = self.admin_detail(item_id)
        # Never return administrator notes, actor tokens, or other authors' content.
        item = {key: item[key] for key in ("id", "origin", "author_name", "distribution_state", "row_version", "published_revision_id", "revision_id", "content", "revisions")}
        with self.connection() as db:
            item["feedback"] = [dict(row) for row in db.execute("SELECT revision_id,decision,feedback,created_at FROM creative_reviews WHERE creative_id=? ORDER BY id DESC LIMIT 50", (item_id,))]
        return item

    def owner_list(self, owner):
        with self.connection() as db:
            rows = db.execute("""SELECT c.id,c.distribution_state,c.row_version,c.updated_at,r.version_no,r.state,
                json_extract(r.content_json,'$.title') title FROM creatives c JOIN creative_revisions r ON r.creative_id=c.id
                AND r.version_no=(SELECT MAX(version_no) FROM creative_revisions WHERE creative_id=c.id)
                WHERE c.owner_user_id=? ORDER BY c.updated_at DESC LIMIT 50""", (owner,)).fetchall()
            return {"items": [dict(row) for row in rows]}

    def save_owned(self, item_id, owner, expected, content):
        with self.connection(write=True) as db:
            item = self._owned(db, item_id, owner, expected)
            if item["distribution_state"] == "archived":
                raise CreativeError(409, "creative_blocked", "此创意已归档")
            if db.execute("SELECT 1 FROM creative_revisions WHERE creative_id=? AND state IN ('submitted','in_review')", (item_id,)).fetchone():
                raise CreativeError(409, "revision_frozen", "审核中的稿件已冻结；先撤回，再修改")
            self._author_content(db, owner, content)
            draft = db.execute("SELECT * FROM creative_revisions WHERE creative_id=? AND state='draft'", (item_id,)).fetchone()
            encoded = dump(content.model_dump())
            self._quota(db, owner, encoded, len(draft["content_json"].encode()) if draft else 0)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            revision_id = draft["id"] if draft else uuid.uuid4().hex
            if draft:
                db.execute("UPDATE creative_revisions SET content_json=?,content_hash=? WHERE id=?", (encoded, digest, revision_id))
            else:
                version = db.execute("SELECT MAX(version_no)+1 FROM creative_revisions WHERE creative_id=?", (item_id,)).fetchone()[0]
                if version > 30:
                    raise CreativeError(429, "revision_quota", "单项创意最多保留 30 个版本")
                db.execute("INSERT INTO creative_revisions(id,creative_id,version_no,content_json,content_hash,created_at) VALUES (?,?,?,?,?,?)", (revision_id, item_id, version, encoded, digest, time.time()))
            db.execute("UPDATE creatives SET row_version=row_version+1,updated_at=? WHERE id=?", (time.time(), item_id))
            self._audit(db, item_id, "user:" + owner, "save_draft", revision_id)
        return self.owner_detail(item_id, owner)

    def submit(self, item_id, owner, body: SubmitRevision):
        with self.connection(write=True) as db:
            item = self._owned(db, item_id, owner)
            revision = self._revision(db, item_id, body.revision_id)
            if revision["content_hash"] != body.content_hash:
                raise CreativeError(409, "revision_conflict", "提交内容与已保存版本不一致")
            if revision["state"] in {"submitted", "in_review"}:
                return self.owner_detail(item_id, owner)
            self._owned(db, item_id, owner, body.expected_version)
            if item["distribution_state"] in {"admin_blocked", "archived"}:
                raise CreativeError(409, "creative_blocked", "管理员已限制此创意，不能通过重新投稿恢复上架")
            if revision["state"] != "draft":
                raise CreativeError(409, "revision_frozen", "请先保存为新草稿后再提交")
            self._author_content(db, owner, CreativeContent.model_validate_json(revision["content_json"]), submitting=True)
            recent = db.execute("SELECT COUNT(*) FROM creative_audit WHERE actor=? AND action='submit' AND created_at>?", ("user:" + owner, time.time()-86400)).fetchone()[0]
            if recent >= 5:
                raise CreativeError(429, "submission_quota", "24 小时内最多提交 5 次审核，请稍后再试")
            pending = db.execute("SELECT COUNT(*) FROM creative_revisions r JOIN creatives c ON c.id=r.creative_id WHERE c.owner_user_id=? AND r.state IN ('submitted','in_review')", (owner,)).fetchone()[0]
            if pending >= 3:
                raise CreativeError(429, "pending_submission_quota", "最多同时保留 3 项待审核稿件")
            db.execute("UPDATE creative_revisions SET state='submitted' WHERE id=?", (revision["id"],))
            db.execute("UPDATE creatives SET row_version=row_version+1,updated_at=?,distribution_state=CASE WHEN distribution_state='author_hidden' THEN 'unpublished' ELSE distribution_state END WHERE id=?", (time.time(), item_id))
            self._audit(db, item_id, "user:" + owner, "submit", revision["id"])
            self._notice(db, item, revision["id"], "submitted", "投稿已收到，等待管理员审核")
        return self.owner_detail(item_id, owner)

    def withdraw(self, item_id, owner, expected, revision_id):
        with self.connection(write=True) as db:
            item = self._owned(db, item_id, owner, expected)
            revision = self._revision(db, item_id, revision_id)
            if revision["state"] not in {"submitted", "in_review"}:
                raise CreativeError(409, "revision_frozen", "此版本已不在审核中，请刷新状态")
            db.execute("UPDATE creative_revisions SET state='withdrawn' WHERE id=?", (revision_id,))
            db.execute("UPDATE creatives SET row_version=row_version+1,updated_at=? WHERE id=?", (time.time(), item_id))
            self._audit(db, item_id, "user:" + owner, "withdraw", revision_id)
            self._notice(db, item, revision_id, "withdrawn", "已撤回审核，已发布的旧版不受影响")
        return self.owner_detail(item_id, owner)

    def hide(self, item_id, owner, expected):
        with self.connection(write=True) as db:
            item = self._owned(db, item_id, owner, expected)
            if item["distribution_state"] != "listed":
                raise CreativeError(409, "creative_unavailable", "只有已上架的创意可以撤下")
            db.execute("UPDATE creatives SET distribution_state='author_hidden',row_version=row_version+1,updated_at=? WHERE id=?", (time.time(), item_id))
            self._bump(db)
            self._audit(db, item_id, "user:" + owner, "author_hide")
        return self.owner_detail(item_id, owner)

    def delete_unpublished(self, item_id, owner, expected):
        removed_assets = []
        with self.connection(write=True) as db:
            item = self._owned(db, item_id, owner, expected)
            if item["published_revision_id"] or item["distribution_state"] in {"admin_blocked", "archived"}:
                raise CreativeError(409, "creative_retained", "已发布或被管理员保留的创意不能直接删除，可申请下架处理")
            if db.execute("SELECT 1 FROM creative_revisions WHERE creative_id=? AND state IN ('submitted','in_review')", (item_id,)).fetchone():
                raise CreativeError(409, "revision_frozen", "请先撤回审核，再删除草稿")
            covers = {r[0] for r in db.execute("SELECT json_extract(content_json,'$.cover_asset_id') FROM creative_revisions WHERE creative_id=?", (item_id,)) if r[0]}
            db.execute("DELETE FROM creative_revisions WHERE creative_id=?", (item_id,))
            db.execute("DELETE FROM creatives WHERE id=?", (item_id,))
            # Keep the create key as a tombstone so a late request cannot resurrect deletion.
            self._audit(db, item_id, "user:" + owner, "delete_unpublished")
            for asset in covers:
                if not db.execute("SELECT 1 FROM creative_revisions WHERE json_extract(content_json,'$.cover_asset_id')=?", (asset,)).fetchone():
                    db.execute("DELETE FROM creative_uploads WHERE asset_id=? AND owner_user_id=?", (asset, owner))
                    db.execute("DELETE FROM creative_assets WHERE id=? AND owner_user_id=?", (asset, owner))
                    removed_assets.append(asset)
        for asset in removed_assets:
            (self.asset_root / f"{asset}.jpg").unlink(missing_ok=True)
        return {"ok": True}

    def favorite(self, owner, item_id, enabled):
        with self.connection(write=True) as db:
            if enabled:
                item = db.execute(self.PUBLIC_SELECT + " AND c.id=?", (item_id,)).fetchone()
                if not item:
                    raise CreativeError(404, "creative_unavailable", "创意未发布或已下架")
                exists = db.execute("SELECT 1 FROM creative_favorites WHERE owner_user_id=? AND creative_id=?", (owner, item_id)).fetchone()
                if not exists and db.execute("SELECT COUNT(*) FROM creative_favorites WHERE owner_user_id=?", (owner,)).fetchone()[0] >= 500:
                    raise CreativeError(429, "favorite_quota", "每个账号最多收藏 500 项创意")
                db.execute("INSERT OR IGNORE INTO creative_favorites(owner_user_id,creative_id,revision_id,created_at) VALUES (?,?,?,?)",
                           (owner, item_id, item["revision_id"], time.time()))
            else:
                db.execute("DELETE FROM creative_favorites WHERE owner_user_id=? AND creative_id=?", (owner, item_id))
        return {"creative_id": item_id, "favorite": bool(enabled)}

    def favorites(self, owner):
        with self.connection() as db:
            rows = db.execute("""SELECT f.creative_id FROM creative_favorites f JOIN creatives c ON c.id=f.creative_id
                JOIN creative_revisions r ON r.id=c.published_revision_id
                WHERE f.owner_user_id=? AND c.distribution_state='listed' AND r.state='approved'
                ORDER BY f.created_at DESC LIMIT 500""", (owner,)).fetchall()
        return {"creative_ids": [row["creative_id"] for row in rows]}

    def create_report(self, reporter, item_id, body):
        encoded = dump({"reason": body.reason, "details": body.details})
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connection(write=True) as db:
            keyed = db.execute("SELECT * FROM creative_report_keys WHERE reporter_user_id=? AND client_id=?", (reporter, body.client_id)).fetchone()
            if keyed:
                if keyed["content_hash"] != digest:
                    raise CreativeError(409, "idempotency_conflict", "此举报请求编号已用于其他内容")
                report = db.execute("SELECT * FROM creative_reports WHERE id=?", (keyed["report_id"],)).fetchone()
                if not report:
                    raise CreativeError(409, "report_unavailable", "举报记录已进入保留流程")
                return self._report_receipt(report)
            item = db.execute(self.PUBLIC_SELECT + " AND c.id=?", (item_id,)).fetchone()
            if not item:
                raise CreativeError(404, "creative_unavailable", "创意未发布或已下架")
            if item["owner_user_id"] == reporter:
                raise CreativeError(409, "self_report_forbidden", "作者不能举报自己的创意，可在“我的创意”中撤下")
            active = db.execute("""SELECT * FROM creative_reports WHERE reporter_user_id=? AND creative_id=?
                AND revision_id=? AND reason=? AND details=? AND state IN ('open','reviewing') ORDER BY created_at DESC LIMIT 1""",
                                (reporter, item_id, item["revision_id"], body.reason, body.details)).fetchone()
            if active:
                db.execute("INSERT INTO creative_report_keys VALUES (?,?,?,?)", (reporter, body.client_id, active["id"], digest))
                return self._report_receipt(active)
            recent = db.execute("SELECT COUNT(*) FROM creative_reports WHERE reporter_user_id=? AND created_at>?", (reporter, time.time()-86400)).fetchone()[0]
            if recent >= 10:
                raise CreativeError(429, "report_quota", "24 小时内最多新建 10 条举报，请稍后再试")
            report_id, now = uuid.uuid4().hex, time.time()
            db.execute("""INSERT INTO creative_reports(id,creative_id,revision_id,reporter_user_id,reason,details,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""", (report_id, item_id, item["revision_id"], reporter, body.reason, body.details, now, now))
            db.execute("INSERT INTO creative_report_keys VALUES (?,?,?,?)", (reporter, body.client_id, report_id, digest))
            self._audit(db, item_id, "user:" + reporter, "report_submitted", item["revision_id"], body.reason)
            return self._report_receipt(db.execute("SELECT * FROM creative_reports WHERE id=?", (report_id,)).fetchone())

    def own_reports(self, reporter):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM creative_reports WHERE reporter_user_id=? ORDER BY created_at DESC LIMIT 100", (reporter,)).fetchall()
        return {"items": [self._report_receipt(row) for row in rows]}

    def reports(self, state="", offset=0):
        with self.connection() as db:
            rows = db.execute("""SELECT rp.*,c.row_version AS creative_row_version,c.distribution_state,c.author_name,
                COALESCE(json_extract(r.content_json,'$.title'),'已删除创意') AS title,
                (SELECT COUNT(*) FROM creative_reports d WHERE d.creative_id=rp.creative_id AND d.revision_id=rp.revision_id
                    AND d.reason=rp.reason AND d.state IN ('open','reviewing')) AS duplicate_count
                FROM creative_reports rp LEFT JOIN creatives c ON c.id=rp.creative_id
                LEFT JOIN creative_revisions r ON r.id=rp.revision_id
                WHERE (?='' OR rp.state=?) ORDER BY CASE rp.state WHEN 'open' THEN 0 WHEN 'reviewing' THEN 1 ELSE 2 END,rp.created_at,rp.id
                LIMIT 41 OFFSET ?""", (state, state, offset)).fetchall()
        return {"items": [dict(row) for row in rows[:40]], "next_offset": offset+40 if len(rows)>40 else None}

    def report_detail(self, report_id):
        with self.connection() as db:
            row = db.execute("""SELECT rp.*,c.row_version AS creative_row_version,c.distribution_state,c.author_name,
                COALESCE(json_extract(r.content_json,'$.title'),'已删除创意') AS title
                FROM creative_reports rp LEFT JOIN creatives c ON c.id=rp.creative_id
                LEFT JOIN creative_revisions r ON r.id=rp.revision_id WHERE rp.id=?""", (report_id,)).fetchone()
            if not row:
                raise CreativeError(404, "report_unavailable", "举报记录不存在")
            return dict(row)

    def decide_report(self, report_id, body: ReportDecision, actor):
        with self.connection(write=True) as db:
            report = db.execute("SELECT * FROM creative_reports WHERE id=?", (report_id,)).fetchone()
            if not report:
                raise CreativeError(404, "report_unavailable", "举报记录不存在")
            if report["row_version"] != body.expected_version:
                raise CreativeError(409, "report_conflict", "举报已被其他管理员处理，请刷新")
            if report["state"] not in {"open", "reviewing"}:
                raise CreativeError(409, "report_resolved", "举报已经处理完成")
            if body.decision != "claim" and not body.resolution.strip():
                raise CreativeError(422, "resolution_required", "请填写举报人可见的处理结果")
            state = "reviewing" if body.decision == "claim" else "dismissed" if body.decision == "dismiss" else "resolved"
            item = self._item(db, report["creative_id"])
            if body.decision == "block":
                if body.expected_creative_version is None or item["row_version"] != body.expected_creative_version:
                    raise CreativeError(409, "revision_conflict", "创意状态已变化，请刷新后再下架")
                if item["distribution_state"] != "listed":
                    raise CreativeError(409, "creative_unavailable", "创意已经撤下或不可公开")
                db.execute("UPDATE creatives SET distribution_state='admin_blocked',row_version=row_version+1,updated_at=? WHERE id=?", (time.time(), item["id"]))
                self._bump(db)
                if item["owner_user_id"]:
                    self._notice(db, item, report["revision_id"], "admin_blocked", "创意已由管理员停止公开，请联系运营人员了解处理方式")
            now = time.time()
            affected = ([report] if body.decision != "block" else db.execute("""SELECT * FROM creative_reports
                WHERE creative_id=? AND revision_id=? AND state IN ('open','reviewing')""",
                (report["creative_id"], report["revision_id"])).fetchall())
            for current in affected:
                db.execute("""UPDATE creative_reports SET state=?,row_version=row_version+1,resolution=?,private_note=?,actor=?,updated_at=? WHERE id=?""",
                           (state, body.resolution.strip(), body.private_note.strip(), actor, now, current["id"]))
                db.execute("""INSERT INTO creative_notifications(owner_user_id,creative_id,revision_id,kind,message,created_at)
                    VALUES (?,?,?,?,?,?)""", (current["reporter_user_id"], current["creative_id"], current["revision_id"], "report_"+state,
                    "举报处理完成：" + body.resolution.strip() if body.decision != "claim" else "举报已进入处理", now))
            self._audit(db, report["creative_id"], actor, "report:" + body.decision, report["revision_id"], body.private_note)
        return self.report_detail(report_id)

    def metrics(self):
        with self.connection() as db:
            one = lambda sql, args=(): db.execute(sql, args).fetchone()[0]
            return {"published": one("SELECT COUNT(*) FROM creatives WHERE distribution_state='listed'"),
                    "community_published": one("SELECT COUNT(*) FROM creatives WHERE distribution_state='listed' AND origin='community'"),
                    "pending_reviews": one("SELECT COUNT(*) FROM creative_revisions WHERE state IN ('submitted','in_review')"),
                    "open_reports": one("SELECT COUNT(*) FROM creative_reports WHERE state IN ('open','reviewing')"),
                    "favorites": one("SELECT COUNT(*) FROM creative_favorites"),
                    "submissions_24h": one("SELECT COUNT(*) FROM creative_audit WHERE action='submit' AND created_at>?", (time.time()-86400,))}

    def reviews(self, state="", offset=0):
        with self.connection() as db:
            rows = db.execute("""SELECT c.id,c.author_name,c.distribution_state,c.row_version,r.id revision_id,r.state,r.version_no,
                r.content_hash,json_extract(r.content_json,'$.title') title FROM creatives c JOIN creative_revisions r ON r.creative_id=c.id
                WHERE c.origin='community' AND r.state IN ('submitted','in_review') AND (?='' OR r.state=?)
                ORDER BY r.created_at,c.id LIMIT 41 OFFSET ?""", (state, state, offset)).fetchall()
            return {"items": [dict(r) for r in rows[:40]], "next_offset": offset+40 if len(rows)>40 else None}

    def review_detail(self, item_id, revision_id):
        item = self.admin_detail(item_id, revision_id)
        if item["origin"] != "community":
            raise CreativeError(404, "creative_unavailable", "此条目不是社区投稿")
        previous = self.admin_detail(item_id, item["published_revision_id"])["content"] if item["published_revision_id"] else None
        with self.connection() as db:
            history = [dict(r) for r in db.execute("SELECT * FROM creative_reviews WHERE creative_id=? ORDER BY id DESC LIMIT 50", (item_id,))]
        return {"item": item, "published_content": previous, "reviews": history}

    def decide(self, item_id, body: ReviewDecision, actor):
        with self.connection(write=True) as db:
            item = self._item(db, item_id, body.expected_version)
            revision = self._revision(db, item_id, body.revision_id)
            if item["origin"] != "community" or revision["state"] not in {"submitted", "in_review"}:
                raise CreativeError(409, "review_stale", "稿件已撤回或审核结束，请刷新")
            if revision["content_hash"] != body.content_hash:
                raise CreativeError(409, "revision_conflict", "审核内容摘要不一致")
            if body.decision in {"changes_requested", "reject"} and not body.feedback.strip():
                raise CreativeError(422, "feedback_required", "请填写作者可见的修改意见或拒绝原因")
            state = {"claim": "in_review", "approve": "approved", "changes_requested": "changes_requested", "reject": "rejected"}[body.decision]
            if body.decision == "approve":
                if item["distribution_state"] in {"admin_blocked", "archived", "author_hidden"}:
                    raise CreativeError(409, "creative_blocked", "此创意已被撤下或限制，不能通过审核操作直接恢复")
                self._author_content(db, item["owner_user_id"], CreativeContent.model_validate_json(revision["content_json"]), submitting=True)
                db.execute("UPDATE creatives SET published_revision_id=?,distribution_state='listed' WHERE id=?", (revision["id"], item_id))
                db.execute("UPDATE creative_revisions SET published_at=? WHERE id=?", (time.time(), revision["id"]))
                self._bump(db)
            db.execute("UPDATE creative_revisions SET state=? WHERE id=?", (state, revision["id"]))
            db.execute("UPDATE creatives SET row_version=row_version+1,updated_at=? WHERE id=?", (time.time(), item_id))
            db.execute("INSERT INTO creative_reviews(creative_id,revision_id,decision,feedback,private_note,actor,created_at) VALUES (?,?,?,?,?,?,?)",
                       (item_id, revision["id"], state, body.feedback.strip(), body.private_note, actor, time.time()))
            self._audit(db, item_id, actor, "review:" + state, revision["id"], body.private_note)
            label = {"in_review": "稿件已进入审核", "approved": "稿件审核通过，已发布到广场", "changes_requested": "稿件需要修改", "rejected": "稿件未通过审核"}[state]
            self._notice(db, item, revision["id"], state, label + ("：" + body.feedback.strip() if body.feedback.strip() else ""))
        return self.review_detail(item_id, body.revision_id)

    def notifications(self, owner, after=0):
        with self.connection() as db:
            rows = db.execute("SELECT id,creative_id,revision_id,kind,message,created_at,read_at FROM creative_notifications WHERE owner_user_id=? AND id>? ORDER BY id LIMIT 50", (owner, after)).fetchall()
            return {"items": [dict(row) for row in rows], "next_after": rows[-1]["id"] if rows else after}

    def read_notifications(self, owner, through_id):
        with self.connection(write=True) as db:
            db.execute("UPDATE creative_notifications SET read_at=COALESCE(read_at,?) WHERE owner_user_id=? AND id<=?", (time.time(), owner, through_id))
        return {"ok": True}
