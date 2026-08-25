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

  console.log("agent-windows.test: OK");
}

run();
