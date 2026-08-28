const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(
  path.join(__dirname, "../src/codexia-agent/AgentView.js"),
  "utf8",
);

const context = {
  window: {},
  localStorage: {
    getItem() { return null; },
    setItem() {},
  },
  setInterval,
  clearInterval,
  Date,
  Set,
  Map,
};
vm.createContext(context);
vm.runInContext(code, context);

const internal = context.window.CodexiaAgentView._internal;

function run() {
  assert.strictEqual(internal.displayStatus({ status: "running" }), "running");
  assert.strictEqual(
    internal.displayStatus({ status: "running", cancel_requested: true }),
    "cancel_requested",
  );
  assert.strictEqual(internal.statusClass({ status: "awaiting_approval" }), "pending");
  assert.strictEqual(internal.statusClass({ status: "failed" }), "failed");

  const projects = [{ id: "p1", name: "Demo", workspace: "/tmp/demo" }];
  const jobs = [
    { id: "old", project_id: "p1", status: "succeeded", created_at: 10, prompt: "old" },
    { id: "active", project_id: "p1", status: "running", created_at: 5, prompt: "active" },
    { id: "hidden", project_id: "p1", status: "running", created_at: 20, prompt: "hidden" },
  ];
  const cards = internal.normalizeJobs(jobs, projects, new Set(["hidden"]));
  assert.deepStrictEqual(Array.from(cards, (card) => card.id), ["active", "old"]);
  assert.strictEqual(cards[0].project.name, "Demo");
  assert.strictEqual(cards[0].statusClass, "running");

  const events = internal.syntheticEvents({
    id: "j1",
    prompt: "Build the page",
    result: "Done",
    created_at: 1,
    finished_at: 2,
  });
  assert.strictEqual(events.length, 2);
  assert.strictEqual(events[0].type, "user_message");
  assert.strictEqual(events[1].type, "assistant_message");
  assert.strictEqual(internal.titleFor({ id: "j1", prompt: "  Build   the page  " }), "Build the page");

  const streamed = internal.displayEventsFor({
    id: "j2",
    events: [
      { id: 1, type: "user_message", content: [{ type: "text", text: "请修复" }] },
      { id: 2, type: "text_delta", message_id: "m1", delta: "这是一" },
      { id: 3, type: "text_delta", message_id: "m1", delta: "句完整回复。" },
      { id: 4, type: "assistant_message", message_id: "m1", text_blocks: [{ type: "text", text: "这是一句完整回复。" }] },
    ],
  });
  assert.strictEqual(streamed.filter((event) => event.type === "assistant_message").length, 1);
  assert.strictEqual(streamed.find((event) => event.type === "assistant_message")._displayText, "这是一句完整回复。");

  const legacyFragments = internal.displayEventsFor({
    id: "j3",
    events: [
      { id: 1, type: "assistant", content: "一句话" },
      { id: 2, type: "assistant", content: "不应被拆开" },
    ],
  });
  assert.strictEqual(legacyFragments.length, 1);
  assert.strictEqual(legacyFragments[0]._displayText, "一句话不应被拆开");

  console.log("agent-windows.test: OK");
}

run();
