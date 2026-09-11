from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import sqlite3
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from agent.api import create_app
from agent.creative.community import CommunityStore
from agent.creative.models import ReviewDecision
from agent.creative.store import CreativeError
from agent.database import TaskStore
from agent.users import UserStore
from tests.test_accounts_api import settings
from tests.test_creative_catalog import content


@pytest.fixture
def setup(tmp_path):
    users = UserStore(tmp_path / "users.db")
    accounts = [users.register_account(f"author{i}@example.test", "isolated-test-123", display_name=f"创作者 {i}") for i in range(2)]
    config = replace(settings(), creative_submissions_enabled=True, admin_ui_enabled=True, admin_token="isolated-community-admin-token-32")
    app = create_app(config, user_store=users, task_store=TaskStore(tmp_path / "agent.db"))
    with TestClient(app) as client:
        yield client, [{"Authorization": "Bearer " + a["token"]} for a in accounts], {"Authorization": "Bearer " + config.admin_token}, app


def create(http, auth, title="社区卡片", client_id=None, **kwargs):
    response = http.post("/api/me/creative/items", headers=auth, json={"client_id": client_id or uuid.uuid4().hex, "content": content(title, license="MIT", **kwargs).model_dump()})
    assert response.status_code == 201, response.text
    return response.json()


def action(item):
    revision = next(r for r in item["revisions"] if r["id"] == item["revision_id"])
    return {"expected_version": item["row_version"], "revision_id": revision["id"], "content_hash": revision["content_hash"]}


def submit(http, auth, item):
    response = http.post(f'/api/me/creative/items/{item["id"]}/submit', headers=auth, json=action(item) | {"sharing_confirmed": True})
    assert response.status_code == 200, response.text
    return response.json()


def review(http, admin, item, decision="approve", **kwargs):
    return http.post(f'/api/admin/creative/reviews/{item["id"]}', headers=admin, json=action(item) | {"decision": decision} | kwargs)


def test_owner_is_server_assigned_and_other_users_cannot_access_or_edit(setup):
    http, (a, b), admin, app = setup
    assert http.get("/api/me/creative/items").status_code == 401
    item = create(http, a)
    assert item["origin"] == "community"
    assert http.get(f'/api/me/creative/items/{item["id"]}', headers=b).status_code == 404
    response = http.put(f'/api/me/creative/items/{item["id"]}/draft', headers=b,
                        json={"expected_version": 1, "content": content().model_dump()})
    assert response.status_code == 404
    assert http.get("/api/me/creative/items", headers=b).json()["items"] == []
    assert http.get("/api/creative/items").json()["items"] == []
    response = http.get(f'/api/me/creative/items/{item["id"]}', headers=a)
    assert "no-store" in response.headers["cache-control"]
    bad = content().model_dump() | {"owner_user_id": "victim", "origin": "official"}
    assert http.post("/api/me/creative/items", headers=a, json={"client_id": uuid.uuid4().hex, "content": bad}).status_code == 422
    guest = http.post("/api/auth/guest", json={"device": {"device_id": "community-guest", "device_name": "guest", "device_type": "android"}}).json()
    assert http.get("/api/me/creative/items", headers={"Authorization": "Bearer " + guest["token"]}).status_code == 403


def test_create_and_submit_retries_are_idempotent(setup):
    http, (a, _), admin, _ = setup
    key = uuid.uuid4().hex
    first = create(http, a, client_id=key)
    assert create(http, a, client_id=key)["id"] == first["id"]
    pending = submit(http, a, first)
    assert submit(http, a, first)["row_version"] == pending["row_version"]
    assert len(http.get("/api/admin/creative/reviews", headers=admin).json()["items"]) == 1
    assert len(http.get("/api/me/creative/notifications", headers=a).json()["items"]) == 1


