const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
let poll;
class Socket { constructor() { Socket.last = this; } close() {} }
const context = { window: {}, URL, WebSocket: Socket, fetch,
  setTimeout: () => 1, clearTimeout() {}, setInterval: fn => { poll = fn; return 1; }, clearInterval() {},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/agent-api.js'), 'utf8'), context);
const api = new context.window.AgentApi();
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  api.websocketTicket = async () => ({ ticket: 'test' });
  const events = [];
  let watcher = api.watchJob('job', event => events.push(event));
  await tick();
  const oldSocket = Socket.last;
  watcher.close();
  oldSocket.onmessage({ data: JSON.stringify({ id: 1, type: 'text' }) });
  assert.equal(events.length, 0, 'queued websocket events after close must be dropped');
  watcher = api.watchJob('job', event => events.push(event));
  await tick();
  api.configure({ token: 'new-session' });
  Socket.last.onmessage({ data: JSON.stringify({ id: 2, type: 'text' }) });
  assert.equal(events.length, 0, 'old account websocket events must be dropped');
  watcher.close();

  api.websocketTicket = async () => { throw new Error('WS unavailable'); };
  let finish;
  let polls = 0;
  api.job = () => { polls++; return new Promise(resolve => { finish = resolve; }); };
  watcher = api.watchJob('job', event => events.push(event));
  await tick();
  const pending = poll();
  await poll();
  assert.equal(polls, 1, 'slow polls must not overlap');
  watcher.close();
  finish({ job: { status: 'succeeded', events: [{ id: 3, type: 'text' }] } });
  await pending;
  assert.equal(events.length, 0, 'in-flight poll finishing after close must be dropped');
  watcher = api.watchJob('job', event => events.push(event));
  await tick();
  const staleAccountPoll = poll();
  api.configure({ token: 'third-session' });
  finish({ job: { status: 'succeeded', events: [{ id: 4, type: 'text' }] } });
  await staleAccountPoll;
  assert.equal(events.length, 0, 'in-flight poll finishing after account change must be dropped');
  watcher.close();

  const requests = [];
  api.request = () => new Promise(resolve => requests.push(resolve));
  const first = api.recoverJob('same-id');
  api.configure({ token: 'fourth-session' });
  const second = api.recoverJob('same-id');
  assert.notEqual(first, second, 'recoveries are scoped to account credentials');
  requests[0]({ job: { id: 'old-account-result' } });
  await first;
  assert.equal(api.recoverJob('same-id'), second, 'old request cleanup must not remove a newer account request');
  requests[1]({ job: { id: 'new-account-result' } });
  await second;
  console.log('agent-api-watch-recovery.test: OK (closed websocket/poll, account isolation, non-overlap, shared requests)');
})().catch(error => { console.error(error); process.exitCode = 1; });
