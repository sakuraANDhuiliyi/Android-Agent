const assert = require('node:assert/strict');
const { Store, normalize, STORAGE_KEY } = require('../src/job-messages');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const storage = () => { const data = new Map(); return { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) }; };
const job = { id: 'job', project_id: 'project', conversation_id: 'conversation' };
function fixture(saved = storage()) {
  let number = 0;
  const who = { connected: true, userId: 'alice' };
  const calls = [], reads = [];
  const client = { baseUrl: 'https://isolated.invalid', sessionVersion: 1, token: 'never-store-this-token',
    sendJobMessage(id, type, payload, key) { const pending = deferred(); calls.push({ id, type, payload, key, ...pending }); return pending.promise; },
    jobMessages(id, options) { const pending = deferred(); reads.push({ id, options, ...pending }); return pending.promise; } };
  const store = new Store(client, () => who, { storage: saved, uuid: () => `stable-uuid-${++number}` });
  const scope = store.scope(job);
  return { store, client, who, scope, calls, reads, saved };
}
function receipt(row, state = 'pending', overrides = {}) {
  return { schema_version: 1, id: 1, task_id: 'job', message_key: row.message_key,
    type: row.type, payload: row.payload, created_at: 100, consumed_at: state === 'pending' ? null : 101,
    delivery_state: state, context_message_id: state === 'consumed' ? 'canonical-message' : null,
    follow_up_job_id: state === 'follow_up_created' ? 'child' : null, follow_up_turn_id: state === 'follow_up_created' ? 'child-turn' : null,
    reason: null, ...overrides };
}
const ack = message => ({ schema_version: 1, job_id: 'job', message });
const list = messages => ({ schema_version: 1, job_id: 'job', messages });

