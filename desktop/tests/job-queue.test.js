const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { Store, normalizeQueue, normalizeReorder, QUEUE_STORAGE_KEY } = require('../src/job-messages');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const storage = () => { const data = new Map(); return { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) }; };
const job = { id: 'job', project_id: 'project', conversation_id: 'conversation' };
const row = (id, extra = {}) => ({ schema_version: 1, id, task_id: 'job', message_key: `m-${id}`, type: 'follow_up',
  payload: { text: `message ${id}` }, created_at: id, consumed_at: null, delivery_state: 'pending', context_message_id: null,
  follow_up_job_id: null, follow_up_turn_id: null, revision: 0, edited_at: null, withdrawn_at: null,
  can_edit: true, can_withdraw: true, reason: null, ...extra });
const snapshot = (ids = [1, 2, 3], rev = 0, extra = {}, messages = ids.map(id => row(id))) => ({ schema_version: 1, job_id: 'job', messages,
  queue: { schema_version: 1, task_id: 'job', version: `q1:${createHash("sha256").update(`${rev}:${ids.join("-")}`).digest("hex")}`, order_revision: rev,
    message_ids: ids, pending_message_ids: ids, can_reorder: true, reason: null, ...extra } });
function fixture(saved = storage()) {
  let serial = 0;
  const reads = [], posts = [], sends = [], edits = [], withdrawals = [];
  const who = { connected: true, userId: 'alice' };
  const client = { baseUrl: 'https://queue.invalid', sessionVersion: 1, token: 'never-cache-token',
    jobMessages(id) { const d = deferred(); reads.push({ id, ...d }); return d.promise; },
    reorderJobMessages(id, body) { const d = deferred(); posts.push({ id, body, ...d }); return d.promise; },
    sendJobMessage(id, type, payload, key) { const d = deferred(); sends.push({ id, type, payload, key, ...d }); return d.promise; },
    editJobMessage(id, messageId, body) { const d = deferred(); edits.push({ id, messageId, body, ...d }); return d.promise; },
    withdrawJobMessage(id, messageId) { const d = deferred(); withdrawals.push({ id, messageId, ...d }); return d.promise; } };
  const store = new Store(client, () => who, { storage: saved, uuid: () => `uuid-${++serial}` });
  const scope = store.scope(job);
  return { store, scope, client, reads, posts, sends, edits, withdrawals, saved, who };
}
const ack = (request, revision = 1, current = snapshot([1, 3, 2], revision)) => ({ ...current, reorder: {
  schema_version: 1, task_id: 'job', reorder_key: request.body.reorder_key, expected_version: request.body.expected_version,
  message_ids: request.body.message_ids, order_revision: revision, created_at: 300 } });
const tick = () => new Promise(resolve => setImmediate(resolve));
async function load(f, data = snapshot()) { const p = f.store.reconcile(f.scope); f.reads.at(-1).resolve(data); await p; }

