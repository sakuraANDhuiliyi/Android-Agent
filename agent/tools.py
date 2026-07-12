from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.paths import (
    STUDIO_JBR,
    build_log_path,
    ensure_local_properties,
    latest_apk_path,
)
from agent.project import new_build_id

ALLOWED_READ_PREFIXES = (
    "app/src/",
    "app/build.gradle.kts",
    "build.gradle.kts",
    "settings.gradle.kts",
    "gradle/",
)

ALLOWED_WRITE_PREFIXES = (
    "app/src/main/java/",
    "app/src/main/res/",
    "app/src/main/AndroidManifest.xml",
    "app/build.gradle.kts",
)

GRADLE_TASKS = {"assembleDebug", "clean"}


@dataclass
class ToolResult:
    ok: bool
    output: Any


def summarize_build_log(log_body: str, tail_lines: int = 120) -> str:
    lines = log_body.splitlines()
    markers = (" error:", "ERROR", "Exception", "FAILED", "Manifest merger failed", "resource linking failed")
    first_error = next((index for index, line in enumerate(lines) if any(marker in line for marker in markers)), None)
    selected: list[str] = []
    if first_error is not None:
        selected.extend(lines[max(0, first_error - 3): first_error + 12])
    tail = lines[-tail_lines:]
    if selected and tail and selected[-1] != tail[0]:
        selected.append("... 日志尾部 ...")
    selected.extend(tail)
    deduped = list(dict.fromkeys(selected))
    return "\n".join(deduped) if deduped else "(构建日志为空)"


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def _resolve_in_workspace(workspace: Path, rel_path: str) -> Path:
    rel = _normalize_rel(rel_path)
    target = (workspace / rel).resolve()
    if not str(target).startswith(str(workspace.resolve())):
        raise PermissionError(f"路径越界: {rel_path}")
    return target


def _is_allowed(rel: str, prefixes: tuple[str, ...]) -> bool:
    rel = _normalize_rel(rel)
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in prefixes)


def _can_browse(rel: str) -> bool:
    rel = _normalize_rel(rel)
    if not rel or rel == ".":
        return True
    if _is_allowed(rel, ALLOWED_READ_PREFIXES):
        return True
    prefix = rel.rstrip("/") + "/"
    return any(
        _normalize_rel(allowed).startswith(prefix) or _normalize_rel(allowed) == rel
        for allowed in ALLOWED_READ_PREFIXES
    )


def browse_roots() -> list[dict[str, str]]:
    roots: list[dict[str, str]] = []
    seen: set[str] = set()
    for allowed in ALLOWED_READ_PREFIXES:
        rel = _normalize_rel(allowed)
        if rel.endswith("/"):
            rel = rel.rstrip("/")
        if rel in seen:
            continue
        seen.add(rel)
        roots.append(
            {
                "name": rel.split("/")[-1] if "/" in rel else rel,
                "path": rel,
                "type": "dir" if allowed.endswith("/") else "file",
            }
        )
    return sorted(roots, key=lambda item: (item["type"] != "dir", item["path"]))


def list_dir_entries(workspace: Path, rel_path: str = ".") -> ToolResult:
    try:
        rel = _normalize_rel(rel_path) or "."
        if rel == ".":
            return ToolResult(True, browse_roots())

        if not _can_browse(rel):
            return ToolResult(False, f"不允许浏览: {rel_path}")

        target = _resolve_in_workspace(workspace, rel)
        if not target.is_dir():
            return ToolResult(False, f"不是目录: {rel_path}")

        entries: list[dict[str, str]] = []
        for child in sorted(target.iterdir()):
            child_rel = _normalize_rel(f"{rel.rstrip('/')}/{child.name}")
            if child.is_dir():
                if not _can_browse(child_rel):
                    continue
            elif not _is_allowed(child_rel, ALLOWED_READ_PREFIXES):
                continue
            entries.append(
                {
                    "name": child.name,
                    "path": child_rel,
                    "type": "dir" if child.is_dir() else "file",
                }
            )
        return ToolResult(True, entries)
    except Exception as e:
        return ToolResult(False, str(e))


def read_file_meta(workspace: Path, rel_path: str) -> ToolResult:
    try:
        rel = _normalize_rel(rel_path)
        if not _is_allowed(rel, ALLOWED_READ_PREFIXES):
            return ToolResult(False, f"不允许读取: {rel_path}")
        target = _resolve_in_workspace(workspace, rel)
        if not target.is_file():
            return ToolResult(False, f"文件不存在: {rel_path}")
        content = target.read_text(encoding="utf-8")
        truncated = len(content) > 100_000
        if truncated:
            content = content[:100_000] + "\n\n... (已截断，文件过大)"
        return ToolResult(
            True,
            {
                "path": rel,
                "content": content,
                "truncated": truncated,
                "size": target.stat().st_size,
            },
        )
    except UnicodeDecodeError:
        return ToolResult(False, f"不是文本文件: {rel_path}")
    except Exception as e:
        return ToolResult(False, str(e))


