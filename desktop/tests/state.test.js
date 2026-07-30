const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "../src/state.js"), "utf8");

const context = {
  window: {},
  localStorage: {
    _data: {},
    getItem(k) { return this._data[k] || null; },
    setItem(k, v) { this._data[k] = v; },
  },
};
vm.createContext(context);
vm.runInNewContext(code, context);
const { reducer, mergeEvents, getState, dispatch } = context.window.DesktopState;

function run() {
  // Initial state
  const s0 = reducer({}, { type: "LAYOUT_SIDEBAR_VIEW", view: "search" });
  assert.strictEqual(s0.sidebarView, "search");
  assert.strictEqual(s0.sidebarCollapsed, false);

  // Project selection resets conversation state
  const s1 = reducer(s0, { type: "SELECT_PROJECT", projectId: "p1" });
  assert.strictEqual(s1.selectedProjectId, "p1");
  assert.strictEqual(s1.conversationId, null);

  // Select conversation
  const s2 = reducer(s1, { type: "SELECT_CONVERSATION", conversationId: "c1" });
  assert.strictEqual(s2.conversationId, "c1");
  assert.strictEqual(s2.jobEvents.length, 0);

  // Job events merge
  const s3 = reducer(s2, {
    type: "JOB_EVENTS",
    events: [{ id: 1, type: "text" }, { id: 2, type: "text" }],
    status: "running",
  });
  assert.strictEqual(s3.jobEvents.length, 2);
  assert.strictEqual(s3.lastEventId, 2);
  assert.strictEqual(s3.running, true);

  // Duplicate events ignored
  const s4 = reducer(s3, {
    type: "JOB_EVENTS",
    events: [{ id: 1, type: "text" }, { id: 3, type: "text" }],
  });
  assert.strictEqual(s4.jobEvents.length, 3);
  assert.strictEqual(s4.lastEventId, 3);

  // Context chips
  const s5 = reducer(s4, { type: "ADD_CONTEXT_CHIP", chip: { key: "file:x", kind: "file", label: "x" } });
  assert.strictEqual(s5.contextChips.length, 1);
  const s6 = reducer(s5, { type: "REMOVE_CONTEXT_CHIP", key: "file:x" });
  assert.strictEqual(s6.contextChips.length, 0);

  // Merge events function
  const merged = mergeEvents([{ id: 1 }, { id: 3 }], [{ id: 2 }, { id: 3 }]);
  const mergedIds = merged.map((e) => e.id);
  assert.strictEqual(mergedIds.length, 3);
  assert.strictEqual(mergedIds[0], 1);
  assert.strictEqual(mergedIds[1], 2);
  assert.strictEqual(mergedIds[2], 3);

  // Terminal selection
  const s7 = reducer(s6, { type: "SELECT_TERMINAL", terminalId: "t1" });
  assert.strictEqual(s7.activeTerminalId, "t1");

  console.log("state.test: OK");
}

run();