def test_full_review_flow_keeps_private_notes_private_and_versions_frozen(setup):
    http, (a, b), admin, _ = setup
    first = submit(http, a, create(http, a))
    edited_content = content("第二版", license="MIT").model_dump()
    url = f'/api/me/creative/items/{first["id"]}'
    assert http.put(url+"/draft", headers=a, json={"expected_version": first["row_version"], "content": edited_content}).status_code == 409
    bypass = http.post(f'/api/admin/creative/items/{first["id"]}/publish', headers=admin,
                       json={k: v for k, v in action(first).items() if k != "content_hash"})
    assert bypass.status_code == 409
    assert review(http, admin, first, private_note="INTERNAL-ONLY", feedback="可以发布").status_code == 200
    public = http.get(f'/api/creative/items/{first["id"]}').json()
    assert public["title"] == "社区卡片" and public["author_name"] == "创作者 0"
    assert public["verification"] == "not_verified"
    own = http.get(url, headers=a).json()
    assert "INTERNAL-ONLY" not in json.dumps(own)
    assert "INTERNAL-ONLY" not in http.get("/api/me/creative/notifications", headers=a).text
    new = http.put(url+"/draft", headers=a, json={"expected_version": own["row_version"], "content": edited_content}).json()
    pending = submit(http, a, new)
    assert http.get(f'/api/creative/items/{first["id"]}').json()["title"] == "社区卡片"
    detail = http.get(f'/api/admin/creative/reviews/{first["id"]}', params={"revision_id": pending["revision_id"]}, headers=admin).json()
    assert detail["published_content"]["title"] == "社区卡片"
    assert detail["item"]["content"]["title"] == "第二版"
    assert review(http, admin, pending, "changes_requested", feedback="请补充说明").status_code == 200
    assert http.get(f'/api/creative/items/{first["id"]}').json()["title"] == "社区卡片"
    own = http.get(url, headers=a).json()
    third = http.put(url+"/draft", headers=a, json={"expected_version": own["row_version"], "content": edited_content}).json()
    assert third["revisions"][0]["version_no"] == 3
    pending = submit(http, a, third)
    assert review(http, admin, pending).status_code == 200
    assert http.get(f'/api/creative/items/{first["id"]}').json()["title"] == "第二版"
    assert http.get("/api/me/creative/notifications", headers=b).json()["items"] == []


def test_withdraw_and_takedown_invalidate_stale_approval(setup):
    http, (a, _), admin, _ = setup
    item = submit(http, a, create(http, a))
    version = {k:v for k,v in action(item).items() if k != "content_hash"}
    assert http.post(f'/api/me/creative/items/{item["id"]}/withdraw', headers=a, json=version).status_code == 200
    assert review(http, admin, item).status_code == 409
    other = submit(http, a, create(http, a, "另一个"))
    assert http.post(f'/api/admin/creative/items/{other["id"]}/visibility/unpublish', headers=admin,
                     json={"expected_version": other["row_version"], "reason": "需要复核"}).status_code == 200
    assert review(http, admin, other).status_code == 409
    assert http.get("/api/creative/items").json()["items"] == []


def test_two_admins_cannot_approve_and_reject_the_same_snapshot(setup):
    http, (a, _), _, app = setup
    item = submit(http, a, create(http, a))
    store = app.state.creative_store
    def decide(decision):
        try:
            store.decide(item["id"], ReviewDecision(**action(item), decision=decision, feedback="说明"), "admin:test")
            return "done"
        except CreativeError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(decide, ["approve", "reject"])) == ["done", "revision_conflict"]


