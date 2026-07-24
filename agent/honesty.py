from __future__ import annotations

import re
from typing import Any


EDIT_TOOLS = frozenset({"write_file", "str_replace"})

# User intents that normally require mutating project files
_EDIT_REQUEST_RE = re.compile(
    r"(修改|改一下|改下|更改|重写|重构|实现|创建|添加|新增|删除|修复|"
    r"下载|改造|设计|打包|构建|assemble|写代码|写一个|帮我做|做成一个|做成)",
    re.IGNORECASE,
)

# Model claims that imply files were changed
_EDIT_CLAIM_RE = re.compile(
    r"(已完成|改造完成|修改完成|已经修改|已修改|已写入|已更新|已创建|代码已|"
    r"文件已|改动了|我已经改|我已修改|打包完成|构建成功后|"
    r"✅\s*改造|✅\s*完成)",
    re.IGNORECASE,
)

_EXPLICIT_NO_EDIT_RE = re.compile(
    r"(本轮未改|没有修改任何文件|未改任何文件|无需改代码|只回答|纯说明)",
    re.IGNORECASE,
)

# Assistant is waiting for / asking for user permission (not claiming edits done)
_AWAITS_USER_RE = re.compile(
    r"(等待你|等待用户|请(你)?(确认|允许|批准|同意)|需要你(的)?确认|"
    r"确认后(再|才能)|是否允许下载|请在(对话|下方|界面).{0,8}(确认|允许)|"
    r"索取权限|请求下载权限)",
    re.IGNORECASE,
)

# Asking for download permission in plain text without calling the tool
_TEXT_ASKS_DOWNLOAD_RE = re.compile(
    r"(请|麻烦|可否).{0,12}(确认|允许|批准).{0,12}下载|"
    r"下载.{0,12}(需要|请).{0,8}(确认|允许)|是否(允许|同意)下载",
    re.IGNORECASE,
)

_USER_BLOCKED_DECISIONS = frozenset({"rejected", "timeout", "canceled"})


def prompt_expects_file_edit(prompt: str) -> bool:
    text = (prompt or "").strip()
    if not text:
        return False
    # Pure meta questions about how to prompt the agent
    if re.search(r"(什么样的|怎么写|如何写).{0,12}提示词", text):
        return False
    return bool(_EDIT_REQUEST_RE.search(text))


def text_claims_file_edit(text: str) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    if _EXPLICIT_NO_EDIT_RE.search(body):
        return False
    if text_awaits_user_action(body):
        return False
    return bool(_EDIT_CLAIM_RE.search(body))


def text_awaits_user_action(text: str) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    return bool(_AWAITS_USER_RE.search(body))


def text_asks_download_permission(text: str) -> bool:
    """True when the model asks for download approval in prose instead of calling the tool."""
    body = (text or "").strip()
    if not body:
        return False
    return bool(_TEXT_ASKS_DOWNLOAD_RE.search(body))


def honesty_nudge_message(*, successful_edits: int) -> str:
    return (
        "【系统强制校验】本轮尚未成功执行 write_file 或 str_replace"
        f"（成功写入次数={successful_edits}）。\n"
        "禁止声称「已完成 / 已修改 / 改造完成」。\n"
        "请立刻调用 write_file 或 str_replace 真实修改工程文件；"
        "若本轮确实只需文字回答、不改代码，必须明确写：「本轮未改任何文件」。"
        "若需要下载资源，必须调用 download_file（会在对话里向用户确认），"
        "不要只在文字里索要权限后结束。"
    )


def download_tool_nudge_message() -> str:
    return (
        "【系统提示】如需下载网络文件，请调用 download_file 工具。"
        "该工具会在对话中向用户弹出确认卡片并暂停等待；"
        "不要只用文字询问「是否允许下载」然后结束本轮。"
    )


def sanitize_final_answer(
    answer: str,
    *,
    changed_files: list[Any] | None,
    successful_edits: int,
    user_prompt: str,
    approval_decisions: list[str] | None = None,
) -> str:
    """Prevent delivering a hallucinated 'edit done' answer when nothing changed."""
    changed = changed_files or []
    text = (answer or "").strip()
    if not text:
        return text

    decisions = [d for d in (approval_decisions or []) if d]
    user_blocked = any(d in _USER_BLOCKED_DECISIONS for d in decisions)
    had_approval = bool(decisions)

    expects = prompt_expects_file_edit(user_prompt)
    claims = text_claims_file_edit(text)
    awaits = text_awaits_user_action(text)

    if successful_edits > 0 or changed:
        # Real edits happened — optionally append a short audit line
        if changed:
            paths = []
            for item in changed[:12]:
                if isinstance(item, dict):
                    paths.append(str(item.get("path") or item))
                else:
                    paths.append(str(item))
            audit = "、".join(paths)
            if "【本轮实际改动】" not in text:
                text = f"{text}\n\n【本轮实际改动】{len(changed)} 个文件：{audit}"
        return text

    # Waiting for / blocked by user permission — not a deception failure
    if user_blocked:
        note = (
            "【系统说明】本轮因用户未批准下载（或确认超时/取消），未写入工程文件。"
            "这不属于「假装已改代码」；可重新发送需求并在确认卡片中点「允许下载」。"
        )
        if note.split("】")[0] in text:
            return text
        if awaits or not claims:
            return f"{text}\n\n{note}" if text else note
        return (
            f"{note}\n\n"
            "以下模型原文可能把计划说成了结果，请以未下载为准：\n\n"
            f"{text}"
        )

    if awaits or text_asks_download_permission(text):
        note = (
            "【系统说明】本轮似乎在等待你确认权限，但任务已结束且未产生文件改动。"
            "请再发一句继续；需要下载时 Agent 应弹出确认卡片，而不是只发文字。"
        )
        if "【系统说明】本轮似乎在等待" in text:
            return text
        return f"{text}\n\n{note}"

    if had_approval and any(d == "approved" for d in decisions) and not changed:
        # Approved but download/write still failed for other reasons
        if not (expects or claims):
            return text

    if not (expects or claims):
        return text

    banner = (
        "【系统校验】本轮未检测到任何文件改动"
        f"（成功 write_file/str_replace = {successful_edits}）。"
        "以下模型原文可能不准确，请勿当作已落地的代码改动：\n\n"
    )
    if "【系统校验】本轮未检测到任何文件改动" in text:
        return text
    return banner + text
