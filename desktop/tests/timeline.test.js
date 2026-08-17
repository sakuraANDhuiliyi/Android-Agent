const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "../src/timeline.js"), "utf8");
const context = { window: {}, globalThis: {} };
vm.createContext(context);
vm.runInNewContext(code, context);
const Timeline = context.window.Timeline || context.globalThis.Timeline;

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok - ${name}`);
  } catch (err) {
    console.error(`  FAIL - ${name}`);
    console.error(err);
    process.exit(1);
  }
}

function convEvent(seq, eventType, payload, turnId = "t1", taskId = "j1") {
  return { seq, event_type: eventType, payload, turn_id: turnId, task_id: taskId, created_at: 1000 + seq };
}

// 1. assistant text snapshots fold into one streaming item, final message replaces it
test("assistant delta merge", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "text", content: "正在", streamed: true },
    { id: 2, type: "text", content: "正在分析", streamed: true },
    { id: 3, type: "text", content: "正在分析代码", streamed: true },
  ], { jobId: "j1" });
  let items = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(items.length, 1);
  assert.strictEqual(items[0].content.text, "正在分析代码");
  assert.strictEqual(items[0].status, "streaming");
  s.ingestTaskEvents([
    {
      id: 4,
      type: "assistant_message",
      message_id: "m1",
      text_blocks: [{ type: "text", text: "正在分析代码，发现三个问题。" }],
      is_final: true,
    },
  ], { jobId: "j1" });
  items = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(items.length, 1);
  assert.strictEqual(items[0].content.text, "正在分析代码，发现三个问题。");
  assert.strictEqual(items[0].messageId, "m1");
});

// 2. duplicate seq / task event ids are dropped
test("duplicate seq/id dedupe", () => {
  const s = Timeline.createStore();
  const ev = convEvent(1, "user_message", { message_id: "u1", content: [{ type: "text", text: "hello" }] });
  s.ingestConversationEvents([ev, ev]);
  s.ingestTaskEvents([{ id: 9, type: "status", message: "working" }, { id: 9, type: "status", message: "working" }]);
  assert.strictEqual(s.items().filter((i) => i.type === "user_message").length, 1);
  assert.strictEqual(s.items().filter((i) => i.type === "status").length, 1);
  assert.strictEqual(s.items()[1].content.messages.length, 1);
});

// 3. tool_call + tool_result pair into one lifecycle item
test("tool call/result pairing", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "tool_call", tool_call_id: "c1", name: "read_file", input: { path: "a.kt" }, ts: 100 },
    { id: 2, type: "tool_result", tool_call_id: "c1", name: "read_file", ok: true, duration_ms: 42, ts: 101 },
  ], { jobId: "j1" });
  const tools = s.items().filter((i) => i.type === "tool");
  assert.strictEqual(tools.length, 1);
  assert.strictEqual(tools[0].status, "success");
  assert.strictEqual(tools[0].metadata.durationMs, 42);
  assert.strictEqual(tools[0].content.input.path, "a.kt");
});

// 4. approval_required links to its tool via tool_call_id
test("approval links to tool", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "tool_call", tool_call_id: "c1", name: "run_command", input: { argv: ["ls"] } },
    {
      id: 2,
      type: "approval_required",
      approval_id: "a1",
      tool_call_id: "c1",
      kind: "process",
      risk: "destructive",
      reason: "workspace 模式下网络、进程或破坏性操作需要审批",
      command: "ls -la",
      cwd: ".",
    },
  ], { jobId: "j1" });
  const tool = s.items().find((i) => i.type === "tool");
  const approval = s.items().find((i) => i.type === "approval");
  assert.strictEqual(tool.status, "waiting_approval");
  assert.strictEqual(tool.approvalId, "a1");
  assert.strictEqual(approval.status, "pending");
  assert.strictEqual(approval.toolCallId, "c1");
  assert.strictEqual(approval.content.command, "ls -la");
  assert.strictEqual(approval.content.cwd, ".");
  assert.strictEqual(approval.content.risk, "destructive");
});

// 5. approval_resolved updates the same item instead of adding a card
test("approval resolve updates in place", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "approval_required", approval_id: "a1", tool_call_id: "c1", kind: "network" },
    { id: 2, type: "approval_resolved", approval_id: "a1", tool_call_id: "c1", decision: "approved" },
  ], { jobId: "j1" });
  const approvals = s.items().filter((i) => i.type === "approval");
  assert.strictEqual(approvals.length, 1);
  assert.strictEqual(approvals[0].status, "approved");
  assert.strictEqual(s.pendingApprovals().length, 0);
});

// 6. reconnect: persisted + live merge without duplicates
test("reconnect reconciliation without duplicates", () => {
  const s = Timeline.createStore();
  // Live first
  s.ingestTaskEvents([
    { id: 1, type: "tool_call", tool_call_id: "c1", name: "write_file", input: { path: "b.kt" } },
    { id: 2, type: "tool_result", tool_call_id: "c1", ok: true, duration_ms: 10 },
  ], { jobId: "j1" });
  // Reconnect: persisted events for the same entities
  s.ingestConversationEvents([
    convEvent(1, "tool_call", { tool_call_id: "c1", name: "write_file", input: { path: "b.kt" } }),
    convEvent(2, "tool_result", { tool_call_id: "c1", name: "write_file", ok: true, duration_ms: 10 }),
  ]);
  // Delivering the same persisted page again must be a no-op
  s.ingestConversationEvents([
    convEvent(1, "tool_call", { tool_call_id: "c1", name: "write_file", input: { path: "b.kt" } }),
    convEvent(2, "tool_result", { tool_call_id: "c1", name: "write_file", ok: true, duration_ms: 10 }),
  ]);
  assert.strictEqual(s.items().filter((i) => i.type === "tool").length, 1);
  assert.strictEqual(s.items()[0].turnId, "t1");
});

// 7. assistant commentary interleaved with tool events keeps order
test("commentary interleaved with tools", () => {
  const s = Timeline.createStore();
  s.ingestConversationEvents([
    convEvent(1, "assistant_message", { message_id: "m1", text_blocks: [{ type: "text", text: "先看一下。" }] }),
    convEvent(2, "tool_call", { tool_call_id: "c1", name: "read_file", input: {} }),
    convEvent(3, "tool_result", { tool_call_id: "c1", ok: true }),
    convEvent(4, "assistant_message", { message_id: "m2", text_blocks: [{ type: "text", text: "看完了。" }] }),
  ]);
  const types = s.items().map((i) => `${i.type}:${i.messageId || i.toolCallId || ""}`);
  assert.strictEqual(types.join("|"), "assistant_message:m1|tool:c1|assistant_message:m2");
});

// 8. timeout / canceled decisions are terminal
test("timeout and canceled approvals", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "approval_required", approval_id: "a1", tool_call_id: "c1", kind: "process" },
    { id: 2, type: "approval_resolved", approval_id: "a1", tool_call_id: "c1", decision: "timeout" },
    { id: 3, type: "approval_required", approval_id: "a2", tool_call_id: "c2", kind: "process" },
  ], { jobId: "j1" });
  s.cancelPending();
  const a1 = s.findApproval("a1");
  const a2 = s.findApproval("a2");
  assert.strictEqual(a1.status, "timeout");
  assert.strictEqual(a2.status, "canceled");
  assert.strictEqual(s.pendingApprovals().length, 0);
});

// 9. malformed / unknown payloads never crash the store
test("malformed and unknown events are safe", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    null,
    { id: 1 },
    { id: 2, type: "tool_call" }, // missing tool_call_id
    { id: 3, type: "assistant_message", text_blocks: "broken" }, // missing message_id
    { id: 4, type: "totally_new_kind", message: "future event" },
    { id: 5, type: "approval_required" }, // missing approval_id
  ]);
  const items = s.items();
  assert.ok(items.length >= 1);
  // unknown type folds into a status line
  assert.ok(items.some((i) => i.type === "status"));
  s.ingestConversationEvents([{ seq: 1, event_type: "mystery", payload: null, turn_id: "t9" }]);
  assert.ok(s.items().length >= 1);
});

// 10. multiple concurrent tools keep separate lifecycles
test("concurrent tools", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "tool_call", tool_call_id: "c1", name: "read_file", input: { path: "a" } },
    { id: 2, type: "tool_call", tool_call_id: "c2", name: "search_code", input: { query: "q" } },
    { id: 3, type: "tool_result", tool_call_id: "c2", ok: false, error_type: "PermissionDenied" },
    { id: 4, type: "tool_result", tool_call_id: "c1", ok: true },
  ]);
  const tools = s.items().filter((i) => i.type === "tool");
  assert.strictEqual(tools.length, 2);
  assert.strictEqual(tools[0].status, "success");
  assert.strictEqual(tools[1].status, "failed");
  assert.strictEqual(tools[1].metadata.errorType, "PermissionDenied");
});

// 11. a job can surface several pending approvals at once
test("multiple pending approvals", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "tool_call", tool_call_id: "c1", name: "run_command", input: { argv: ["a"] } },
    { id: 2, type: "approval_required", approval_id: "a1", tool_call_id: "c1", kind: "process" },
    { id: 3, type: "tool_call", tool_call_id: "c2", name: "download_file", input: { url: "https://x" } },
    { id: 4, type: "approval_required", approval_id: "a2", tool_call_id: "c2", kind: "download_file" },
  ]);
  assert.strictEqual(s.pendingApprovals().length, 2);
  s.setApprovalDecision("a1", "rejected");
  const pending = s.pendingApprovals();
  assert.strictEqual(pending.length, 1);
  assert.strictEqual(pending[0].approvalId, "a2");
});

// 12. plan updates merge into the same item
test("plan updates merge in place", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents([
    { id: 1, type: "plan", message: "理解需求 -> 修改代码" },
    { id: 2, type: "plan", message: "理解需求 -> 修改代码 -> 构建" },
  ], { jobId: "j1" });
  const plans = s.items().filter((i) => i.type === "plan");
  assert.strictEqual(plans.length, 1);
  assert.strictEqual(plans[0].content.text, "理解需求 -> 修改代码 -> 构建");
});

// Bonus: status folding, user echo adoption, changes item
test("status folding and local echo adoption", () => {
  const s = Timeline.createStore();
  s.addLocalUserMessage("帮我改启动页", {});
  s.ingestTaskEvents([
    { id: 1, type: "status", message: "正在检查项目结构" },
    { id: 2, type: "status", message: "正在搜索文件" },
    { id: 3, type: "tool_call", tool_call_id: "c1", name: "list_files", input: {} },
    { id: 4, type: "status", message: "正在运行测试" },
  ], { jobId: "j1" });
  const statusGroups = s.items().filter((i) => i.type === "status");
  assert.strictEqual(statusGroups.length, 2);
  assert.strictEqual(statusGroups[0].content.messages.length, 2);
  // persisted user message adopts the local echo instead of duplicating
  s.ingestConversationEvents([
    convEvent(1, "user_message", { message_id: "u1", content: [{ type: "text", text: "帮我改启动页" }] }),
  ]);
  assert.strictEqual(s.items().filter((i) => i.type === "user_message").length, 1);
  assert.strictEqual(s.items().find((i) => i.type === "user_message").turnId, "t1");
});

test("changes item aggregates files", () => {
  const s = Timeline.createStore();
  s.ingestConversationEvents([
    convEvent(1, "changes", {
      files: [
        { path: "a.kt", change: "modified" },
        { path: "b.xml", change: "added" },
      ],
    }),
  ]);
  const changes = s.items().filter((i) => i.type === "changes");
  assert.strictEqual(changes.length, 1);
  assert.strictEqual(changes[0].content.files.length, 2);
  assert.strictEqual(changes[0].turnId, "t1");
});

// —— P0 regression: text_delta protocol ——

// 15. deltas split across **bold**, `inline code` and fenced blocks still
// produce ONE assistant item with the exact ordered text; never status lines.
test("text_delta regression: split chunks merge into one message", () => {
  const s = Timeline.createStore();
  const full =
    "修复完成，要点：\n\n**粗体重点** 与 `行内代码` 说明\n\n```kotlin\nfun main() {\n  println(\"hi\")\n}\n```\n\n结束。";
  // Split the text at hostile boundaries: inside **bold**, inside `code`,
  // inside a fenced block and right at the fence backticks.
  const cuts = [17, 24, 31, 40, 52, 63, 75];
  let prev = 0;
  const deltas = [];
  for (const c of cuts) {
    deltas.push(full.slice(prev, c));
    prev = c;
  }
  deltas.push(full.slice(prev));
  s.ingestTaskEvents(
    deltas.map((d, i) => ({ id: i + 1, type: "text_delta", delta: d, message_id: "m1", stream_id: "s1" })),
    { jobId: "j1", turnId: "t1" },
  );
  let assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 1, "one streaming item for all deltas");
  assert.strictEqual(assistants[0].content.text, full, "delta text complete and ordered");
  assert.strictEqual(assistants[0].status, "streaming");
  assert.strictEqual(s.items().filter((i) => i.type === "status").length, 0, "no delta became a status line");

  // Full snapshot for the same output: reconcile in place, still one item.
  s.ingestTaskEvents(
    [{ id: 100, type: "text", content: full, message_id: "m1", stream_id: "s1", streamed: true }],
    { jobId: "j1", turnId: "t1" },
  );
  assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 1, "snapshot reconciles into the same item");
  assert.strictEqual(assistants[0].content.text, full);

  // Authoritative assistant_message: finalize the SAME item, no second bubble.
  s.ingestTaskEvents(
    [
      {
        id: 101,
        type: "assistant_message",
        message_id: "m1",
        is_final: true,
        provider: "anthropic",
        model: "claude",
        text_blocks: [{ type: "text", text: full }],
      },
    ],
    { jobId: "j1", turnId: "t1" },
  );
  assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 1, "assistant_message never adds a second bubble");
  assert.strictEqual(assistants[0].status, "done");
  assert.strictEqual(assistants[0].metadata.isFinal, true);
  assert.strictEqual(assistants[0].metadata.stream, false, "streaming object cleaned");
  assert.strictEqual(assistants[0].messageId, "m1");
  assert.strictEqual(assistants[0].key, "msg:m1");
});