def test_upload_retry_ownership_and_public_reference_boundaries(setup):
    http, (a, b), admin, _ = setup
    out = BytesIO(); Image.new("RGB", (40, 30), "blue").save(out, "PNG"); raw = out.getvalue()
    body = {"client_id": uuid.uuid4().hex, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    upload = http.post("/api/me/creative/uploads", headers=a, json=body).json()
    path = f'/api/me/creative/uploads/{upload["id"]}'
    assert http.get(path, headers=b).status_code == 404
    assert http.put(path+"/content", headers=b, content=raw).status_code == 404
    assert http.put(path+"/content", headers=a, content=b"wrong").status_code == 422
    done = http.put(path+"/content", headers=a, content=raw).json()
    assert done["state"] == "complete"
    assert http.put(path+"/content", headers=a, content=raw).json()["asset_id"] == done["asset_id"]
    assert http.post("/api/me/creative/uploads", headers=a, json=body).json()["asset_id"] == done["asset_id"]
    asset = done["asset_id"]
    assert http.get(f'/api/me/creative/assets/{asset}', headers=b).status_code == 404
    assert http.get(f'/api/creative/assets/{asset}').status_code == 404
    bad = content("偷用封面", license="MIT", cover_asset_id=asset).model_dump()
    assert http.post("/api/me/creative/items", headers=b, json={"client_id": uuid.uuid4().hex, "content": bad}).status_code == 422
    item = submit(http, a, create(http, a, cover_asset_id=asset))
    assert review(http, admin, item).status_code == 200
    assert http.get(f'/api/creative/assets/{asset}').status_code == 200
    assert http.delete(path, headers=a).status_code == 409


def test_submission_requires_explicit_confirmation_and_blocks_likely_secrets(setup):
    http, (a, _), admin, _ = setup
    empty = content(license="MIT").model_dump(); empty["files"] = []
    item = http.post("/api/me/creative/items", headers=a, json={"client_id": uuid.uuid4().hex, "content": empty}).json()
    # Empty source cannot enter the queue.
    response = http.post(f'/api/me/creative/items/{item["id"]}/submit', headers=a, json=action(item) | {"sharing_confirmed": True})
    assert response.status_code == 422
    assert http.post(f'/api/me/creative/items/{item["id"]}/submit', headers=a, json=action(item)).status_code == 422
    secret = 'val api_key = "a-credential-that-should-stay-private"'
    payload = content("私密测试", license="MIT").model_dump()
    payload["files"][0]["content"] = secret
    item = http.post("/api/me/creative/items", headers=a, json={"client_id": uuid.uuid4().hex, "content": payload}).json()
    response = http.post(f'/api/me/creative/items/{item["id"]}/submit', headers=a, json=action(item) | {"sharing_confirmed": True})
    assert response.status_code == 422 and "sensitive_content" in response.text
    assert secret not in response.text
    assert http.get("/api/admin/creative/reviews", headers=admin).json()["items"] == []


def test_notification_read_is_scoped_and_author_hide_cannot_override_admin_block(setup):
    http, (a, b), admin, _ = setup
    item = submit(http, a, create(http, a))
    assert review(http, admin, item).status_code == 200
    notices = http.get("/api/me/creative/notifications", headers=a).json()["items"]
    http.post("/api/me/creative/notifications/read", headers=b, json={"through_id": notices[-1]["id"]})
    assert all(n["read_at"] is None for n in http.get("/api/me/creative/notifications", headers=a).json()["items"])
    http.post("/api/me/creative/notifications/read", headers=a, json={"through_id": notices[-1]["id"]})
    assert all(n["read_at"] is not None for n in http.get("/api/me/creative/notifications", headers=a).json()["items"])
    own = http.get(f'/api/me/creative/items/{item["id"]}', headers=a).json()
    assert http.post(f'/api/me/creative/items/{item["id"]}/unpublish', headers=a, json={"expected_version": own["row_version"]}).status_code == 200
    assert http.get(f'/api/creative/items/{item["id"]}').status_code == 404
    own = http.get(f'/api/me/creative/items/{item["id"]}', headers=a).json()
    http.post(f'/api/admin/creative/items/{item["id"]}/visibility/unpublish', headers=admin, json={"expected_version": own["row_version"], "reason": "限制传播"})
    own = http.get(f'/api/me/creative/items/{item["id"]}', headers=a).json()
    assert http.post(f'/api/me/creative/items/{item["id"]}/unpublish', headers=a, json={"expected_version": own["row_version"]}).status_code == 409


def test_online_backup_restores_committed_wal_data_covers_accounts_and_private_feedback(setup, tmp_path):
    from agent.creative.backup import create_bundle, restore_bundle
    from agent.creative.assets import save_cover
    http, (a, b), admin, app = setup
    item = submit(http, a, create(http, a))
    assert review(http, admin, item, feedback="公开意见", private_note="PRIVATE-BACKUP-NOTE").status_code == 200
    assert http.put(f'/api/me/creative/favorites/{item["id"]}', headers=b).status_code == 200
    report = http.post(f'/api/creative/items/{item["id"]}/reports', headers=b,
                       json={"client_id": uuid.uuid4().hex, "reason": "other", "details": "备份恢复举报记录"}).json()
    raw = BytesIO(); Image.new("RGB", (10, 10), "red").save(raw, "PNG")
    store = app.state.creative_store
    cover = save_cover(store, raw.getvalue())
    original = sqlite3.connect(store.db_path)
    try:
        original.execute("PRAGMA journal_mode=WAL"); original.execute("PRAGMA wal_autocheckpoint=0")
        original.execute("CREATE TABLE backup_wal_probe(value TEXT)"); original.execute("INSERT INTO backup_wal_probe VALUES ('committed-in-wal')"); original.commit()
        bundle, restored = tmp_path / "bundle", tmp_path / "restored"
        create_bundle(store.db_path, app.state.user_store.db_path, bundle)
        original.execute("UPDATE backup_wal_probe SET value='after-backup'"); original.commit()
        restore_bundle(bundle, restored)
    finally:
        original.close()
    restored_store = CommunityStore(restored / "agent.db")
    assert restored_store.public_detail(item["id"])["title"] == "社区卡片"
    assert (restored / "creative-assets" / (cover["id"] + ".jpg")).read_bytes() == (store.asset_root / (cover["id"] + ".jpg")).read_bytes()
    with sqlite3.connect(restored / "agent.db") as db:
        assert db.execute("SELECT value FROM backup_wal_probe").fetchone()[0] == "committed-in-wal"
        assert db.execute("SELECT private_note FROM creative_reviews").fetchone()[0] == "PRIVATE-BACKUP-NOTE"
        assert db.execute("SELECT COUNT(*) FROM creative_favorites").fetchone()[0] == 1
        assert db.execute("SELECT state FROM creative_reports WHERE id=?", (report["id"],)).fetchone()[0] == "open"
    token = a["Authorization"].removeprefix("Bearer ")
    assert UserStore(restored / "users.db").authenticate_identity(token)
    with pytest.raises(ValueError, match="新目录"):
        restore_bundle(bundle, restored)
    (bundle / "creative-assets" / (cover["id"] + ".jpg")).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="摘要"):
        restore_bundle(bundle, tmp_path / "bad-restore")
    assert not (tmp_path / "bad-restore").exists()


