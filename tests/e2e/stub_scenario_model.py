#!/usr/bin/env python3
"""Scenario-scripted, OpenAI-compatible streaming stub model.

Loads E2E scenario scripts from a scenarios directory (JSON) and replays
them, so the real agent service, the Electron desktop app and the Android
fixture tests all drive the exact same backend behaviour.

Protocol
--------
- The test driver embeds a ``[[scenario_id]]`` marker in the user prompt.
- The step index for the current model call is derived statelessly from
  the request messages: it equals the number of assistant messages after
  the last user message (each step consumes exactly one model call).
- Step types::

    {"type": "tool",  "text": "optional narration",
     "calls": [{"name": "write_file", "arguments": {...}}],
     "delay_ms": 0, "chunk": 24}          -> finish_reason "tool_calls"
    {"type": "final", "text": "final answer",
     "delay_ms": 0, "chunk": 8}           -> finish_reason "stop"

  Steps beyond the scripted list fall back to a default final answer so an
  under-scripted scenario degrades instead of hanging the agent loop.

Environment
-----------
- ``AGENT_E2E_STUB_PORT``     listen port (default 9478)
- ``AGENT_E2E_SCENARIO_DIR``  scenarios directory
  (default: <repo>/tests/e2e/scenarios)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("AGENT_E2E_STUB_PORT", "9478"))
MODEL = "deepseek-v4-pro"
DEFAULT_SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

MARKER_RE = re.compile(r"\[\[\s*([0-9A-Za-z_.-]+)\s*\]\]")
DEFAULT_FINAL = "已按场景脚本完成全部步骤。"

SCENARIOS: dict[str, dict] = {}


def load_scenarios(directory: Path | None = None) -> dict[str, dict]:
    global SCENARIOS
    scenario_dir = directory or Path(
        os.environ.get("AGENT_E2E_SCENARIO_DIR") or DEFAULT_SCENARIO_DIR
    )
    loaded: dict[str, dict] = {}
    if scenario_dir.is_dir():
        for path in sorted(scenario_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            scenario_id = str(data.get("id") or path.stem)
            if isinstance(data.get("steps"), list):
                loaded[scenario_id] = data
    SCENARIOS = loaded
    return loaded


def sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def chunk(delta: dict, finish: str | None = None) -> bytes:
    return sse(
        {
            "id": "chatcmpl-e2e",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


def usage_chunk() -> bytes:
    return sse(
        {
            "id": "chatcmpl-e2e",
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


def chunks_of(text: str, size: int = 10) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def last_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return ""
    return ""


def user_marker_text(messages: list[dict]) -> str:
    """Text of the most recent user message bearing a scenario marker.

    The agent's honesty nudge appends synthetic user messages after the
    scripted ones; those carry no marker and must not reset the scenario.
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            continue
        if MARKER_RE.search(text):
            return text
    return ""


def scenario_for(messages: list[dict]) -> dict | None:
    match = MARKER_RE.search(user_marker_text(messages))
    if not match:
        return None
    return SCENARIOS.get(match.group(1))


def round_step_index(messages: list[dict]) -> int:
    """Number of assistant model calls since the marker-bearing user turn."""
    start = -1
    for index, message in enumerate(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        if isinstance(content, list):
            text = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if MARKER_RE.search(text or ""):
            start = index
    return sum(
        1 for message in messages[start + 1 :] if message.get("role") == "assistant"
    )


def dangling_tool_call_ids(messages: list[dict]) -> list[str]:
    dangling: list[str] = []
    for index, message in enumerate(messages):
        calls = message.get("tool_calls")
        if message.get("role") != "assistant" or not calls:
            continue
        answered: set[str] = set()
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool":
            answered.add(messages[cursor].get("tool_call_id"))
            cursor += 1
        dangling.extend(call.get("id") for call in calls if call.get("id") not in answered)
    return dangling


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.rstrip("/") == "/__scenarios":
            payload = json.dumps(
                {"scenarios": sorted(SCENARIOS.keys()), "model": MODEL}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path.rstrip("/") != "/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        dangling = dangling_tool_call_ids(messages)
        if dangling:
            payload = json.dumps(
                {
                    "error": {
                        "message": (
                            "An assistant message with 'tool_calls' must be "
                            "followed by tool messages responding to each "
                            "'tool_call_id' (dangling: "
                            f"{', '.join(str(item) for item in dangling)})"
                        ),
                        "type": "invalid_request_error",
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        scenario = scenario_for(messages)
        if scenario is None:
            step = {"type": "final", "text": DEFAULT_FINAL}
        else:
            steps = scenario.get("steps") or []
            index = round_step_index(messages)
            if 0 <= index < len(steps):
                step = steps[index]
            elif steps and steps[-1].get("type") == "final":
                # The agent's honesty check re-prompts after a final answer
                # that changed no files; replay that answer instead of a
                # generic fallback so the conversation stays coherent.
                step = steps[-1]
            else:
                step = {"type": "final", "text": DEFAULT_FINAL}
            if not isinstance(step, dict) or step.get("type") not in {"tool", "final"}:
                step = {"type": "final", "text": DEFAULT_FINAL}

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        self.wfile.write(chunk({"role": "assistant", "content": ""}))

        delay_ms = float(step.get("delay_ms") or 0)
        if delay_ms:
            time.sleep(delay_ms / 1000.0)

        if step.get("type") == "final":
            text = str(step.get("text") or DEFAULT_FINAL)
            chunk_size = max(1, int(step.get("chunk") or 8))
            for fragment in chunks_of(text, chunk_size):
                time.sleep(0.01)
                self.wfile.write(chunk({"content": fragment}))
            self.wfile.write(chunk({}, finish="stop"))
        else:
            narration = str(step.get("text") or "")
            if narration:
                for fragment in chunks_of(narration, 8):
                    time.sleep(0.01)
                    self.wfile.write(chunk({"content": fragment}))
            calls = step.get("calls") or []
            for position, call in enumerate(calls):
                name = str(call.get("name") or "")
                arguments = json.dumps(call.get("arguments") or {}, ensure_ascii=False)
                self.wfile.write(
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": position,
                                    "id": f"call_e2e_{scenario_key(scenario)}_{round_step_index(messages)}_{position}",
                                    "type": "function",
                                    "function": {"name": name, "arguments": ""},
                                }
                            ]
                        }
                    )
                )
                for fragment in chunks_of(arguments, 24):
                    time.sleep(0.005)
                    self.wfile.write(
                        chunk(
                            {
                                "tool_calls": [
                                    {"index": position, "function": {"arguments": fragment}}
                                ]
                            }
                        )
                    )
            self.wfile.write(chunk({}, finish="tool_calls"))

        if include_usage:
            self.wfile.write(usage_chunk())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def scenario_key(scenario: dict | None) -> str:
    if not scenario:
        return "none"
    return str(scenario.get("id") or "none")[-24:]


def main() -> int:
    scenarios = load_scenarios()
    if not scenarios:
        print(f"no scenarios found in {DEFAULT_SCENARIO_DIR}", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"scenario stub model on {PORT} ({len(scenarios)} scenarios)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
