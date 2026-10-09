const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
  if (!file.startsWith(root + path.sep)) return res.writeHead(403).end();
  fs.readFile(file, (err, data) => {
    if (err) return res.writeHead(404).end();
    res.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream'); res.end(data);
  });
});
const job = { id: 'job', project_id: 'project', conversation_id: 'conversation', turn_id: 'turn', status: 'running', prompt: 'current task' };
async function fixture(page) {
  await page.evaluate(job => {
    window.fixtureNumber = (window.fixtureNumber || 0) + 1;
    const api = AiPanel.client;
    api.configure({ baseUrl: location.origin, token: `fixture-${fixtureNumber}` });
    window.requests = []; window.reads = []; window.watchers = []; window.receipts = []; window.jobReads = []; window.withdrawals = []; window.controlRequests = [];
    api.job = async id => { jobReads.push(id); return { job: id === 'child' ? { ...job, id, turn_id: 'child-turn', status: 'paused' } : job }; };
    api.jobs = async () => ({ jobs: [job] }); api.projects = async () => ({ projects: [{ id: 'project', name: 'Project' }] }); api.models = async () => ({ models: [] });
    api.listApprovals = async () => ({ approvals: [] }); api.conversationEvents = async () => ({ events: [], has_more: false });
    api.watchJob = (id, callback) => { watchers.push({ id, callback }); return { close() {} }; };
    api.jobMessages = async id => {
      reads.push(id);
      if (AiPanel.getState().userId !== 'alice') throw Object.assign(new Error('foreign account job'), { status: 404 });
      return { schema_version: 1, job_id: id, messages: receipts.filter(row => row.task_id === id) };
    };
    api.sendJobMessage = (id, type, payload, key) => new Promise((resolve, reject) => { requests.push({ id, type, payload, key, resolve, reject }); });
    api.withdrawJobMessage = (id, messageId) => new Promise((resolve, reject) => { withdrawals.push({ id, messageId, resolve, reject }); });
    api.cancelJob = api.resumeJob = async id => { controlRequests.push(id); throw new Error('unexpected task control'); };
    window.receiptFor = (index, state = 'pending', extra = {}) => {
      const request = requests[index];
      return { schema_version: 1, id: index + 1, task_id: request.id, message_key: request.key, type: request.type,
        payload: request.payload, created_at: 100 + index, consumed_at: ['pending', 'blocked', 'withdrawn'].includes(state) ? null : 200,
        delivery_state: state, context_message_id: state === 'consumed' ? `steer:${index}` : null,
        follow_up_job_id: state === 'follow_up_created' ? 'child' : null, follow_up_turn_id: state === 'follow_up_created' ? 'child-turn' : null,
        withdrawn_at: state === 'withdrawn' ? 300 : null, can_withdraw: request.type === 'follow_up' && ['pending', 'blocked'].includes(state), reason: null, ...extra };
    };
    window.complete = (index, state = 'pending', extra = {}) => {
      const message = receiptFor(index, state, extra); receipts.push(message);
      requests[index].resolve({ schema_version: 1, job_id: requests[index].id, message });
    };
    AiPanel.messages.records.clear(); AiPanel.messages.save();
    AiPanel.debug.setState({ connected: true, userId: 'alice', selectedProjectId: 'project', conversationId: 'conversation', currentJobId: null, currentJob: null, running: false, jobStatus: null });
    AiPanel.adoptJob(job);
    const state = CodexiaAgentView._internal.getState();
    state.selectedId = 'job'; state.selectedProjectId = 'project'; state.selectedConversationId = 'conversation'; state.busy = false; state.messageMode = 'steer'; state.goal = ''; state.planMode = false; state.contextFiles = [];
    CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project', name: 'Project' }], jobs: [job] });
    document.getElementById('promptInput').value = ''; document.getElementById('cxAgentPrompt').value = '';
  }, job);
}
async function reconcile(page) { await page.evaluate(() => AiPanel.messages.reconcile(AiPanel.messages.scope(AiPanel.getState().currentJob))); }

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1300, height: 1000 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/src/index.html?__agent_windows_fixture=1`);
    await page.waitForFunction(() => window.AiPanel?.debug && window.CodexiaAgentView?._internal.getState().initialized);
    for (const surface of ['ai', 'cx']) {
      await fixture(page);
      if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
      const input = surface === 'ai' ? '#promptInput' : '#cxAgentPrompt';
      const send = surface === 'ai' ? '#btnSend' : '#cxSendAgent';
      const host = surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts';
      await page.locator(input).fill('same guidance');
      await page.locator(send).click(); await page.locator(send).dispatchEvent('click');
      assert.equal(await page.evaluate(() => requests.length), 1, 'busy stops double click');
      assert.equal(await page.locator(input).inputValue(), '');
      await page.locator(input).fill('new draft while waiting');
      await page.evaluate(() => complete(0));
      await page.waitForFunction(() => AiPanel.messages.list(AiPanel.messages.scope(AiPanel.getState().currentJob))[0]?.phase === 'received');
      assert.equal(await page.locator(input).inputValue(), 'new draft while waiting');
      assert.match(await page.locator(host).textContent(), /已接收，等待处理/);
      assert.equal(await page.locator('.cx-thread-message').filter({ hasText: 'same guidance' }).count(), 0, 'POST does not fabricate a canonical user bubble');
      await page.locator(input).fill('same guidance'); await page.locator(send).click();
      assert.notEqual(await page.evaluate(() => requests[0].key), await page.evaluate(() => requests[1].key));
      await page.evaluate(() => complete(1));
      await page.evaluate(() => { receipts = [receiptFor(0, 'consumed'), receiptFor(1, 'consumed')]; }); await reconcile(page);
      assert.equal(await page.locator(`${host} [data-state="consumed"]`).count(), 2);
      assert.match(await page.locator(host).textContent(), /已加入本轮上下文/);

      // Follow-up mode records authoritative child identity; viewing a paused
      // child performs GET and selection only, never implicit resume.
      await page.locator(surface === 'ai' ? '#btnFollowUp' : '#cxMessageModes [data-message-mode="follow_up"]').click();
      await page.locator(input).fill('next task'); await page.locator(send).click();
      assert.equal(await page.evaluate(() => requests[2].type), 'follow_up');
      await page.evaluate(() => complete(2, 'follow_up_created'));
      await page.locator(host).getByRole('button', { name: '查看后续任务' }).click();
      await page.waitForFunction(surface => surface === 'ai' ? AiPanel.getState().currentJobId === 'child' : CodexiaAgentView._internal.getState().selectedId === 'child', surface);
      assert.ok(await page.evaluate(() => jobReads.includes('child')));
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
    }

    await fixture(page);
    await page.locator('#btnSteer').click(); await page.locator('#promptInput').fill('lost response'); await page.locator('#btnSend').click();
    await page.evaluate(() => { receipts.push(receiptFor(0, 'consumed')); requests[0].reject(new Error('lost response')); });
    await page.locator('#aiMessageReceipts').getByRole('button', { name: '核对并重试' }).click();
    await page.waitForFunction(() => document.querySelector('#aiMessageReceipts [data-state="consumed"]'));
    assert.equal(await page.evaluate(() => requests.length), 1, 'lost successful response only reconciles via GET');

    // If GET confirms no receipt, only explicit retry repeats original UUID/body.
    await fixture(page); await page.locator('#promptInput').fill('retry original'); await page.locator('#btnSend').click();
    await page.evaluate(() => requests[0].reject(new Error('offline')));
    await page.locator('#aiMessageReceipts').getByRole('button', { name: '核对并重试' }).click();
    assert.equal(await page.evaluate(() => requests[0].key === requests[1].key), true);
    assert.equal(await page.evaluate(() => requests[1].payload.text), 'retry original'); await page.evaluate(() => complete(1));

    // A → B → A and same-conversation new job selection retain current drafts;
    // receipt reconciliation belongs to its original job, not the current one.
    for (const surface of ['ai', 'cx']) {
      await fixture(page);
      if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
      const input = surface === 'ai' ? '#promptInput' : '#cxAgentPrompt'; const send = surface === 'ai' ? '#btnSend' : '#cxSendAgent';
      await page.locator(input).fill('old pending'); await page.locator(send).click();
      await page.evaluate(({ surface, job }) => {
        if (surface === 'ai') AiPanel.adoptJob({ ...job, id: 'new-job' });
        else { const s = CodexiaAgentView._internal.getState(); s.selectedId = 'new-job'; CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project' }], jobs: [{ ...job, id: 'new-job' }] }); }
      }, { surface, job });
      await page.locator(input).fill('keep new job draft'); await page.evaluate(() => complete(0));
      assert.equal(await page.locator(input).inputValue(), 'keep new job draft');
      assert.equal(await page.locator(surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts').isHidden(), true);
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
    }
    await fixture(page); await page.locator('#promptInput').fill('old account'); await page.locator('#btnSend').click();
    await page.evaluate(() => { AiPanel.client.configure({ token: 'bob' }); AiPanel.debug.setState({ userId: 'bob' }); });
    await page.locator('#promptInput').fill('new account draft'); await page.evaluate(() => complete(0));
    assert.equal(await page.locator('#promptInput').inputValue(), 'new account draft');
    assert.equal(await page.locator('#aiMessageReceipts').isHidden(), true);

    // Both composers preserve a newer draft through conversation A → B → A,
    // and neither adopts an old account's late POST response.
    for (const surface of ['ai', 'cx']) {
      for (const change of ['conversation', 'account']) {
        await fixture(page);
        if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
        const input = surface === 'ai' ? '#promptInput' : '#cxAgentPrompt';
        await page.locator(input).fill('delayed original'); await page.locator(surface === 'ai' ? '#btnSend' : '#cxSendAgent').click();
        await page.evaluate(({ surface, change, job }) => {
          if (change === 'account') {
            AiPanel.client.configure({ token: 'new-account' }); AiPanel.debug.setState({ userId: 'bob' });
          } else if (surface === 'ai') {
            AiPanel.debug.setState({ conversationId: 'other' }); AiPanel.debug.setState({ conversationId: 'conversation' });
          } else {
            const state = CodexiaAgentView._internal.getState(); state.selectedConversationId = 'other'; state.selectedConversationId = 'conversation';
            CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project' }], jobs: [job] });
          }
        }, { surface, change, job });
        await page.locator(input).fill('new draft after navigation'); await page.evaluate(() => complete(0));
        assert.equal(await page.locator(input).inputValue(), 'new draft after navigation');
        if (change === 'account') assert.equal(await page.locator(surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts').isHidden(), true);
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
    }

    await fixture(page); await page.locator('#promptInput').fill('pending POST after consumed GET'); await page.locator('#btnSend').click();
    await page.evaluate(() => { receipts.push(receiptFor(0, 'consumed')); }); await reconcile(page);
    await page.evaluate(() => complete(0, 'pending'));
    await page.waitForFunction(() => document.querySelector('#aiMessageReceipts [data-state="consumed"]'));
    assert.equal(await page.locator('#aiMessageReceipts [data-state="pending"]').count(), 0);

    // A malformed mixed schema cannot claim consumption/creation or expose a
    // child action. Both entries use the exact same parser and safe renderer.
    for (const [type, delivery, extra, expected] of [
      ['steer', 'unapplied', { consumed_at: null }, 'unapplied'],
      ['follow_up', 'blocked', { consumed_at: null, reason: 'parent_failed' }, 'blocked'],
      ['steer', 'pending', { consumed_at: 300 }, 'unknown'],
      ['follow_up', 'follow_up_created', { follow_up_job_id: 'job' }, 'unknown'],
      ['steer', 'consumed', { context_message_id: null }, 'unknown'],
    ]) {
      await fixture(page);
      await page.evaluate(({ type, delivery, extra }) => {
        requests.push({ id: 'job', key: 'fixture-row', type, payload: { text: 'matrix row' } });
        receipts.push(receiptFor(0, delivery, extra));
      }, { type, delivery, extra }); await reconcile(page);
      assert.equal(await page.locator('#aiMessageReceipts .message-receipt').getAttribute('data-state'), expected);
      await page.locator('[data-mode="agent-windows"]').click();
      assert.equal(await page.locator('#cxMessageReceipts .message-receipt').getAttribute('data-state'), expected);
      assert.equal(await page.locator('#cxMessageReceipts').getByRole('button', { name: '查看后续任务' }).count(), 0);
      await page.locator('#cxSidebarDownload').click();
    }

    for (const surface of ['ai', 'cx']) {
      await fixture(page);
      await page.evaluate(() => {
        requests.push({ id: 'job', key: 'child-row', type: 'follow_up', payload: { text: 'child receipt' } });
        receipts.push(receiptFor(0, 'follow_up_created'));
        AiPanel.client.job = async () => ({ job: { id: 'child', project_id: 'foreign', conversation_id: 'conversation', turn_id: 'child-turn' } });
      }); await reconcile(page);
      if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
      const host = surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts';
      await page.locator(host).getByRole('button', { name: '查看后续任务' }).click();
      assert.match(await page.locator(host).textContent(), /归属与回执不一致/);
      assert.equal(await page.evaluate(surface => surface === 'ai' ? AiPanel.getState().currentJobId : CodexiaAgentView._internal.getState().selectedId, surface), 'job');
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
    }

    // Withdraw only an authoritative queued follow-up. Both surfaces share
    // one operation, retain drafts, and reconcile uncertain/conflicting results.
    for (const surface of ['ai', 'cx']) {
      const host = surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts';
      const input = surface === 'ai' ? '#promptInput' : '#cxAgentPrompt';
      const send = surface === 'ai' ? '#btnSend' : '#cxSendAgent';
      const setup = async () => {
        await fixture(page);
        if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
        await page.locator(surface === 'ai' ? '#btnFollowUp' : '#cxMessageModes [data-message-mode="follow_up"]').click();
        await page.locator(input).fill('完成后补充测试覆盖'); await page.locator(send).click();
        await page.evaluate(() => complete(0));
        await page.locator(host).getByRole('button', { name: '撤回追问', exact: true }).waitFor();
      };
      const finish = () => page.evaluate(() => {
        receipts = [receiptFor(0, 'withdrawn')];
        withdrawals.at(-1).resolve({ schema_version: 1, job_id: 'job', message: receipts[0] });
      });
      await setup();
      await page.locator(host).getByRole('button', { name: '撤回追问', exact: true }).evaluate(button => { button.click(); button.click(); });
      assert.equal(await page.evaluate(() => withdrawals.length), 1);
      assert.match(await page.locator(host).textContent(), /撤回中/);
      await page.locator(input).fill('撤回期间写下的新草稿'); await finish();
      await page.locator(`${host} [data-state="withdrawn"]`).waitFor();
      assert.equal(await page.locator(input).inputValue(), '撤回期间写下的新草稿');
      assert.equal(await page.locator(host).getByRole('button', { name: '撤回追问', exact: true }).count(), 0);
      assert.equal(await page.locator(host).getByRole('button', { name: '移除本地记录' }).count(), 0);
      await page.evaluate(() => { receipts = [receiptFor(0)]; }); await reconcile(page);
      assert.equal(await page.locator(`${host} [data-state="withdrawn"]`).count(), 1, 'old GET does not resurrect');
      if (process.env.AGENT_MESSAGES_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.AGENT_MESSAGES_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.AGENT_MESSAGES_SCREENSHOT_DIR, `withdraw-${surface}.png`), fullPage: true });
      }
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();

      await setup();
      await page.locator(host).getByRole('button', { name: '撤回追问', exact: true }).click();
      await page.evaluate(() => withdrawals[0].reject(new Error('离线')));
      await page.locator(host).getByRole('button', { name: '核对并重试撤回' }).click();
      await page.waitForFunction(() => withdrawals.length === 2);
      assert.equal(await page.evaluate(() => withdrawals[0].messageId === withdrawals[1].messageId), true);
      await finish(); await page.locator(`${host} [data-state="withdrawn"]`).waitFor();
      assert.equal(await page.evaluate(() => requests.length), 1, 'withdraw retry never sends a new follow-up');
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();

      await setup();
      await page.locator(host).getByRole('button', { name: '撤回追问', exact: true }).click();
      await page.evaluate(() => {
        receipts = [receiptFor(0, 'follow_up_created')];
        withdrawals[0].reject(Object.assign(new Error('conflict'), { status: 409 }));
      });
      await page.locator(`${host} [data-state="follow_up_created"]`).waitFor();
      assert.equal(await page.locator(host).getByRole('button', { name: '核对并重试撤回' }).count(), 0);
      await page.locator(host).getByRole('button', { name: '查看后续任务' }).click();
      await page.waitForFunction(surface => surface === 'ai' ? AiPanel.getState().currentJobId === 'child' : CodexiaAgentView._internal.getState().selectedId === 'child', surface);
      assert.deepEqual(await page.evaluate(() => controlRequests), [], 'conflict and viewing never cancel/resume child');
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();

      for (const change of ['job', 'account']) {
        await setup(); await page.locator(host).getByRole('button', { name: '撤回追问', exact: true }).click();
        await page.evaluate(({ surface, change, job }) => {
          if (change === 'account') { AiPanel.client.configure({ token: 'bob' }); AiPanel.debug.setState({ userId: 'bob' }); }
          else if (surface === 'ai') AiPanel.adoptJob({ ...job, id: 'new-job' });
          else { CodexiaAgentView._internal.getState().selectedId = 'new-job'; CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project' }], jobs: [{ ...job, id: 'new-job' }] }); }
        }, { surface, change, job });
        await page.locator(input).fill('保留切换后的新草稿'); await finish();
        assert.equal(await page.locator(input).inputValue(), '保留切换后的新草稿');
        assert.equal(await page.locator(host).isHidden(), true, `withdrawal response isolation: ${surface}/${change}`);
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
      await setup();
      await page.evaluate(() => { receipts = [receiptFor(0, 'pending', { can_withdraw: false })]; }); await reconcile(page);
      assert.equal(await page.locator(host).getByRole('button', { name: '撤回追问', exact: true }).count(), 0, 'updated capability removes action');
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
    }

    // Untrusted body text is escaped; narrow layouts and keyboard disclosure
    // work on both actual entry points.
    await fixture(page); await page.locator('#btnSteer').click();
    const hostile = '<img src=x onerror="window.messageXss=1">' + 'long'.repeat(70);
    await page.locator('#promptInput').fill(hostile); await page.locator('#btnSend').click(); await page.evaluate(() => complete(0));
    await page.locator('#aiMessageReceipts > summary').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('#aiMessageReceipts').getAttribute('open'), null);
    await page.keyboard.press('Enter');
    for (const surface of ['ai', 'cx']) {
      if (surface === 'cx') { await page.setViewportSize({ width: 1300, height: 1000 }); await page.locator('[data-mode="agent-windows"]').click(); }
      await page.setViewportSize({ width: 390, height: 844 });
      const host = surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts';
      assert.equal(await page.locator(`${host} img`).count(), 0);
      assert.equal(await page.locator(host).evaluate(node => node.scrollWidth > node.clientWidth + 1), false);
    }
    assert.equal(await page.evaluate(() => window.messageXss), undefined);
    assert.deepEqual(errors, []);
    console.log('job-messages-browser.test: OK (both composers, stable retries, truthful receipts, child viewing, late responses, isolation, escaping, narrow UI)');
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
