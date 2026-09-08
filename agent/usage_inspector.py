"""Aggregations for the Usage Inspector (turn / conversation / project)."""

from __future__ import annotations

import json
import time
from typing import Any

from agent import pricing
from agent.conversation_events import ConversationEventStore, ConversationEventType

_USAGE_TYPES = (ConversationEventType.USAGE,)
_TOOL_CALL_TYPES = (ConversationEventType.TOOL_CALL,)


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _num(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _cost_display(value: float | None) -> dict[str, Any]:
    if value is None:
        return {"cost_usd": None, "cost_available": False}
    return {"cost_usd": round(value, 4), "cost_available": True}


def _duration(turn: dict[str, Any], fallback_start: float | None, fallback_end: float | None) -> float | None:
    started = turn.get("started_at") or fallback_start
    finished = turn.get("finished_at") or fallback_end
    if started is None:
        return None
    end = finished if finished is not None else time.time()
    return max(0.0, float(end) - float(started))


class _TurnAccumulator:
    def __init__(self) -> None:
        self.input_tokens = 0.0
        self.output_tokens = 0.0
        self.cached_tokens = 0.0
        self.cache_creation_tokens = 0.0
        self.tool_calls = 0
        self.cost_usd: float | None = 0.0
        self.cost_available = True
        self.models: list[str] = []
        self.providers: list[str] = []
        self.first_event_at: float | None = None
        self.last_event_at: float | None = None

    def add_usage(self, payload: dict[str, Any], created_at: float) -> None:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else payload
        model = payload.get("model")
        self.input_tokens += _num(usage.get("input_tokens"))
        self.output_tokens += _num(usage.get("output_tokens"))
        self.cached_tokens += _num(usage.get("cached_tokens"))
        self.cache_creation_tokens += _num(usage.get("cache_creation_tokens"))
        if model and model not in self.models:
            self.models.append(str(model))
        provider = payload.get("provider")
        if provider and provider not in self.providers:
            self.providers.append(str(provider))
        event_cost = pricing.estimate_cost(
            model,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_tokens=usage.get("cached_tokens"),
            cache_creation_tokens=usage.get("cache_creation_tokens"),
        )
        if event_cost is None:
            self.cost_available = False
        elif self.cost_available:
            self.cost_usd += event_cost
        self.first_event_at = created_at if self.first_event_at is None else min(self.first_event_at, created_at)
        self.last_event_at = created_at if self.last_event_at is None else max(self.last_event_at, created_at)

    def add_tool_call(self) -> None:
        self.tool_calls += 1

    def usage_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cached_tokens": int(self.cached_tokens),
            "cache_creation_tokens": int(self.cache_creation_tokens),
            "total_tokens": int(self.input_tokens + self.output_tokens),
        }


def _summarize_turn(
    turn: dict[str, Any],
    acc: _TurnAccumulator | None,
) -> dict[str, Any]:
    acc = acc or _TurnAccumulator()
    duration = _duration(turn, acc.first_event_at, acc.last_event_at)
    cached_ratio = None
    if acc.input_tokens > 0:
        cached_ratio = round(min(1.0, acc.cached_tokens / acc.input_tokens), 4)
    return {
        "turn_id": turn.get("id"),
        "task_id": turn.get("task_id"),
        "status": turn.get("status"),
        "models": acc.models or ([turn.get("model")] if turn.get("model") else []),
        "providers": acc.providers or ([turn.get("provider")] if turn.get("provider") else []),
        "started_at": turn.get("started_at") or acc.first_event_at,
        "finished_at": turn.get("finished_at") or acc.last_event_at,
        "duration_seconds": round(duration, 1) if duration is not None else None,
        **acc.usage_dict(),
        "cached_ratio": cached_ratio,
        "tool_calls": acc.tool_calls,
        **_cost_display(acc.cost_usd if acc.cost_available else None),
    }


