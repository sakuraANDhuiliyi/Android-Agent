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

  const eventsByType = Object.fromEntries(
    eventsPage.events.map((event) => [event.event_type, event.payload]),
  );
  const buildSummary = eventsByType.build_summary;
  assert.strictEqual(buildSummary.kind, "build");
  assert.strictEqual(buildSummary.task, "assembleDebug");
  assert.strictEqual(buildSummary.success, true);
  assert.strictEqual(buildSummary.apk_size_bytes, 2048);
  assert.ok(buildSummary.tool_call_id);
  assert.ok("duration_ms" in buildSummary);
  assert.ok("error_count" in buildSummary);
  const testSummary = eventsByType.test_summary;
  assert.strictEqual(testSummary.kind, "test");
  assert.deepStrictEqual(testSummary.tests, { passed: 10, failed: 2, skipped: 0 });
  assert.deepStrictEqual(eventsByType.changes, {
    files: [
      { path: "app/src/main/AndroidManifest.xml", change: "modified" },
      { path: "app/src/main/java/com/example/demo/SettingsActivity.kt", change: "modified" },
    ],
    additions: 12,
    deletions: 3,
  });
  assert.strictEqual(eventsByType.artifact.kind, "apk");
  assert.ok(eventsByType.artifact.url);

  const turnsPage = assertSuccessFixture("conversation_turns_200.json", [
    "conversation_id",
    "schema_version",
    "turns",
  ]);
  const contractTurn = turnsPage.turns[0];
  assert.strictEqual(contractTurn.id, "turn-001");
  assert.strictEqual(contractTurn.task_id, "job-001");
  assert.strictEqual(contractTurn.status, "succeeded");
  assert.ok(contractTurn.trace_id);
  assert.strictEqual(contractTurn.user_preview, "Add dark mode toggle");
  assert.strictEqual(contractTurn.event_counts.build_summary, 1);

  const trace = assertSuccessFixture("turn_trace_200.json", [
    "schema_version",
    "conversation_id",
    "turn_id",
    "task_id",
    "job_id",
    "trace_id",
    "status",
    "created_at",
    "queue_ms",
    "total_ms",
    "steps",
  ]);
  assert.strictEqual(trace.trace_id, "trace-001");
  assert.strictEqual(trace.queue_ms, 500);
  assert.strictEqual(trace.total_ms, 24000);
  assert.ok(trace.steps.length >= 2);
  for (const step of trace.steps) {
    assert.ok("seq" in step);
    assert.ok("at" in step);
    assert.ok("type" in step);
    assert.ok("label" in step);
    assert.ok("detail" in step);
    assert.ok("event_id" in step);
    assert.ok("duration_ms" in step);
  }
  assert.ok(trace.steps.some((step) => step.type === "user_message"));
  assert.ok(trace.steps.some((step) => step.label === "Turn 完成"));

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

  const projectSettings = assertSuccessFixture("project_settings_200.json", [
    "user_id",
    "project_id",
    "settings",
    "permission_profiles",
  ]);
  assert.strictEqual(projectSettings.settings.permission_profile, "standard");
  assert.ok(Array.isArray(projectSettings.settings.disabled_rules));
  assert.ok(Array.isArray(projectSettings.settings.disabled_skills));
  assert.deepStrictEqual(
    projectSettings.permission_profiles.map((item) => item.profile),
    ["safe", "standard", "full_access"],
  );
  for (const profile of projectSettings.permission_profiles) {
    assert.ok(profile.label);
    assert.ok(profile.risk_actions);
    assert.ok("read" in profile.risk_actions);
  }

  const rulesPage = assertSuccessFixture("rules_200.json", [
    "candidates",
    "loaded",
    "skipped",
    "total_chars",
    "budget",
  ]);
  const disabledRule = rulesPage.candidates.find((item) => !item.enabled);
  assert.ok(disabledRule, "rules fixture should contain a disabled rule");
  assert.strictEqual(disabledRule.id, "rules:legacy-java.md");
  assert.ok(
    rulesPage.skipped.some((item) => item.reason === "disabled_by_user"),
  );

  const skillsPage = assertSuccessFixture("skills_200.json", ["skills"]);
  const enabledSkill = skillsPage.skills.find((item) => item.enabled);
  const disabledSkill = skillsPage.skills.find((item) => !item.enabled);
  assert.strictEqual(enabledSkill.name, "android-build");
  assert.strictEqual(disabledSkill.name, "release-apk");

  const conversationUsage = assertSuccessFixture("usage_conversation_200.json", [
    "user_id",
    "conversation_id",
    "totals",
    "turns",
  ]);
  assert.strictEqual(conversationUsage.totals.turns, 2);
  assert.strictEqual(conversationUsage.totals.tool_calls, 18);
  assert.ok(conversationUsage.totals.cost_available);
  assert.ok(conversationUsage.turns.every((turn) => "cached_ratio" in turn));

  const usageSummary = assertSuccessFixture("usage_summary_200.json", [
    "totals",
    "by_model",
    "by_day",
    "project_count",
  ]);
  assert.strictEqual(usageSummary.totals.turns, 42);
  assert.strictEqual(usageSummary.by_model[0].model, "gpt-4o");
  assert.ok(usageSummary.by_day.every((day) => /^\d{4}-\d{2}-\d{2}$/.test(day.date)));

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
