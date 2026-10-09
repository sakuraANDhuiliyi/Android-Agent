const assert = require('node:assert/strict');
const { describe } = require('../src/delivery-ui');
const run = { state: 'passed', task: 'assembleDebug', run_id: 'call-build', evidence_time: 1791500000, duration_ms: 1250, source_match: 'match', reason: null };
const tests = { ...run, task: 'testDebugUnitTest', run_id: 'call-tests', counts: { total: 3, passed: 2, failed: 0, skipped: 1 } };
const job = { id: 'job-a', status: 'succeeded', has_apk: true, has_build_log: true,
  verification: { schema_version: 1, job_id: 'job-a', scope: 'job', build: run, unit_tests: tests, installation: { state: 'unknown' } } };
const check = (value, key) => describe(value).checks.find(row => row.key === key);
function altered(part, value) { return { ...job, verification: { ...job.verification, [part]: value } }; }
let cases = 0;
function test(name, fn) { fn(); cases++; console.log('ok - ' + name); }
test('successful command and JUnit have separate facts; APK never proves installation', () => {
  assert.equal(check(job, 'build').state, 'passed');
  assert.equal(check(job, 'unit_tests').value, '通过 · 2 通过 / 0 失败 / 1 跳过');
  assert.equal(check(job, 'apk').available, true);
  assert.equal(check(job, 'installation').state, 'unknown');
  assert.equal(check(altered('installation', { state: 'passed' }), 'installation').state, 'unknown');
});
test('a succeeded job alone carries no verification', () => {
  for (const value of [undefined, null, {}, { status: 'succeeded' }, { ...job, verification: undefined, has_apk: false, has_build_log: false }]) {
    assert.equal(describe(value).checks.filter(row => row.available).length, 0);
  }
});
test('schema, scope and job identity must match exactly', () => {
  for (const patch of [{ schema_version: 2 }, { schema_version: '1' }, { job_id: 'other' }, { scope: 'project' }, { job_id: null }]) {
    const value = { ...job, verification: { ...job.verification, ...patch } };
    assert.equal(check(value, 'build').state, 'unknown'); assert.equal(check(value, 'unit_tests').state, 'unknown');
  }
});
test('missing invocation identity/time or wrong Gradle task cannot pass', () => {
  for (const patch of [{ run_id: '' }, { run_id: null }, { evidence_time: null }, { evidence_time: Infinity }, { evidence_time: -1 }, { task: 'assembleRelease' }, { state: 'constructor' }]) {
    assert.equal(check(altered('build', { ...run, ...patch }), 'build').state, 'unknown');
  }
});
test('malformed totals never produce a success badge', () => {
  for (const counts of [{ total: -1, passed: -1, failed: 0, skipped: 0 }, { total: '3', passed: 2, failed: 0, skipped: 1 }, { total: 3, passed: 3, failed: 0, skipped: 1 }, { total: 1.5, passed: 1.5, failed: 0, skipped: 0 }, { total: Infinity, passed: Infinity, failed: 0, skipped: 0 }, { total: 3, passed: 3 }, { total: 1, passed: 0, failed: 1, skipped: 0 }, null]) {
    assert.equal(check(altered('unit_tests', { ...tests, counts }), 'unit_tests').state, 'unknown');
  }
});
test('zero tests, missing reports and skipped tests are distinct', () => {
  const zero = { total: 0, passed: 0, failed: 0, skipped: 0 };
  assert.equal(check(altered('unit_tests', { ...tests, state: 'no_tests', counts: zero }), 'unit_tests').state, 'no_tests');
  assert.equal(check(altered('unit_tests', { ...tests, state: 'unknown', counts: null }), 'unit_tests').state, 'unknown');
  assert.equal(check(altered('unit_tests', { ...tests, state: 'skipped', counts: { total: 3, passed: 0, failed: 0, skipped: 3 } }), 'unit_tests').state, 'skipped');
  assert.equal(check(altered('unit_tests', { ...tests, state: 'passed', counts: zero }), 'unit_tests').state, 'unknown');
  assert.equal(check(altered('unit_tests', { ...tests, state: 'passed', counts: { total: 3, passed: 0, failed: 0, skipped: 3 } }), 'unit_tests').state, 'unknown');
  assert.equal(check(altered('unit_tests', { ...tests, state: 'no_tests' }), 'unit_tests').state, 'unknown');
});
test('failure/cancel/interruption remain distinct while a prior build can pass', () => {
  for (const state of ['failed', 'canceled', 'interrupted']) {
    const value = altered('unit_tests', { ...tests, state, counts: null });
    value.status = state === 'interrupted' ? 'failed' : state;
    assert.equal(check(value, 'unit_tests').state, state); assert.equal(check(value, 'build').state, 'passed');
  }
});
test('execution fact and end-of-turn input association are independent', () => {
  for (const source_match of ['changed', 'unknown', 'unsupported', null]) {
    const row = check(altered('build', { ...run, source_match }), 'build');
    assert.equal(row.state, 'passed'); assert.equal(row.available, false);
    assert.equal(row.value, '此前执行通过');
    assert.equal(row.sourceMatch, source_match === 'changed' ? 'changed' : 'unknown');
  }
  assert.match(describe(job).note, /不代表之后的工作区/);
});
test('not_run remains distinct without inventing a run', () => {
  const value = altered('unit_tests', { state: 'not_run', task: 'testDebugUnitTest', run_id: null, evidence_time: null, source_match: 'unknown', counts: null });
  assert.equal(check(value, 'unit_tests').state, 'not_run'); assert.equal(check(value, 'unit_tests').evidenceTime, null);
});
test('status and evidence are deterministic without mutating DTO', () => {
  const before = JSON.stringify(job); describe(job); assert.equal(JSON.stringify(job), before);
  assert.match(describe({ status: 'running', cancel_requested: true }).label, /等待执行进程退出/);
  assert.equal(describe({ status: 'canceled' }).terminal, true);
  assert.equal(describe({ status: 'running' }).terminal, false);
  assert.equal(check(altered('build', { ...run, duration_ms: -1 }), 'build').durationMs, null);
});
console.log(`delivery-ui.test: OK (${cases} data matrix groups)`);