// 16. two distinct model outputs (different message_id) stay two items.
test("text_delta identity: distinct message_ids stay separate", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents(
    [
      { id: 1, type: "text_delta", delta: "第一段说明", message_id: "m1" },
      { id: 2, type: "tool_call", tool_call_id: "c1", name: "read_file", input: {} },
      { id: 3, type: "tool_result", tool_call_id: "c1", ok: true },
      { id: 4, type: "text_delta", delta: "第二段说明", message_id: "m2" },
      { id: 5, type: "assistant_message", message_id: "m1", text_blocks: [{ type: "text", text: "第一段说明" }] },
      { id: 6, type: "assistant_message", message_id: "m2", is_final: true, text_blocks: [{ type: "text", text: "第二段说明" }] },
    ],
    { jobId: "j1", turnId: "t1" },
  );
  const assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 2);
  assert.strictEqual(assistants[0].content.text, "第一段说明");
  assert.strictEqual(assistants[1].content.text, "第二段说明");
  assert.strictEqual(assistants[1].metadata.isFinal, true);
});

// 17. legacy deltas without identity are adopted by the later assistant_message.
test("text_delta legacy compatibility: no identity adopted by message", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents(
    [
      { id: 1, type: "text_delta", delta: "正在分析" },
      { id: 2, type: "text_delta", delta: "代码结构" },
    ],
    { jobId: "j1", turnId: "t1" },
  );
  s.ingestTaskEvents(
    [{ id: 3, type: "assistant_message", message_id: "m9", is_final: true, text_blocks: [{ type: "text", text: "正在分析代码结构，完成。" }] }],
    { jobId: "j1", turnId: "t1" },
  );
  const assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 1, "legacy stream adopted, not duplicated");
  assert.strictEqual(assistants[0].messageId, "m9");
  assert.strictEqual(assistants[0].content.text, "正在分析代码结构，完成。");
  assert.strictEqual(assistants[0].status, "done");
});

