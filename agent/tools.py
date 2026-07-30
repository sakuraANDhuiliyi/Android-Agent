from __future__ import annotations

import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
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
from agent.processes import (
    CancelToken,
    cancel_process,
    run_command as _run_command,
)
from agent.project import new_build_id
from agent.safe_paths import resolve_workspace_path
from agent.tool_registry import (
    ToolSpec,
    get_anthropic_tool_definitions,
    register_tool,
)
from agent.tool_runtime import ToolContext, execute_tool
from agent.workspace import WorkspaceRepository

# Import repo / skill / subagent tools so they register with the global registry.
import agent.repo_tools  # noqa: F401
import agent.skill_tools  # noqa: F401
import agent.subagent_tools  # noqa: F401

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


@dataclass
class ToolResult:
    ok: bool
    output: Any
    error_type: str | None = None


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
    return resolve_workspace_path(workspace, rel)


def _is_allowed(rel: str, prefixes: tuple[str, ...]) -> bool:
    rel = _normalize_rel(rel)
    for prefix in prefixes:
        normalized = _normalize_rel(prefix)
        if prefix.endswith("/"):
            root = normalized.rstrip("/")
            if rel == root or rel.startswith(root + "/"):
                return True
        elif rel == normalized:
            return True
    return False


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
        try:
            roots = [resolve_workspace_path(workspace, root_rel)]
        except PermissionError:
            return

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
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            if any(part in IGNORE_DIR_NAMES for part in path.parts):
                continue
            try:
                rel = path.relative_to(workspace).as_posix()
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


# —— Web search ——

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _web_search_impl(
    query: str,
    *,
    api_key: str,
    max_results: int = 5,
    include_answer: bool = True,
    search_depth: str = "basic",
) -> ToolResult:
    """Search the web via Tavily Search API (core implementation)."""
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


def web_search(
    query: str,
    *,
    api_key: str,
    max_results: int = 5,
    include_answer: bool = True,
    search_depth: str = "basic",
) -> ToolResult:
    """Search the web via Tavily Search API (public API, no runtime approval)."""
    return _web_search_impl(
        query,
        api_key=api_key,
        max_results=max_results,
        include_answer=include_answer,
        search_depth=search_depth,
    )


def _handle_web_search(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    api_key = ""
    if ctx.settings is not None:
        api_key = getattr(ctx.settings, "tavily_api_key", "") or ""
    return _web_search_impl(
        tool_input.get("query", ""),
        api_key=api_key,
        max_results=int(tool_input.get("max_results", 5) or 5),
        include_answer=bool(tool_input.get("include_answer", True)),
        search_depth=str(tool_input.get("search_depth", "basic") or "basic"),
    )


# —— Download file ——


def _validate_download_url(url: str) -> str:
    """Validate download URL. Blocks non-http(s) and obvious SSRF targets."""
    import ipaddress
    from urllib.parse import urlparse

    raw = (url or "").strip()
    if not raw:
        raise ValueError("url 不能为空")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅允许 http/https 下载")
    if not parsed.hostname:
        raise ValueError("无效的 URL")
    host = parsed.hostname.lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".localhost"):
        raise ValueError("禁止下载内网/本机地址")
    # Block literal private/link-local/loopback IPs.
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise ValueError("禁止下载内网/本机地址")
    except ValueError as exc:
        if "禁止下载" in str(exc):
            raise
        # hostname is not an IP literal — still block common cloud metadata names.
        if host.startswith("169.254.") or host == "0.0.0.0":
            raise ValueError("禁止下载内网/本机地址") from None
    # Reject userinfo (cred stuffing / weird redirects).
    if parsed.username or parsed.password:
        raise ValueError("URL 不得包含用户名或密码")
    return raw


