(() => {
  "use strict";

  /**
   * Timeline Normalizer — the single place that converts task_events (live,
   * flat: {id, type, ts, ...payload}) and conversation_events (persisted,
   * nested: {seq, event_type, payload, turn_id, task_id, created_at}) into
   * one ordered, de-duplicated list of TimelineItem.
   *
   * TimelineItem = {
   *   key, seq, timestamp, turnId, type, status,
   *   messageId, toolCallId, approvalId, jobId,
   *   content, metadata
   * }
   *
   * Streaming protocol (authoritative):
   *  - text_delta: incremental fragment of ONE model output -> APPEND to the
   *    streaming item identified by message_id (fallback: stream_id, then the
   *    turn's open legacy stream). Never treated as a status/log line.
   *  - text: full snapshot of ONE model output -> REPLACE/reconcile the same
   *    streaming item (legacy events without message_id replace the turn's
   *    single open stream).
   *  - assistant_message: canonical structured message. It ADOPTS the matching
   *    streaming item (same message_id) in place — no second bubble is ever
   *    created — and marks it done/final.
   *
   * Merge rules:
   *  - tool_call + tool_result merge by tool_call_id into one lifecycle item.
   *  - approval_required / approval_resolved merge by approval_id; the
   *    approval is linked to its tool via tool_call_id.
   *  - live events and persisted events for the same entity merge by their
   *    stable business id; persisted payload wins on conflict.
   *  - duplicate deliveries (same task event id / same conversation seq) are
   *    idempotent no-ops.
   *  - consecutive low-value status events fold into one "working" group.
   */

  const STATUS_TYPES = new Set([
    "status",
    "session",
    "mcp_status",
    "system",
    "system_note",
    "hook",
  ]);

  const LIFECYCLE_TYPES = new Set([
    "turn_started",
    "turn_completed",
    "turn_failed",
    "turn_canceled",
    "turn_interrupted",
    "lifecycle_reconciled",
    "completed",
    "failed",
    "canceled",
    "done",
    "queued",
  ]);

  const APPROVAL_TERMINAL = new Set(["approved", "rejected", "timeout", "canceled"]);

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function textOfUserMessage(payload) {
    const content = payload && payload.content;
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content
        .filter((b) => isPlainObject(b) && b.type === "text" && typeof b.text === "string")
        .map((b) => b.text)
        .join("");
    }
    return "";
  }

  function textOfAssistantMessage(payload) {
    if (!payload) return "";
    if (typeof payload.text === "string") return payload.text;
    const blocks = payload.text_blocks;
    if (Array.isArray(blocks)) {
      return blocks
        .filter((b) => isPlainObject(b) && b.type === "text" && typeof b.text === "string")
        .map((b) => b.text)
        .join("");
    }
    return "";
  }

  function createStore() {
    /** @type {Array<object>} */
    const items = [];
    const byKey = new Map();
    const toolByCallId = new Map();
    const approvalById = new Map();
    const msgById = new Map();
    const turnIndex = new Map();
    const openStatusGroup = new Map(); // turnId -> item
    const seenTaskIds = new Set();
    const seenConvSeqs = new Set();
    let posCounter = 0;

    function idxOfTurn(turnId) {
      const key = turnId || "_";
      if (!turnIndex.has(key)) turnIndex.set(key, turnIndex.size);
      return turnIndex.get(key);
    }

    function sortItems() {
      items.sort((a, b) => {
        const ta = a.turnOrd;
        const tb = b.turnOrd;
        if (ta !== tb) return ta - tb;
        return a.pos - b.pos;
      });
    }

    function addItem(item) {
      item.pos = posCounter++;
      item.turnOrd = idxOfTurn(item.turnId);
      items.push(item);
      byKey.set(item.key, item);
      closeStatusGroup(item.turnId);
      return item;
    }

    function rekey(item, nextKey) {
      if (!item || item.key === nextKey || byKey.has(nextKey)) return;
      byKey.delete(item.key);
      item.key = nextKey;
      byKey.set(nextKey, item);
    }

    function removeItem(item) {
      if (!item) return;
      const i = items.indexOf(item);
      if (i >= 0) items.splice(i, 1);
      byKey.delete(item.key);
      if (item.messageId && msgById.get(item.messageId) === item) msgById.delete(item.messageId);
      if (item.toolCallId && toolByCallId.get(item.toolCallId) === item) toolByCallId.delete(item.toolCallId);
      if (item.approvalId && approvalById.get(item.approvalId) === item) approvalById.delete(item.approvalId);
    }

    function closeStatusGroup(turnId) {
      const key = turnId || "_";
      const group = openStatusGroup.get(key);
      if (group) {
        group.metadata.open = false;
        openStatusGroup.delete(key);
      }
    }

    function foldStatus(turnId, timestamp, message, extra) {
      const key = turnId || "_";
      let group = openStatusGroup.get(key);
      if (!group) {
        group = {
          key: `status:${key}:${posCounter}`,
          seq: null,
          timestamp,
          turnId: turnId || null,
          type: "status",
          status: "running",
          messageId: null,
          toolCallId: null,
          approvalId: null,
          jobId: null,
          content: { messages: [] },
          metadata: { open: true },
        };
        addItem(group);
        openStatusGroup.set(key, group);
      }
      if (message && group.content.messages[group.content.messages.length - 1] !== message) {
        group.content.messages.push(message);
      }
      group.timestamp = timestamp || group.timestamp;
      if (extra) Object.assign(group.metadata, extra);
      group.metadata.version = (group.metadata.version || 0) + 1;
      return group;
    }

    function upsertTool(callId, turnId, patch, timestamp, jobId) {
      let item = toolByCallId.get(callId);
      if (!item) {
        item = {
          key: `tool:${callId}`,
          seq: null,
          timestamp,
          turnId: turnId || null,
          type: "tool",
          status: "running",
          messageId: null,
          toolCallId: callId,
          approvalId: null,
          jobId: jobId || null,
          content: { name: "", input: null, output: null },
          metadata: {},
        };
        addItem(item);
        toolByCallId.set(callId, item);
      }
      if (timestamp && !item.timestamp) item.timestamp = timestamp;
      if (turnId && !item.turnId) {
        item.turnId = turnId;
        item.turnOrd = idxOfTurn(turnId);
      }
      if (jobId && !item.jobId) item.jobId = jobId;
      if (patch.name && !item.content.name) item.content.name = patch.name;
      if (patch.input != null && item.content.input == null) item.content.input = patch.input;
      if (patch.risk) item.metadata.risk = patch.risk;
      if (patch.startedAt && !item.metadata.startedAt) item.metadata.startedAt = patch.startedAt;
      if (patch.output !== undefined) item.content.output = patch.output;
      if (patch.ok !== undefined) item.metadata.ok = patch.ok;
      if (patch.durationMs != null) item.metadata.durationMs = patch.durationMs;
      if (patch.errorType) item.metadata.errorType = patch.errorType;
      if (patch.status) item.status = patch.status;
      if (patch.approvalId) item.approvalId = patch.approvalId;
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function upsertApproval(approvalId, turnId, patch, timestamp, jobId) {
      let item = approvalById.get(approvalId);
      if (!item) {
        item = {
          key: `approval:${approvalId}`,
          seq: null,
          timestamp,
          turnId: turnId || null,
          type: "approval",
          status: "pending",
          messageId: null,
          toolCallId: null,
          approvalId,
          jobId: jobId || null,
          content: {},
          metadata: {},
        };
        addItem(item);
        approvalById.set(approvalId, item);
      }
      if (timestamp && !item.timestamp) item.timestamp = timestamp;
      if (turnId && !item.turnId) {
        item.turnId = turnId;
        item.turnOrd = idxOfTurn(turnId);
      }
      if (jobId && !item.jobId) item.jobId = jobId;
      if (patch.request) {
        // Persisted payload wins; live payload only fills gaps.
        item.content = { ...patch.request, ...item.content };
        if (patch.authoritative) item.content = { ...item.content, ...patch.request };
      }
      if (patch.kind) item.metadata.kind = patch.kind;
      if (patch.toolCallId) {
        item.toolCallId = patch.toolCallId;
        const tool = toolByCallId.get(patch.toolCallId);
        if (tool) {
          tool.approvalId = approvalId;
          if (item.status === "pending") tool.status = "waiting_approval";
        }
      }
      if (patch.decision) {
        item.status = patch.decision;
        item.metadata.decision = patch.decision;
        item.metadata.resolvedAt = timestamp || Date.now() / 1000;
        const tool = item.toolCallId ? toolByCallId.get(item.toolCallId) : null;
        if (tool && tool.status === "waiting_approval") {
          tool.status = patch.decision === "approved" ? "running" : patch.decision;
        }
      }
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function upsertAssistant(messageId, turnId, patch, timestamp, authoritative, jobId) {
      let item = msgById.get(messageId);
      if (!item) {
        item = {
          key: `msg:${messageId}`,
          seq: null,
          timestamp,
          turnId: turnId || null,
          type: "assistant_message",
          status: "done",
          messageId,
          toolCallId: null,
          approvalId: null,
          jobId: jobId || null,
          content: { text: "" },
          metadata: {},
        };
        addItem(item);
        msgById.set(messageId, item);
      }
      if (jobId && !item.jobId) item.jobId = jobId;
      if (authoritative || !item.content.text) {
        if (typeof patch.text === "string") item.content.text = patch.text;
      }
      if (patch.isFinal !== undefined) item.metadata.isFinal = patch.isFinal;
      if (patch.provider) item.metadata.provider = patch.provider;
      if (patch.model) item.metadata.model = patch.model;
      if (patch.status) item.status = patch.status;
      if (timestamp && !item.timestamp) item.timestamp = timestamp;
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function streamItemFor(messageId, turnId) {
      // One streaming item per model output. Prefer the stable message_id;
      // fall back to the turn-level legacy stream for old events that carry
      // no identity.
      const identity = messageId || `turn:${turnId || "_"}`;
      const key = `stream:${identity}`;
      let item = byKey.get(key);
      if (!item && messageId) {
        // Compatibility: adopt the turn's open UNIDENTIFIED legacy stream
        // (created by old events without message_id) instead of opening a
        // second stream for the same model output.
        const legacy = byKey.get(`stream:turn:${turnId || "_"}`);
        if (legacy && legacy.status === "streaming" && !legacy.messageId) {
          item = legacy;
          rekey(item, key);
          item.messageId = messageId;
          msgById.set(messageId, item);
        }
      }
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: null,
          turnId: turnId || null,
          type: "assistant_message",
          status: "streaming",
          messageId: messageId || null,
          toolCallId: null,
          approvalId: null,
          jobId: null,
          content: { text: "" },
          metadata: { stream: true },
        };
        addItem(item);
        if (messageId) msgById.set(messageId, item);
      }
      if (turnId && !item.turnId) {
        item.turnId = turnId;
        item.turnOrd = idxOfTurn(turnId);
      }
      return item;
    }

    /** Legacy accessor: the turn's open un-identified stream. */
    function streamItem(turnId) {
      return streamItemFor(null, turnId);
    }

    function openStreamsOfTurn(turnId) {
      return items.filter(
        (i) => i.metadata.stream && i.status === "streaming" && (i.turnId === turnId || (!turnId && !i.turnId)),
      );
    }

    // —— Event handlers shared by both sources ——

    function handleUserMessage(p, ctx) {
      const text = textOfUserMessage(p);
      const key = `user:${ctx.turnId || ctx.jobId || "?"}:${(text || "").length}:${(text || "").slice(0, 32)}`;
      let item = byKey.get(key);
      if (!item && text) {
        // Adopt a local optimistic echo (same text, not yet anchored).
        const echo = items.find(
          (i) =>
            i.type === "user_message" &&
            !i.messageId &&
            i.turnId == null &&
            i.content.text === text,
        );
        if (echo) {
          item = echo;
          rekey(item, key);
          item.turnId = ctx.turnId || null;
          item.turnOrd = idxOfTurn(item.turnId);
          item.jobId = ctx.jobId || item.jobId;
        }
      }
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: ctx.timestamp,
          turnId: ctx.turnId || null,
          type: "user_message",
          status: "done",
          messageId: p.message_id || null,
          toolCallId: null,
          approvalId: null,
          jobId: ctx.jobId || null,
          content: { text },
          metadata: {},
        };
        addItem(item);
      }
      if (ctx.authoritative) {
        item.content.text = text;
        if (p.message_id) item.messageId = p.message_id;
      }
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function handleAssistantMessage(p, ctx) {
      const messageId = p.message_id;
      const text = textOfAssistantMessage(p);
      if (!messageId) {
        // Malformed: fall back to the turn's legacy stream so nothing crashes.
        const s = streamItem(ctx.turnId);
        if (text) s.content.text = text;
        s.status = "done";
        s.metadata.stream = false;
        if (p.is_final !== undefined) s.metadata.isFinal = p.is_final;
        if (ctx.jobId && !s.jobId) s.jobId = ctx.jobId;
        s.metadata.version = (s.metadata.version || 0) + 1;
        return s;
      }
      // Adopt the in-progress streaming item of this exact model output so a
      // delta stream never produces a second bubble.
      let item = msgById.get(messageId);
      const streamKey = `stream:${messageId}`;
      const stream = byKey.get(streamKey);
      if (stream && stream !== item) {
        item = stream;
        msgById.set(messageId, item);
      }
      if (!item) {
        // Compatibility key for legacy streams without identity: the turn's
        // currently open stream IS this model output.
        const open = openStreamsOfTurn(ctx.turnId);
        if (open.length) {
          item = open[open.length - 1];
          msgById.set(messageId, item);
        }
      }
      if (item) {
        // Canonical message wins over streamed fragments.
        if (typeof text === "string" && text) item.content.text = text;
        item.messageId = messageId;
        item.status = "done";
        item.metadata.stream = false;
        item.metadata.isFinal = p.is_final;
        if (p.provider) item.metadata.provider = p.provider;
        if (p.model) item.metadata.model = p.model;
        if (ctx.timestamp && !item.timestamp) item.timestamp = ctx.timestamp;
        if (ctx.turnId && !item.turnId) {
          item.turnId = ctx.turnId;
          item.turnOrd = idxOfTurn(ctx.turnId);
        }
        if (ctx.jobId && !item.jobId) item.jobId = ctx.jobId;
        // Rekey stream:<id> -> msg:<id> keeping DOM/state continuity via key
        // migration handled by the view layer (rekey is index-safe here).
        rekey(item, `msg:${messageId}`);
        item.metadata.version = (item.metadata.version || 0) + 1;
        return item;
      }
      return upsertAssistant(
        messageId,
        ctx.turnId,
        {
          text,
          isFinal: p.is_final,
          provider: p.provider,
          model: p.model,
          status: "done",
        },
        ctx.timestamp,
        ctx.authoritative,
        ctx.jobId,
      );
    }

    function handleTextDelta(p, ctx) {
      // Incremental fragment of one model output: APPEND, never replace, and
      // never treat as a status line. Identity: message_id > stream_id > the
      // turn's open legacy stream.
      const messageId = p.message_id || p.stream_id || null;
      const fragment =
        typeof p.delta === "string"
          ? p.delta
          : typeof p.content === "string"
            ? p.content
            : textOfAssistantMessage(p);
      const s = streamItemFor(messageId, ctx.turnId);
      if (fragment) s.content.text += fragment;
      if (ctx.timestamp && !s.timestamp) s.timestamp = ctx.timestamp;
      if (ctx.jobId && !s.jobId) s.jobId = ctx.jobId;
      s.metadata.version = (s.metadata.version || 0) + 1;
      return s;
    }

    function handleTextSnapshot(p, ctx) {
      // Full snapshot of one model output: replace/reconcile the SAME item the
      // deltas have been appended to.
      const messageId = p.message_id || p.stream_id || null;
      const text = typeof p.content === "string" ? p.content : textOfAssistantMessage(p);
      if (messageId) {
        const s = streamItemFor(messageId, ctx.turnId);
        if (typeof text === "string") s.content.text = text;
        if (ctx.timestamp && !s.timestamp) s.timestamp = ctx.timestamp;
        if (ctx.jobId && !s.jobId) s.jobId = ctx.jobId;
        s.metadata.version = (s.metadata.version || 0) + 1;
        return s;
      }
      if (p.message_id && !p.streamed) return handleAssistantMessage(p, ctx);
      // Legacy events without identity: reconcile against the turn's open
      // streams. A snapshot that extends the current text appends (old backends
      // sometimes send per-block snapshots); otherwise it replaces.
      const open = openStreamsOfTurn(ctx.turnId);
      const s = open.length ? open[open.length - 1] : streamItem(ctx.turnId);
      if (typeof text === "string" && text) {
        if (s.content.text && text.length < s.content.text.length && s.content.text.startsWith(text)) {
          // Stale/partial snapshot of already-streamed content: ignore.
        } else if (s.content.text && !text.startsWith(s.content.text) && !s.content.text.startsWith(text)) {
          s.content.text += text;
        } else {
          s.content.text = text;
        }
      }
      if (ctx.timestamp && !s.timestamp) s.timestamp = ctx.timestamp;
      if (ctx.jobId && !s.jobId) s.jobId = ctx.jobId;
      s.metadata.version = (s.metadata.version || 0) + 1;
      return s;
    }

    function handleToolCall(p, ctx) {
      const callId = p.tool_call_id;
      if (!callId) return null;
      return upsertTool(
        callId,
        ctx.turnId,
        {
          name: p.name || p.tool || "",
          input: p.input != null ? p.input : p.arguments,
          risk: p.risk,
          startedAt: ctx.timestamp,
          status: "running",
        },
        ctx.timestamp,
        ctx.jobId,
      );
    }

    function handleToolResult(p, ctx) {
      const callId = p.tool_call_id;
      if (!callId) return null;
      const output =
        p.structured_output != null
          ? p.structured_output
          : p.model_output != null
            ? p.model_output
            : p.output != null
              ? p.output
              : p.message;
      const ok = p.ok !== undefined ? Boolean(p.ok) : undefined;
      return upsertTool(
        callId,
        ctx.turnId,
        {
          name: p.name || "",
          output,
          ok,
          durationMs: p.duration_ms,
          errorType: p.error_type,
          status: ok === undefined ? "done" : ok ? "success" : "failed",
        },
        ctx.timestamp,
        ctx.jobId,
      );
    }

    function handleApprovalRequired(p, ctx) {
      const approvalId = p.approval_id;
      if (!approvalId) return null;
      const request = {};
      for (const [k, v] of Object.entries(p)) {
        if (["id", "type", "ts", "seq", "event_type", "turn_id", "task_id", "created_at"].includes(k)) continue;
        request[k] = v;
      }
      const nested = isPlainObject(p.request) ? p.request : {};
      const merged = { ...nested, ...request };
      return upsertApproval(
        approvalId,
        ctx.turnId,
        {
          request: merged,
          kind: p.kind || merged.requested_capability || "tool",
          toolCallId: p.tool_call_id || null,
          authoritative: ctx.authoritative,
        },
        ctx.timestamp,
        ctx.jobId,
      );
    }

    function handleApprovalResolved(p, ctx) {
      const approvalId = p.approval_id;
      if (!approvalId) return null;
      const decision = APPROVAL_TERMINAL.has(p.decision) ? p.decision : "rejected";
      return upsertApproval(
        approvalId,
        ctx.turnId,
        { decision, kind: p.kind, toolCallId: p.tool_call_id || null },
        ctx.timestamp,
        ctx.jobId,
      );
    }

    function handlePlan(p, ctx) {
      const key = `plan:${ctx.turnId || ctx.jobId || "_"}`;
      let item = byKey.get(key);
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: ctx.timestamp,
          turnId: ctx.turnId || null,
          type: "plan",
          status: "active",
          messageId: null,
          toolCallId: null,
          approvalId: null,
          jobId: ctx.jobId || null,
          content: { text: "", steps: [] },
          metadata: {},
        };
        addItem(item);
      }
      if (typeof p.message === "string") item.content.text = p.message;
      if (Array.isArray(p.steps)) item.content.steps = p.steps;
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function handleChanges(p, ctx) {
      const key = `changes:${ctx.turnId || ctx.jobId || "_"}`;
      let item = byKey.get(key);
      if (!item && ctx.turnId && ctx.jobId) {
        // A live card created before the turn id was known is keyed by jobId.
        // Adopt (rekey) it so canonical events never create a second card.
        const provisional = byKey.get(`changes:${ctx.jobId}`);
        if (provisional) {
          item = provisional;
          rekey(item, key);
          item.turnId = ctx.turnId;
          item.turnOrd = idxOfTurn(ctx.turnId);
        }
      }
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: ctx.timestamp,
          turnId: ctx.turnId || null,
          type: "changes",
          status: "done",
          messageId: null,
          toolCallId: null,
          approvalId: null,
          jobId: ctx.jobId || null,
          content: { files: [] },
          metadata: {},
        };
        addItem(item);
      }
      if (Array.isArray(p.files)) item.content.files = p.files;
      if (p.diff_status) item.metadata.diffStatus = p.diff_status;
      if (p.diff_reason) item.metadata.diffReason = p.diff_reason;
      if (ctx.turnId && !item.turnId) {
        item.turnId = ctx.turnId;
        item.turnOrd = idxOfTurn(ctx.turnId);
      }
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function handleCheckpoint(p, ctx) {
      const cpId = p.checkpoint_id || p.id;
      if (!cpId) return null;
      const key = `checkpoint:${cpId}`;
      let item = byKey.get(key);
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: ctx.timestamp,
          turnId: ctx.turnId || null,
          type: "checkpoint",
          status: "done",
          messageId: null,
          toolCallId: null,
          approvalId: null,
          jobId: ctx.jobId || null,
          content: {},
          metadata: {},
        };
        addItem(item);
      }
      item.content = { ...item.content, checkpointId: cpId, kind: p.kind, fileCount: p.file_count };
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function handleUsage(p, ctx, type) {
      const key = `usage:${ctx.turnId || ctx.jobId || "_"}:${type}`;
      let item = byKey.get(key);
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: ctx.timestamp,
          turnId: ctx.turnId || null,
          type: type === "usage" ? "usage" : "provider_change",
          status: "done",
          messageId: null,
          toolCallId: null,
          approvalId: null,
          jobId: ctx.jobId || null,
          content: {},
          metadata: {},
        };
        addItem(item);
      }
      Object.assign(item.content, p);
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function handleLifecycle(p, ctx, type) {
      const status =
        type === "turn_completed" || type === "completed" || type === "done"
          ? "succeeded"
          : type === "turn_failed" || type === "failed"
            ? "failed"
            : type === "turn_canceled" || type === "canceled"
              ? "canceled"
              : type === "turn_interrupted"
                ? "interrupted"
                : type;
      // Terminal lifecycle: close any streaming/pending leftovers of the turn.
      // Keep already-received text; just stop the cursor.
      if (["succeeded", "failed", "canceled", "interrupted"].includes(status)) {
        for (const stream of openStreamsOfTurn(ctx.turnId)) {
          if (!stream.content.text) removeItem(stream);
          else {
            stream.status = "done";
            stream.metadata.stream = false;
            stream.metadata.version = (stream.metadata.version || 0) + 1;
          }
        }
        closeStatusGroup(ctx.turnId);
        for (const tool of items.filter((i) => i.type === "tool" && i.status === "running")) {
          if (!ctx.turnId || tool.turnId === ctx.turnId) {
            tool.status = status === "canceled" ? "canceled" : tool.status;
            tool.metadata.version = (tool.metadata.version || 0) + 1;
          }
        }
      }
      const key = `lifecycle:${ctx.turnId || ctx.jobId || "_"}:${status}`;
      let item = byKey.get(key);
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: ctx.timestamp,
          turnId: ctx.turnId || null,
          type: "lifecycle",
          status,
          messageId: null,
          toolCallId: null,
          approvalId: null,
          jobId: ctx.jobId || null,
          content: {},
          metadata: {},
        };
        addItem(item);
      }
      if (p.error || p.message) item.content.message = p.error || p.message;
      // Diff-review readiness reported by the terminal payload — the changes
      // card and review button are driven by this, never by guessing.
      if (p.diff_status) item.metadata.diffStatus = p.diff_status;
      if (p.diff_reason) item.metadata.diffReason = p.diff_reason;
      if (p.after_checkpoint_id) item.metadata.afterCheckpointId = p.after_checkpoint_id;
      item.metadata.version = (item.metadata.version || 0) + 1;
      return item;
    }

    function handleStatusLike(p, ctx) {
      const message = typeof p.message === "string" ? p.message : typeof p.content === "string" ? p.content : "";
      return foldStatus(ctx.turnId, ctx.timestamp, message, {});
    }

    function handleError(p, ctx) {
      const message = p.error || p.message || "未知错误";
      const key = `error:${ctx.turnId || ctx.jobId || "_"}:${String(message).slice(0, 48)}`;
      let item = byKey.get(key);
      if (!item) {
        item = {
          key,
          seq: null,
          timestamp: ctx.timestamp,
          turnId: ctx.turnId || null,
          type: "error",
          status: "failed",
          messageId: null,
          toolCallId: null,
          approvalId: null,
          jobId: ctx.jobId || null,
          content: { text: String(message) },
          metadata: {},
        };
        addItem(item);
      }
      return item;
    }

    // Private model reasoning must never reach the UI. Only an explicit
    // user-visible summary from the backend (none today) may be shown.
    const PRIVATE_TYPES = new Set([
      "reasoning",
      "reasoning_delta",
      "reasoning_summary",
      "thinking",
      "thought",
      "chain_of_thought",
    ]);

    function dispatchEvent(type, payload, ctx) {
      if (PRIVATE_TYPES.has(type)) return null;
      const p = isPlainObject(payload) ? payload : { message: payload == null ? "" : String(payload) };
      try {
        if (type === "user_message" || type === "user") return handleUserMessage(p, ctx);
        if (type === "assistant_message") return handleAssistantMessage(p, ctx);
        // text_delta: incremental fragment -> APPEND. Never a status line.
        if (type === "text_delta") return handleTextDelta(p, ctx);
        // text / assistant: full snapshot of one model output -> REPLACE.
        if (type === "text" || type === "assistant") return handleTextSnapshot(p, ctx);
        if (type === "tool_call") return handleToolCall(p, ctx);
        if (type === "tool_result") return handleToolResult(p, ctx);
        if (type === "approval_required") return handleApprovalRequired(p, ctx);
        if (type === "approval_resolved") return handleApprovalResolved(p, ctx);
        if (type === "plan") return handlePlan(p, ctx);
        if (type === "changes") return handleChanges(p, ctx);
        if (type === "checkpoint" || type === "context_checkpoint") return handleCheckpoint(p, ctx);
        if (type === "usage" || type === "provider_switch" || type === "model_switch") {
          return handleUsage(p, ctx, type);
        }
        if (type === "malformed_tool_call" || type === "error") return handleError(p, ctx);
        if (type === "recovery_note") return handleStatusLike({ message: p.content || p.message }, ctx);
        if (LIFECYCLE_TYPES.has(type)) return handleLifecycle(p, ctx, type);
        if (STATUS_TYPES.has(type)) return handleStatusLike(p, ctx);
        // Unknown types: never crash; surface as a folded status line.
        return handleStatusLike(p, ctx);
      } catch (_) {
        return null;
      }
    }

    return {
      /** Live or polled task events: {id, type, ts, ...payload}. */
      ingestTaskEvents(events, { jobId = null, turnId = null } = {}) {
        let changed = false;
        for (const ev of Array.isArray(events) ? events : []) {
          if (!ev || typeof ev !== "object") continue;
          if (ev.id != null) {
            if (seenTaskIds.has(ev.id)) continue;
            seenTaskIds.add(ev.id);
          }
          const before = items.length;
          const ctx = {
            turnId: ev.turn_id || turnId,
            jobId: ev.task_id || ev.job_id || jobId,
            timestamp: ev.ts || ev.created_at || null,
            authoritative: false,
          };
          const item = dispatchEvent(ev.type, ev, ctx);
          if (item) item.metadata.version = (item.metadata.version || 0) + 1;
          changed = changed || Boolean(item) || items.length !== before;
        }
        sortItems();
        return changed;
      },

      /** Persisted conversation events — authoritative source after reconnect. */
      ingestConversationEvents(events) {
        let changed = false;
        const turnByJob = new Map();
        for (const ev of Array.isArray(events) ? events : []) {
          if (!ev || typeof ev !== "object") continue;
          if (ev.seq != null) {
            if (seenConvSeqs.has(ev.seq)) continue;
            seenConvSeqs.add(ev.seq);
          }
          if (ev.task_id && ev.turn_id) turnByJob.set(ev.task_id, ev.turn_id);
          const payload = isPlainObject(ev.payload) ? ev.payload : {};
          const ctx = {
            turnId: ev.turn_id || null,
            jobId: ev.task_id || null,
            timestamp: ev.created_at || null,
            authoritative: true,
          };
          const item = dispatchEvent(ev.event_type, payload, ctx);
          if (item) {
            if (ev.seq != null && item.seq == null) item.seq = ev.seq;
            changed = true;
          }
        }
        // Anchor live-only items (no turnId yet) to their persisted turn.
        for (const item of items) {
          if (item.turnId == null && item.jobId && turnByJob.has(item.jobId)) {
            const turnId = turnByJob.get(item.jobId);
            item.turnId = turnId;
            item.turnOrd = idxOfTurn(turnId);
            if (item.key === `stream:_`) rekey(item, `stream:${turnId}`);
            if (item.type === "status") {
              openStatusGroup.delete("_");
              openStatusGroup.set(turnId, item);
            }
            changed = true;
          }
        }
        sortItems();
        return changed;
      },

      /** Local optimistic user echo before any server event arrives. */
      addLocalUserMessage(text, { jobId = null, turnId = null } = {}) {
        return handleUserMessage({ content: text, source: "local" }, { turnId, jobId, timestamp: Date.now() / 1000 });
      },

      items() {
        return items.slice();
      },

      pendingApprovals() {
        return items.filter((i) => i.type === "approval" && i.status === "pending");
      },

      findApproval(approvalId) {
        return approvalById.get(approvalId) || null;
      },

      /** Optimistic local decision; server event remains the final word. */
      setApprovalDecision(approvalId, decision) {
        const item = approvalById.get(approvalId);
        if (!item) return;
        item.status = decision;
        item.metadata.decision = decision;
        item.metadata.resolvedAt = Date.now() / 1000;
        delete item.metadata.resolveError;
        item.metadata.version = (item.metadata.version || 0) + 1;
        const tool = item.toolCallId ? toolByCallId.get(item.toolCallId) : null;
        if (tool && tool.status === "waiting_approval") {
          tool.status = decision === "approved" ? "running" : decision;
          tool.metadata.version = (tool.metadata.version || 0) + 1;
        }
      },

      /** Mark an approval optimistically-resolving failed; keep pending. */
      markApprovalError(approvalId, message) {
        const item = approvalById.get(approvalId);
        if (!item) return;
        item.metadata.resolveError = message || "操作失败，请重试";
        item.metadata.version = (item.metadata.version || 0) + 1;
      },

      clearApprovalError(approvalId) {
        const item = approvalById.get(approvalId);
        if (!item) return;
        delete item.metadata.resolveError;
        item.metadata.version = (item.metadata.version || 0) + 1;
      },

      cancelPending() {
        for (const item of items) {
          if (item.type === "approval" && item.status === "pending") {
            item.status = "canceled";
            item.metadata.decision = "canceled";
            item.metadata.version = (item.metadata.version || 0) + 1;
          }
          if (item.type === "tool" && (item.status === "running" || item.status === "waiting_approval")) {
            item.status = "canceled";
            item.metadata.version = (item.metadata.version || 0) + 1;
          }
          if (item.type === "status" && item.metadata.open) {
            item.metadata.open = false;
            item.status = "done";
          }
          if (item.type === "assistant_message" && item.status === "streaming") {
            item.status = "done";
          }
        }
      },

      reset() {
        items.length = 0;
        byKey.clear();
        toolByCallId.clear();
        approvalById.clear();
        msgById.clear();
        turnIndex.clear();
        openStatusGroup.clear();
        seenTaskIds.clear();
        seenConvSeqs.clear();
        posCounter = 0;
      },

      _debug: { byKey, toolByCallId, approvalById, msgById },
    };
  }

  const api = { createStore };
  if (typeof window !== "undefined") window.Timeline = api;
  if (typeof globalThis !== "undefined") globalThis.Timeline = api;
})();
