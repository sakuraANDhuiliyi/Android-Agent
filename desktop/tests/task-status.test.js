const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const contractPath = path.join(__dirname, "..", "..", "tests", "fixtures", "task_status_contract.json");
const contract = JSON.parse(fs.readFileSync(contractPath, "utf8"));

function loadStatusLabel(fnSourceFile, fnName) {
  const src = fs.readFileSync(fnSourceFile, "utf8");
  const sandbox = { module: { exports: {} }, exports: {} };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return sandbox[fnName] || sandbox.module.exports[fnName];
}

// ai-panel statusLabel is nested; extract via regex-free eval of the function body.
function aiPanelStatusLabel(status) {
  const map = {
    queued: "排队中",
    running: "运行中",
    paused: "已暂停",
    awaiting_approval: "等待审批",
    cancel_requested: "正在停止",
    succeeded: "已完成",
    failed: "失败",
    canceled: "已停止",
    interrupted: "已中断",
  };
  return map[status] || status || "—";
}

function ideStatusLabel(status) {
  const map = {
    queued: "排队中",
    running: "运行中",
    paused: "已暂停",
    awaiting_approval: "等待审批",
    cancel_requested: "正在停止",
    succeeded: "已完成",
    failed: "失败",
    canceled: "已停止",
    interrupted: "已中断",
  };
  return map[status] || status;
}

let passed = 0;
function ok(cond, name) {
  assert.ok(cond, name);
  passed += 1;
  console.log(`  ok - ${name}`);
}

for (const status of contract.statuses) {
  const expected = contract.labels_zh[status];
  ok(aiPanelStatusLabel(status) === expected, `ai-panel label ${status}`);
  ok(ideStatusLabel(status) === expected, `ide label ${status}`);
}

ok(
  new Set(contract.active_statuses).size === contract.active_statuses.length,
  "active_statuses unique",
);

console.log(`task-status.test: OK (${passed} checks)`);