def _resolve_public_addresses(url: str) -> set[str]:
    """Resolve every address for a URL and reject mixed/private DNS answers."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"下载地址 DNS 解析失败: {host}: {exc}") from exc
    addresses = {str(record[4][0]) for record in records if record[4]}
    if not addresses:
        raise ValueError(f"下载地址 DNS 无有效结果: {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"禁止下载解析到内网/保留地址的 URL: {host}")
    return addresses


@contextmanager
def _pinned_http_stream(url: str, addresses: set[str]):
    """Create an httpx stream whose TCP backend uses a validated address."""
    import httpcore
    import httpx
    from httpcore._backends.sync import SyncBackend

    pinned_address = sorted(addresses)[0]

    class PinnedBackend:
        def __init__(self) -> None:
            self.backend = SyncBackend()

        def connect_tcp(
            self,
            host,
            port,
            timeout=None,
            local_address=None,
            socket_options=None,
        ):
            return self.backend.connect_tcp(
                pinned_address,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        def connect_unix_socket(self, path, timeout=None, socket_options=None):
            return self.backend.connect_unix_socket(
                path,
                timeout=timeout,
                socket_options=socket_options,
            )

        def sleep(self, seconds):
            return self.backend.sleep(seconds)

    transport = httpx.HTTPTransport()
    transport._pool = httpcore.ConnectionPool(  # type: ignore[attr-defined]
        network_backend=PinnedBackend(),
        retries=0,
    )
    with httpx.Client(
        transport=transport,
        follow_redirects=False,
        timeout=60.0,
    ) as client:
        with client.stream("GET", url) as response:
            yield response


def _download_file_impl(
    workspace: Path,
    url: str,
    dest_path: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    cancel_check: CancelCheck | None = None,
) -> ToolResult:
    """Download a remote file after approval has been granted."""
    try:
        url = _validate_download_url(url)
    except ValueError as exc:
        return ToolResult(False, str(exc))

    rel = _normalize_rel(dest_path or "")
    if not rel or rel == ".":
        return ToolResult(False, "必须指定保存路径 path（相对工程根，例如 downloads/icon.png）")
    if ".." in rel.split("/"):
        return ToolResult(False, "路径非法")

    try:
        target = _resolve_in_workspace(workspace, rel)
    except PermissionError as exc:
        return ToolResult(False, str(exc))

    if target.exists() and target.is_dir():
        return ToolResult(False, f"目标是目录，请指定文件路径: {rel}")

    try:
        import httpx
    except ImportError:
        return ToolResult(False, "缺少 httpx 依赖，请执行 pip install httpx")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_name(f".{target.name}.{os.getpid()}.part")
        # Follow redirects manually so each hop is re-validated (SSRF guard).
        current = url
        size = 0
        with temp_target.open("xb") as out:
            for _hop in range(5):
                before_addresses = _resolve_public_addresses(current)
                with _pinned_http_stream(current, before_addresses) as response:
                    after_addresses = _resolve_public_addresses(current)
                    if before_addresses != after_addresses:
                        raise ValueError("下载地址 DNS 结果在连接期间发生变化")
                    if response.status_code in {301, 302, 303, 307, 308}:
                        loc = response.headers.get("location")
                        if not loc:
                            raise ValueError("重定向缺少 Location")
                        from urllib.parse import urljoin

                        current = _validate_download_url(urljoin(current, loc))
                        continue
                    if response.status_code >= 400:
                        raise ValueError(
                            f"下载失败 HTTP {response.status_code}: {current}"
                        )
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > max_bytes:
                        raise ValueError(
                            f"文件过大（Content-Length={content_length}），"
                            f"上限 {max_bytes} 字节"
                        )
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    expected_types = {
                        ".apk": {
                            "application/vnd.android.package-archive",
                            "application/octet-stream",
                            "application/zip",
                        },
                        ".json": {
                            "application/json",
                            "text/json",
                            "text/plain",
                        },
                        ".png": {"image/png", "application/octet-stream"},
                        ".jpg": {"image/jpeg", "application/octet-stream"},
                        ".jpeg": {"image/jpeg", "application/octet-stream"},
                        ".webp": {"image/webp", "application/octet-stream"},
                        ".zip": {
                            "application/zip",
                            "application/octet-stream",
                            "application/x-zip-compressed",
                        },
                    }.get(target.suffix.lower())
                    if (
                        content_type
                        and expected_types
                        and content_type not in expected_types
                    ):
                        raise ValueError(
                            f"响应 Content-Type {content_type} 与目标 "
                            f"{target.suffix.lower()} 不匹配"
                        )
                    for chunk in response.iter_bytes():
                        if cancel_check:
                            cancel_check()
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError(
                                f"下载中止：超过大小上限 {max_bytes} 字节"
                            )
                        out.write(chunk)
                    break
            else:
                raise ValueError("重定向次数过多")
        if size <= 0:
            raise ValueError("下载结果为空文件")
        os.replace(temp_target, target)
        return ToolResult(True, f"已下载 {size} 字节 -> {rel}")
    except Exception as exc:
        temp_target = locals().get("temp_target")
        if isinstance(temp_target, Path) and temp_target.is_file():
            try:
                temp_target.unlink()
            except OSError:
                pass
        if exc.__class__.__name__ == "CancellationRequested":
            raise
        return ToolResult(False, f"下载异常: {exc}")
    finally:
        temp_target = locals().get("temp_target")
        if isinstance(temp_target, Path):
            temp_target.unlink(missing_ok=True)


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
    """Download a remote file into the workspace after explicit user approval.

    This public entry point retains the original approval behavior and is used by
    direct callers. The registry handler delegates to the core implementation.
    """
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

    return _download_file_impl(
        workspace,
        url,
        dest_path,
        max_bytes=max_bytes,
        cancel_check=cancel_check,
    )


def _handle_download_file(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    if not ctx.task_id:
        return ToolResult(
            False,
            "download_file 必须在任务上下文中运行，且需要用户明确确认后才能下载",
        )
    return _download_file_impl(
        ctx.workspace,
        tool_input.get("url", ""),
        tool_input.get("path", ""),
        max_bytes=int(tool_input.get("max_bytes", MAX_DOWNLOAD_BYTES) or MAX_DOWNLOAD_BYTES),
        cancel_check=ctx.cancel_check,
    )


# —— Gradle ——


def _gradle_process_key(user_id: str, project_id: str) -> str:
    return f"gradle:{user_id}:{project_id}"


def cancel_gradle(user_id: str, project_id: str) -> bool:
    """Cancel the active Gradle process for a user/project."""
    return cancel_process(_gradle_process_key(user_id, project_id))


def _cancel_token_from_check(
    cancel_check: CancelCheck | None, stop_event: threading.Event
) -> CancelToken:
    """Bridge the legacy cancel_check callable to a CancelToken."""
    token = CancelToken()
    if cancel_check is None:
        return token

    def watcher() -> None:
        while not stop_event.is_set():
            try:
                cancel_check()
            except Exception:
                token.cancel()
                return
            time.sleep(0.2)

    threading.Thread(target=watcher, daemon=True).start()
    return token


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

    env: dict[str, str] = {}
    if "JAVA_HOME" not in os.environ and STUDIO_JBR.is_dir():
        env["JAVA_HOME"] = str(STUDIO_JBR)

    sdk = ensure_local_properties(workspace)
    if sdk:
        env.setdefault("ANDROID_HOME", str(sdk))
        env.setdefault("ANDROID_SDK_ROOT", str(sdk))
    elif "ANDROID_HOME" not in os.environ:
        return ToolResult(
            False,
            "未找到 Android SDK。请安装 Android Studio，或设置环境变量 ANDROID_HOME，"
            "例如: export ANDROID_HOME=$HOME/Library/Android/sdk",
        )

    cmd = [str(gradlew), task, "--no-daemon", "--stacktrace"]
    stop_event = threading.Event()
    token = _cancel_token_from_check(cancel_check, stop_event)
    try:
        result = _run_command(
            cmd,
            cwd=workspace,
            workspace=workspace,
            env=env,
            timeout_seconds=15 * 60,
            cancel_token=token,
            process_key=_gradle_process_key(user_id, project_id),
            combine_output=True,
            task_log_path=log_file,
        )
    finally:
        stop_event.set()

    if not result.ok:
        log_body = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
        tail = summarize_build_log(log_body)
        return ToolResult(
            False,
            f"Gradle 失败 (exit {result.returncode})\n日志: {log_file}\n\n--- 关键日志摘要 ---\n{tail}",
            error_type=result.error_type,
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


def _handle_run_gradle(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    return run_gradle(
        ctx.workspace,
        ctx.user_id,
        ctx.project_id,
        tool_input.get("task", "assembleDebug"),
        cancel_check=ctx.cancel_check,
    )


# —— run_command ——


def run_command(
    workspace: Path,
    argv: list[str],
    *,
    cwd: str | None = None,
    combine_output: bool = False,
    timeout_seconds: float = 300.0,
    cancel_check: CancelCheck | None = None,
    task_log_path: Path | None = None,
) -> ToolResult:
    """Run a non-interactive command inside the workspace.

    This is the public entry point; it does not perform runtime approval. The
    registry handler is used by the agent runtime after approval.
    """
    stop_event = threading.Event()
    token = _cancel_token_from_check(cancel_check, stop_event)
    try:
        result = _run_command(
            argv,
            cwd=cwd,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            cancel_token=token,
            combine_output=combine_output,
            task_log_path=task_log_path,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "CancellationRequested":
            raise
        return ToolResult(False, f"run_command 异常: {exc}", error_type=exc.__class__.__name__)
    finally:
        stop_event.set()

    output = result.stdout
    if not combine_output and result.stderr:
        output = f"{output}\n\n--- stderr ---\n{result.stderr}".strip()
    if result.truncated:
        output += "\n... (输出已截断，完整内容见任务日志)"
    if not result.ok:
        return ToolResult(
            False,
            f"命令退出码 {result.returncode}\n{output}",
            error_type=result.error_type,
        )
    return ToolResult(True, output)


def _handle_run_command(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    argv = tool_input.get("argv", [])
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return ToolResult(False, "argv 必须是字符串数组")
    if not argv:
        return ToolResult(False, "argv 不能为空")
    return run_command(
        ctx.workspace,
        argv,
        cwd=tool_input.get("cwd"),
        combine_output=bool(tool_input.get("combine_output", False)),
        timeout_seconds=float(tool_input.get("timeout_seconds", 300.0) or 300.0),
        cancel_check=ctx.cancel_check,
    )


def _handle_git_status(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    repo = WorkspaceRepository(ctx.user_id, ctx.project_id)
    return ToolResult(True, repo.git_status())


def _handle_git_diff(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    repo = WorkspaceRepository(ctx.user_id, ctx.project_id)
    return ToolResult(
        True,
        repo.git_diff(
            path=tool_input.get("path"),
            staged=bool(tool_input.get("staged", False)),
            from_revision=tool_input.get("from_revision"),
            to_revision=tool_input.get("to_revision"),
        ),
    )


def _handle_git_log(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    repo = WorkspaceRepository(ctx.user_id, ctx.project_id)
    max_count = int(tool_input.get("max_count", 20) or 20)
    return ToolResult(True, repo.git_log(max_count=max_count))


# —— Registry handlers for simple workspace tools ——


def _handle_list_dir(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    return list_dir(ctx.workspace, tool_input.get("path", "."))


def _handle_read_file(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    path = tool_input.get("path")
    if not path:
        return ToolResult(False, "缺少必填参数 path（相对工程根的文件路径）")
    return read_file(ctx.workspace, str(path))


def _handle_write_file(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    path = tool_input.get("path")
    if not path:
        return ToolResult(False, "缺少必填参数 path（例如 app/src/main/java/.../GameView.kt）")
    if "content" not in tool_input:
        return ToolResult(False, "缺少必填参数 content（文件完整内容）")
    return write_file(ctx.workspace, str(path), tool_input.get("content") or "")


def _handle_str_replace(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    path = tool_input.get("path")
    if not path:
        return ToolResult(False, "缺少必填参数 path")
    return str_replace(
        ctx.workspace,
        str(path),
        tool_input.get("old_string", ""),
        tool_input.get("new_string", ""),
        replace_all=bool(tool_input.get("replace_all", False)),
    )


def _handle_glob(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    return glob_files(
        ctx.workspace,
        tool_input.get("pattern", ""),
        tool_input.get("path", "app/src"),
    )


def _handle_grep(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolResult:
    return grep_files(
        ctx.workspace,
        tool_input.get("query", ""),
        tool_input.get("path", "app/src"),
        case_insensitive=bool(tool_input.get("case_insensitive", False)),
        max_hits=int(tool_input.get("max_hits", 50) or 50),
    )


# —— Register built-in tools ——

register_tool(
    ToolSpec(
        name="list_dir",
        description="列出工程内目录内容。path 为相对工程根的路径，默认为 .",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对路径，例如 app/src/main",
                }
            },
            "required": [],
        },
        category="workspace",
        read_only=True,
        handler=_handle_list_dir,
    )
)

register_tool(
    ToolSpec(
        name="glob",
        description="按文件名模式查找可读文件。pattern 支持 * ?，例如 **/*.kt 或 strings.xml",
        input_schema={
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
        category="workspace",
        read_only=True,
        handler=_handle_glob,
    )
)

register_tool(
    ToolSpec(
        name="grep",
        description="在可读源码中按正则搜索内容，返回 path:line: 文本",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "正则或普通文本"},
                "path": {"type": "string", "description": "搜索根路径，默认 app/src"},
                "case_insensitive": {"type": "boolean"},
                "max_hits": {"type": "integer", "description": "最多返回条数，默认 50"},
            },
            "required": ["query"],
        },
        category="workspace",
        read_only=True,
        handler=_handle_grep,
    )
)

register_tool(
    ToolSpec(
        name="read_file",
        description="读取工程内文本文件",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对路径，例如 app/src/main/java/.../MainActivity.kt",
                }
            },
            "required": ["path"],
        },
        category="workspace",
        read_only=True,
        handler=_handle_read_file,
    )
)

register_tool(
    ToolSpec(
        name="str_replace",
        description="精确替换文件中的一段文本（优先于整文件写入）。old_string 必须在文件中唯一，除非 replace_all=true",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        category="workspace",
        workspace_write=True,
        replay_policy="requires_approval_on_recovery",
        handler=_handle_str_replace,
    )
)

register_tool(
    ToolSpec(
        name="write_file",
        description="写入工程内文件（覆盖整文件）。仅在新建文件或大范围重写时使用；小改动请用 str_replace",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        category="workspace",
        workspace_write=True,
        replay_policy="requires_approval_on_recovery",
        handler=_handle_write_file,
    )
)

register_tool(
    ToolSpec(
        name="run_gradle",
        description="在工程目录执行 Gradle。常用 task: assembleDebug。",
        input_schema={
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
        category="build",
        starts_process=True,
        destructive=False,
        default_timeout_seconds=900.0,
        replay_policy="requires_approval_on_recovery",
        handler=_handle_run_gradle,
    )
)

register_tool(
    ToolSpec(
        name="web_search",
        description=(
            "使用 Tavily 在互联网上搜索实时信息（文档、API、错误解决方案、库用法等）。"
            "当本地工程信息不足、需要查最新资料时调用。返回摘要与若干条带 URL 的结果。"
        ),
        input_schema={
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
        category="network",
        network_access=True,
        approval_kind="network",
        handler=_handle_web_search,
    )
)

register_tool(
    ToolSpec(
        name="download_file",
        description=(
            "从 HTTP/HTTPS URL 下载文件到工程目录。"
            "【强制】调用后任务会暂停，并在对话界面显示确认卡片（允许/拒绝）；"
            "仅当用户点击允许后才会真正下载。用户拒绝或超时则不会下载。"
            "不要改用纯文字索要权限——必须调用本工具才会出现确认卡片。"
            "优先保存到 downloads/ 下。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要下载的 http/https URL"},
                "path": {
                    "type": "string",
                    "description": "保存路径（相对工程根），例如 downloads/logo.png",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": f"最大下载字节数，默认 {MAX_DOWNLOAD_BYTES}",
                },
            },
            "required": ["url", "path"],
        },
        category="network",
        network_access=True,
        workspace_write=True,
        destructive=True,
        default_timeout_seconds=600.0,
        approval_kind="download_file",
        handler=_handle_download_file,
    )
)

register_tool(
    ToolSpec(
        name="run_command",
        description=(
            "在工程目录内运行非交互式命令。使用 argv 数组执行，不经过 shell。"
            "需要用户审批后才会执行。完整输出可写入任务日志，模型输出可能截断。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "命令参数数组，例如 [\"python3\", \"-c\", \"print(1)\"]",
                },
                "cwd": {
                    "type": "string",
                    "description": "相对 workspace 的工作目录，默认为 workspace 根",
                },
                "combine_output": {
                    "type": "boolean",
                    "description": "是否合并 stdout 和 stderr，默认 false",
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "超时秒数，默认 300",
                },
            },
            "required": ["argv"],
        },
        category="process",
        starts_process=True,
        destructive=True,
        default_timeout_seconds=300.0,
        approval_kind="process",
        handler=_handle_run_command,
    )
)

register_tool(
    ToolSpec(
        name="git_status",
        description="返回当前 workspace 的 Git 状态，包括分支、dirty、untracked、modified、renamed、deleted 文件。",
        input_schema={
            "type": "object",
            "properties": {},
        },
        category="git",
        read_only=True,
        default_timeout_seconds=30.0,
        handler=_handle_git_status,
    )
)

register_tool(
    ToolSpec(
        name="git_diff",
        description="返回当前 workspace 的 Git diff。可指定 path、staged、from_revision/to_revision。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对路径，可选"},
                "staged": {"type": "boolean", "description": "是否查看 staged diff，默认 false"},
                "from_revision": {"type": "string", "description": "起始 revision，可选"},
                "to_revision": {"type": "string", "description": "目标 revision，可选"},
            },
        },
        category="git",
        read_only=True,
        default_timeout_seconds=30.0,
        handler=_handle_git_diff,
    )
)

register_tool(
    ToolSpec(
        name="git_log",
        description="返回当前 workspace 的 Git 提交历史。",
        input_schema={
            "type": "object",
            "properties": {
                "max_count": {"type": "integer", "description": "最大返回条数，默认 20"},
            },
        },
        category="git",
        read_only=True,
        default_timeout_seconds=30.0,
        handler=_handle_git_log,
    )
)


# —— Public dispatch and schema APIs ——


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
    recovery_replays: list[dict[str, Any]] | None = None,
    recovery_mode: bool = False,
    run_mode: str = "workspace",
) -> ToolResult:
    """Dispatch a built-in tool through the unified Tool Runtime."""
    try:
        return execute_tool(
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
            recovery_replays=recovery_replays,
            recovery_mode=recovery_mode,
            run_mode=run_mode,
        )
    except Exception as exc:
        if exc.__class__.__name__ in {
            "CancellationRequested",
            "ApprovalEventPersistenceError",
        }:
            raise
        return ToolResult(False, f"工具 {name} 执行异常: {exc}")


def get_tool_definitions(settings=None) -> list[dict]:
    """Return Anthropic-style tool definitions projected from the registry."""
    tools = get_anthropic_tool_definitions(settings)
    if settings is None or not getattr(settings, "tavily_api_key", ""):
        tools = [tool for tool in tools if tool["name"] != "web_search"]
    return tools


# Compatibility constants for callers that imported them directly.
BASE_TOOL_DEFINITIONS = [t for t in get_tool_definitions() if t["name"] != "download_file"]
WEB_SEARCH_TOOL_DEFINITION = next(
    (t for t in get_anthropic_tool_definitions() if t["name"] == "web_search"),
    {
        "name": "web_search",
        "description": "使用 Tavily 在互联网上搜索实时信息",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "include_answer": {"type": "boolean"},
                "search_depth": {"type": "string"},
            },
            "required": ["query"],
        },
    },
)
DOWNLOAD_FILE_TOOL_DEFINITION = next(
    (t for t in get_anthropic_tool_definitions() if t["name"] == "download_file"),
    {
        "name": "download_file",
        "description": "从 HTTP/HTTPS URL 下载文件到工程目录",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["url", "path"],
        },
    },
)
TOOL_DEFINITIONS = get_tool_definitions
