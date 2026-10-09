const assert = require('node:assert/strict');
const { Store, normalize, normalizeEdit, STORAGE_KEY } = require('../src/job-messages');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const storage = () => { const data = new Map(); return { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) }; };
const job = { id: 'job', project_id: 'project', conversation_id: 'conversation' };
function fixture(saved = storage()) {
  let number = 0;
  const who = { connected: true, userId: 'alice' };
  const calls = [], reads = [], withdrawals = [], edits = [];
  const client = { baseUrl: 'https://isolated.invalid', sessionVersion: 1, token: 'never-store-this-token',
    sendJobMessage(id, type, payload, key) { const pending = deferred(); calls.push({ id, type, payload, key, ...pending }); return pending.promise; },
    withdrawJobMessage(id, messageId) { const pending = deferred(); withdrawals.push({ id, messageId, ...pending }); return pending.promise; },
    editJobMessage(id, messageId, body) { const pending = deferred(); edits.push({ id, messageId, body, ...pending }); return pending.promise; },
    jobMessages(id, options) { const pending = deferred(); reads.push({ id, options, ...pending }); return pending.promise; } };
  const store = new Store(client, () => who, { storage: saved, uuid: () => `stable-uuid-${++number}` });
  const scope = store.scope(job);
  return { store, client, who, scope, calls, reads, withdrawals, edits, saved };
}
function receipt(row, state = 'pending', overrides = {}) {
  return { schema_version: 1, id: 1, task_id: 'job', message_key: row.message_key,
    type: row.type, payload: row.payload, created_at: 100, consumed_at: ['pending', 'blocked', 'withdrawn'].includes(state) ? null : 101,
    delivery_state: state, context_message_id: state === 'consumed' ? 'canonical-message' : null,
    follow_up_job_id: state === 'follow_up_created' ? 'child' : null, follow_up_turn_id: state === 'follow_up_created' ? 'child-turn' : null,
    withdrawn_at: state === 'withdrawn' ? 102 : null, can_withdraw: row.type === 'follow_up' && ['pending', 'blocked'].includes(state),
    revision: 0, edited_at: null, can_edit: row.type === 'follow_up' && state === 'pending',
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

  const followBody = { message_key: 'withdraw-key', type: 'follow_up', payload: { text: 'queued next task' } };
  const pendingFollow = receipt(followBody);
  const withdrawn = receipt(followBody, 'withdrawn');
  const createdFollow = receipt(followBody, 'follow_up_created');
  for (const extra of [{ type: 'steer' }, { consumed_at: 101 }, { context_message_id: 'context' },
    { follow_up_job_id: 'child' }, { follow_up_turn_id: 'turn' }, { withdrawn_at: null }, { withdrawn_at: 0 },
    { withdrawn_at: '102' }, { withdrawn_at: Infinity }, { can_withdraw: true }]) {
    const parsed = normalize({ ...withdrawn, ...extra }, f.scope);
    assert.equal(parsed.delivery_state, 'unknown'); assert.equal(parsed.can_withdraw, false);
  }
  for (const flag of [undefined, null, 1, 'true', false]) assert.equal(normalize({ ...pendingFollow, can_withdraw: flag }, f.scope).can_withdraw, false);
  assert.equal(normalize({ ...pendingFollow, withdrawn_at: 101 }, f.scope).delivery_state, 'unknown');
  assert.equal(normalize(receipt(followBody, 'blocked'), f.scope).can_withdraw, true);
  assert.equal(normalize({ ...createdFollow, can_withdraw: true }, f.scope).can_withdraw, false);
  const w = fixture(); w.store.merge(w.scope, pendingFollow);
  const wp = w.store.withdraw(w.scope, followBody.message_key);
  assert.equal(w.store.withdraw(w.scope, followBody.message_key), wp, 'withdrawal double click shares one request');
  assert.deepEqual(w.withdrawals.map(({ id, messageId }) => ({ id, messageId })), [{ id: 'job', messageId: 1 }]);
  assert.equal(JSON.parse(w.saved.getItem(STORAGE_KEY))[0].kind, 'withdraw');
  assert.ok(!w.saved.getItem(STORAGE_KEY).includes(w.client.token));
  w.withdrawals[0].reject(new Error('withdraw response lost')); await wp;
  assert.equal(w.store.list(w.scope)[0].withdrawPhase, 'unconfirmed');
  const wr = fixture(w.saved);
  assert.equal(wr.withdrawals.length, 0, 'restart never posts withdrawal');
  assert.equal(wr.store.list(wr.scope)[0].can_withdraw, false, 'cached eligibility is not authoritative');
  const reconcileWithdrawal = wr.store.retryWithdrawal(wr.scope, followBody.message_key);
  wr.reads[0].resolve(list([withdrawn])); await reconcileWithdrawal;
  assert.equal(wr.withdrawals.length, 0, 'lost success is recovered using GET only');
  assert.equal(wr.store.list(wr.scope)[0].delivery_state, 'withdrawn');
  assert.deepEqual(JSON.parse(wr.saved.getItem(STORAGE_KEY)), []);
  wr.store.merge(wr.scope, pendingFollow);
  assert.equal(wr.store.list(wr.scope)[0].delivery_state, 'withdrawn', 'late pending cannot resurrect withdrawn message');
  assert.equal(wr.store.list(wr.scope)[0].can_withdraw, false);

  const retryWithdraw = w.store.retryWithdrawal(w.scope, followBody.message_key);
  w.reads[0].resolve(list([pendingFollow])); await new Promise(setImmediate);
  assert.equal(w.withdrawals[1].messageId, 1, 'explicit retry uses original numeric ID');
  w.withdrawals[1].resolve(ack(withdrawn)); await retryWithdraw;
  assert.equal(w.store.list(w.scope)[0].withdrawPhase, undefined);
  assert.equal(w.calls.length, 0, 'withdrawing never resends original message');

  const conflictWithdrawal = fixture(); conflictWithdrawal.store.merge(conflictWithdrawal.scope, pendingFollow);
  const conflicted = conflictWithdrawal.store.withdraw(conflictWithdrawal.scope, followBody.message_key);
  conflictWithdrawal.withdrawals[0].reject(Object.assign(new Error('created already'), { status: 409,
    message: 'untrusted error body with wrong child' })); await new Promise(setImmediate);
  assert.equal(conflictWithdrawal.reads.length, 1, '409 reconciles via authoritative GET');
  conflictWithdrawal.reads[0].resolve(list([createdFollow])); await conflicted;
  assert.equal(conflictWithdrawal.store.list(conflictWithdrawal.scope)[0].follow_up_job_id, 'child');
  assert.equal(conflictWithdrawal.store.list(conflictWithdrawal.scope)[0].withdrawPhase, undefined);
  assert.equal(conflictWithdrawal.calls.length, 0);
  assert.deepEqual(JSON.parse(conflictWithdrawal.saved.getItem(STORAGE_KEY)), []);

  const denied = fixture(); denied.store.merge(denied.scope, pendingFollow);
  denied.store.merge(denied.scope, { ...pendingFollow, can_withdraw: false });
  await denied.store.withdraw(denied.scope, followBody.message_key);
  assert.equal(denied.withdrawals.length, 0, 'fresh eligibility revocation removes action');
  denied.store.merge(denied.scope, receipt(followBody, 'blocked'));
  const blockedWithdrawal = denied.store.withdraw(denied.scope, followBody.message_key);
  denied.withdrawals[0].resolve(ack(withdrawn)); await blockedWithdrawal;
  assert.equal(denied.store.list(denied.scope)[0].delivery_state, 'withdrawn', 'blocked follow-up may be withdrawn');

  for (const mode of ['wrong-job', 'wrong-id', 'wrong-key', 'contradictory', 'offline']) {
    const bad = fixture(); bad.store.merge(bad.scope, pendingFollow);
    const p = bad.store.withdraw(bad.scope, followBody.message_key);
    if (mode === 'offline') bad.withdrawals[0].reject(new Error('offline'));
    else bad.withdrawals[0].resolve(ack({ ...withdrawn, ...(mode === 'wrong-job' ? { task_id: 'other' } :
      mode === 'wrong-id' ? { id: 2 } : mode === 'wrong-key' ? { message_key: 'other' } : { consumed_at: 101 }) }));
    await p; assert.equal(bad.store.list(bad.scope)[0].withdrawPhase, 'unconfirmed');
    const retry = bad.store.retryWithdrawal(bad.scope, followBody.message_key);
    bad.reads[0].reject(new Error('GET offline')); await retry;
    assert.equal(bad.withdrawals.length, 1, 'failed GET never causes another withdrawal POST');
  }

  // Late old-session completion must not end a new session's withdrawal.
  const stale = fixture(); stale.store.merge(stale.scope, pendingFollow);
  const oldWithdrawal = stale.store.withdraw(stale.scope, followBody.message_key);
  stale.who.userId = 'bob'; stale.client.sessionVersion++;
  assert.equal(stale.store.list(stale.store.scope(job)).length, 0);
  stale.who.userId = 'alice'; stale.client.sessionVersion++;
  const newScope = stale.store.scope(job);
  const newWithdrawal = stale.store.withdraw(newScope, followBody.message_key);
  stale.withdrawals[0].resolve(ack(withdrawn)); await oldWithdrawal;
  assert.equal(stale.store.list(newScope)[0].withdrawPhase, 'sending');
  assert.equal(stale.store.list(newScope)[0].delivery_state, 'pending');
  stale.withdrawals[1].resolve(ack(withdrawn)); await newWithdrawal;
  assert.equal(stale.store.list(newScope)[0].delivery_state, 'withdrawn');

  const editedReceipt = (number, text, state = 'pending', extra = {}) => receipt(followBody, state,
    { revision: number, edited_at: number ? 100 + number : null, payload: { text }, ...extra });
  const editReply = (f, index, currentMessage, overrides = {}) => {
    const request = f.edits[index];
    return { ...ack(currentMessage), edit: { schema_version: 1, task_id: 'job', message_id: 1,
      edit_key: request.body.edit_key, expected_revision: request.body.expected_revision,
      revision: request.body.expected_revision + 1, created_at: 200, payload: request.body.payload, ...overrides } };
  };
  for (const extra of [{ revision: undefined }, { revision: '0' }, { revision: -1 }, { revision: 1.5 },
    { revision: 0, edited_at: 101 }, { revision: 1, edited_at: null }, { revision: 1, edited_at: NaN },
    { can_edit: 'true' }, { can_edit: 1 }, { can_edit: undefined }, { type: 'steer' }, { delivery_state: 'blocked' }]) {
    assert.equal(normalize({ ...pendingFollow, ...extra }, f.scope).can_edit, false);
  }
  const e = fixture(); e.store.merge(e.scope, pendingFollow); e.store.openEditor(e.scope, followBody.message_key);
  e.store.updateEditor(e.scope, followBody.message_key, 'first revision');
  const ep = e.store.saveEdit(e.scope, followBody.message_key);
  await e.store.saveEdit(e.scope, followBody.message_key);
  assert.equal(e.edits.length, 1); assert.notEqual(e.edits[0].body.edit_key, followBody.message_key);
  assert.equal(e.edits[0].body.expected_revision, 0);
  assert.equal(e.saved.getItem(STORAGE_KEY).includes(e.client.token), false);
  e.store.updateEditor(e.scope, followBody.message_key, 'newer unsaved draft');
  e.edits[0].resolve(editReply(e, 0, editedReceipt(1, 'first revision'))); await ep;
  assert.equal(e.store.list(e.scope)[0].editor.text, 'newer unsaved draft');
  assert.equal(e.store.list(e.scope)[0].editor.baseRevision, 1);
  assert.equal(e.store.list(e.scope)[0].id, pendingFollow.id);
  assert.equal(e.store.list(e.scope)[0].created_at, pendingFollow.created_at);
  assert.equal(e.store.list(e.scope)[0].message_key, pendingFollow.message_key);
  assert.equal(JSON.parse(e.saved.getItem(STORAGE_KEY))[0].kind, 'edit_draft');
  const newDraftReload = fixture(e.saved);
  assert.equal(newDraftReload.store.list(newDraftReload.scope)[0].editor.text, 'newer unsaved draft');
  assert.equal(newDraftReload.store.list(newDraftReload.scope)[0].can_edit, false);
  assert.equal(newDraftReload.edits.length, 0);
  newDraftReload.store.closeEditor(newDraftReload.scope, followBody.message_key);
  assert.deepEqual(JSON.parse(newDraftReload.saved.getItem(STORAGE_KEY)), []);
  e.store.merge(e.scope, editedReceipt(0, 'stale original'));
  assert.equal(e.store.list(e.scope)[0].payload.text, 'first revision');
  e.store.merge(e.scope, { ...pendingFollow, revision: undefined, can_edit: undefined });
  assert.equal(e.store.list(e.scope)[0].revision, 1, 'legacy snapshots cannot overwrite versioned content');

  assert.equal(e.store.list(e.scope)[0].can_edit, false);
  assert.equal(e.store.list(e.scope)[0].can_withdraw, false);
  assert.equal(await e.store.saveEdit(e.scope, followBody.message_key), false);
  assert.equal(e.edits.length, 1);
  for (const invalidRevision of [undefined, '2', -1, 1.5]) {
    const legacy = fixture(); legacy.store.merge(legacy.scope, editedReceipt(2, 'latest'));
    legacy.store.openEditor(legacy.scope, followBody.message_key);
    legacy.store.merge(legacy.scope, { ...pendingFollow, revision: invalidRevision });
    assert.equal(legacy.store.list(legacy.scope)[0].revision, 2);
    assert.equal(legacy.store.list(legacy.scope)[0].can_edit, false);
    assert.equal(await legacy.store.saveEdit(legacy.scope, followBody.message_key), false);
    assert.equal(legacy.edits.length, 0);
  }
  const noStorage = fixture(); noStorage.store.merge(noStorage.scope, pendingFollow);
  noStorage.store.openEditor(noStorage.scope, followBody.message_key);
  noStorage.store.updateEditor(noStorage.scope, followBody.message_key, 'must persist before send');
  const workingSetItem = noStorage.saved.setItem;
  noStorage.saved.setItem = () => { throw new Error('disk unavailable'); };
  assert.equal(await noStorage.store.saveEdit(noStorage.scope, followBody.message_key), false);
  assert.equal(noStorage.edits.length, 0, 'failed durable edit identity never sends a POST');
  assert.equal(noStorage.store.list(noStorage.scope)[0].editIntent.payload.text, 'must persist before send');

  const stableDiskKey = noStorage.store.list(noStorage.scope)[0].editIntent.edit_key;
  noStorage.saved.setItem = workingSetItem;
  const diskRetry = noStorage.store.retryEdit(noStorage.scope, followBody.message_key);
  noStorage.reads[0].resolve(list([pendingFollow])); await new Promise(setImmediate);
  assert.equal(noStorage.edits[0].body.edit_key, stableDiskKey);
  noStorage.edits[0].resolve(editReply(noStorage, 0, editedReceipt(1, 'must persist before send'))); await diskRetry;
  assert.equal(noStorage.store.list(noStorage.scope)[0].editIntent, undefined);

  const secretEdit = fixture(); secretEdit.store.merge(secretEdit.scope, pendingFollow);
  secretEdit.store.openEditor(secretEdit.scope, followBody.message_key);
  secretEdit.store.updateEditor(secretEdit.scope, followBody.message_key, 'password=synthetic_edit_secret');
  const secretPromise = secretEdit.store.saveEdit(secretEdit.scope, followBody.message_key);
  secretEdit.store.updateEditor(secretEdit.scope, followBody.message_key, 'new draft survives reload');
  secretEdit.edits[0].reject(new Error('response lost')); await secretPromise;
  const reopenedEdit = fixture(secretEdit.saved);
  assert.equal(reopenedEdit.edits.length, 0);
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].editor.text, 'new draft survives reload');
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].can_edit, false);
  const sameTextGet = reopenedEdit.store.reconcile(reopenedEdit.scope);
  reopenedEdit.reads[0].resolve(list([editedReceipt(1, 'password=synthetic_edit_secret')])); await sameTextGet;
  assert.ok(reopenedEdit.store.list(reopenedEdit.scope)[0].editIntent, 'same-body GET cannot acknowledge this edit key');
  reopenedEdit.store.closeEditor(reopenedEdit.scope, followBody.message_key);
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].editor.open, false);
  reopenedEdit.store.openEditor(reopenedEdit.scope, followBody.message_key);
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].editor.open, true);
  const exactRetry = reopenedEdit.store.retryEdit(reopenedEdit.scope, followBody.message_key);
  reopenedEdit.reads[1].resolve(list([editedReceipt(2, 'latest from another client', 'follow_up_created')]));
  await new Promise(setImmediate);
  assert.deepEqual(reopenedEdit.edits[0].body, secretEdit.edits[0].body, 'manual retry retains exact key/revision/body even after child creation');
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].can_edit, false);
  reopenedEdit.edits[0].resolve(editReply(reopenedEdit, 0, editedReceipt(1, 'password=[REDACTED]'), { payload: { text: 'password=[REDACTED]' } }));
  await exactRetry;
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].revision, 2, 'old ACK never rolls back newer current receipt');
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].payload.text, 'latest from another client');
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].delivery_state, 'follow_up_created');
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].editIntent, undefined);
  assert.equal(reopenedEdit.store.list(reopenedEdit.scope)[0].editor.text, 'new draft survives reload');
  assert.equal(JSON.parse(reopenedEdit.saved.getItem(STORAGE_KEY))[0].kind, 'edit_draft');

  const cas = fixture(); cas.store.merge(cas.scope, pendingFollow); cas.store.openEditor(cas.scope, followBody.message_key);
  cas.store.updateEditor(cas.scope, followBody.message_key, 'my preserved draft');
  const cp2 = cas.store.saveEdit(cas.scope, followBody.message_key);
  cas.edits[0].reject(Object.assign(new Error('revision changed'), { status: 409 })); await new Promise(setImmediate);
  cas.reads[0].resolve(list([editedReceipt(1, 'other editor changed this')])); await cp2;
  assert.equal(cas.store.list(cas.scope)[0].editor.text, 'my preserved draft');
  assert.equal(cas.store.list(cas.scope)[0].editor.baseRevision, 0);
  assert.equal(cas.store.list(cas.scope)[0].editIntent, undefined);
  const conflictReload = fixture(cas.saved);
  assert.equal(conflictReload.store.list(conflictReload.scope)[0].editor.text, 'my preserved draft');
  assert.equal(conflictReload.edits.length, 0);
  assert.equal(conflictReload.store.list(conflictReload.scope)[0].can_edit, false);
  await assert.rejects(cas.store.saveEdit(cas.scope, followBody.message_key), /核对/);
  cas.store.rebaseEditor(cas.scope, followBody.message_key);
  const cp3 = cas.store.saveEdit(cas.scope, followBody.message_key);
  assert.notEqual(cas.edits[0].body.edit_key, cas.edits[1].body.edit_key);
  assert.equal(cas.edits[1].body.expected_revision, 1);
  cas.edits[1].resolve(editReply(cas, 1, editedReceipt(2, 'my preserved draft'))); await cp3;
  assert.equal(cas.store.list(cas.scope)[0].editor, undefined);
  assert.equal(cas.calls.length, 0); assert.equal(cas.withdrawals.length, 0, 'editing never withdraws or sends a new message');

  for (const extra of [{ schema_version: 2 }, { task_id: 'other' }, { message_id: 99 }, { edit_key: '' },
    { expected_revision: -1 }, { revision: 3 }, { created_at: '200' }, { payload: { text: '' } }]) {
    const sample = editReply(cas, 0, editedReceipt(1, 'first')).edit;
    assert.equal(normalizeEdit({ ...sample, ...extra }, cas.scope, 1), null);
  }
  for (const terminal of ['withdrawn', 'follow_up_created']) {
    const t = fixture(); t.store.merge(t.scope, pendingFollow); t.store.openEditor(t.scope, followBody.message_key);
    t.store.updateEditor(t.scope, followBody.message_key, 'accepted revision');
    const p = t.store.saveEdit(t.scope, followBody.message_key);
    t.edits[0].reject(new Error('lost')); await p;
    const retry = t.store.retryEdit(t.scope, followBody.message_key);
    t.reads[0].resolve(list([editedReceipt(1, 'accepted revision', terminal)])); await new Promise(setImmediate);
    assert.equal(t.edits.length, 2);
    t.edits[1].resolve(editReply(t, 1, editedReceipt(1, 'accepted revision', terminal))); await retry;
    assert.equal(t.store.list(t.scope)[0].delivery_state, terminal);
    assert.equal(t.store.list(t.scope)[0].editIntent, undefined);
  }
  const editStale = fixture(); editStale.store.merge(editStale.scope, pendingFollow); editStale.store.openEditor(editStale.scope, followBody.message_key);
  editStale.store.updateEditor(editStale.scope, followBody.message_key, 'same frozen edit');
  const oldEdit = editStale.store.saveEdit(editStale.scope, followBody.message_key);
  editStale.client.sessionVersion++;
  const newEditScope = editStale.store.scope(job);
  const newEdit = editStale.store.submitEdit(newEditScope, followBody.message_key);
  editStale.edits[0].resolve(editReply(editStale, 0, editedReceipt(1, 'same frozen edit'))); await oldEdit;
  assert.equal(editStale.store.list(newEditScope)[0].editIntent.phase, 'sending');
  editStale.edits[1].resolve(editReply(editStale, 1, editedReceipt(1, 'same frozen edit'))); await newEdit;
  assert.equal(editStale.store.list(newEditScope)[0].editIntent, undefined);
  console.log('job-messages.test: OK (stable retry, restart reconciliation, identity, ordering, failures, child isolation)');
})().catch(error => { console.error(error); process.exitCode = 1; });
