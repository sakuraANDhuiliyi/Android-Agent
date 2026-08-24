const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "../src/approval-inbox.js"), "utf8");
const context = { window: {}, Date };
vm.createContext(context);
vm.runInNewContext(code, context);
const inbox = context.window.ApprovalInbox;

const items = inbox.buildApprovalInbox({
  jobs: [
    { id: "j2", project_id: "p1", conversation_id: "c1", status: "succeeded" },
    { id: "j1", project_id: "p1", conversation_id: "c1", status: "awaiting_approval", prompt: "构建" },
  ],
  approvalsByJob: {
    j1: [
      { id: "a2", kind: "network", created_at: 20, payload: { url: "https://example.com/icon.png" } },
      { id: "a1", kind: "process", created_at: 10, payload: { command: "./gradlew clean" } },
    ],
    j2: [{ id: "ignored", kind: "process", created_at: 1, payload: {} }],
  },
  projects: [{ id: "p1", name: "跑酷" }],
  conversations: [{ id: "c1", title: "部署脚本调整" }],
});

assert.strictEqual(items.length, 2);
assert.strictEqual(items[0].approval.id, "a1");
assert.strictEqual(items[0].projectName, "跑酷");
assert.strictEqual(items[0].conversationTitle, "部署脚本调整");
assert.strictEqual(inbox.kindLabel(items[0]), "运行命令");
assert.strictEqual(inbox.intent(items[0]), "./gradlew clean");
assert.strictEqual(inbox.isDestructive(items[0]), true);
assert.strictEqual(inbox.isDestructive(items[1]), false);
assert.strictEqual(inbox.relativeTime(100, 220), "2 分钟前");

console.log("approval-inbox.test: OK");
