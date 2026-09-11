from __future__ import annotations

import gzip
import json
from pathlib import Path

from .models import CreativeContent
from .store import CreativeError, CreativeStore


def import_builtins(store: CreativeStore, actor: str, *, publish=False):
    bundle = Path(__file__).with_name("builtin_catalog.json.gz")
    if not bundle.is_file():
        raise CreativeError(503, "seed_missing", "内置目录尚未导出，请运行 scripts/export_creative_seed.py")
    payload = json.loads(gzip.decompress(bundle.read_bytes()))
    if payload.get("schema_version") != 1 or len(payload["items"]) > 500:
        raise CreativeError(422, "seed_invalid", "内置目录格式不兼容")
    # Validate the entire trusted release bundle before making any mutation.
    entries = [(entry["legacy_id"], CreativeContent.model_validate(entry["content"])) for entry in payload["items"]]
    created = skipped = published = 0
    for legacy_id, content in entries:
        result = store.create(content, actor, legacy_id=legacy_id)
        if not result["created"]:
            skipped += 1
            continue
        created += 1
        if publish:
            item = store.admin_detail(result["id"])
            store.publish(item["id"], item["row_version"], item["revision_id"], actor, "管理员导入并发布内置演示；尚未进行平台构建验证")
            published += 1
    return {"created": created, "skipped": skipped, "published": published}
