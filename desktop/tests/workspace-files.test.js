"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { createHash } = require("node:crypto");
const { remoteScope, remotePath } = require("../src/workspace-files");
const digest = (text) => createHash("sha256").update(text).digest("hex");

async function run() {
  for (const path of ["/tmp/file", "C:\\file", "../file", "app/../../file", "file:///tmp/file", "", "app/\0file"]) {
    assert.throws(() => remotePath(path), undefined, path);
  }
  assert.equal(remotePath("app\\src/./Main.kt"), "app/src/Main.kt");
  assert.equal(remotePath(".", true), ".");
  let pending;
  const calls = [];
  class FakeApi {
    configure(config) { Object.assign(this, config); }
    async listProjectFiles(id, path) { calls.push(["list", id, path]); return { entries: [{ name: "src", path: "app/src", type: "dir" }] }; }
    async searchProjectFiles() { return { entries: [] }; }
    async readProjectFile() { return pending || { content: "old", revision: digest("old"), writable: true }; }
    async writeProjectFile(...args) { calls.push(["write", ...args]); }
  }
  const api = new FakeApi();
  api.configure({ baseUrl: "https://example.test", token: "sensitive-token" });
  const scope = remoteScope(api, { id: "p1", name: "Cloud" }, "alice");
  assert.equal((await scope.list())[0].loaded, false);
  assert.equal((await scope.read("app/a.kt")).writable, true);
  const saved = await scope.write("app/a.kt", "new", digest("old"));
  assert.equal(saved.revision, digest("new"));
  assert.deepEqual(calls[1], ["write", "p1", "app/a.kt", "new", digest("old")]);
  await assert.rejects(scope.write("app/a.kt", "new", null), /缺少文件版本/);
  assert(!scope.uri("app/a.kt").includes("sensitive-token"));
  assert.notEqual(scope.uri("app/a.kt"), remoteScope(api, { id: "p2" }, "alice").uri("app/a.kt"));
  assert.notEqual(scope.uri("app/a.kt"), remoteScope(api, { id: "p1" }, "bob").uri("app/a.kt"));
  pending = Promise.resolve({ content: "partial", truncated: true, revision: digest("partial"), writable: true });
  assert.equal((await scope.read("app/a.kt")).writable, false);
  let resolve;
  pending = new Promise((r) => { resolve = r; });
  const reading = scope.read("app/a.kt");
  api.token = "another-account";
  resolve({ content: "old account secret" });
  await assert.rejects(reading, /账号或服务已切换/);
  await assert.rejects(scope.write("app/a.kt", "x", digest("old")), /账号或服务已切换/);

  // Exercise the real HTTP client's route/body encoding without binding a port.
  const requests = [];
  const context = { window: {}, fetch: async (url, options) => {
    requests.push({ url, ...options });
    return { ok: true, status: 200, text: async () => "{}" };
  } };
  vm.runInNewContext(fs.readFileSync(require.resolve("../src/agent-api.js"), "utf8"), context);
  const real = new context.window.AgentApi();
  real.configure({ baseUrl: "https://example.test", token: "test" });
  await real.listProjectFiles("p 1", "app/src");
  await real.readProjectFile("p 1", "app/a b.kt");
  await real.writeProjectFile("p 1", "app/a b.kt", "content", digest("old"));
  assert.equal(requests[0].url, "https://example.test/api/projects/p%201/files?path=app%2Fsrc");
  assert.equal(requests[1].url, "https://example.test/api/projects/p%201/files/content?path=app%2Fa%20b.kt");
  assert.equal(requests[2].method, "PUT");
  assert.deepEqual(JSON.parse(requests[2].body), { path: "app/a b.kt", content: "content", expected_revision: digest("old") });
  console.log("workspace-files.test: OK (paths, scope isolation, account switch, truncation, revision writes, API contracts)");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