def test_c1_asset_schema_migrates_without_losing_existing_records(tmp_path):
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE creative_assets(id TEXT PRIMARY KEY,digest TEXT NOT NULL,size INTEGER NOT NULL,mime TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,created_at REAL NOT NULL)")
        db.execute("INSERT INTO creative_assets VALUES ('old','digest',20,'image/jpeg',1,1,1)")
    store = CommunityStore(database)
    with store.connection() as db:
        row = db.execute("SELECT * FROM creative_assets WHERE id='old'").fetchone()
        assert row["size"] == 20 and row["owner_user_id"] is None


def test_disabled_author_cannot_submit_or_have_pending_draft_approved(setup):
    http, (a, _), admin, app = setup
    item = submit(http, a, create(http, a))
    owner = app.state.user_store.authenticate_identity(a["Authorization"].removeprefix("Bearer ")).user_id
    app.state.user_store.admin_update_account(owner, disabled=True)
    assert http.get("/api/me/creative/items", headers=a).status_code == 401
    response = review(http, admin, item)
    assert response.status_code == 409 and response.json()["error"]["code"] == "author_unavailable"
    assert http.get("/api/creative/items").json()["items"] == []


def test_upload_reservations_have_quota_but_retries_do_not_reserve_twice(setup):
    from agent.creative.assets import begin_upload
    from agent.creative.models import UploadRequest
    http, (a, _), _, app = setup
    owner = app.state.user_store.authenticate_identity(a["Authorization"].removeprefix("Bearer ")).user_id
    store = app.state.creative_store
    bodies = [UploadRequest(client_id=uuid.uuid4().hex, sha256="a"*64, size=1) for _ in range(100)]
    first = begin_upload(store, owner, bodies[0])
    for body in bodies[1:]:
        with store.connection(write=True) as db:
            db.execute("UPDATE creative_uploads SET state='complete' WHERE owner_user_id=?", (owner,))
        begin_upload(store, owner, body)
    assert begin_upload(store, owner, bodies[0])["id"] == first["id"]
    with pytest.raises(CreativeError) as error:
        begin_upload(store, owner, UploadRequest(client_id=uuid.uuid4().hex, sha256="b"*64, size=1))
    assert error.value.code == "upload_quota"


