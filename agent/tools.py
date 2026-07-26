from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

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
IGNORE_DIR_NAMES = {".git", ".gradle", "build", "node_modules", "__pycache__", ".idea"}

CancelCheck = Callable[[], None]

_active_gradle: dict[str, subprocess.Popen] = {}
_gradle_lock = threading.Lock()


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


def str_replace(
    workspace: Path,
    rel_path: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> ToolResult:
    try:
        rel = _normalize_rel(rel_path)
        if not _is_allowed(rel, ALLOWED_WRITE_PREFIXES):
            return ToolResult(False, f"不允许写入: {rel_path}")
        if old_string == "":
            return ToolResult(False, "old_string 不能为空")
        target = _resolve_in_workspace(workspace, rel)
        if not target.is_file():
            return ToolResult(False, f"文件不存在: {rel_path}")
        content = target.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return ToolResult(False, f"未找到要替换的文本: {rel}")
        if count > 1 and not replace_all:
            return ToolResult(
                False,
                f"匹配到 {count} 处，请提供更唯一的 old_string，或设置 replace_all=true",
            )
        if replace_all:
            updated = content.replace(old_string, new_string)
            replaced = count
        else:
            updated = content.replace(old_string, new_string, 1)
            replaced = 1
        target.write_text(updated, encoding="utf-8")
        return ToolResult(True, f"已替换 {rel}（{replaced} 处，现 {len(updated)} 字符）")
    except Exception as e:
        return ToolResult(False, str(e))


def _iter_readable_files(workspace: Path, root_rel: str = "app/src"):
    workspace = workspace.resolve()
    root_rel = _normalize_rel(root_rel) or "."
    if root_rel in {".", ""}:
        roots = [workspace / _normalize_rel(p).rstrip("/") for p in ALLOWED_READ_PREFIXES if p.endswith("/")]
        roots += [workspace / _normalize_rel(p) for p in ALLOWED_READ_PREFIXES if not p.endswith("/")]
    else:
        roots = [(workspace / root_rel).resolve()]

    for root in roots:
        if root.is_file():
            try:
                rel = root.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if _is_allowed(rel, ALLOWED_READ_PREFIXES):
                yield root, rel
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORE_DIR_NAMES for part in path.parts):
                continue
            try:
                rel = path.resolve().relative_to(workspace).as_posix()
            except ValueError:
                continue
            if _is_allowed(rel, ALLOWED_READ_PREFIXES):
                yield path, rel


def glob_files(workspace: Path, pattern: str, path: str = "app/src") -> ToolResult:
    try:
        if not pattern or not str(pattern).strip():
            return ToolResult(False, "pattern 不能为空")
        pattern = str(pattern).strip().replace("\\", "/")
        matches: list[str] = []
        for _, rel in _iter_readable_files(workspace, path):
            posix = Path(rel).as_posix()
            name = Path(rel).name
            matched = False
            try:
                matched = Path(posix).match(pattern) or Path(name).match(pattern)
            except Exception:
                matched = False
            if not matched:
                matched = fnmatch(posix, pattern) or fnmatch(name, pattern)
            if matched:
                matches.append(posix)
        matches = sorted(dict.fromkeys(matches))
        if len(matches) > 200:
            shown = matches[:200]
            return ToolResult(True, "\n".join(shown) + f"\n... 另有 {len(matches) - 200} 个匹配未列出")
        return ToolResult(True, "\n".join(matches) if matches else "(无匹配文件)")
    except Exception as e:
        return ToolResult(False, str(e))


def grep_files(
    workspace: Path,
    query: str,
    path: str = "app/src",
    *,
    case_insensitive: bool = False,
    max_hits: int = 50,
) -> ToolResult:
    try:
        if not query:
            return ToolResult(False, "query 不能为空")
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(query, flags)
        except re.error as exc:
            return ToolResult(False, f"无效正则: {exc}")

        hits: list[str] = []
        files_scanned = 0
        for file_path, rel in _iter_readable_files(workspace, path):
            files_scanned += 1
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{rel}:{line_no}: {line.strip()[:200]}")
                    if len(hits) >= max_hits:
                        return ToolResult(
                            True,
                            "\n".join(hits) + f"\n... 已达 {max_hits} 条上限（扫描 {files_scanned} 个文件）",
                        )
        if not hits:
            return ToolResult(True, f"(无匹配，已扫描 {files_scanned} 个文件)")
        return ToolResult(True, "\n".join(hits))
    except Exception as e:
        return ToolResult(False, str(e))


def _gradle_key(user_id: str, project_id: str) -> str:
    return f"{user_id}:{project_id}"


def cancel_gradle(user_id: str, project_id: str) -> bool:
    key = _gradle_key(user_id, project_id)
    with _gradle_lock:
        proc = _active_gradle.get(key)
    if not proc or proc.poll() is not None:
        return False
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return True


