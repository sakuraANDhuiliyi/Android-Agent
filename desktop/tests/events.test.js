const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "../src/agent-api.js"), "utf8");

class FakeWebSocket {
  constructor(url) {
    this.url = new URL(url);
    this.readyState = 1;
    this.listeners = {};
    FakeWebSocket.last = this;
    setTimeout(() => this.emit("open"), 0);
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener() {}
  set onmessage(fn) { this._onmessage = fn; }
  set onerror(fn) { this._onerror = fn; }
  set onclose(fn) { this._onclose = fn; }
  emit(type, data) {
    const handler = { message: "_onmessage", error: "_onerror", close: "_onclose" }[type];
    if (handler && this[handler]) this[handler](data);
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
  fetch: () => Promise.resolve({ status: 200, text: () => "{}", ok: true }),
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

async function run() {
  const api = new AgentApi();
  api.configure({ baseUrl: "http://127.0.0.1:8000", token: "tok" });

  const events = [];
  const watcher = api.watchJob("j1", (msg) => events.push(msg), { afterEventId: 5 });

  FakeWebSocket.last.inject({ id: 6, type: "text" });
  FakeWebSocket.last.inject({ id: 7, type: "text" });
  FakeWebSocket.last.inject({ id: 6, type: "text" }); // duplicate (client deduplicates via state reducer)

  await new Promise((r) => setTimeout(r, 50));
  assert.strictEqual(events.filter((e) => e.kind === "event").length, 3);
  assert.strictEqual(FakeWebSocket.last.url.searchParams.get("after_event_id"), "5");

  watcher.close();
  console.log("events.test: OK");
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
