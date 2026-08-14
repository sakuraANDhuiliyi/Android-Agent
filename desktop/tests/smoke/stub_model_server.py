#!/usr/bin/env python3
"""Local OpenAI-compatible streaming stub for the Electron smoke test.

Serves POST /chat/completions with SSE chunks so the real agent service and
Electron desktop run end-to-end without any external network access.
"""
import json
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9477
MODEL = "deepseek-v4-pro"

MARKDOWN_ANSWER = (
    "## 缓存方案对比\n\n"
    "| 方案 | 命中延迟 | 适用场景 |\n"
    "| --- | --- | --- |\n"
    "| LruCache | 低 | 图片/位图缓存 |\n"
    "| Room | 中 | 结构化数据持久化 |\n\n"
    "**结论**：优先使用 `LruCache`，配合 `Bitmap` 解码参数。\n\n"
    "```kotlin\n"
    "val cache = LruCache<String, Bitmap>(8 * 1024 * 1024)\n"
    "```\n\n"
    "以上。"
)

COMMENTARY = "我先写入文件，再汇报结果。"

FINAL_AFTER_TOOL = (
    "已修复：`app/src/main/res/values/strings.xml` 中的 `smoke_label` "
    "已更新为 **hello-smoke-v2**。"
)

GENERIC = "收到，这是冒烟测试的第三轮回复：流式、折叠与审查改动均正常。"

NEW_STRINGS = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<resources>\n"
    '    <string name="smoke_label">hello-smoke-v2</string>\n'
    '    <string name="app_name">Smoke App</string>\n'
    "</resources>\n"
)

TOOL_ARGS = json.dumps(
    {"path": "app/src/main/res/values/strings.xml", "content": NEW_STRINGS},
    ensure_ascii=False,
)


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def chunk(delta, finish=None):
    return sse(
        {
            "id": "chatcmpl-smoke",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


def usage_chunk():
    return sse(
        {
            "id": "chatcmpl-smoke",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": MODEL,
            "choices": [],
            "usage": {
                "prompt_tokens": 32,
                "completion_tokens": 64,
                "total_tokens": 96,
            },
        }
    )


def chunks_of(text, size=10):
    return [text[i : i + size] for i in range(0, len(text), size)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path.rstrip("/") != "/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    last_user = content
                elif isinstance(content, list):
                    last_user = "".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                break
        has_tool = any(m.get("role") == "tool" for m in messages)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        self.wfile.write(chunk({"role": "assistant", "content": ""}))

        if has_tool:
            for frag in chunks_of(FINAL_AFTER_TOOL, 8):
                time.sleep(0.02)
                self.wfile.write(chunk({"content": frag}))
            self.wfile.write(chunk({}, finish="stop"))
        elif re.search("修改|修复|写入|更新", last_user):
            for frag in chunks_of(COMMENTARY, 6):
                time.sleep(0.02)
                self.wfile.write(chunk({"content": frag}))
            self.wfile.write(
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_smoke_001",
                                "type": "function",
                                "function": {"name": "write_file", "arguments": ""},
                            }
                        ]
                    }
                )
            )
            for frag in chunks_of(TOOL_ARGS, 24):
                time.sleep(0.01)
                self.wfile.write(
                    chunk(
                        {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": frag}}
                            ]
                        }
                    )
                )
            self.wfile.write(chunk({}, finish="tool_calls"))
        elif re.search("表格|对比|markdown|流式|缓存", last_user):
            for frag in chunks_of(MARKDOWN_ANSWER, 8):
                time.sleep(0.02)
                self.wfile.write(chunk({"content": frag}))
            self.wfile.write(chunk({}, finish="stop"))
        else:
            for frag in chunks_of(GENERIC, 8):
                time.sleep(0.02)
                self.wfile.write(chunk({"content": frag}))
            self.wfile.write(chunk({}, finish="stop"))

        if include_usage:
            self.wfile.write(usage_chunk())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"stub model server on {PORT}", flush=True)
    server.serve_forever()