// 18. live changes keyed by jobId rekey+merge when canonical turn arrives.
test("live changes then canonical changes: one card", () => {
  const s = Timeline.createStore();
  // Live card arrives before the turn id is known (jobId only).
  s.ingestTaskEvents(
    [{ id: 1, type: "changes", files: [{ path: "a.kt", change: "modified" }] }],
    { jobId: "j1" },
  );
  let changes = s.items().filter((i) => i.type === "changes");
  assert.strictEqual(changes.length, 1);
  // Canonical event carries the real turn id and the full file list.
  s.ingestConversationEvents([
    convEvent(1, "changes", {
      files: [
        { path: "a.kt", change: "modified" },
        { path: "b.xml", change: "added" },
      ],
      diff_status: "ready",
    }),
  ]);
  changes = s.items().filter((i) => i.type === "changes");
  assert.strictEqual(changes.length, 1, "live+canonical merge into one card");
  assert.strictEqual(changes[0].key, "changes:t1", "rekeyed to the stable turn key");
  assert.strictEqual(changes[0].turnId, "t1");
  assert.strictEqual(changes[0].content.files.length, 2);
  assert.strictEqual(changes[0].metadata.diffStatus, "ready");
});

// 19. live tool without turnId is anchored when canonical events arrive.
test("live tool without turnId anchored by canonical events", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents(
    [
      { id: 1, type: "tool_call", tool_call_id: "c1", name: "read_file", input: { path: "x.kt" } },
      { id: 2, type: "tool_result", tool_call_id: "c1", ok: true, duration_ms: 5 },
    ],
    { jobId: "j1" },
  );
  let tools = s.items().filter((i) => i.type === "tool");
  assert.strictEqual(tools.length, 1);
  assert.strictEqual(tools[0].turnId, null);
  s.ingestConversationEvents([
    convEvent(1, "tool_call", { tool_call_id: "c1", name: "read_file", input: { path: "x.kt" } }),
    convEvent(2, "tool_result", { tool_call_id: "c1", name: "read_file", ok: true, duration_ms: 5 }),
  ]);
  tools = s.items().filter((i) => i.type === "tool");
  assert.strictEqual(tools.length, 1, "still exactly one tool");
  assert.strictEqual(tools[0].turnId, "t1", "anchored to the persisted turn");
});

