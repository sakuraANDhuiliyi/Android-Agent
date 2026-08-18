(() => {
  "use strict";

  const assert = require("assert");
  const fs = require("fs");
  const path = require("path");
  const vm = require("vm");

  const FIXTURES = path.join(__dirname, "../../tests/fixtures/api_contract");
  const code = fs.readFileSync(path.join(__dirname, "../src/agent-api.js"), "utf8");
  const context = { window: {}, fetch: global.fetch, URL, WebSocket: class {}, setTimeout, clearTimeout, setInterval, clearInterval };
  vm.createContext(context);
  vm.runInNewContext(code, context);
  const AgentApi = context.window.AgentApi;

  function loadJson(name) {
    return JSON.parse(fs.readFileSync(path.join(FIXTURES, name), "utf8"));
  }

  function assertErrorFixture(name, expectedStatus) {
    const payload = loadJson(name);
    const parsed = AgentApi.parseApiError(payload, expectedStatus, "error");
    assert.strictEqual(parsed.schemaVersion, payload.error.schema_version);
    assert.strictEqual(parsed.code, payload.error.code);
    assert.strictEqual(parsed.retryable, payload.error.retryable);
    assert.strictEqual(parsed.userMessage, payload.error.user_message);
    assert.ok(payload.detail);
  }

  function assertSuccessFixture(name, requiredKeys) {
    const payload = loadJson(name);
    for (const key of requiredKeys) {
      assert.ok(key in payload, `${name} missing ${key}`);
    }
    return payload;
  }

  const manifest = loadJson("manifest.json");
  assert.strictEqual(manifest.schema_version, 1);
  for (const status of manifest.error_statuses) {
    assert.ok(
      manifest.endpoints.some((entry) => entry.status === status),
      `missing fixture for ${status}`,
    );
  }
  for (const entry of manifest.endpoints) {
    assert.ok(entry.fixture, entry.id);
    assert.ok(fs.existsSync(path.join(FIXTURES, entry.fixture)), entry.fixture);
  }
  for (const relative of manifest.client_tests) {
    assert.ok(
      fs.existsSync(path.join(__dirname, "..", "..", relative)),
      relative,
    );
  }

  assertSuccessFixture("health_200.json", [
    "status",
    "user_id",
    "provider",
    "model",
    "api_key_configured",
    "port",
  ]);

  const job = assertSuccessFixture("job_get_200.json", ["job"]).job;
  assert.strictEqual(job.display_status, "running");
  assert.strictEqual(job.status_label, "运行中");
  assert.strictEqual(job.cancel_requested, false);
  assert.ok(job.apk_url);

  const message = assertSuccessFixture("job_message_201.json", ["job_id", "message"]).message;
  assert.strictEqual(message.message_key, "client-msg-001");
  assert.strictEqual(message.type, "steer");

  const eventsPage = assertSuccessFixture("conversation_events_200.json", [
    "conversation_id",
    "schema_version",
    "events",
    "next_after_seq",
    "has_more",
    "direction",
  ]);
  assert.strictEqual(eventsPage.schema_version, 1);
  assert.ok(eventsPage.events.every((event) => event.schema_version === 1));

  const wsDone = assertSuccessFixture("ws/job_done.json", [
    "schema_version",
    "type",
    "status",
    "display_status",
    "status_label",
    "result",
  ]);
  assert.strictEqual(wsDone.type, "done");
  assert.strictEqual(wsDone.display_status, "succeeded");

  const terminalDone = assertSuccessFixture("ws/terminal_done.json", [
    "schema_version",
    "type",
    "status",
    "exit_code",
  ]);
  assert.strictEqual(terminalDone.type, "done");

  assertErrorFixture("errors/unauthorized_401.json", 401);
  assertErrorFixture("errors/not_found_404.json", 404);
  assertErrorFixture("errors/conflict_409.json", 409);
  assertErrorFixture("errors/payload_too_large_413.json", 413);
  assertErrorFixture("errors/validation_422.json", 422);
  assertErrorFixture("errors/rate_limited_429.json", 429);
  assertErrorFixture("errors/internal_error_500.json", 500);

  const deprecation = loadJson("deprecation.json");
  assert.ok(Array.isArray(deprecation.deprecated_fields));

  console.log("api-contract.test: OK");
})();
