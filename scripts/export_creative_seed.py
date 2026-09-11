#!/usr/bin/env python3
"""Export the current Android catalog as a deterministic server seed bundle.

No database writes. --check compares against the checked-in bundle; importing is
an explicit administrator action and never overwrites an existing creative.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.generate_creative_catalog import load, pattern_literal, style_literal

DEST = ROOT / "agent/creative/builtin_catalog.json.gz"
KOTLIN = ROOT / "android-app/app/src/main/java/com/androidagent/client/creative"


def seed_entries():
    data, foundation, layouts, count = load()
    recipe_type = (KOTLIN / "CreativeRecipe.kt").read_text()
    imports = re.search(r'private val COMPOSE_IMPORTS = """(.*?)"""\.trimIndent', recipe_type, re.S)
    prelude = textwrap.dedent(imports.group(1)).strip() if imports else ""
    catalog = (KOTLIN / "CreativeCatalog.kt").read_text()
    starts = list(re.finditer(r'CreativeRecipe\(\s*id = "([a-z0-9-]+)"', catalog))
    entries = []
    for i, match in enumerate(starts):
        block = catalog[match.end():starts[i + 1].start() if i + 1 < len(starts) else len(catalog)]
        def field(name):
            return re.search(rf'\b{name} = "([^"]*)"', block).group(1)
        source = textwrap.dedent(re.search(r'source = """(.*?)"""\.trimIndent', block, re.S).group(1)).strip("\n").replace("${'$'}", "$")
        if "import androidx.compose." not in source:
            source = prelude + "\n\n" + source
        entries.append({"legacy_id": match.group(1), "content": {
            "title": field("title"), "summary": field("summary"),
            "category_id": re.search(r'category = CreativeCategory\.(\w+)', block).group(1).lower(),
            "tags": re.findall(r'"([^"]+)"', re.search(r'tags = setOf\((.*?)\)', block).group(1)),
            "native_preview_id": match.group(1), "files": [{"path": "Recipe.kt", "content": source}],
            "integration": "识别现有工程后接入可打开页面，保留主题与导航，执行 assembleDebug 并报告结果。",
            "attribution": "Android Agent 内置原创演示",
        }})
    patterns = {p["id"]: p for p in data["patterns"]}
    # Preserve the same interleaved style order as the Android generator.
    for index in range(max(len(style["patterns"]) for style in data["styles"])):
        for style in data["styles"]:
            if index >= len(style["patterns"]):
                continue
            pattern = patterns[style["patterns"][index]]
            name, body = layouts[pattern["id"]]
            key = f'{style["id"]}-{pattern["id"]}'
            title = f'{style["label"]} · {pattern["title"]}'
            entry = "Creative" + "".join(part[0].upper() + part[1:] for part in key.split("-"))
            references = [style["reference"], data["patternReference"]]
            source = "\n".join([
                f"// {title}",
                "// 原创 Compose 演示；复制到已启用 Compose Material3 的 Kotlin 文件。最低 SDK 24。",
                f"// 用法：{entry}()；示例数据与交互不连接真实业务服务。",
                *[f'// 设计参考：{ref["url"]}' for ref in references],
                foundation, body, "\n@Composable",
                f"fun {entry}(modifier: Modifier = Modifier) {{",
                f"    val style = {style_literal(style)}",
                f"    val pattern = {pattern_literal(pattern)}",
                f"    Box(modifier) {{ {name}(style, pattern, interactive = true) }}", "}", "",
            ])
            # Match CreativeStyleSources.build: copied recipes can coexist in one package.
            helpers = re.findall(r"(?:data class|fun) (\w+)\(", foundation) + [name]
            source = re.sub(r"\b(" + "|".join(map(re.escape, helpers)) + r")\b",
                            lambda match: entry + match.group(0), source)
            entries.append({"legacy_id": key, "content": {
                "title": title, "summary": f'{style["summary"]}。{pattern["summary"]}。',
                "category_id": pattern["category"].lower(),
                "tags": [style["label"], pattern["title"], style["id"], pattern["id"]],
                "native_preview_id": key, "references": references,
                "files": [{"path": "Recipe.kt", "content": source}],
                "integration": "识别 XML/Compose 工程，适配现有主题与导航并接入页面，构建验证。",
                "attribution": "Android Agent 原创 Compose 演示；参考公开设计语言",
            }})
    assert len(entries) == count
    assert len({entry["legacy_id"] for entry in entries}) == count
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    entries = seed_entries()
    raw = json.dumps({"schema_version": 1, "items": entries}, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = gzip.compress(raw, mtime=0)
    if args.check:
        if not DEST.exists() or gzip.decompress(DEST.read_bytes()) != raw:
            raise SystemExit("Creative seed is stale; run scripts/export_creative_seed.py")
    else:
        DEST.write_bytes(encoded)
    print(f"Creative seed: {len(entries)} items; {len(encoded)} compressed bytes; sha256={hashlib.sha256(raw).hexdigest()[:16]}")


if __name__ == "__main__":
    main()