def run_gradle(
    workspace: Path,
    user_id: str,
    project_id: str,
    task: str = "assembleDebug",
    cancel_check: CancelCheck | None = None,
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
    key = _gradle_key(user_id, project_id)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=workspace,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with _gradle_lock:
            _active_gradle[key] = proc

        chunks: list[str] = []
        assert proc.stdout is not None
        while True:
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    cancel_gradle(user_id, project_id)
                    raise
            line = proc.stdout.readline()
            if line:
                chunks.append(line)
            elif proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    chunks.append(rest)
                break
            else:
                time.sleep(0.05)

        returncode = proc.wait(timeout=5)
        log_body = "".join(chunks)
        log_file.write_text(log_body, encoding="utf-8")

        if returncode != 0:
            tail = summarize_build_log(log_body)
            return ToolResult(
                False,
                f"Gradle 失败 (exit {returncode})\n日志: {log_file}\n\n--- 关键日志摘要 ---\n{tail}",
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
        cancel_gradle(user_id, project_id)
        return ToolResult(False, "Gradle 超时 (15 分钟)")
    except Exception as e:
        cancel_gradle(user_id, project_id)
        # Re-raise cancellation-like errors for the loop to handle
        if e.__class__.__name__ == "CancellationRequested":
            raise
        return ToolResult(False, str(e))
    finally:
        with _gradle_lock:
            if _active_gradle.get(key) is not None and _active_gradle[key].poll() is not None:
                _active_gradle.pop(key, None)
            elif key in _active_gradle and _active_gradle[key].poll() is not None:
                _active_gradle.pop(key, None)


def web_search(
    query: str,
    *,
    api_key: str,
    max_results: int = 5,
    include_answer: bool = True,
    search_depth: str = "basic",
) -> ToolResult:
    """Search the web via Tavily Search API."""
    query = (query or "").strip()
    if not query:
        return ToolResult(False, "query 不能为空")
    if not api_key:
        return ToolResult(
            False,
            "未配置 Tavily API Key。请在 config.yaml 设置 tavily_api_key，或设置环境变量 TAVILY_API_KEY",
        )

    max_results = max(1, min(int(max_results or 5), 10))
    depth = search_depth if search_depth in {"basic", "advanced"} else "basic"

    try:
        import httpx
    except ImportError:
        return ToolResult(False, "缺少 httpx 依赖，请执行 pip install httpx")

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "include_answer": bool(include_answer),
        "search_depth": depth,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post("https://api.tavily.com/search", json=payload)
        if response.status_code >= 400:
            detail = response.text[:500]
            return ToolResult(False, f"Tavily 请求失败 HTTP {response.status_code}: {detail}")
        data = response.json()
    except Exception as exc:
        return ToolResult(False, f"Tavily 请求异常: {exc}")

    lines: list[str] = [f"查询: {data.get('query', query)}"]
    answer = data.get("answer")
    if answer:
        lines.append(f"摘要: {answer}")
    results = data.get("results") or []
    if not results:
        lines.append("（无搜索结果）")
    else:
        lines.append(f"结果 ({len(results)}):")
        for index, item in enumerate(results, start=1):
            title = item.get("title") or "(无标题)"
            url = item.get("url") or ""
            content = (item.get("content") or "").strip()
            if len(content) > 500:
                content = content[:500] + "…"
            score = item.get("score")
            score_text = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
            lines.append(f"{index}. {title}{score_text}")
            if url:
                lines.append(f"   URL: {url}")
            if content:
                lines.append(f"   {content}")
    return ToolResult(True, "\n".join(lines))


MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB


def _validate_download_url(url: str) -> str:
    from urllib.parse import urlparse

    raw = (url or "").strip()
    if not raw:
        raise ValueError("url 不能为空")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅允许 http/https 下载")
    if not parsed.netloc:
        raise ValueError("无效的 URL")
    return raw


def download_file(
    workspace: Path,
    url: str,
    dest_path: str,
    *,
    user_id: str,
    task_id: str | None,
    tool_call_id: str | None = None,
    on_event=None,
    set_status=None,
    cancel_check: CancelCheck | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout_sec: float = 300.0,
) -> ToolResult:
    """Download a remote file into the workspace after explicit user approval."""
    from agent.approvals import request_user_approval

    try:
        url = _validate_download_url(url)
    except ValueError as exc:
        return ToolResult(False, str(exc))

    rel = _normalize_rel(dest_path or "")
    if not rel or rel == ".":
        return ToolResult(False, "必须指定保存路径 path（相对工程根，例如 downloads/icon.png）")
    if ".." in rel.split("/"):
        return ToolResult(False, "路径非法")

    # Prefer downloads/ ; still allow other non-escape relative paths under workspace
    try:
        target = _resolve_in_workspace(workspace, rel)
    except PermissionError as exc:
        return ToolResult(False, str(exc))

    if target.exists() and target.is_dir():
        return ToolResult(False, f"目标是目录，请指定文件路径: {rel}")

    if not task_id:
        return ToolResult(
            False,
            "download_file 必须在任务上下文中运行，且需要用户明确确认后才能下载",
        )

    decision = request_user_approval(
        job_id=task_id,
        user_id=user_id,
        kind="download_file",
        tool_call_id=tool_call_id,
        payload={
            "message": f"请求下载文件到 {rel}（请在对话确认卡片中选择允许或拒绝）",
            "url": url,
            "path": rel,
            "max_bytes": int(max_bytes),
        },
        on_event=on_event,
        set_status=set_status,
        timeout_sec=max(timeout_sec, 600.0),
        cancel_check=cancel_check,
    )
    if decision != "approved":
        reason = {
            "rejected": "用户拒绝了此次下载",
            "timeout": "等待用户确认超时（未下载）",
            "canceled": "任务已取消，下载中止",
        }.get(decision, f"未获批准: {decision}")
        return ToolResult(False, reason)

    try:
        import httpx
    except ImportError:
        return ToolResult(False, "缺少 httpx 依赖，请执行 pip install httpx")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
            if response.status_code >= 400:
                return ToolResult(
                    False,
                    f"下载失败 HTTP {response.status_code}: {url}",
                )
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                return ToolResult(
                    False,
                    f"文件过大（Content-Length={content_length}），上限 {max_bytes} 字节",
                )
            size = 0
            with target.open("wb") as out:
                for chunk in response.iter_bytes():
                    if cancel_check:
                        cancel_check()
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        out.close()
                        target.unlink(missing_ok=True)
                        return ToolResult(False, f"下载中止：超过大小上限 {max_bytes} 字节")
                    out.write(chunk)
        return ToolResult(True, f"已下载 {size} 字节 -> {rel}")
    except Exception as exc:
        if target.exists() and target.is_file():
            try:
                target.unlink()
            except OSError:
                pass
        if exc.__class__.__name__ == "CancellationRequested":
            raise
        return ToolResult(False, f"下载异常: {exc}")


def dispatch_tool(
    workspace: Path,
    user_id: str,
    project_id: str,
    name: str,
    tool_input: dict,
    cancel_check: CancelCheck | None = None,
    settings=None,
    on_event=None,
    task_id: str | None = None,
    tool_call_id: str | None = None,
    set_status=None,
) -> ToolResult:
    try:
        return _dispatch_tool_inner(
            workspace,
            user_id,
            project_id,
            name,
            tool_input or {},
            cancel_check=cancel_check,
            settings=settings,
            on_event=on_event,
            task_id=task_id,
            tool_call_id=tool_call_id,
            set_status=set_status,
        )
    except Exception as exc:
        # Avoid circular import with agent.loop.CancellationRequested
        if exc.__class__.__name__ in {
            "CancellationRequested",
            "ApprovalEventPersistenceError",
        }:
            raise
        # Tool arg / IO bugs must not kill the whole agent as "provider unavailable"
        return ToolResult(False, f"工具 {name} 执行异常: {exc}")


def _dispatch_tool_inner(
    workspace: Path,
    user_id: str,
    project_id: str,
    name: str,
    tool_input: dict,
    cancel_check: CancelCheck | None = None,
    settings=None,
    on_event=None,
    task_id: str | None = None,
    tool_call_id: str | None = None,
    set_status=None,
) -> ToolResult:
    if name == "list_dir":
        return list_dir(workspace, tool_input.get("path", "."))
    if name == "read_file":
        path = tool_input.get("path")
        if not path:
            return ToolResult(False, "缺少必填参数 path（相对工程根的文件路径）")
        return read_file(workspace, str(path))
    if name == "write_file":
        path = tool_input.get("path")
        if not path:
            return ToolResult(False, "缺少必填参数 path（例如 app/src/main/java/.../GameView.kt）")
        if "content" not in tool_input:
            return ToolResult(False, "缺少必填参数 content（文件完整内容）")
        return write_file(workspace, str(path), tool_input.get("content") or "")
    if name == "str_replace":
        path = tool_input.get("path")
        if not path:
            return ToolResult(False, "缺少必填参数 path")
        return str_replace(
            workspace,
            str(path),
            tool_input.get("old_string", ""),
            tool_input.get("new_string", ""),
            replace_all=bool(tool_input.get("replace_all", False)),
        )
    if name == "glob":
        return glob_files(
            workspace,
            tool_input.get("pattern", ""),
            tool_input.get("path", "app/src"),
        )
    if name == "grep":
        return grep_files(
            workspace,
            tool_input.get("query", ""),
            tool_input.get("path", "app/src"),
            case_insensitive=bool(tool_input.get("case_insensitive", False)),
            max_hits=int(tool_input.get("max_hits", 50) or 50),
        )
    if name == "run_gradle":
        return run_gradle(
            workspace,
            user_id,
            project_id,
            tool_input.get("task", "assembleDebug"),
            cancel_check=cancel_check,
        )
    if name == "web_search":
        api_key = ""
        if settings is not None:
            api_key = getattr(settings, "tavily_api_key", "") or ""
        return web_search(
            tool_input.get("query", ""),
            api_key=api_key,
            max_results=int(tool_input.get("max_results", 5) or 5),
            include_answer=bool(tool_input.get("include_answer", True)),
            search_depth=str(tool_input.get("search_depth", "basic") or "basic"),
        )
    if name == "download_file":
        return download_file(
            workspace,
            tool_input.get("url", ""),
            tool_input.get("path", ""),
            user_id=user_id,
            task_id=task_id,
            tool_call_id=tool_call_id,
            on_event=on_event,
            set_status=set_status,
            cancel_check=cancel_check,
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
        "name": "glob",
        "description": "按文件名模式查找可读文件。pattern 支持 * ?，例如 **/*.kt 或 strings.xml",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式"},
                "path": {
                    "type": "string",
                    "description": "搜索根路径，默认 app/src",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "在可读源码中按正则搜索内容，返回 path:line: 文本",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "正则或普通文本"},
                "path": {"type": "string", "description": "搜索根路径，默认 app/src"},
                "case_insensitive": {"type": "boolean"},
                "max_hits": {"type": "integer", "description": "最多返回条数，默认 50"},
            },
            "required": ["query"],
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
        "name": "str_replace",
        "description": "精确替换文件中的一段文本（优先于整文件写入）。old_string 必须在文件中唯一，除非 replace_all=true",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_file",
        "description": "写入工程内文件（覆盖整文件）。仅在新建文件或大范围重写时使用；小改动请用 str_replace",
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
        "description": "在工程目录执行 Gradle。常用 task: assembleDebug。每个任务结束前必须成功执行 assembleDebug",
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

WEB_SEARCH_TOOL_DEFINITION = {
    "name": "web_search",
    "description": (
        "使用 Tavily 在互联网上搜索实时信息（文档、API、错误解决方案、库用法等）。"
        "当本地工程信息不足、需要查最新资料时调用。返回摘要与若干条带 URL 的结果。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或自然语言问题"},
            "max_results": {
                "type": "integer",
                "description": "返回条数，1-10，默认 5",
            },
            "include_answer": {
                "type": "boolean",
                "description": "是否包含 Tavily 生成的简短摘要，默认 true",
            },
            "search_depth": {
                "type": "string",
                "enum": ["basic", "advanced"],
                "description": "basic 更快；advanced 更深入（消耗更多额度）",
            },
        },
        "required": ["query"],
    },
}

