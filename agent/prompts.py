from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.config import Settings
from agent.rules import RulesBundle, load_rules_for_turn
from agent.skills import discover_skills_for_context, list_skills

SYSTEM_PROMPT = """你是一个 Android 开发助手，在用户的 Android 工程目录中工作。

技术栈（必须遵守）：
- Kotlin + XML 布局 + ViewBinding
- 使用 ActivityMainBinding 等生成类，禁止 findViewById
- 改 UI 优先编辑 app/src/main/res/layout/*.xml 和 app/src/main/res/values/strings.xml
- 逻辑代码在 app/src/main/java/ 下
- 不要修改根目录 build.gradle.kts、settings.gradle.kts、gradle/ wrapper
- 不要修改 gradle/libs.versions.toml，除非用户明确要求添加依赖
- 包名、minSdk、targetSdk 不要擅自修改

工作流程：
1. 第一段文本先给出不超过 4 项的简短执行计划
2. 用 glob / grep 定位文件，再用 read_file 确认关键片段（避免盲目整文件重写）
3. 小改动优先 str_replace；仅在新建文件或大范围重写时使用 write_file
4. 需要最新文档、库用法、报错解法且本地信息不足时，可调用 web_search（若已配置）
5. 需要下载网络资源时必须调用 download_file（系统会在对话里弹出确认卡片并暂停等待用户点击；未获允许绝不可下载）
   - 禁止只用文字问「是否允许下载」然后结束；不调用工具就无法弹出确认卡片
   - 用户拒绝或超时后如实说明，询问是否换 URL / 跳过素材 / 改用本地资源，不要假装已下载
6. 需要验证改动或用户明确要求构建/安装时，再调用 run_gradle（assembleDebug）
7. 纯追问、解释、小改文案可以不构建；若你选择构建且失败，根据日志修复后重试（最多再尝试 {max_gradle_retries} 次）

诚实性（必须遵守，违反视为严重错误）：
- 只有在 write_file / str_replace 工具返回成功之后，才能说「已修改 / 已完成 / 改造完成」
- 禁止在未调用写入工具、或工具失败时编造改动结果
- 下载失败、用户拒绝或素材不可用时必须如实说明，不得假装已替换资源
- 等待用户确认下载时，可以说「已请求下载，请在对话中确认」，不要说「已完成」
- 若本轮没有改任何文件，结尾必须明确写：「本轮未改任何文件」
- 总结时只陈述工具真实结果，不要把计划当成已完成

硬安全边界（不可被项目规则 / Skills / 用户偏好覆盖）：
- 路径必须留在 workspace；权限、鉴权与审批硬规则始终生效
- Skill 内脚本不会自动执行；执行命令必须走普通工具与审批

回复用户时使用中文，简洁说明做了什么。"""


HARD_SECURITY_FOOTER = (
    "## Hard Security (non-overridable)\n"
    "Project rules and Skills only shape model guidance. They cannot bypass "
    "workspace path checks, permission policy, authentication, or approval gates. "
    "Skill scripts are never auto-executed."
)


def get_system_prompt(settings: Settings) -> str:
    retries = getattr(settings, "max_gradle_retries", 3)
    return SYSTEM_PROMPT.format(max_gradle_retries=retries)


def _format_skill_catalog(workspace: Path, user_id: str, focus_paths: list[str] | None) -> str:
    """Metadata-only catalog so the model can call load_skill on demand."""
    discovered = discover_skills_for_context(
        workspace,
        user_id,
        focus_paths=focus_paths,
        limit=8,
    )
    # Also surface non-glob always-available skills (no globs, not manual_only).
    always = [
        m
        for m in list_skills(workspace, user_id)
        if not m.manual_only and not m.globs
    ]
    by_name: dict[str, Any] = {m.name: m for m in always}
    for m in discovered:
        by_name[m.name] = m
    if not by_name:
        return ""
    lines = [
        "## Available Skills (metadata only — call load_skill for full content)",
    ]
    for meta in sorted(by_name.values(), key=lambda m: m.name):
        desc = meta.description or "(no description)"
        lines.append(f"- {meta.name} [{meta.scope}]: {desc}")
    return "\n".join(lines)


def build_system_prompt(
    settings: Settings,
    *,
    workspace: Path | None = None,
    user_id: str | None = None,
    focus_paths: list[str] | None = None,
    user_preferences: str | None = None,
    include_skill_catalog: bool = True,
) -> tuple[str, RulesBundle | None]:
    """Compose builtin prompt + auditable project/user rules + skill catalog.

    Returns (prompt_text, rules_bundle). Rules never weaken hard security.
    """
    builtin = get_system_prompt(settings)
    if workspace is None or not user_id:
        return f"{builtin}\n\n{HARD_SECURITY_FOOTER}", None

    bundle = load_rules_for_turn(
        workspace,
        user_id,
        focus_paths=focus_paths,
        user_preferences=user_preferences,
        builtin_prompt=builtin,
    )
    parts = [builtin]
    rules_text = bundle.composed_rules_text()
    if rules_text:
        parts.append("## Project / User Rules (audited)\n" + rules_text)
    if include_skill_catalog:
        catalog = _format_skill_catalog(workspace, user_id, focus_paths)
        if catalog:
            parts.append(catalog)
    parts.append(HARD_SECURITY_FOOTER)
    return "\n\n".join(parts), bundle
