"""Reproduce the dangling tool_call 400: rebuild messages as of 08:00:01."""
import sys
import json
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.conversation_context import build_openai_messages

DB = Path("/Users/sakura/Android Agent/data/agent.db")
CONV = "240521e1670d"
AS_OF = 1786838401  # 2026-08-16 08:00:01 local

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """
    SELECT id, event_type, payload_json, created_at
    FROM conversation_events
    WHERE conversation_id = ?
      AND created_at < ?
    ORDER BY seq
    """,
    (CONV, AS_OF),
).fetchall()

events = []
for row in rows:
    payload = row["payload_json"]
    try:
        payload = json.loads(payload) if isinstance(payload, str) else payload
    except Exception:
        payload = {}
    events.append(
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "payload": payload or {},
        }
    )

print(f"events as of 08:00:01: {len(events)}")
types = {}
for e in events:
    types[e["event_type"]] = types.get(e["event_type"], 0) + 1
print("types:", types)

messages = build_openai_messages(events, current_user_prompt="为什么失败")
print(f"\nmessages: {len(messages)}")
pending_tool_calls = []
for i, m in enumerate(messages):
    tc = m.get("tool_calls")
    if tc:
        # check followed-by tool messages
        followed = set()
        j = i + 1
        while j < len(messages) and messages[j].get("role") == "tool":
            followed.add(messages[j].get("tool_call_id"))
            j += 1
        ids = [c["id"] for c in tc]
        dangling = [x for x in ids if x not in followed]
        status = f"OK (followed)" if not dangling else f"DANGLING {dangling}"
        print(f"[{i}] assistant tool_calls {ids} -> {status}")
        if dangling:
            pending_tool_calls.append((i, dangling))
    elif m.get("role") == "tool":
        pass

print("\nDANGLING FOUND:" if pending_tool_calls else "\nNo dangling tool_calls — filter worked.")
for i, ids in pending_tool_calls:
    print(" at message", i, ids)
    print(" message:", json.dumps(messages[i], ensure_ascii=False)[:400])