def test_pending_upload_limit_and_cancel_release(setup):
    http, (a, _), _, _ = setup
    payloads = [{"client_id": uuid.uuid4().hex, "sha256": "c"*64, "size": 1} for _ in range(3)]
    first = http.post("/api/me/creative/uploads", headers=a, json=payloads[0]).json()
    assert http.post("/api/me/creative/uploads", headers=a, json=payloads[1]).status_code == 201
    assert http.post("/api/me/creative/uploads", headers=a, json=payloads[0]).json()["id"] == first["id"]
    assert http.post("/api/me/creative/uploads", headers=a, json=payloads[2]).status_code == 429
    assert http.delete("/api/me/creative/uploads/"+first["id"], headers=a).status_code == 200
    assert http.post("/api/me/creative/uploads", headers=a, json=payloads[2]).status_code == 201


def test_submission_limits_count_events_not_retries(setup):
    http, (a, _), admin, _ = setup
    pending = [submit(http, a, create(http, a)) for _ in range(3)]
    assert submit(http, a, pending[0])["id"] == pending[0]["id"]
    extra = create(http, a)
    assert http.post(f'/api/me/creative/items/{extra["id"]}/submit', headers=a, json=action(extra) | {"sharing_confirmed": True}).status_code == 429
    for item in pending:
        assert review(http, admin, item).status_code == 200
    submit(http, a, extra)
    submit(http, a, create(http, a))
    extra = create(http, a)
    response = http.post(f'/api/me/creative/items/{extra["id"]}/submit', headers=a, json=action(extra) | {"sharing_confirmed": True})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "submission_quota"


def test_pausing_submissions_preserves_drafts_moderation_and_messages(setup):
    http, (a, _), admin, app = setup
    pending = submit(http, a, create(http, a))
    withdrawn = submit(http, a, create(http, a))
    paused = create_app(replace(settings(), creative_submissions_enabled=False, admin_ui_enabled=True, admin_token=admin["Authorization"].removeprefix("Bearer ")),
                        user_store=app.state.user_store, task_store=app.state.task_store)
    with TestClient(paused) as client:
        assert client.get("/api/me/creative/identity", headers=a).json()["submissions_enabled"] is False
        assert len(client.get("/api/me/creative/items", headers=a).json()["items"]) == 2
        draft = create(client, a)
        response = client.post(f'/api/me/creative/items/{draft["id"]}/submit', headers=a, json=action(draft) | {"sharing_confirmed": True})
        assert response.status_code == 409 and response.json()["error"]["code"] == "submissions_paused"
        assert len(client.get("/api/admin/creative/reviews", headers=admin).json()["items"]) == 2
        assert review(client, admin, pending).status_code == 200
        assert client.get('/api/creative/items/'+pending["id"]).status_code == 200
        assert client.post(f'/api/me/creative/items/{withdrawn["id"]}/withdraw', headers=a, json={k:v for k,v in action(withdrawn).items() if k!="content_hash"}).status_code == 200
        assert client.get("/api/me/creative/notifications", headers=a).json()["items"]


def test_author_response_matches_shared_android_contract(setup):
    http, (a, _), _, _ = setup
    payload = {"title": "投稿契约", "license": "MIT", "files": [{"path": "Card.kt", "content": "@Composable fun Card() {}"}, {"path": "strings.xml", "content": "<resources />"}]}
    created = http.post("/api/me/creative/items", headers=a, json={"client_id": uuid.uuid4().hex, "content": payload}).json()
    actual = http.get(f'/api/me/creative/items/{created["id"]}', headers=a).json()
    actual.update(id="c"*32, revision_id="d"*32, author_name="契约作者")
    actual["revisions"][0].update(id="d"*32, created_at=1000.0)
    fixture = Path(__file__).parent / "fixtures/api_contract/creative_author_200.json"
    assert actual == json.loads(fixture.read_text())