DOWNLOAD_FILE_TOOL_DEFINITION = {
    "name": "download_file",
    "description": (
        "从 HTTP/HTTPS URL 下载文件到工程目录。"
        "【强制】调用后任务会暂停，并在对话界面显示确认卡片（允许/拒绝）；"
        "仅当用户点击允许后才会真正下载。用户拒绝或超时则不会下载。"
        "不要改用纯文字索要权限——必须调用本工具才会出现确认卡片。"
        "优先保存到 downloads/ 下。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要下载的 http/https URL"},
            "path": {
                "type": "string",
                "description": "保存路径（相对工程根），例如 downloads/logo.png",
            },
        },
        "required": ["url", "path"],
    },
}


def get_tool_definitions(settings=None) -> list[dict]:
    tools = list(BASE_TOOL_DEFINITIONS)
    if settings is not None:
        max_retries = getattr(settings, "max_gradle_retries", 3)
        for tool in tools:
            if tool["name"] == "run_gradle":
                tool = dict(tool)
                tool["description"] = (
                    f"在工程目录执行 Gradle。常用 task: assembleDebug。"
                    f"编译失败后最多再尝试 {max_retries} 次修复构建。"
                )
                idx = next(i for i, t in enumerate(tools) if t["name"] == "run_gradle")
                tools[idx] = tool
                break
        if getattr(settings, "tavily_api_key", ""):
            tools.append(dict(WEB_SEARCH_TOOL_DEFINITION))
        tools.append(dict(DOWNLOAD_FILE_TOOL_DEFINITION))
    else:
        tools.append(dict(DOWNLOAD_FILE_TOOL_DEFINITION))
    return tools


TOOL_DEFINITIONS = BASE_TOOL_DEFINITIONS
