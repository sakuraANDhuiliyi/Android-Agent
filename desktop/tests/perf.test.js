const assert = require("assert");
const {
  windowTurns,
  truncateText,
  isBinaryText,
  shouldUseDiffNotice,
  restoreConfirmMessage,
  formatRestoreError,
  createToastThrottle,
  readDraft,
  writeDraft,
  KEEP_TURNS_DEFAULT,
} = require("../src/perf");

function run() {
  const many = Array.from({ length: 120 }, (_, i) => ({ key: `t${i}` }));
  const windowed = windowTurns(many, 40);
  assert.strictEqual(windowed.hidden, 80);
  assert.strictEqual(windowed.turns.length, 40);
  assert.strictEqual(windowed.turns[0].key, "t80");
  assert.strictEqual(windowed.turns[39].key, "t119");
  const small = windowTurns(many.slice(0, 10), KEEP_TURNS_DEFAULT);
  assert.strictEqual(small.hidden, 0);
  assert.strictEqual(small.turns.length, 10);

  const short = truncateText("abc", 10);
  assert.strictEqual(short.truncated, false);
  const long = truncateText("x".repeat(50), 10, "…");
  assert.ok(long.truncated);
  assert.ok(long.text.endsWith("…"));
  assert.ok(long.text.length < 50);

  assert.strictEqual(isBinaryText("hello"), false);
  assert.strictEqual(isBinaryText("a\u0000b"), true);

  assert.strictEqual(shouldUseDiffNotice({ original: "a", modified: "b" }).notice, false);
  assert.strictEqual(shouldUseDiffNotice({ binary: true }).reason, "binary");
  assert.strictEqual(shouldUseDiffNotice({ path: "icon.png" }).reason, "binary");
  const huge = shouldUseDiffNotice({ original: "a".repeat(800_000), modified: "b".repeat(800_000) });
  assert.strictEqual(huge.reason, "too_large");

  const ok = restoreConfirmMessage({ fileCount: 3, kind: "before_turn" });
  assert.strictEqual(ok.blocked, false);
  assert.ok(ok.text.includes("3 个文件"));
  const blocked = restoreConfirmMessage({
    fileCount: 2,
    conflicts: [{ path: "app/Main.kt" }, { path: "res/values.xml" }],
  });
  assert.strictEqual(blocked.blocked, true);
  assert.ok(blocked.text.includes("Main.kt"));

  const conflictMsg = formatRestoreError({
    error: "conflict",
    conflicts: [{ path: "a.kt" }, { path: "b.kt" }],
  });
  assert.ok(conflictMsg.includes("a.kt"));

  let t = 0;
  const gate = createToastThrottle({ minIntervalMs: 1000, now: () => t });
  assert.strictEqual(gate("boom"), true);
  t = 200;
  assert.strictEqual(gate("boom"), false);
  t = 1200;
  assert.strictEqual(gate("boom"), true);
  assert.strictEqual(gate("other"), true);

  const storage = {
    _data: {},
    getItem(k) { return this._data[k] || null; },
    setItem(k, v) { this._data[k] = v; },
  };
  writeDraft(storage, "p1", "c1", "hello draft");
  assert.strictEqual(readDraft(storage, "p1", "c1"), "hello draft");
  writeDraft(storage, "p1", "c1", "");
  assert.strictEqual(readDraft(storage, "p1", "c1"), "");
  writeDraft(storage, "p1", "c2", "kept while offline");
  assert.strictEqual(readDraft(storage, "p1", "c2"), "kept while offline");

  console.log(`ok - ${module.filename}`);
}

run();