(async () => {
  const f = fixture();
  const a = f.store.create(f.scope, 'steer', 'same text');
  const pending = f.store.submit(f.scope, a.message_key);
  assert.equal(f.store.submit(f.scope, a.message_key), pending);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].key, a.message_key);
  assert.equal(f.saved.getItem(STORAGE_KEY).includes('never-store-this-token'), false);
  f.calls[0].reject(new Error('response lost')); await pending;
  assert.equal(f.store.list(f.scope)[0].phase, 'unconfirmed');

  // A fresh process only loads the exact immutable intent. No automatic POST.
  const reloaded = fixture(f.saved);
  assert.equal(reloaded.store.list(reloaded.scope)[0].message_key, a.message_key);
  assert.equal(reloaded.calls.length, 0);
  const retry = reloaded.store.retry(reloaded.scope, a.message_key);
  assert.equal(reloaded.calls.length, 0, 'GET comes before an explicit retry');
  reloaded.reads[0].resolve(list([receipt(a, 'consumed')])); await retry;
  assert.equal(reloaded.calls.length, 0, 'a lost response is reconciled without repeating POST');
  assert.equal(reloaded.store.list(reloaded.scope)[0].delivery_state, 'consumed');
  assert.deepEqual(JSON.parse(reloaded.saved.getItem(STORAGE_KEY)), []);

  const tryAgain = f.store.retry(f.scope, a.message_key);
  f.reads[0].resolve(list([])); await new Promise(setImmediate);
  assert.equal(f.calls[1].key, a.message_key);
  assert.deepEqual(f.calls[1].payload, { text: 'same text' });
  f.calls[1].resolve(ack(receipt(a))); await tryAgain;
  const b = f.store.create(f.scope, 'steer', 'same text');
  assert.notEqual(a.message_key, b.message_key, 'same text in a new intent gets a new UUID');

  // Out-of-order pending POST/list responses cannot overwrite consumed evidence.
  f.store.merge(f.scope, receipt(a, 'consumed'));
  f.store.merge(f.scope, receipt(a, 'pending'));
  assert.equal(f.store.list(f.scope).find(row => row.message_key === a.message_key).delivery_state, 'consumed');
  assert.equal(f.store.merge(f.scope, receipt(a, 'consumed', { id: 999 })), false);
  for (const raw of [receipt(a, 'consumed', { context_message_id: null }), receipt(a, 'consumed', { consumed_at: -1 }), receipt(a, 'evil')]) {
    assert.equal(normalize(raw, f.scope).delivery_state, 'unknown');
  }
  for (const raw of [
    receipt(a, 'pending', { consumed_at: 101 }), receipt(a, 'pending', { context_message_id: 'unexpected' }),
    receipt(a, 'pending', { follow_up_job_id: 'child' }), receipt(a, 'blocked', { consumed_at: null }),
    receipt(a, 'unapplied', { type: 'follow_up', consumed_at: null }), receipt(a, 'consumed', { follow_up_turn_id: 'unexpected' }),
    receipt(a, 'follow_up_created', { type: 'follow_up', follow_up_job_id: 'job' }),
    receipt(a, 'follow_up_created', { type: 'follow_up', context_message_id: 'unexpected' }),
    receipt(a, 'blocked', { type: 'follow_up', consumed_at: 101 }),
  ]) assert.equal(normalize(raw, f.scope).delivery_state, 'unknown', 'contradictory receipt fields are unknown');
  for (const raw of [receipt(a, 'pending', { schema_version: 2 }), receipt(a, 'pending', { task_id: 'other' }), receipt(a, 'pending', { id: 0 })]) {
    assert.equal(normalize(raw, f.scope), null);
  }

  // Failed and stale responses are never applied to a different account/server.
  const g = fixture(); const row = g.store.create(g.scope, 'follow_up', 'keep original body');
  const late = g.store.submit(g.scope, row.message_key);
  g.who.userId = 'bob'; g.client.sessionVersion += 1;
  g.calls[0].resolve(ack(receipt(row, 'follow_up_created'))); await late;
  assert.equal(g.store.list(g.store.scope(job)).length, 0);
  assert.equal(g.store.list(g.scope)[0].phase, 'unconfirmed');
  g.who.userId = 'alice'; g.client.baseUrl = 'https://another.invalid';
  assert.equal(g.store.list(g.store.scope(job)).length, 0);
  assert.equal(g.store.list({ ...g.scope, conversation: 'other' }).length, 0);
  assert.equal(g.store.list({ ...g.scope, project: 'other' }).length, 0);

  for (const code of [403, 404, 409, 422, 500]) {
    const h = fixture(); const item = h.store.create(h.scope, 'steer', 'body');
    const promise = h.store.submit(h.scope, item.message_key);
    h.calls[0].reject(Object.assign(new Error('server error'), { status: code })); await promise;
    assert.equal(h.store.list(h.scope)[0].retryable, code === 500);
  }
  const redacted = fixture(); const c = redacted.store.create(redacted.scope, 'steer', 'password=synthetic_receipt_secret');
  const cp = redacted.store.submit(redacted.scope, c.message_key);
  redacted.calls[0].reject(new Error('response lost')); await cp;
  const reopened = fixture(redacted.saved);
  const rp = reopened.store.retry(reopened.scope, c.message_key);
  reopened.reads[0].resolve(list([receipt(c, 'consumed', { payload: { text: 'password=[REDACTED]' } })]));
  await new Promise(setImmediate);
  assert.equal(reopened.calls[0].key, c.message_key);
  assert.equal(reopened.calls[0].payload.text, 'password=synthetic_receipt_secret');
  reopened.calls[0].resolve(ack(receipt(c, 'consumed', { payload: { text: 'password=[REDACTED]' } }))); await rp;
  assert.equal(reopened.store.list(reopened.scope)[0].delivery_state, 'consumed');
  assert.deepEqual(JSON.parse(reopened.saved.getItem(STORAGE_KEY)), []);

  const child = fixture(); const childRow = receipt({ message_key: 'child-key', type: 'follow_up', payload: { text: 'next' } }, 'follow_up_created');
  child.client.job = async id => ({ job: { ...job, id, turn_id: 'child-turn', status: 'paused' } });
  assert.equal((await child.store.child(child.scope, childRow)).status, 'paused');
  child.client.job = async id => ({ job: { ...job, id, turn_id: 'child-turn', conversation_id: 'foreign' } });
  await assert.rejects(child.store.child(child.scope, childRow), /归属/);
  child.client.job = async id => ({ job: { ...job, id, turn_id: 'wrong-turn' } });
  await assert.rejects(child.store.child(child.scope, childRow), /归属/);
  const disabled = fixture({ getItem: () => '{invalid', setItem: () => { throw new Error('storage full'); } });
  disabled.store.create(disabled.scope, 'steer', 'retained in memory');
  assert.equal(disabled.store.persistenceError, true);
  console.log('job-messages.test: OK (stable retry, restart reconciliation, identity, ordering, failures, child isolation)');
})().catch(error => { console.error(error); process.exitCode = 1; });
