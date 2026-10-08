"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { createHash, webcrypto } = require("node:crypto");
const WorkspaceFiles = require("../src/workspace-files");
const digest = (text) => createHash("sha256").update(text).digest("hex");
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

class Element {
  constructor() { this.children = []; this.dataset = {}; this.style = {}; this.hidden = false; this.listeners = {}; this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } }; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text || ""; }
  set innerHTML(value) { this.textContent = value; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  focus() {}
}
const elements = new Map();
const document = { getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); }, createElement() { return new Element(); }, querySelectorAll() { return []; } };
const models = new Map();
const monaco = { Uri: { parse: (uri) => uri }, editor: {
  getModel: (uri) => models.get(uri),
  setModelLanguage() {},
  createModel(content, language, uri) {
    let value = content, version = 1;
    const listeners = [];
    const model = { uri, getValue: () => value, getAlternativeVersionId: () => version, getLanguageId: () => language,
      setValue(next) { value = next; version += 1; listeners.forEach((fn) => fn()); },
      onDidChangeContent: (fn) => listeners.push(fn), dispose() { models.delete(uri); },
    };
    models.set(uri, model); return model;
  },
} };
const editor = { setModel() {}, updateOptions() {}, focus() {}, getPosition: () => null, saveViewState() {}, restoreViewState() {} };
const writes = [], localCalls = [];
const data = new Map();
let delayedRead = null, delayedList = null, delayedWrite = null, conflict = false;
class Api {
  configure(values) { Object.assign(this, values); }
  async listProjectFiles(id) { return delayedList?.id === id ? delayedList.promise : { entries: [{ path: "app/a.kt", name: id, type: "file" }] }; }
  async searchProjectFiles() { return { entries: [{ path: "app/a.kt" }] }; }
  async readProjectFile(id, path) {
    if (delayedRead?.id === id) return delayedRead.promise;
    const content = data.get(id) || id;
    return { content, revision: digest(content), writable: true, truncated: path.endsWith("large.kt") };
  }
  async writeProjectFile(id, path, content, expected) {
    writes.push({ id, path, content, expected });
    if (conflict) throw Object.assign(new Error("文件已发生变化"), { status: 409 });
    if (delayedWrite) await delayedWrite.promise;
    data.set(id, content);
  }
}
const client = new Api(); client.configure({ baseUrl: "https://example.test", token: "alice" });
const aiState = { userId: "alice", selectedProjectId: "a" };
const window = {
  WorkspaceFiles, crypto: webcrypto,
  agentDesktop: {
    async readTree() { localCalls.push("tree"); return { children: [] }; },
    async listFiles() { return []; },
    async readFile(path) { localCalls.push(["read", path]); return { content: "local" }; },
    async writeFile(path, content) { localCalls.push(["write", path, content]); return { ok: true }; },
    async relative(_root, path) { return path; },
  },
  AiPanel: { client, getState: () => aiState, onWorkspaceChanged() {}, onActiveFileChanged() {}, selectProject: async (id) => { aiState.selectedProjectId = id; await test.openRemoteProject({ id }, client, aiState.userId); } },
  confirm: () => true, dispatchEvent() {},
};
const context = { window, document, console, TextEncoder, setTimeout: () => 1, clearTimeout() {}, localStorage: { getItem: () => null, setItem() {} }, CustomEvent: class {}, CSS: { escape: (s) => s } };
// Run the production renderer functions with a model/DOM adapter; no copied logic.
const source = fs.readFileSync(require.resolve("../src/renderer.js"), "utf8").replace("  bootMonaco();", `  window.test = { state, openRemoteProject, openProjectFile, openPath, saveActive, reloadPathIfOpen, openFolder, activeTab, visibleTabs, refreshTree, initialize(m, e) { monaco = m; editor = e; } };`);
vm.runInNewContext(source, context);
const test = window.test;
test.initialize(monaco, editor);
window.EditorApp = test;

