const assert = require("assert");
const fs = require("fs");
const http = require("http");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "../src/agent-api.js"), "utf8");

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.listeners = {};
    FakeWebSocket.last = this;
    setTimeout(() => this.emit("open"), 0);
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener() {}
  emit(type, data) {
    (this.listeners[type] || []).forEach((fn) => fn(data));
  }
  send() {}
  close() { this.readyState = 3; }
  inject(payload) {
    this.emit("message", { data: JSON.stringify(payload) });
  }
}

const context = {
  window: {},
  fetch: global.fetch,
  URL,
  WebSocket: FakeWebSocket,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
};
vm.createContext(context);
vm.runInNewContext(code, context);
const AgentApi = context.window.AgentApi;

let server = null;
let requests = [];
let lastResponse = { status: "ok" };

function startServer(handler) {
  return new Promise((resolve) => {
    server = http.createServer((req, res) => {
      const record = { method: req.method, url: req.url, headers: req.headers };
      requests.push(record);
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        const parsed = body ? JSON.parse(body) : {};
        record.body = parsed;
        handler(req, res, parsed);
      });
    });
    server.listen(0, "127.0.0.1", () => {
      resolve(server.address().port);
    });
  });
}

function stopServer() {
  return new Promise((resolve) => server?.close?.(resolve) || resolve());
}

async function run() {
  const port = await startServer((req, res, body) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ...lastResponse, path: req.url, method: req.method, body }));
  });

  const api = new AgentApi();
  api.configure({ baseUrl: `http://127.0.0.1:${port}`, token: "secret" });

  const health = await api.health();
  assert.strictEqual(health.path, "/api/health");
  assert.strictEqual(health.method, "GET");

  const projects = await api.projects();
  assert.strictEqual(projects.path, "/api/projects");

  const conv = await api.createConversation("p1", "test");
  assert.strictEqual(conv.path, "/api/projects/p1/conversations");
  assert.strictEqual(conv.body.title, "test");

  await api.renameConversation("c1", "new");
  const renameReq = requests.find((r) => r.url === "/api/conversations/c1" && r.method === "PATCH");
  assert.ok(renameReq);

  await api.archiveConversation("c1");
  const archiveReq = requests.find((r) => r.url === "/api/conversations/c1" && r.method === "DELETE");
  assert.ok(archiveReq);

  await api.pauseJob("j1");
  const pauseReq = requests.find((r) => r.url === "/api/jobs/j1/pause" && r.method === "POST");
  assert.ok(pauseReq);

  await api.steerJob("j1", "focus");
  const steerReq = requests.find((r) => r.url === "/api/jobs/j1/messages" && r.method === "POST");
  assert.ok(steerReq);
  assert.strictEqual(steerReq.body.type, "steer");
  assert.strictEqual(steerReq.body.payload.text, "focus");
  assert.ok(steerReq.body.message_key);

  await api.restoreCheckpoint("p1", "cp1", "app/src/Main.kt");
  const restoreReq = requests.find((r) => r.url.includes("/checkpoints/cp1/restore"));
  assert.deepStrictEqual(restoreReq.body, { path: "app/src/Main.kt" });

  await api.turnDiff("p1", "turn1");
  const diffReq = requests.find((r) => r.url === "/api/projects/p1/diff?turn_id=turn1");
  assert.ok(diffReq);

  await api.createTerminal("p1", { shell: "/bin/bash" });
  const termReq = requests.find((r) => r.url === "/api/projects/p1/terminals" && r.method === "POST");
  assert.ok(termReq);

  await api.terminalInput("t1", "hello");
  const inputReq = requests.find((r) => r.url === "/api/terminals/t1/input" && r.method === "POST");
  assert.ok(inputReq);

  // Authorization header
  const authed = requests.find((r) => r.headers.authorization === "Bearer secret");
  assert.ok(authed);

  await stopServer();
  console.log("agent-api.test: OK");
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