def test_private_draft_deletion_is_owned_and_late_create_does_not_resurrect_it(setup):
    http, (a, b), admin, _ = setup
    key = uuid.uuid4().hex
    item = create(http, a, client_id=key)
    url = f'/api/me/creative/items/{item["id"]}'
    assert http.delete(url, params={"expected_version": item["row_version"]}, headers=b).status_code == 404
    assert http.delete(url, params={"expected_version": item["row_version"]}, headers=a).status_code == 200
    late = http.post("/api/me/creative/items", headers=a, json={"client_id": key, "content": content("社区卡片", license="MIT").model_dump()})
    assert late.status_code == 404
    assert http.get("/api/me/creative/items", headers=a).json()["items"] == []
    published = submit(http, a, create(http, a))
    assert review(http, admin, published).status_code == 200
    own = http.get(f'/api/me/creative/items/{published["id"]}', headers=a).json()
    assert http.delete(f'/api/me/creative/items/{published["id"]}', params={"expected_version": own["row_version"]}, headers=a).status_code == 409


def test_favorites_are_account_scoped_idempotent_and_hide_unavailable_items(setup):
    http, (a, b), admin, _ = setup
    published = submit(http, a, create(http, a))
    assert review(http, admin, published).status_code == 200
    item_id = published["id"]
    assert http.put(f"/api/me/creative/favorites/{item_id}", headers=b).json()["favorite"] is True
    assert http.put(f"/api/me/creative/favorites/{item_id}", headers=b).status_code == 200
    assert http.get("/api/me/creative/favorites", headers=b).json()["creative_ids"] == [item_id]
    assert http.get("/api/me/creative/favorites", headers=a).json()["creative_ids"] == []
    own = http.get(f"/api/me/creative/items/{item_id}", headers=a).json()
    assert http.post(f"/api/me/creative/items/{item_id}/unpublish", headers=a,
                     json={"expected_version": own["row_version"]}).status_code == 200
    assert http.get("/api/me/creative/favorites", headers=b).json()["creative_ids"] == []
    assert http.delete(f"/api/me/creative/favorites/{item_id}", headers=b).status_code == 200
    assert http.get("/api/me/creative/favorites", headers=b).json()["creative_ids"] == []


def test_report_is_idempotent_private_and_resolvable_with_reporter_notice(setup):
    http, (a, b), admin, _ = setup
    published = submit(http, a, create(http, a))
    assert review(http, admin, published).status_code == 200
    item_id, key = published["id"], uuid.uuid4().hex
    body = {"client_id": key, "reason": "misleading", "details": "说明与公开源码不一致"}
    assert http.post(f"/api/creative/items/{item_id}/reports", json=body).status_code == 401
    guest = http.post("/api/auth/guest", json={"device": {"device_id": "report-guest", "device_name": "guest", "device_type": "android"}}).json()
    assert http.post(f"/api/creative/items/{item_id}/reports", headers={"Authorization": "Bearer " + guest["token"]}, json=body).status_code == 403
    first = http.post(f"/api/creative/items/{item_id}/reports", headers=b, json=body)
    assert first.status_code == 201
    assert "no-store" in first.headers["cache-control"] and first.headers["vary"] == "Authorization"
    report = first.json()
    assert http.post(f"/api/creative/items/{item_id}/reports", headers=b, json=body).json()["id"] == report["id"]
    assert http.post(f"/api/creative/items/{item_id}/reports", headers=b,
                     json=body | {"details": "复用编号但更换内容"}).status_code == 409
    assert http.post(f"/api/creative/items/{item_id}/reports", headers=a,
                     json=body | {"client_id": uuid.uuid4().hex}).status_code == 409
    queued = http.get("/api/admin/creative/reports", headers=admin).json()["items"]
    assert len(queued) == 1 and queued[0]["details"] == body["details"] and queued[0]["reporter_user_id"]
    resolved = http.post(f'/api/admin/creative/reports/{report["id"]}', headers=admin,
                         json={"expected_version": report["row_version"], "decision": "resolve",
                               "resolution": "已核对并修正文案", "private_note": "REPORTER-MUST-NOT-SEE"})
    assert resolved.status_code == 200
    own = http.get("/api/me/creative/reports", headers=b).json()["items"][0]
    assert own["resolution"] == "已核对并修正文案" and "private" not in own
    notices = http.get("/api/me/creative/notifications", headers=b).json()["items"]
    assert any("已核对并修正文案" in row["message"] for row in notices)
    assert "REPORTER-MUST-NOT-SEE" not in json.dumps(own, ensure_ascii=False)
    assert http.get(f"/api/creative/items/{item_id}").status_code == 200