async function run() {
  await test.openRemoteProject({ id: "a", name: "A" }, client, "alice");
  assert.equal(test.state.root, null);
  assert.equal(elements.get("explorerRootName").textContent, "云端 · A");
  assert.equal(test.state.fileIndex[0], "app/a.kt");
  const a = await test.openPath("app/a.kt");
  a.model.setValue("changed");
  await test.saveActive();
  assert.deepEqual(writes[0], { id: "a", path: "app/a.kt", content: "changed", expected: digest("a") });
  assert.equal(a.revision, digest("changed"));
  assert.equal(a.dirty, false);
  assert.equal(localCalls.length, 0);

  a.model.setValue("draft"); conflict = true;
  await test.saveActive();
  assert.equal(a.dirty, true);
  assert.equal(a.model.getValue(), "draft");
  assert.equal(a.revision, digest("changed"));
  assert.match(elements.get("toast").textContent, /草稿已保留/);
  conflict = false;

  await test.openRemoteProject({ id: "b" }, client, "alice");
  const b = await test.openPath("app/a.kt");
  assert.notEqual(a.model.uri, b.model.uri);
  assert.equal(test.visibleTabs().length, 1);
  assert.equal(b.model.getValue(), "b");
  await test.openRemoteProject({ id: "a" }, client, "alice");
  assert.equal(test.activeTab(), a);
  assert.equal(a.model.getValue(), "draft");

  delayedWrite = deferred();
  const writesBeforeSave = writes.length;
  const saving = test.saveActive();
  // Let hashing complete so we actually cover editing during the pending PUT.
  while (writes.length === writesBeforeSave) await new Promise((r) => setImmediate(r));
  a.model.setValue("newer draft");
  delayedWrite.resolve(); await saving; delayedWrite = null;
  assert.equal(a.dirty, true);
  assert.equal(a.revision, digest("draft"));
  await test.saveActive();
  assert.equal(writes.at(-1).expected, digest("draft"));

  const large = await test.openPath("app/large.kt");
  const count = writes.length;
  await test.saveActive();
  assert.equal(large.writable, false);
  assert.equal(writes.length, count);
  await assert.rejects(test.openPath("/tmp/not-a-cloud-file"), /相对路径/);
  assert.equal(localCalls.length, 0);

  delayedRead = { id: "a", ...deferred() };
  const opening = test.openPath("app/slow.kt");
  await test.openRemoteProject({ id: "b" }, client, "alice");
  delayedRead.resolve({ content: "late a", revision: digest("late a") });
  await opening; delayedRead = null;
  assert.equal(test.activeTab(), b);
  assert(!test.state.tabs.some((tab) => tab.path === "app/slow.kt"));

  delayedList = { id: "a", ...deferred() };
  const selectingA = test.openRemoteProject({ id: "a" }, client, "alice");
  await test.openRemoteProject({ id: "b" }, client, "alice");
  delayedList.resolve({ entries: [{ name: "wrong-a", path: "app/wrong.kt", type: "file" }] });
  await selectingA; delayedList = null;
  assert.equal(test.state.scope.projectId, "b");
  assert(!elements.get("fileTree").children.some((el) => el.dataset.path === "app/wrong.kt"));

  await test.openFolder("/local");
  const local = await test.openPath("/local/app/a.kt");
  assert.equal(local.scope.kind, "local");
  const localCount = localCalls.length;
  aiState.selectedProjectId = "a";
  await test.openProjectFile({ id: "a" }, "app/a.kt");
  assert.equal(test.activeTab(), a);
  assert.equal(localCalls.length, localCount);
  client.token = "bob"; aiState.userId = "bob";
  await test.openRemoteProject({ id: "a" }, client, "bob");
  assert.equal(test.visibleTabs().length, 0);
  assert.equal(test.activeTab(), null);
  console.log("remote-editor.test: OK (cloud read/write, conflict drafts, in-flight edits, project/account isolation, stale responses, readonly truncation, local separation)");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