// 20. live assistant stream merges with the canonical assistant_message.
test("live assistant stream merges with canonical message", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents(
    [
      { id: 1, type: "text_delta", delta: "分析", message_id: "m1" },
      { id: 2, type: "text_delta", delta: "完成", message_id: "m1" },
    ],
    { jobId: "j1", turnId: "t1" },
  );
  s.ingestConversationEvents([
    convEvent(1, "assistant_message", {
      message_id: "m1",
      is_final: true,
      text_blocks: [{ type: "text", text: "分析完成。" }],
    }),
  ]);
  const assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 1);
  assert.strictEqual(assistants[0].content.text, "分析完成。");
  assert.strictEqual(assistants[0].status, "done");
  assert.strictEqual(assistants[0].turnId, "t1");
});

// 21. full reconnect replay of every event keeps counts stable.
test("reconnect full replay keeps entity counts stable", () => {
  const mk = () => {
    const s = Timeline.createStore();
    const conv = [
      convEvent(1, "user_message", { message_id: "u1", content: [{ type: "text", text: "改一下首页" }] }),
      convEvent(2, "assistant_message", { message_id: "m1", text_blocks: [{ type: "text", text: "先看看。" }] }),
      convEvent(3, "tool_call", { tool_call_id: "c1", name: "read_file", input: {} }),
      convEvent(4, "tool_result", { tool_call_id: "c1", name: "read_file", ok: true }),
      convEvent(5, "approval_required", { approval_id: "a1", tool_call_id: "c2", kind: "process" }),
      convEvent(6, "approval_resolved", { approval_id: "a1", decision: "approved" }),
      convEvent(7, "changes", { files: [{ path: "a.kt", change: "modified" }] }),
      convEvent(8, "assistant_message", { message_id: "m2", is_final: true, text_blocks: [{ type: "text", text: "完成。" }] }),
      convEvent(9, "completed", { message: "任务完成" }),
    ];
    s.ingestConversationEvents(conv);
    return s;
  };
  const once = mk();
  const twice = mk();
  // Simulate reconnect: replay the SAME page again on the second store.
  twice.ingestConversationEvents([
    convEvent(1, "user_message", { message_id: "u1", content: [{ type: "text", text: "改一下首页" }] }),
    convEvent(2, "assistant_message", { message_id: "m1", text_blocks: [{ type: "text", text: "先看看。" }] }),
    convEvent(3, "tool_call", { tool_call_id: "c1", name: "read_file", input: {} }),
    convEvent(4, "tool_result", { tool_call_id: "c1", name: "read_file", ok: true }),
    convEvent(5, "approval_required", { approval_id: "a1", tool_call_id: "c2", kind: "process" }),
    convEvent(6, "approval_resolved", { approval_id: "a1", decision: "approved" }),
    convEvent(7, "changes", { files: [{ path: "a.kt", change: "modified" }] }),
    convEvent(8, "assistant_message", { message_id: "m2", is_final: true, text_blocks: [{ type: "text", text: "完成。" }] }),
    convEvent(9, "completed", { message: "任务完成" }),
  ]);
  const sig = (store) =>
    store
      .items()
      .map((i) => `${i.type}:${i.key}`)
      .sort()
      .join("|");
  assert.strictEqual(sig(twice), sig(once), "replay adds nothing");
  assert.strictEqual(twice.items().filter((i) => i.type === "assistant_message").length, 2);
  assert.strictEqual(twice.items().filter((i) => i.type === "tool").length, 1);
  assert.strictEqual(twice.items().filter((i) => i.type === "changes").length, 1);
  assert.strictEqual(twice.items().filter((i) => i.type === "approval").length, 1);
});