def _totals_from_turns(turns: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "turns": len(turns),
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tokens": 0,
        "tool_calls": 0,
        "duration_seconds": 0.0,
        "cost_usd": 0.0,
        "cost_available": True,
    }
    for turn in turns:
        for key in ("input_tokens", "output_tokens", "cached_tokens", "cache_creation_tokens", "total_tokens", "tool_calls"):
            totals[key] += int(turn.get(key) or 0)
        if turn.get("duration_seconds") is not None:
            totals["duration_seconds"] += float(turn["duration_seconds"])
        if turn.get("cost_usd") is None:
            totals["cost_available"] = False
        elif totals["cost_available"]:
            totals["cost_usd"] += float(turn["cost_usd"])
    if not totals["cost_available"]:
        totals["cost_usd"] = None
    totals["duration_seconds"] = round(totals["duration_seconds"], 1)
    totals["cost_usd"] = round(totals["cost_usd"], 4) if totals["cost_usd"] is not None else None
    return totals


def conversation_usage(
    store: Any,
    user_id: str,
    conversation_id: str,
    *,
    turn_limit: int = 100,
) -> dict[str, Any] | None:
    """Per-turn usage breakdown for one conversation."""
    event_store = ConversationEventStore(store)
    if not event_store.has_conversation(conversation_id, user_id):
        return None
    with store._connect() as conn:  # noqa: SLF001
        turn_rows = conn.execute(
            """SELECT * FROM conversation_turns
               WHERE conversation_id=? AND user_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (conversation_id, user_id, max(1, min(turn_limit, 500))),
        ).fetchall()
        event_rows = conn.execute(
            """SELECT turn_id, event_type, payload_json, created_at
               FROM conversation_events
               WHERE conversation_id=?
                 AND event_type IN ('usage','tool_call')""",
            (conversation_id,),
        ).fetchall()
    turns = {row["id"]: dict(row) for row in turn_rows}
    accumulators: dict[str | None, _TurnAccumulator] = {}
    for row in event_rows:
        acc = accumulators.setdefault(row["turn_id"], _TurnAccumulator())
        payload = _parse_payload(row["payload_json"])
        if row["event_type"] in _USAGE_TYPES:
            acc.add_usage(payload, float(row["created_at"] or 0))
        elif row["event_type"] in _TOOL_CALL_TYPES:
            acc.add_tool_call()
    turn_items = [
        _summarize_turn(turns[turn_id], accumulators.get(turn_id))
        for turn_id in sorted(
            turns,
            key=lambda tid: turns[tid].get("created_at") or 0,
            reverse=True,
        )
    ]
    return {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "totals": _totals_from_turns(turn_items),
        "turns": turn_items,
    }


def _model_key(model: str | None) -> str:
    return (model or "").strip() or "unknown"


def usage_summary(
    store: Any,
    user_id: str,
    *,
    project_id: str | None = None,
    days: int | None = 30,
) -> dict[str, Any]:
    """Aggregated usage across a project (or all projects) for recent days."""
    days = max(0, int(days or 0))
    since: float | None = None
    if days > 0:
        since = time.time() - days * 86400.0
    params: list[Any] = [user_id]
    where = "c.user_id=?"
    if project_id is not None:
        where += " AND c.project_id=?"
        params.append(project_id)
    if since is not None:
        where += " AND e.created_at >= ?"
        params.append(since)
    query = (
        "SELECT e.turn_id, e.event_type, e.payload_json, e.created_at, c.project_id, "
        "t.id AS turn_row_id, t.status, t.started_at, t.finished_at, t.model AS turn_model, "
        "t.provider AS turn_provider "
        "FROM conversation_events e "
        "JOIN conversations c ON c.id = e.conversation_id "
        "LEFT JOIN conversation_turns t ON t.id = e.turn_id "
        f"WHERE e.event_type IN ('usage','tool_call') AND {where} "
        "ORDER BY e.created_at ASC"
    )
    with store._connect() as conn:  # noqa: SLF001
        rows = conn.execute(query, params).fetchall()

    turn_acc: dict[str, _TurnAccumulator] = {}
    turn_meta: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    totals = {
        "turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tokens": 0,
        "tool_calls": 0,
        "cost_usd": 0.0,
        "cost_available": True,
    }
    projects: set[str] = set()

    for row in rows:
        created_at = float(row["created_at"] or 0)
        turn_id = row["turn_id"] or ""
        turn = turn_meta.setdefault(
            turn_id,
            {
                "id": row["turn_row_id"] or turn_id,
                "task_id": None,
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "model": row["turn_model"],
                "provider": row["turn_provider"],
            },
        )
        projects.add(row["project_id"])
        if row["event_type"] in _TOOL_CALL_TYPES:
            acc = turn_acc.setdefault(turn_id, _TurnAccumulator())
            acc.add_tool_call()
            totals["tool_calls"] += 1
            continue
        payload = _parse_payload(row["payload_json"])
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else payload
        model = payload.get("model") or turn.get("model")
        provider = payload.get("provider") or turn.get("provider")
        input_tokens = _num(usage.get("input_tokens"))
        output_tokens = _num(usage.get("output_tokens"))
        cached_tokens = _num(usage.get("cached_tokens"))
        cache_creation = _num(usage.get("cache_creation_tokens"))
        cost = pricing.estimate_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation,
        )
        acc = turn_acc.setdefault(turn_id, _TurnAccumulator())
        acc.add_usage({**payload, "model": model, "provider": provider}, created_at)
        totals["input_tokens"] += int(input_tokens)
        totals["output_tokens"] += int(output_tokens)
        totals["cached_tokens"] += int(cached_tokens)
        totals["cache_creation_tokens"] += int(cache_creation)
        totals["total_tokens"] += int(input_tokens + output_tokens)
        if cost is None:
            totals["cost_available"] = False
        else:
            totals["cost_usd"] += cost

        model_key = _model_key(model)
        model_bucket = by_model.setdefault(
            model_key,
            {
                "model": model_key,
                "provider": provider or "",
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "tool_calls": 0,
                "turns": set(),
                "cost_usd": 0.0,
                "cost_available": True,
            },
        )
        model_bucket["input_tokens"] += int(input_tokens)
        model_bucket["output_tokens"] += int(output_tokens)
        model_bucket["cached_tokens"] += int(cached_tokens)
        model_bucket["total_tokens"] += int(input_tokens + output_tokens)
        model_bucket["turns"].add(turn_id)
        if provider:
            model_bucket["provider"] = str(provider)
        if cost is None:
            model_bucket["cost_available"] = False
        else:
            model_bucket["cost_usd"] += cost

        day = time.strftime("%Y-%m-%d", time.localtime(created_at))
        day_bucket = by_day.setdefault(
            day,
            {
                "date": day,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "cost_available": True,
                "turns": set(),
            },
        )
        day_bucket["input_tokens"] += int(input_tokens)
        day_bucket["output_tokens"] += int(output_tokens)
        day_bucket["total_tokens"] += int(input_tokens + output_tokens)
        day_bucket["turns"].add(turn_id)
        if cost is None:
            day_bucket["cost_available"] = False
        else:
            day_bucket["cost_usd"] += cost

    totals["turns"] = len(turn_acc)

    for bucket in by_model.values():
        bucket["turns"] = len(bucket["turns"])
        bucket["cost_usd"] = (
            round(bucket["cost_usd"], 4) if bucket["cost_available"] else None
        )
    for bucket in by_day.values():
        bucket["turns"] = len(bucket["turns"])
        bucket["cost_usd"] = (
            round(bucket["cost_usd"], 4) if bucket["cost_available"] else None
        )
    if not totals["cost_available"]:
        totals["cost_usd"] = None
    else:
        totals["cost_usd"] = round(totals["cost_usd"], 4)
    totals["turns"] = len(turn_acc)

    return {
        "user_id": user_id,
        "project_id": project_id,
        "days": days,
        "since": since,
        "totals": totals,
        "by_model": sorted(
            by_model.values(), key=lambda item: -(item["total_tokens"])
        ),
        "by_day": [by_day[key] for key in sorted(by_day)],
        "project_count": len(projects),
    }