(async () => {
  const scope = fixture().scope, good = snapshot();
  assert.ok(normalizeQueue(good.queue, good.messages, scope));
  for (const patch of [ { schema_version: 2 }, { task_id: 'other' }, { order_revision: -1 }, { order_revision: 1.5 },
    { version: 'not-opaque-q1' }, { version: 'q1:short' }, { version: 'q1:' + 'A'.repeat(64) }, { reason: 'parent_failed' }, { reason: 'unknown' }, { version: null }, { can_reorder: 'true' }, { message_ids: [1, 1, 3] },
    { message_ids: [1, 2] }, { pending_message_ids: [2, 1, 3] }, { pending_message_ids: [1, 3] }, { pending_message_ids: [1, 2, 4] } ]) {
    assert.equal(normalizeQueue({ ...good.queue, ...patch }, good.messages, scope), null, JSON.stringify(patch));
  }
  const historical = [row(1, { delivery_state: 'follow_up_created', consumed_at: 100, follow_up_job_id: 'child', follow_up_turn_id: 'ct' }),
    row(2, { delivery_state: 'withdrawn', withdrawn_at: 200, can_withdraw: false }), row(3), row(4)];
  assert.ok(normalizeQueue(snapshot([1, 2, 3, 4], 0, { pending_message_ids: [3, 4] }).queue, historical, scope));
  assert.equal(normalizeQueue(good.queue, [row(1, { delivery_state: 'blocked', reason: 'parent_failed' }), row(2), row(3)], scope), null);
  assert.ok(normalizeQueue({ ...good.queue, can_reorder: false, reason: 'parent_failed' }, [row(1, { delivery_state: 'blocked' }), row(2), row(3)], scope));

  // A whole pending permutation is persisted before the first network write.
  const f = fixture(); await load(f);
  assert.equal(await f.store.moveQueue(f.scope, 1, -1), false);
  assert.equal(await f.store.moveQueue(f.scope, 3, 1), false);
  const moved = f.store.moveQueue(f.scope, 2, 1);
  assert.equal(f.posts.length, 1); assert.deepEqual(f.posts[0].body.message_ids, [1, 3, 2]);
  assert.equal(f.store.queueReady(f.scope), false);
  assert.ok(f.saved.getItem(QUEUE_STORAGE_KEY).includes(f.posts[0].body.reorder_key));
  assert.equal(f.saved.getItem(QUEUE_STORAGE_KEY).includes('never-cache-token'), false);
  assert.equal(await f.store.moveQueue(f.scope, 1, 1), false, 'double clicks cannot start a different intent');
  f.posts[0].reject(new Error('lost accepted response')); await moved;
  const background = f.reads.at(-1); background.resolve(snapshot([1, 3, 2], 1)); await tick();
  assert.ok(f.store.queue(f.scope).pending, 'a matching GET is not an operation ACK');
  const reloaded = fixture(f.saved); assert.equal(reloaded.posts.length, 0); await load(reloaded, snapshot([1, 3, 2], 1));
  assert.equal(reloaded.posts.length, 0, 'reload performs only GET');
  const retried = reloaded.store.retryReorder(reloaded.scope); reloaded.reads.at(-1).resolve(snapshot([3, 1, 2], 2, { can_reorder: false, reason: 'parent_failed' })); await tick();
  assert.deepEqual(reloaded.posts[0].body, f.posts[0].body, 'retry exact key/token/body even after capability disappears');
  reloaded.posts[0].resolve(ack(reloaded.posts[0], 1, snapshot([1, 3, 2], 1))); await retried;
  assert.equal(reloaded.store.queue(reloaded.scope).pending, null);
  assert.deepEqual(reloaded.store.queue(reloaded.scope).snapshot.message_ids, [3, 1, 2], 'old ACK cannot sort');
  reloaded.reads.at(-1).resolve(snapshot([3, 1, 2], 2)); await tick();
  assert.equal(reloaded.store.queueReady(reloaded.scope), true);
  const proper = ack(reloaded.posts[0]).reorder;
  for (const patch of [{ task_id: 'other' }, { reorder_key: 'different' }, { expected_version: 'q1:other' }, { message_ids: [1, 2, 3] }, { order_revision: 0 }, { created_at: NaN }]) {
    assert.equal(normalizeReorder({ ...proper, ...patch }, reloaded.scope, reloaded.posts[0].body), null);
  }

  // Both mutation boundaries fence obsolete GETs; final refresh is a NEW GET.
  for (const kind of ['send', 'edit', 'withdraw']) {
    const x = fixture(); await load(x); const stale = x.store.reconcile(x.scope), oldRead = x.reads.at(-1);
    let operation, request;
    if (kind === 'send') { const local = x.store.create(x.scope, 'follow_up', 'new fourth'); operation = x.store.submit(x.scope, local.message_key); request = x.sends[0]; }
    else if (kind === 'edit') { x.store.openEditor(x.scope, 'm-2'); x.store.updateEditor(x.scope, 'm-2', 'new edit'); operation = x.store.saveEdit(x.scope, 'm-2'); request = x.edits[0]; }
    else { operation = x.store.withdraw(x.scope, 'm-2'); request = x.withdrawals[0]; }
    assert.equal(x.store.queueReady(x.scope), false);
    const during = x.store.reconcile(x.scope), duringRead = x.reads.at(-1); assert.notEqual(duringRead, oldRead);
    if (kind === 'send') request.resolve({ schema_version: 1, job_id: 'job', message: row(4, { message_key: request.key, payload: request.payload }) });
    else if (kind === 'edit') request.resolve({ schema_version: 1, job_id: 'job', message: row(2, { revision: 1, edited_at: 100, payload: request.body.payload }), edit: {
      schema_version: 1, task_id: 'job', message_id: 2, edit_key: request.body.edit_key, expected_revision: 0, revision: 1, created_at: 100, payload: request.body.payload } });
    else request.resolve({ schema_version: 1, job_id: 'job', message: row(2, { delivery_state: 'withdrawn', withdrawn_at: 100, can_withdraw: false }) });
    await operation;
    const freshRead = x.reads.at(-1); assert.notEqual(freshRead, duringRead, `${kind}: completion never reuses in-flight GET`);
    oldRead.resolve(snapshot()); duringRead.resolve(snapshot()); await Promise.all([stale, during]);
    assert.equal(x.store.queueReady(x.scope), false, `${kind}: obsolete GET cannot restore controls`);
    const latest = kind === 'send' ? snapshot([1, 2, 3, 4]) : kind === 'edit' ? snapshot([1, 2, 3], 0, {}, [row(1), row(2, { revision: 1, edited_at: 100, payload: request.body.payload }), row(3)])
      : snapshot([1, 2, 3], 0, { pending_message_ids: [1, 3] }, [row(1), row(2, { delivery_state: 'withdrawn', withdrawn_at: 100, can_withdraw: false }), row(3)]);
    freshRead.resolve(latest); await tick(); assert.equal(x.store.queueReady(x.scope), true);
  }

  const conflict = fixture(); await load(conflict); const cp = conflict.store.moveQueue(conflict.scope, 1, 1);
  conflict.posts[0].reject(Object.assign(new Error('stale token'), { status: 409 })); await cp;
  assert.equal(conflict.store.queue(conflict.scope).pending, null); assert.match(conflict.store.queue(conflict.scope).error, /队列已变化/);
  conflict.reads.at(-1).resolve(snapshot([1, 2, 3, 4])); await tick(); assert.equal(conflict.posts.length, 1, 'CAS never auto-rebases/resends');

  const broken = storage(), originalWrite = broken.setItem; let fail = false;
  broken.setItem = (key, value) => { if (fail && key === QUEUE_STORAGE_KEY) throw new Error('quota'); originalWrite(key, value); };
  const disk = fixture(broken); await load(disk); fail = true;
  assert.equal(await disk.store.moveQueue(disk.scope, 1, 1), false); assert.equal(disk.posts.length, 0);
  const stable = disk.store.queue(disk.scope).pending.reorder_key; fail = false;
  const retry = disk.store.retryReorder(disk.scope); disk.reads.at(-1).resolve(snapshot()); await tick();
  assert.equal(disk.posts[0].body.reorder_key, stable); disk.posts[0].resolve(ack(disk.posts[0])); await retry;
  disk.reads.at(-1).resolve(snapshot([2, 1, 3], 1)); await tick();

  const session = fixture(); await load(session); const sp = session.store.moveQueue(session.scope, 1, 1);
  session.client.sessionVersion++; const newScope = session.store.scope(job); const ng = session.store.reconcile(newScope);
  session.reads.at(-1).resolve(snapshot([3, 2, 1], 2)); await ng;
  session.posts[0].resolve(ack(session.posts[0])); await sp;
  assert.ok(session.store.queue(newScope).pending, 'old session ACK cannot confirm current-session intent');
  assert.deepEqual(session.store.queue(newScope).snapshot.message_ids, [3, 2, 1]);
  assert.equal(session.store.queue(newScope).active.size, 0);
  session.who.userId = 'bob'; assert.equal(session.store.queueReady(newScope), false);
  assert.equal(await session.store.submitReorder(newScope), false); assert.equal(session.posts.length, 1);

  const consistency = fixture(); await load(consistency);
  consistency.store.merge(consistency.scope, row(1, { revision: 1, edited_at: 500, payload: { text: 'newer body' } }));
  await load(consistency, snapshot()); assert.equal(consistency.store.queueReady(consistency.scope), false, 'old body page cannot grant ordering');
  consistency.store.merge(consistency.scope, row(2, { delivery_state: 'follow_up_created', consumed_at: 600, follow_up_job_id: 'child', follow_up_turn_id: 'turn' }));
  await load(consistency, snapshot()); assert.equal(consistency.store.queueReady(consistency.scope), false, 'old state page cannot re-enable pending position');
  assert.equal(normalizeQueue(snapshot([3, 1, 2, 4], 0, { pending_message_ids: [3, 4] }).queue, historical, scope), null, 'created history must be a prefix ignoring withdrawn slots');

  const aba = fixture(); await load(aba); aba.store.watch('surface', aba.scope, true);
  aba.reads.at(-1).resolve(snapshot()); await tick();
  const oldOperation = aba.store.moveQueue(aba.scope, 1, 1);
  const otherScope = aba.store.scope({ ...job, id: 'other-job' });
  aba.store.watch('surface', otherScope, true); aba.store.watch('surface', aba.scope, true);
  aba.reads.at(-1).resolve(snapshot([2, 1, 3], 1)); await tick();
  aba.posts[0].resolve(ack(aba.posts[0])); await oldOperation;
  assert.ok(aba.store.queue(aba.scope).pending, 'ABA late ACK retains original durable intent');
  aba.store.dispose();

  const legacy = fixture(); await load(legacy); await load(legacy, { schema_version: 1, job_id: 'job', messages: good.messages });
  assert.equal(legacy.store.queueReady(legacy.scope), false); assert.deepEqual(legacy.store.queue(legacy.scope).snapshot.message_ids, [1, 2, 3]);
  await load(legacy, snapshot([3, 2, 1], 2)); await load(legacy, snapshot([1, 2, 3], 1));
  assert.equal(legacy.store.queueReady(legacy.scope), false); assert.deepEqual(legacy.store.queue(legacy.scope).snapshot.message_ids, [3, 2, 1]);
  console.log('job-queue.test: OK (strict snapshots/ACKs, durable exact retry, mutation generation barriers, CAS, storage, sessions, legacy)');
})().catch(error => { console.error(error); process.exitCode = 1; });