// 22. private reasoning events never reach the item list.
test("private reasoning is dropped", () => {
  const s = Timeline.createStore();
  s.ingestTaskEvents(
    [
      { id: 1, type: "reasoning", content: "secret thought A" },
      { id: 2, type: "reasoning_delta", delta: "secret thought B" },
      { id: 3, type: "thinking", content: "secret thought C" },
      { id: 4, type: "chain_of_thought", content: "secret thought D" },
      { id: 5, type: "text_delta", delta: "visible answer", message_id: "m1" },
    ],
    { jobId: "j1", turnId: "t1" },
  );
  const all = JSON.stringify(s.items());
  assert.ok(!all.includes("secret thought"), "no private reasoning in items");
  const assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 1);
  assert.strictEqual(assistants[0].content.text, "visible answer");
});

// 23. M4: history-first reconciliation. The conversation page (canonical
// assistant_message) is loaded BEFORE the job watcher replays its task events
// from cursor 0. Replayed text_delta/text events must adopt the settled item
// instead of opening a second bubble that duplicates the turn's text.
test("history-first replay never duplicates turn text", () => {
  const s = Timeline.createStore();
  s.ingestConversationEvents([
    convEvent(1, "user_message", { content: "帮我分析" }),
    convEvent(
      2,
      "assistant_message",
      { message_id: "m1", is_final: false, text_blocks: [{ type: "text", text: "先看一下文件。" }] },
    ),
    convEvent(3, "tool_call", { tool_call_id: "c1", name: "read_file", input: {} }),
    convEvent(4, "tool_result", { tool_call_id: "c1", ok: true }),
    convEvent(
      5,
      "assistant_message",
      { message_id: "m2", is_final: true, text_blocks: [{ type: "text", text: "分析完成，共两个问题。" }] },
    ),
  ]);
  // Watcher replay of the same job from cursor 0 (restart / switch back).
  s.ingestTaskEvents(
    [
      { id: 1, type: "text_delta", delta: "先看", message_id: "m1" },
      { id: 2, type: "text_delta", delta: "一下文件。", message_id: "m1" },
      { id: 3, type: "text", content: "先看一下文件。", message_id: "m1", streamed: true },
      { id: 4, type: "assistant_message", message_id: "m1", text_blocks: [{ type: "text", text: "先看一下文件。" }] },
      { id: 5, type: "text_delta", delta: "分析完成，", message_id: "m2" },
      { id: 6, type: "text_delta", delta: "共两个问题。", message_id: "m2" },
      { id: 7, type: "assistant_message", message_id: "m2", is_final: true, text_blocks: [{ type: "text", text: "分析完成，共两个问题。" }] },
    ],
    { jobId: "j1", turnId: "t1" },
  );
  const assistants = s.items().filter((i) => i.type === "assistant_message");
  assert.strictEqual(assistants.length, 2, "no extra bubbles from replay");
  assert.strictEqual(assistants[0].content.text, "先看一下文件。", "m1 text not doubled");
  assert.strictEqual(assistants[1].content.text, "分析完成，共两个问题。", "m2 text not doubled");
  const full = JSON.stringify(s.items());
  assert.strictEqual(full.indexOf("先看一下文件。"), full.lastIndexOf("先看一下文件。"), "m1 text appears exactly once");
  assert.strictEqual(
    full.indexOf("分析完成，共两个问题。"),
    full.lastIndexOf("分析完成，共两个问题。"),
    "m2 text appears exactly once",
  );
});

console.log(`timeline.test: OK (${passed} tests)`);
