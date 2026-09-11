"""Catalog publication/visibility boundaries, using isolated databases and no model."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.api import create_app
from agent.config import Settings
from agent.creative.models import CreativeContent, SourceFile
from agent.creative.seed import import_builtins
from agent.creative.store import CreativeError, CreativeStore
from agent.database import TaskStore
from agent.users import UserStore
from tests.test_accounts_api import settings


def content(title="登录按钮", **kwargs):
    return CreativeContent(title=title, files=[SourceFile(path="Button.kt", content="@Composable fun Button() {}")], **kwargs)


@pytest.fixture
def store(tmp_path):
    return CreativeStore(tmp_path / "agent.db")


def create(store, title="登录按钮", **kwargs):
    result = store.create(content(title, **kwargs), "admin:test")
    return store.admin_detail(result["id"])


def publish(store, item):
    return store.publish(item["id"], item["row_version"], item["revision_id"], "admin:test")


def test_drafts_do_not_leak_and_editing_preserves_public_revision(store):
    item = create(store)
    assert store.catalog()["items"] == []
    with pytest.raises(CreativeError):
        store.public_detail(item["id"])
    first = publish(store, item)
    initial = store.public_detail(item["id"])
    edited = store.save_draft(first["id"], first["row_version"], content("未公开的新版"), "admin:test")
    assert edited["revision_id"] != first["revision_id"]
    assert store.public_detail(item["id"])["title"] == "登录按钮"
    assert "未公开" not in str(store.catalog())
    publish(store, edited)
    latest = store.public_detail(item["id"])
    assert latest["title"] == "未公开的新版"
    assert latest["content_hash"] != initial["content_hash"]
    assert store.admin_detail(item["id"], first["revision_id"])["content"]["title"] == "登录按钮"


def test_stale_admin_cannot_publish_after_takedown(store):
    item = publish(store, create(store))
    hidden = store.change_visibility(item["id"], item["row_version"], "unpublish", "admin:other", "需要复核")
    with pytest.raises(CreativeError) as stale:
        publish(store, item)
    assert stale.value.code == "revision_conflict"
    with pytest.raises(CreativeError) as blocked:
        publish(store, hidden)
    assert blocked.value.code == "creative_blocked"
    assert store.catalog()["items"] == []
    restored = store.change_visibility(item["id"], hidden["row_version"], "restore", "admin:test", "复核完成")
    assert store.catalog()["items"] == []
    publish(store, restored)
    assert len(store.catalog()["items"]) == 1


def test_two_concurrent_editors_cannot_overwrite_each_other(store):
    item = create(store)
    def edit(index):
        try:
            CreativeStore(store.db_path).save_draft(item["id"], item["row_version"], content(f"editor-{index}"), "admin:test")
            return "saved"
        except CreativeError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(edit, range(2)))
    assert sorted(outcomes) == ["revision_conflict", "saved"]


def test_cursor_is_bound_to_query_and_catalog_generation(store):
    items = [publish(store, create(store, f"登录按钮 {i}")) for i in range(3)]
    first = store.catalog(query="登录", limit=2)
    assert len(first["items"]) == 2
    second = store.catalog(query="登录", limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    with pytest.raises(CreativeError) as invalid:
        store.catalog(query="other", limit=2, cursor=first["next_cursor"])
    assert invalid.value.code == "invalid_cursor"
    item = items[0]
    store.placement(item["id"], item["row_version"], True, 5, "admin:test")
    with pytest.raises(CreativeError) as changed:
        store.catalog(query="登录", limit=2, cursor=first["next_cursor"])
    assert changed.value.code == "catalog_changed"


@pytest.mark.parametrize("path", ["../secret.kt", "/tmp/x.kt", "a/../../x.kt", "a\\b.kt", ".env", ".git/config.txt", "app.apk", "a//b.kt", "C:secret.kt"])
def test_source_paths_cannot_escape_or_import_executables(path):
    with pytest.raises(ValidationError):
        SourceFile(path=path, content="hello")


def test_source_budget_counts_bytes_and_rejects_duplicate_paths():
    with pytest.raises(ValidationError):
        SourceFile(path="x.kt", content="中" * 100000)
    with pytest.raises(ValidationError):
        CreativeContent(title="test", files=[SourceFile(path="A.kt", content="a"), SourceFile(path="a.kt", content="b")])


def test_seed_import_is_complete_and_does_not_restore_hidden_items(store):
    result = import_builtins(store, "admin:test", publish=True)
    assert result["created"] == result["published"]
    assert 6 <= result["created"] <= 500
    item = store.admin_detail(store.catalog(limit=1)["items"][0]["id"])
    store.change_visibility(item["id"], item["row_version"], "unpublish", "admin:test", "运营下架")
    again = import_builtins(store, "admin:test", publish=True)
    assert again == {"created": 0, "skipped": result["created"], "published": 0}
    assert store.admin_detail(item["id"])["distribution_state"] == "admin_blocked"


def test_empty_catalog_can_bootstrap_builtins_for_deployment(tmp_path):
    config = replace(settings(), creative_bootstrap_builtins=True)
    task_store = TaskStore(tmp_path / "agent.db")
    app = create_app(config, user_store=UserStore(tmp_path / "users.db"), task_store=task_store)
    with TestClient(app) as http:
        page = http.get("/api/creative/items", params={"limit": 100})
    assert page.status_code == 200, page.text
    assert 6 <= len(page.json()["items"]) <= 100
    assert all(item["origin"] == "official" for item in page.json()["items"])


@pytest.fixture
def client(tmp_path):
    config = replace(settings(), admin_ui_enabled=True, admin_token="test-admin-token-for-creative-catalog")
    app = create_app(config, user_store=UserStore(tmp_path / "users.db"), task_store=TaskStore(tmp_path / "agent.db"))
    # The normal lifespan runs against isolated stores; no job or model request is submitted.
    with TestClient(app) as http:
        yield http, {"Authorization": "Bearer " + config.admin_token}


def test_api_admin_auth_and_public_dto_boundary(client):
    http, auth = client
    assert http.get("/api/admin/creative/items").status_code == 401
    assert http.post("/api/admin/creative/items", json=content().model_dump()).status_code == 401
    response = http.post("/api/admin/creative/items", json=content().model_dump(), headers=auth)
    assert response.status_code == 201, response.text
    item = response.json()
    assert http.get("/api/admin/creative/items", params={"q": item["id"]}, headers=auth).json()["items"][0]["id"] == item["id"]
    assert http.get("/api/creative/items").json()["items"] == []
    payload = {"expected_version": item["row_version"], "revision_id": item["revision_id"]}
    assert http.post(f'/api/admin/creative/items/{item["id"]}/publish', json=payload, headers=auth).status_code == 200
    listed = http.get("/api/creative/items").json()["items"][0]
    assert not ({"content", "owner_user_id", "row_version", "revisions", "review_state"} & listed.keys())
    assert listed["verification"] == "not_verified"
    assert "files" in http.get(f'/api/creative/items/{item["id"]}').json()["content"]
    page = http.get("/api/creative/items")
    assert http.get("/api/creative/items", headers={"If-None-Match": page.headers["etag"]}).status_code == 304
    normalized = page.json()
    normalized["items"][0].update(id="a" * 32, revision_id="b" * 32, published_at=1000.0)
    fixture = Path(__file__).parent / "fixtures/api_contract/creative_catalog_200.json"
    assert normalized == json.loads(fixture.read_text())


def test_cannot_assign_admin_fields_through_content(client):
    http, auth = client
    payload = content().model_dump() | {"origin": "community", "owner_user_id": "someone", "verification": "passed"}
    assert http.post("/api/admin/creative/items", json=payload, headers=auth).status_code == 422


def test_cover_is_decoded_private_until_published_and_revoked_on_takedown(client):
    from PIL import Image
    http, auth = client
    raw = BytesIO()
    Image.new("RGB", (30, 20), "red").save(raw, format="PNG")
    response = http.post("/api/admin/creative/covers", content=raw.getvalue(), headers=auth)
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["mime"] == "image/jpeg"
    url = f'/api/creative/assets/{asset["id"]}'
    assert http.get(url).status_code == 404
    assert http.get(f'/api/admin/creative/assets/{asset["id"]}', headers=auth).status_code == 200
    item = http.post("/api/admin/creative/items", json=content(cover_asset_id=asset["id"]).model_dump(), headers=auth).json()
    published = http.post(f'/api/admin/creative/items/{item["id"]}/publish', headers=auth,
                          json={"expected_version": item["row_version"], "revision_id": item["revision_id"]}).json()
    image = http.get(url)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.content.startswith(b"\xff\xd8")
    http.post(f'/api/admin/creative/items/{item["id"]}/visibility/unpublish', headers=auth,
              json={"expected_version": published["row_version"], "reason": "复核"})
    assert http.get(url).status_code == 404


@pytest.mark.parametrize("raw", [b"<svg onload='alert(1)'></svg>", b"\x89PNG\r\n\x1a\nnot-an-image", b"x" * (1536 * 1024 + 1)])
def test_invalid_and_oversized_uploads_are_rejected(client, raw):
    http, auth = client
    result = http.post("/api/admin/creative/covers", content=raw, headers=auth)
    assert result.status_code in {413, 422}, result.text


def test_categories_are_dynamic_and_conflict_checked(client):
    http, auth = client
    body = {"id": "new-category", "label": "新分类", "sort_order": -1}
    assert http.put("/api/admin/creative/categories", json=body, headers=auth).status_code == 200
    assert http.get("/api/creative/categories").json()[0]["label"] == "新分类"
    assert http.put("/api/admin/creative/categories", json=body, headers=auth).status_code == 409


def test_capabilities_do_not_claim_unimplemented_submission_or_execution(client):
    http, _auth = client
    capabilities = http.get("/api/creative/capabilities").json()
    assert capabilities["catalog_enabled"] is True
    assert capabilities["submissions_enabled"] is False
    assert capabilities["applications_enabled"] is False
    assert capabilities["favorites_enabled"] is True and capabilities["max_favorites"] == 500
    assert capabilities["reports_enabled"] is True and capabilities["max_daily_reports"] == 10