def list_dir(workspace: Path, rel_path: str = ".") -> ToolResult:
    try:
        target = _resolve_in_workspace(workspace, rel_path)
        if not target.is_dir():
            return ToolResult(False, f"不是目录: {rel_path}")
        entries = []
        for child in sorted(target.iterdir()):
            kind = "dir" if child.is_dir() else "file"
            entries.append(f"[{kind}] {child.name}")
        return ToolResult(True, "\n".join(entries) if entries else "(空目录)")
    except Exception as e:
        return ToolResult(False, str(e))


def read_file(workspace: Path, rel_path: str) -> ToolResult:
    try:
        rel = _normalize_rel(rel_path)
        if not _is_allowed(rel, ALLOWED_READ_PREFIXES):
            return ToolResult(False, f"不允许读取: {rel_path}")
        target = _resolve_in_workspace(workspace, rel)
        if not target.is_file():
            return ToolResult(False, f"文件不存在: {rel_path}")
        content = target.read_text(encoding="utf-8")
        if len(content) > 100_000:
            content = content[:100_000] + "\n\n... (已截断，文件过大)"
        return ToolResult(True, content)
    except Exception as e:
        return ToolResult(False, str(e))


def is_writable_path(rel_path: str) -> bool:
    return _is_allowed(_normalize_rel(rel_path), ALLOWED_WRITE_PREFIXES)


def write_file(workspace: Path, rel_path: str, content: str) -> ToolResult:
    try:
        rel = _normalize_rel(rel_path)
        if not _is_allowed(rel, ALLOWED_WRITE_PREFIXES):
            return ToolResult(False, f"不允许写入: {rel_path}")
        target = _resolve_in_workspace(workspace, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"已写入 {rel} ({len(content)} 字符)")
    except Exception as e:
        return ToolResult(False, str(e))


def run_gradle(
    workspace: Path,
    user_id: str,
    project_id: str,
    task: str = "assembleDebug",
) -> ToolResult:
    if task not in GRADLE_TASKS:
        return ToolResult(False, f"不允许的任务: {task}，仅支持 {sorted(GRADLE_TASKS)}")

    gradlew = workspace / "gradlew"
    if not gradlew.is_file():
        return ToolResult(False, "gradlew 不存在")

    build_id = new_build_id()
    log_file = build_log_path(user_id, project_id, build_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    if "JAVA_HOME" not in env and STUDIO_JBR.is_dir():
        env["JAVA_HOME"] = str(STUDIO_JBR)

    sdk = ensure_local_properties(workspace)
    if sdk:
        env.setdefault("ANDROID_HOME", str(sdk))
        env.setdefault("ANDROID_SDK_ROOT", str(sdk))
    elif "ANDROID_HOME" not in env:
        return ToolResult(
            False,
            "未找到 Android SDK。请安装 Android Studio，或设置环境变量 ANDROID_HOME，"
            "例如: export ANDROID_HOME=$HOME/Library/Android/sdk",
        )

    cmd = [str(gradlew), task, "--no-daemon", "--stacktrace"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        log_body = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        log_file.write_text(log_body, encoding="utf-8")

        if proc.returncode != 0:
            tail = summarize_build_log(log_body)
            return ToolResult(
                False,
                f"Gradle 失败 (exit {proc.returncode})\n日志: {log_file}\n\n--- 关键日志摘要 ---\n{tail}",
            )

        msg = f"Gradle {task} 成功\n日志: {log_file}"
        if task == "assembleDebug":
            apk = workspace / "app/build/outputs/apk/debug/app-debug.apk"
            if apk.is_file():
                out = latest_apk_path(user_id, project_id)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(apk, out)
                msg += f"\nAPK: {out}"
            else:
                msg += f"\n警告: 未找到 {apk}"
        return ToolResult(True, msg)
    except subprocess.TimeoutExpired:
        return ToolResult(False, "Gradle 超时 (15 分钟)")
    except Exception as e:
        return ToolResult(False, str(e))


def dispatch_tool(
    workspace: Path,
    user_id: str,
    project_id: str,
    name: str,
    tool_input: dict,
) -> ToolResult:
    if name == "list_dir":
        return list_dir(workspace, tool_input.get("path", "."))
    if name == "read_file":
        return read_file(workspace, tool_input["path"])
    if name == "write_file":
        return write_file(workspace, tool_input["path"], tool_input["content"])
    if name == "run_gradle":
        return run_gradle(
            workspace,
            user_id,
            project_id,
            tool_input.get("task", "assembleDebug"),
        )
    return ToolResult(False, f"未知工具: {name}")


BASE_TOOL_DEFINITIONS = [
    {
        "name": "list_dir",
        "description": "列出工程内目录内容。path 为相对工程根的路径，默认为 .",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对路径，例如 app/src/main",
                }
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": "读取工程内文本文件",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对路径，例如 app/src/main/java/.../MainActivity.kt",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "写入工程内文件（覆盖）。仅限 app/src/main 下源码资源及 app/build.gradle.kts",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_gradle",
        "description": "在工程目录执行 Gradle。常用 task: assembleDebug",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "enum": ["assembleDebug", "clean"],
                    "description": "Gradle 任务名",
                }
            },
            "required": [],
        },
    },
]


def get_tool_definitions(settings=None) -> list[dict]:
    return list(BASE_TOOL_DEFINITIONS)


TOOL_DEFINITIONS = BASE_TOOL_DEFINITIONS
