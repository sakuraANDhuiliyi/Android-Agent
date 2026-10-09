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
    window.requests = []; window.reads = []; window.watchers = []; window.receipts = []; window.jobReads = []; window.withdrawals = []; window.edits = []; window.controlRequests = [];
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
    api.editJobMessage = (id, messageId, body) => new Promise((resolve, reject) => { edits.push({ id, messageId, body, resolve, reject }); });
    api.cancelJob = api.resumeJob = async id => { controlRequests.push(id); throw new Error('unexpected task control'); };
    window.receiptFor = (index, state = 'pending', extra = {}) => {
      const request = requests[index];
      return { schema_version: 1, id: index + 1, task_id: request.id, message_key: request.key, type: request.type,
        payload: request.payload, created_at: 100 + index, consumed_at: ['pending', 'blocked', 'withdrawn'].includes(state) ? null : 200,
        delivery_state: state, context_message_id: state === 'consumed' ? `steer:${index}` : null,
        follow_up_job_id: state === 'follow_up_created' ? 'child' : null, follow_up_turn_id: state === 'follow_up_created' ? 'child-turn' : null,
        revision: 0, edited_at: null, can_edit: request.type === 'follow_up' && state === 'pending',
        withdrawn_at: state === 'withdrawn' ? 300 : null, can_withdraw: request.type === 'follow_up' && ['pending', 'blocked'].includes(state), reason: null, ...extra };
    };
    window.complete = (index, state = 'pending', extra = {}) => {
      const message = receiptFor(index, state, extra); receipts.push(message);
      requests[index].resolve({ schema_version: 1, job_id: requests[index].id, message });
    };
    window.completeEdit = (index, current = {}, saved = {}) => {
      const request = edits[index];
      const message = { ...receiptFor(0), revision: request.body.expected_revision + 1, edited_at: 400,
        payload: request.body.payload, ...current };
      receipts = [message];
      request.resolve({ schema_version: 1, job_id: request.id, message, edit: { schema_version: 1,
        task_id: request.id, message_id: request.messageId, edit_key: request.body.edit_key,
        expected_revision: request.body.expected_revision, revision: request.body.expected_revision + 1,
        created_at: 400, payload: request.body.payload, ...saved } });
    };
    AiPanel.messages.records.clear(); AiPanel.messages.queues.clear(); AiPanel.messages.save(); AiPanel.messages.saveQueueIntents();
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
        await page.evaluate(async ({ surface, change, job }) => {
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
        await page.evaluate(async ({ surface, change, job }) => {
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

    // A queued editor is separate from both composers. CAS and uncertain ACKs
    // preserve the draft and operation identity across both actual entry points.
    for (const surface of ['ai', 'cx']) {
      const host = surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts';
      const input = surface === 'ai' ? '#promptInput' : '#cxAgentPrompt';
      const setup = async () => {
        await fixture(page);
        if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
        await page.locator(surface === 'ai' ? '#btnFollowUp' : '#cxMessageModes [data-message-mode="follow_up"]').click();
        await page.locator(input).fill('排队追问原文');
        await page.locator(surface === 'ai' ? '#btnSend' : '#cxSendAgent').click();
        await page.evaluate(() => complete(0));
        await page.locator(host).getByRole('button', { name: '编辑追问', exact: true }).click();
      };
      const editor = page.locator(`${host} .message-edit-text`);
      await setup(); await editor.fill('修改后保留队位');
      await page.locator(host).getByRole('button', { name: '保存修改' }).evaluate(button => { button.click(); button.click(); });
      assert.equal(await page.evaluate(() => edits.length), 1, 'double save keeps one edit identity');
      await editor.fill('保存期间新写的草稿'); await page.locator(input).fill('独立主输入草稿');
      await page.evaluate(() => completeEdit(0));
      await page.waitForFunction(() => !AiPanel.messages.list(AiPanel.messages.scope(AiPanel.getState().currentJob))[0].editIntent);
      assert.equal(await editor.inputValue(), '保存期间新写的草稿');
      assert.equal(await page.locator(input).inputValue(), '独立主输入草稿');
      assert.match(await page.locator(host).textContent(), /追问 · 修改后保留队位/);
      await editor.focus(); await editor.evaluate(node => node.setSelectionRange(2, 5));
      await page.evaluate(() => { receipts = [receiptFor(0, 'pending', { revision: 1, edited_at: 400, payload: { text: '修改后保留队位' }, reason: 'parent_paused' })]; });
      await reconcile(page);
      assert.deepEqual(await editor.evaluate(node => [document.activeElement === node, node.selectionStart, node.selectionEnd]), [true, 2, 5]);
      await page.evaluate(() => { receipts = [receiptFor(0)]; }); await reconcile(page);
      assert.match(await page.locator(host).textContent(), /追问 · 修改后保留队位/, 'stale GET cannot undo revision');
      if (process.env.AGENT_MESSAGES_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.AGENT_MESSAGES_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.AGENT_MESSAGES_SCREENSHOT_DIR, `edit-${surface}.png`), fullPage: true });
      }
      await page.locator(host).getByRole('button', { name: '保存修改' }).click();
      assert.equal(await page.evaluate(() => edits[1].body.expected_revision), 1);
      await page.evaluate(() => completeEdit(1)); await editor.waitFor({ state: 'detached' });
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();

      await setup(); await editor.fill('冲突仍保留的草稿');
      await page.locator(host).getByRole('button', { name: '保存修改' }).click();
      await page.evaluate(() => { receipts = [receiptFor(0, 'pending', { revision: 1, edited_at: 400, payload: { text: '另一窗口保存的正文' } })]; edits[0].reject(Object.assign(new Error('CAS'), { status: 409 })); });
      await page.locator(host).getByRole('button', { name: '以最新版本继续编辑' }).waitFor();
      assert.equal(await editor.inputValue(), '冲突仍保留的草稿');
      assert.equal(await page.locator(host).getByRole('button', { name: '保存修改' }).isDisabled(), true);
      await page.locator(host).getByRole('button', { name: '以最新版本继续编辑' }).click();
      await page.locator(host).getByRole('button', { name: '保存修改' }).click();
      assert.equal(await page.evaluate(() => edits[1].body.expected_revision), 1);
      assert.equal(await page.evaluate(() => edits[1].body.edit_key !== edits[0].body.edit_key), true);
      await page.evaluate(() => completeEdit(1)); await editor.waitFor({ state: 'detached' });
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();

      await setup(); await editor.fill('password=synthetic_edit_secret');
      await page.locator(host).getByRole('button', { name: '保存修改' }).click();
      await page.evaluate(() => { receipts = [receiptFor(0, 'follow_up_created', { revision: 2, edited_at: 500, payload: { text: '最新正文' } })]; edits[0].reject(new Error('lost response')); });
      await reconcile(page);
      await page.locator(host).getByRole('button', { name: '收起编辑' }).click();
      await page.locator(host).getByRole('button', { name: '展开编辑草稿' }).click();
      assert.equal(await editor.inputValue(), 'password=synthetic_edit_secret');
      await page.locator(host).getByRole('button', { name: '核对并重试编辑' }).click();
      await page.waitForFunction(() => edits.length === 2);
      assert.deepEqual(await page.evaluate(() => edits[1].body), await page.evaluate(() => edits[0].body));
      await page.evaluate(() => completeEdit(1, { revision: 1, payload: { text: 'password=[REDACTED]' } }, { payload: { text: 'password=[REDACTED]' } }));
      await page.locator(host).getByRole('button', { name: '核对并重试编辑' }).waitFor({ state: 'detached' });
      assert.match(await page.locator(host).textContent(), /追问 · 最新正文/);
      assert.equal(await page.locator(`${host} [data-state="follow_up_created"]`).count(), 1);
      assert.deepEqual(await page.evaluate(() => controlRequests), []);
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();

      for (const change of ['job', 'account']) {
        await setup(); await editor.fill('late edit'); await page.locator(host).getByRole('button', { name: '保存修改' }).click();
        await page.evaluate(async ({ surface, change, job }) => {
          if (change === 'account') { AiPanel.client.configure({ token: 'bob' }); AiPanel.debug.setState({ userId: 'bob' }); }
          else if (surface === 'ai') AiPanel.adoptJob({ ...job, id: 'new-job' });
          else { CodexiaAgentView._internal.getState().selectedId = 'new-job'; CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project' }], jobs: [{ ...job, id: 'new-job' }] }); }
        }, { surface, change, job });
        await page.locator(input).fill('切换后的草稿'); await page.evaluate(() => completeEdit(0));
        assert.equal(await page.locator(input).inputValue(), '切换后的草稿');
        assert.equal(await page.locator(host).isHidden(), true);
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
      await setup(); await editor.fill('<img src=x onerror="window.messageXss=1">' + '长正文'.repeat(60));
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await page.locator(host).evaluate(node => node.scrollWidth > node.clientWidth + 1), false);
      assert.equal(await page.locator(`${host} img`).count(), 0);
      await page.setViewportSize({ width: 1300, height: 1000 });
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
    }

    // The blocker is a read-only detail: viewing neither selects an execution
    // target nor starts its watcher/approval path, and preserves both drafts.
    for (const surface of ['ai', 'cx']) {
      const host = surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts';
      const input = surface === 'ai' ? '#promptInput' : '#cxAgentPrompt';
      const dialog = page.getByRole('dialog', { name: '阻塞任务详情' });
      const setup = async (target = 'child') => {
        await fixture(page);
        if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
        await page.evaluate(({ target, job }) => {
          requests.push({ id: 'job', key: 'blocked-row', type: 'follow_up', payload: { text: '等待前序完成的追问' } });
          receipts = [receiptFor(0)];
          const scope = AiPanel.messages.scope(job);
          AiPanel.messages.merge(scope, receipts[0]); AiPanel.messages.openEditor(scope, 'blocked-row');
          AiPanel.messages.updateEditor(scope, 'blocked-row', '保留未保存的编辑草稿');
          receipts = [receiptFor(0, 'blocked', { reason: 'parent_failed', blocking_job_id: target,
            blocking_turn_id: target === 'job' ? 'turn' : 'child-turn' })];
          window.blockerJob = { ...job, id: target, turn_id: target === 'job' ? 'turn' : 'child-turn', status: 'failed',
            prompt: '<img src=x onerror="window.blockerXss=1">真实阻塞请求', error_message: '<script>window.blockerXss=1</script>真实错误' };
          AiPanel.client.job = async id => { jobReads.push(id); return { job: blockerJob }; };
        }, { target, job });
        await reconcile(page); await page.locator(input).fill('保留主输入草稿');
      };
      for (const target of ['child', 'job']) {
        await setup(target);
        const watchersBefore = await page.evaluate(() => watchers.length);
        await reconcile(page); await reconcile(page); // Unchanged polling keeps existing DOM handlers usable.
        await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).click();
        await dialog.waitFor();
        assert.match(await dialog.textContent(), /执行失败/); assert.match(await dialog.textContent(), /真实阻塞请求/);
        assert.match(await dialog.textContent(), /真实错误/); assert.match(await dialog.textContent(), /本任务最近一次验证/);
        assert.match(await dialog.textContent(), /未验证/);
        assert.equal(await page.evaluate(surface => surface === 'ai' ? AiPanel.getState().currentJobId : CodexiaAgentView._internal.getState().selectedId, surface), 'job');
        assert.equal(await page.evaluate(() => watchers.length), watchersBefore);
        assert.deepEqual(await page.evaluate(() => controlRequests), []);
        assert.equal(await page.locator(input).inputValue(), '保留主输入草稿');
        assert.equal(await page.locator(`${host} textarea`).inputValue(), '保留未保存的编辑草稿');
        assert.equal(await dialog.locator('button').count(), 1, 'detail only offers closing');
        assert.equal(await dialog.locator('img,script').count(), 0);
        await dialog.evaluate(async node => { await Promise.all(node.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {}))); });
        assert.equal(await dialog.evaluate(node => {
          const rect = node.getBoundingClientRect();
          return Number(getComputedStyle(node).opacity) === 1 && rect.width > 200 && rect.height > 200
            && rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight + 1 && rect.right <= innerWidth + 1;
        }), true, 'fully painted blocker detail lies within viewport');
        if (process.env.AGENT_MESSAGES_SCREENSHOT_DIR && target === 'child') {
          fs.mkdirSync(process.env.AGENT_MESSAGES_SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: path.join(process.env.AGENT_MESSAGES_SCREENSHOT_DIR, `blocker-${surface}.png`), fullPage: true });
        }
        await page.setViewportSize({ width: 390, height: 844 });
        assert.equal(await dialog.evaluate(node => node.scrollWidth > node.clientWidth + 1), false);
        await page.setViewportSize({ width: 1300, height: 1000 });
        await dialog.getByRole('button', { name: '关闭', exact: true }).click();
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
      for (const invalid of [{ blocking_turn_id: null }, { revision: undefined }, { revision: -1 }, { schema_version: 2 }, { reason: 'legacy_missing_receipt' }]) {
        await setup();
        await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).click(); await dialog.waitFor();
        await page.evaluate(invalid => { receipts = [{ ...receipts[0], ...invalid }]; }, invalid); await reconcile(page);
        await dialog.waitFor({ state: 'detached' });
        assert.equal(await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).count(), 0);
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
      for (const mismatch of [{ id: 'foreign' }, { project_id: 'foreign' }, { conversation_id: 'foreign' }, { turn_id: 'foreign' }]) {
        await setup(); await page.evaluate(mismatch => { blockerJob = { ...blockerJob, ...mismatch }; }, mismatch);
        await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).click();
        assert.equal(await dialog.count(), 0); assert.match(await page.locator(host).textContent(), /归属与回执不一致/);
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
      await setup();
      await page.evaluate(() => { window.blockerReads = []; AiPanel.client.job = id => new Promise(resolve => { blockerReads.push({ id, resolve }); }); });
      await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).click();
      await page.waitForFunction(() => blockerReads.length === 1);
      await reconcile(page); // A normal polling refresh can replace the captured row.
      await page.evaluate(() => blockerReads[0].resolve({ job: blockerJob }));
      await dialog.waitFor(); await dialog.getByRole('button', { name: '关闭', exact: true }).click();
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      for (const change of ['job', 'conversation', 'account', 'receipt', 'background']) {
        await setup();
        await page.evaluate(() => { window.blockerReads = []; AiPanel.client.job = id => new Promise(resolve => { blockerReads.push({ id, resolve }); }); });
        await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).click();
        await page.waitForFunction(() => blockerReads.length === 1);
        await page.evaluate(async ({ surface, change, job }) => {
          if (change === 'account') { AiPanel.client.configure({ token: 'bob' }); AiPanel.debug.setState({ userId: 'bob' }); }
          else if (change === 'background') window.dispatchEvent(new Event('blur'));
          else if (change === 'receipt') { receipts = [{ ...receipts[0], blocking_turn_id: null }]; }
          else if (surface === 'ai') {
            if (change === 'job') { AiPanel.adoptJob({ ...job, id: 'other' }); AiPanel.adoptJob(job); }
            else { await AiPanel.selectConversation('other', { loadHistory: false }); await AiPanel.selectConversation('conversation', { loadHistory: false }); }
          } else {
            const state = CodexiaAgentView._internal.getState();
            if (change === 'job') { state.selectedId = 'other'; state.selectedId = 'job'; }
            else { state.selectedConversationId = 'other'; state.selectedConversationId = 'conversation'; }
            CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project' }], jobs: [job] });
          }
        }, { surface, change, job });
        if (change === 'receipt') await reconcile(page);
        await page.locator(input).fill('较晚响应不能改变新草稿');
        await page.evaluate(() => blockerReads[0].resolve({ job: blockerJob }));
        assert.equal(await dialog.count(), 0, `${surface}/${change} rejects late view`);
        assert.equal(await page.locator(input).inputValue(), '较晚响应不能改变新草稿');
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
      for (const change of ['job', 'account']) {
        await setup(); await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).click(); await dialog.waitFor();
        await page.evaluate(({ surface, change, job }) => {
          if (change === 'account') { AiPanel.client.configure({ token: 'bob' }); AiPanel.debug.setState({ userId: 'bob' }); }
          else if (surface === 'ai') AiPanel.adoptJob({ ...job, id: 'new-job' });
          else { CodexiaAgentView._internal.getState().selectedId = 'new-job'; CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project' }], jobs: [{ ...job, id: 'new-job' }] }); }
        }, { surface, change, job });
        await dialog.waitFor({ state: 'detached' });
        if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
      }
      await setup(); await page.locator(host).getByRole('button', { name: '查看阻塞任务' }).click(); await dialog.waitFor();
      await page.evaluate(() => window.dispatchEvent(new Event('blur'))); await dialog.waitFor({ state: 'detached' });
      if (surface === 'cx') await page.locator('#cxSidebarDownload').click();
    }
    assert.equal(await page.evaluate(() => window.blockerXss), undefined);

    // Authoritative queue controls are shared by both real composers. Local
    // receipt chronology does not determine position; historical ACKs never sort.
    for (const surface of ['ai', 'cx']) {
      await fixture(page);
      if (surface === 'cx') await page.locator('[data-mode="agent-windows"]').click();
      const host = surface === 'ai' ? '#aiMessageReceipts' : '#cxMessageReceipts';
      const input = surface === 'ai' ? '#promptInput' : '#cxAgentPrompt';
      await page.evaluate(() => {
        window.reorders = [];
        window.queueState = { schema_version: 1, task_id: 'job', version: 'q1:' + 'a'.repeat(64) + '', order_revision: 0,
          message_ids: [3, 1, 2], pending_message_ids: [3, 1, 2], can_reorder: true, reason: null };
        receipts = [1, 2, 3].map(id => ({ schema_version: 1, id, task_id: 'job', message_key: `queue-${id}`, type: 'follow_up',
          payload: { text: `追问 ${id} <script>not executed</script>` }, created_at: id, consumed_at: null,
          delivery_state: 'pending', context_message_id: null, follow_up_job_id: null, follow_up_turn_id: null,
          revision: 0, edited_at: null, withdrawn_at: null, can_withdraw: true, can_edit: true, reason: 'parent_paused' }));
        receipts.push({ ...receipts[0], id: 4, message_key: 'steer-history', type: 'steer', payload: { text: '历史引导' }, can_edit: false, can_withdraw: false });
        AiPanel.client.jobMessages = async id => ({ schema_version: 1, job_id: id, messages: receipts, queue: queueState });
        AiPanel.client.reorderJobMessages = (id, body) => new Promise((resolve, reject) => reorders.push({ id, body, resolve, reject }));
        window.queueAck = index => ({ schema_version: 1, job_id: 'job', messages: [], queue: { ...queueState, message_ids: [1, 2, 3] },
          reorder: { schema_version: 1, task_id: 'job', ...reorders[index].body, order_revision: 1, created_at: 500 } });
      });
      await reconcile(page);
      const ids = () => page.locator(`${host} [data-queue-position]`).evaluateAll(nodes => nodes.map(node => Number(node.dataset.messageId)));
      assert.deepEqual(await ids(), [3, 1, 2]);
      const row = id => page.locator(`${host} [data-message-id="${id}"]`);
      assert.equal(await row(3).getByRole('button', { name: '上移追问' }).isDisabled(), true);
      assert.equal(await row(2).getByRole('button', { name: '下移追问' }).isDisabled(), true);
      await row(1).getByRole('button', { name: '编辑追问' }).click();
      await row(1).getByRole('textbox').fill('独立编辑草稿');
      await page.locator(input).fill('主输入草稿不受排序影响');
      await row(1).getByRole('button', { name: '下移追问' }).click();
      assert.deepEqual(await page.evaluate(() => reorders[0].body.message_ids), [3, 2, 1]);
      assert.deepEqual(await ids(), [3, 1, 2], 'no optimistic ordering');
      assert.equal(await page.locator(surface === 'ai' ? '#btnStop' : '#cxStopTask').isEnabled(), true, 'sorting never disables Stop');
      assert.equal(await row(3).getByRole('button', { name: '下移追问' }).isDisabled(), true);
      await page.evaluate(() => { queueState = { ...queueState, version: 'q1:' + 'b'.repeat(64) + '', order_revision: 1,
        message_ids: [3, 2, 1], pending_message_ids: [3, 2, 1] }; reorders[0].reject(new Error('accepted response lost')); });
      await page.locator(host).getByRole('button', { name: '核对并重试排序' }).waitFor();
      await reconcile(page); assert.deepEqual(await ids(), [3, 2, 1]);
      assert.equal(await row(1).getByRole('textbox').inputValue(), '独立编辑草稿');
      assert.equal(await page.locator(input).inputValue(), '主输入草稿不受排序影响');
      assert.equal(await page.locator(host).getByRole('button', { name: '核对并重试排序' }).count(), 1, 'GET order match is not own ACK');
      await page.locator(host).getByRole('button', { name: '核对并重试排序' }).click();
      assert.deepEqual(await page.evaluate(() => reorders[1].body), await page.evaluate(() => reorders[0].body));
      await page.evaluate(() => { queueState = { ...queueState, version: 'q1:' + 'c'.repeat(64) + '', order_revision: 2,
        message_ids: [2, 3, 1], pending_message_ids: [2, 3, 1] }; reorders[1].resolve(queueAck(1)); });
      await page.waitForFunction(host => document.querySelector(`${host} [data-queue-position="1"]`)?.dataset.messageId === '2', host);
      assert.deepEqual(await ids(), [2, 3, 1], 'old ACK cannot replace current GET order');
      await row(3).getByRole('button', { name: '上移追问' }).click();
      await page.evaluate(() => reorders[2].reject(Object.assign(new Error('conflict'), { status: 409 })));
      await page.waitForFunction(host => document.querySelector(`${host} .message-queue-status`)?.textContent.includes('队列已变化'), host);
      assert.equal(await page.evaluate(() => reorders.length), 3, 'conflict does not auto-rebase');
      assert.equal(await row(1).getByRole('textbox').inputValue(), '独立编辑草稿');
      const workingOutput = path.join(root, '..', '.artifacts', 'iteration-008'); fs.mkdirSync(workingOutput, { recursive: true });
      await page.locator(`${host} .message-queue-status`).scrollIntoViewIfNeeded();
      assert.equal(await row(3).getByRole('button', { name: '上移追问' }).isEnabled(), true);
      await page.screenshot({ path: path.join(workingOutput, `queue-${surface}-working.png`) });
      // Background-return invalidates late ACKs, even if the actual server
      // accepted the move. The original request remains explicitly retryable.
      await row(3).getByRole('button', { name: '上移追问' }).click();
      await page.evaluate(() => { window.dispatchEvent(new Event('blur')); reorders[3].resolve(queueAck(3)); });
      await page.locator(host).getByRole('button', { name: '核对并重试排序' }).waitFor();
      assert.equal(await page.locator(input).inputValue(), '主输入草稿不受排序影响');
      await page.locator(host).getByRole('button', { name: '核对并重试排序' }).click();
      assert.deepEqual(await page.evaluate(() => reorders[4].body), await page.evaluate(() => reorders[3].body));
      await page.evaluate(() => reorders[4].resolve(queueAck(4)));
      await page.locator(host).getByRole('button', { name: '核对并重试排序' }).waitFor({ state: 'detached' });
      for (const control of ['pause', 'resume', 'cancel']) {
        await page.evaluate(({ control, job }) => {
          const current = { ...job, status: control === 'resume' ? 'paused' : 'running', cancel_requested: false };
          AiPanel.client.jobMessages = async id => ({ schema_version: 1, job_id: id, messages: receipts, queue: queueState });
          AiPanel.adoptJob(current);
          CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project', name: 'Project' }], jobs: [current] });
        }, { control, job });
        await reconcile(page);
        await page.evaluate(() => {
          window.controlReads = []; window.sourceControls = [];
          AiPanel.client.jobMessages = id => new Promise(resolve => controlReads.push({ id, resolve }));
          const control = id => new Promise(resolve => sourceControls.push({ id, resolve }));
          AiPanel.client.pauseJob = AiPanel.client.resumeJob = AiPanel.client.cancel = control;
          void AiPanel.messages.reconcile(AiPanel.messages.scope(AiPanel.getState().currentJob));
        });
        const controlId = surface === 'ai' ? { pause: '#btnPauseJob', resume: '#btnResumeJob', cancel: '#btnStop' }[control]
          : { pause: '#cxPauseTask', resume: '#cxResumeTask', cancel: '#cxStopTask' }[control];
        await page.locator(controlId).click();
        assert.equal(await row(3).getByRole('button', { name: '上移追问' }).isDisabled(), true);
        await page.evaluate(() => {
          controlReads[0].resolve({ schema_version: 1, job_id: 'job', messages: receipts, queue: queueState });
          void AiPanel.messages.reconcile(AiPanel.messages.scope(AiPanel.getState().currentJob));
        });
        assert.equal(await row(3).getByRole('button', { name: '上移追问' }).isDisabled(), true, `${surface}/${control} old GET cannot enable while control pending`);
        await page.evaluate(({ control, job }) => sourceControls[0].resolve({ job: { ...job,
          status: control === 'pause' ? 'paused' : 'running', cancel_requested: control === 'cancel' } }), { control, job });
        await page.waitForFunction(() => controlReads.length >= 3 && AiPanel.messages.queue(AiPanel.messages.scope(AiPanel.getState().currentJob)).active.size === 0);
        await page.evaluate(() => { for (const read of controlReads.slice(0, -1)) read.resolve({ schema_version: 1, job_id: 'job', messages: receipts, queue: queueState }); });
        assert.equal(await row(3).getByRole('button', { name: '上移追问' }).isDisabled(), true, `${surface}/${control} completion fences GET started during control`);
        await page.evaluate(control => {
          if (control === 'cancel') queueState = { ...queueState, can_reorder: false, reason: 'parent_canceled' };
          const data = { schema_version: 1, job_id: 'job', messages: receipts, queue: queueState };
          controlReads.at(-1).resolve(data); AiPanel.client.jobMessages = async () => data;
        }, control);
        await reconcile(page);
        assert.equal(await row(3).getByRole('button', { name: '上移追问' }).isEnabled(), control !== 'cancel');
      }
      await page.evaluate(() => { AiPanel.messages.create(AiPanel.messages.scope(AiPanel.getState().currentJob), 'follow_up', '未确认本地追问'); });
      const local = page.locator(`${host} .message-receipt`).filter({ hasText: '未确认本地追问' });
      assert.equal(await local.getAttribute('data-queue-position'), null);
      assert.equal(await local.getByRole('button', { name: '上移追问' }).count(), 0);
      assert.equal(await page.locator(`${host} script`).count(), 0);
      const output = path.join(root, '..', '.artifacts', 'iteration-008'); fs.mkdirSync(output, { recursive: true });
      await page.screenshot({ path: path.join(output, `queue-${surface}.png`) });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await page.locator(host).evaluate(node => node.scrollWidth > node.clientWidth + 1), false);
      await page.setViewportSize({ width: 1300, height: 1000 });
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