def test_report_block_uses_creative_version_and_notifies_author_without_private_note(setup):
    http, (a, b), admin, app = setup
    published = submit(http, a, create(http, a))
    assert review(http, admin, published).status_code == 200
    item_id = published["id"]
    report = http.post(f"/api/creative/items/{item_id}/reports", headers=b,
                       json={"client_id": uuid.uuid4().hex, "reason": "privacy", "details": "源码包含个人联系信息"}).json()
    third = app.state.user_store.register_account("reporter3@example.test", "isolated-test-123", display_name="举报用户 3")
    c = {"Authorization": "Bearer " + third["token"]}
    duplicate = http.post(f"/api/creative/items/{item_id}/reports", headers=c,
                          json={"client_id": uuid.uuid4().hex, "reason": "copyright", "details": "来源授权信息无法核实"}).json()
    detail = http.get(f'/api/admin/creative/reports/{report["id"]}', headers=admin).json()
    stale = http.post(f'/api/admin/creative/reports/{report["id"]}', headers=admin,
                      json={"expected_version": report["row_version"], "expected_creative_version": detail["creative_row_version"]+1,
                            "decision": "block", "resolution": "已临时停止公开"})
    assert stale.status_code == 409 and http.get(f"/api/creative/items/{item_id}").status_code == 200
    blocked = http.post(f'/api/admin/creative/reports/{report["id"]}', headers=admin,
                        json={"expected_version": report["row_version"], "expected_creative_version": detail["creative_row_version"],
                              "decision": "block", "resolution": "已临时停止公开", "private_note": "PRIVATE-LEGAL-NOTE"})
    assert blocked.status_code == 200
    assert http.get(f"/api/creative/items/{item_id}").status_code == 404
    author_notices = http.get("/api/me/creative/notifications", headers=a).json()["items"]
    assert any("停止公开" in row["message"] for row in author_notices)
    assert "PRIVATE-LEGAL-NOTE" not in json.dumps(author_notices, ensure_ascii=False)
    metrics = http.get("/api/admin/creative/metrics", headers=admin).json()
    assert metrics["open_reports"] == 0 and metrics["favorites"] == 0
    assert http.get("/api/me/creative/reports", headers=c).json()["items"][0]["id"] == duplicate["id"]
    assert http.get("/api/me/creative/reports", headers=c).json()["items"][0]["state"] == "resolved"


def test_report_daily_quota_counts_new_reports_not_idempotent_retries(setup):
    http, (a, b), admin, _ = setup
    published = submit(http, a, create(http, a))
    assert review(http, admin, published).status_code == 200
    route = f'/api/creative/items/{published["id"]}/reports'
    for index in range(10):
        body = {"client_id": uuid.uuid4().hex, "reason": "other", "details": f"第 {index+1} 条独立问题说明"}
        created = http.post(route, headers=b, json=body)
        assert created.status_code == 201
        report = created.json()
        assert http.post(route, headers=b, json=body).json()["id"] == report["id"]
        assert http.post(f'/api/admin/creative/reports/{report["id"]}', headers=admin,
                         json={"expected_version": report["row_version"], "decision": "dismiss",
                               "resolution": "已核对，此项无需处理"}).status_code == 200
    response = http.post(route, headers=b, json={"client_id": uuid.uuid4().hex, "reason": "other", "details": "第十一条独立问题说明"})
    assert response.status_code == 429 and response.json()["error"]["code"] == "report_quota"
