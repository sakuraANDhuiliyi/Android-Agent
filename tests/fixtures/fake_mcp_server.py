#!/usr/bin/env python3
"""Local fake MCP stdio server for tests (no network).

Speaks JSON-RPC over newline-delimited stdin/stdout:
initialize, tools/list, tools/call.

Env:
  FAKE_MCP_MODE=normal|timeout|crash|secret
  FAKE_MCP_SECRET=...  (echoed only in tool result when mode=secret — for redaction tests)
  FAKE_MCP_TOOLS_VERSION=1|2  (schema refresh)
"""
from __future__ import annotations

import json
import os
import sys
import time


def _tools_v1():
    return [
        {
            "name": "echo",
            "description": "Echo a message",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
        {
            "name": "add",
            "description": "Add two numbers",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        },
    ]


def _tools_v2():
    tools = _tools_v1()
    tools.append(
        {
            "name": "ping",
            "description": "Return pong",
            "inputSchema": {"type": "object", "properties": {}},
        }
    )
    return tools


def _respond(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = os.environ.get("FAKE_MCP_MODE", "normal")
    version = os.environ.get("FAKE_MCP_TOOLS_VERSION", "1")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "notifications/initialized":
            continue

        if method == "initialize":
            _respond(
                msg_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "1.0"},
                },
            )
            continue

        if method == "tools/list":
            tools = _tools_v2() if version == "2" else _tools_v1()
            _respond(msg_id, {"tools": tools})
            continue

        if method == "tools/call":
            if mode == "timeout":
                time.sleep(60)
            if mode == "crash":
                sys.stderr.write("fake crash\n")
                sys.stderr.flush()
                os._exit(1)
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                text = str(args.get("message", ""))
                if mode == "secret":
                    secret = os.environ.get("FAKE_MCP_SECRET", "")
                    text = f"{text} secret={secret}"
                _respond(
                    msg_id,
                    {
                        "content": [{"type": "text", "text": text}],
                        "isError": False,
                    },
                )
            elif name == "add":
                total = float(args.get("a", 0)) + float(args.get("b", 0))
                _respond(
                    msg_id,
                    {
                        "content": [{"type": "text", "text": str(total)}],
                        "isError": False,
                    },
                )
            elif name == "ping":
                _respond(
                    msg_id,
                    {"content": [{"type": "text", "text": "pong"}], "isError": False},
                )
            else:
                _respond(msg_id, error={"code": -32601, "message": f"unknown tool {name}"})
            continue

        if msg_id is not None:
            _respond(msg_id, error={"code": -32601, "message": f"unknown method {method}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
