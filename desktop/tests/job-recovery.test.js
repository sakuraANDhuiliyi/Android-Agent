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
    res.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream');
    res.end(data);
  });
});
const original = { id: 'interrupted', project_id: 'project', conversation_id: 'conversation', status: 'interrupted', can_recover: true, recovery_job_id: null, prompt: 'Preserve my conversation', updated_at: 2 };
const recovered = { ...original, id: 'recovered', status: 'running', can_recover: false, updated_at: 3 };

async function fixture(page, job = original) {
  await page.evaluate(job => {
    const api = AiPanel.client;
    api.configure({ baseUrl: location.origin, token: 'isolated-test-account' });
    window.calls = []; window.watchers = []; window.resultJob = null;
    api.job = async () => ({ job });
    api.jobs = async () => ({ jobs: [job] });
    api.projects = async () => ({ projects: [{ id: 'project', name: 'Project' }] });
    api.models = async () => ({ models: [] });
    api.listApprovals = async () => ({ approvals: [] });
    api.conversationEvents = async () => ({ events: [], has_more: false });
    api.watchJob = (id, callback) => {
      const watcher = { id, callback, closed: false }; watchers.push(watcher);
      return { close() { watcher.closed = true; } };
    };
    api.request = (url, options) => {
      calls.push({ url, method: options.method });
      return new Promise((resolve, reject) => { window.finishRecovery = resolve; window.failRecovery = reject; });
    };
    AiPanel.debug.setState({ connected: true, userId: 'test-user', selectedProjectId: 'project', conversationId: 'conversation', currentJobId: null, currentJob: null, controlBusy: null, jobStatus: null, running: false });
    AiPanel.adoptJob(job);
    const state = CodexiaAgentView._internal.getState();
    state.selectedProjectId = 'project'; state.selectedConversationId = 'conversation'; state.selectedId = job.id;
    state.controlBusy = null;
    CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project', name: 'Project' }], jobs: [job] });
  }, job);
}
async function settle(page, job = recovered) {
  await page.evaluate(job => { window.finishRecovery({ job }); }, job);
}
async function state(page) {
  return page.evaluate(() => ({ id: AiPanel.getState().currentJobId, conversation: AiPanel.getState().conversationId, busy: AiPanel.getState().controlBusy, status: AiPanel.getState().jobStatus }));
}

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.agentDesktop = {
        getDefaultWorkspace: async () => null, getRepoRoot: async () => '/',
        readTree: async () => ({ entries: [] }), listFiles: async () => [], readFile: async () => ({ content: '' }),
        writeFile: async () => {}, exists: async () => false, stat: async () => ({}),
        basename: async value => value?.split('/').pop() || value,
        dirname: async value => value?.split('/').slice(0, -1).join('/') || '/',
        joinPath: async (...parts) => parts.join('/'), relative: async (_from, to) => to, normalize: async value => value,
        agentStatus: async () => ({ running: false, managed: false, port: 8000 }),
        onAgentServerExit: () => () => {}, onMenu: () => () => {},
      };
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/src/index.html?__agent_windows_fixture=1`);
    await page.waitForFunction(() => window.AiPanel?.debug && window.EditorApp?.openDiff);
    for (const value of [undefined, false, 'true']) {
      await fixture(page, { ...original, can_recover: value });
      assert.equal(await page.locator('#btnRecoverJob').isHidden(), true, 'only authoritative boolean true enables recovery');
      assert.equal(await page.locator('#cxRecoverTask').getAttribute('hidden'), '');
    }
    await fixture(page, { ...original, can_recover: false, recovery_job_id: 'existing' });
    assert.equal(await page.locator('#btnRecoverJob').textContent(), '查看恢复任务');
    await fixture(page, { ...original, status: 'paused', can_recover: false });
    assert.equal(await page.locator('#btnRecoverJob').isHidden(), true);
    assert.equal(await page.locator('#btnResumeJob').isVisible(), true);

    await fixture(page);
    await page.locator('#promptInput').fill('unsent draft');
    await page.evaluate(() => AiPanel.debug.ingestConversationEvents([{ seq: 1, event_type: 'user_message', payload: { turn_id: 'original-turn', content: 'keep this history' } }]));
    await page.waitForFunction(() => document.getElementById('aiMessages').textContent.includes('keep this history'));
    const before = await page.locator('#aiMessages').textContent();
    await page.locator('#btnRecoverJob').click();
    await page.locator('#btnRecoverJob').dispatchEvent('click');
    assert.equal(await page.locator('#btnRecoverJob').isDisabled(), true);
    assert.equal(await page.evaluate(() => calls.length), 1, 'busy blocks a second click');
    await settle(page);
    await page.waitForFunction(() => AiPanel.getState().currentJobId === 'recovered' && !AiPanel.getState().controlBusy);
    assert.equal((await state(page)).conversation, 'conversation');
    assert.equal(await page.locator('#promptInput').inputValue(), 'unsent draft');
    assert.equal(await page.locator('#aiMessages').textContent(), before, 'recovery keeps the canonical conversation timeline');
    assert.ok(await page.evaluate(() => watchers.some(w => w.id === 'recovered' && !w.closed)), 'new job has a live subscription');

    // A duplicate POST may return an existing paused or succeeded job. Opening
    // it never silently invokes resume or creates another task.
    for (const status of ['paused', 'succeeded']) {
      await fixture(page, { ...original, can_recover: false, recovery_job_id: 'recovered' });
      await page.evaluate(job => { AiPanel.client.job = async id => { calls.push({ url: `/api/jobs/${id}`, method: 'GET' }); return { job }; }; }, { ...recovered, status });
      await page.locator('#btnRecoverJob').click();
      await page.waitForFunction(status => AiPanel.getState().jobStatus === status && !AiPanel.getState().controlBusy, status);
      assert.equal(await page.evaluate(() => calls.length), 1);
      assert.equal(await page.evaluate(() => calls[0].url), '/api/jobs/recovered');
      assert.equal(await page.evaluate(() => calls[0].method), 'GET');
      if (status === 'succeeded') assert.equal(await page.evaluate(() => watchers.filter(w => !w.closed).length), 0);
    }
    for (const status of [409, 401, 0]) {
      await fixture(page);
      await page.locator('#btnRecoverJob').click();
      await page.evaluate(status => failRecovery(Object.assign(new Error('test recovery failure'), { status })), status);
      await page.waitForFunction(() => !AiPanel.getState().controlBusy);
      assert.equal((await state(page)).id, original.id);
      assert.equal(await page.locator('#btnRecoverJob').isEnabled(), true);
    }

    // A → B → A is a new binding even though the final IDs match.
    await fixture(page);
    await page.locator('#btnRecoverJob').click();
    await page.evaluate(async () => {
      await AiPanel.selectConversation('other', { loadHistory: false });
      await AiPanel.selectConversation('conversation', { loadHistory: false });
    });
    await settle(page);
    await page.waitForTimeout(50);
    assert.equal((await state(page)).id, original.id, 'old recovery response cannot adopt into a new binding');
    assert.equal((await state(page)).busy, null, 'new bindings must not inherit an abandoned busy control');
    await fixture(page);
    await page.locator('#btnRecoverJob').click();
    await page.evaluate(() => AiPanel.client.configure({ token: 'new-account' }));
    await settle(page);
    await page.waitForTimeout(50);
    assert.equal((await state(page)).id, original.id, 'old account response is ignored');

    // A previously closed callback for the same job cannot re-enter a new watcher.
    await fixture(page, recovered);
    await page.evaluate(job => { window.oldWatcher = watchers[0]; AiPanel.adoptJob(job); }, recovered);
    await page.evaluate(() => oldWatcher.callback({ kind: 'job', job: { id: 'recovered', status: 'failed' } }));
    assert.equal((await state(page)).status, 'running');

    await fixture(page, recovered);
    await page.evaluate(() => {
      AiPanel.client.job = () => new Promise(resolve => { window.finishOldDetail = resolve; });
      window.oldDone = watchers[0].callback({ kind: 'done', status: 'succeeded' });
    });
    await page.evaluate(async () => {
      await AiPanel.selectConversation('other', { loadHistory: false });
      await AiPanel.selectConversation('conversation', { loadHistory: false });
      finishOldDetail({ job: { id: 'recovered', status: 'failed', events: [{ id: 999, type: 'text', text: 'FOREIGN TAIL' }] } });
      await oldDone;
    });
    assert.equal((await state(page)).status, 'running', 'old done reconciliation must not overwrite a later binding');
    assert.ok(!(await page.locator('#aiMessages').textContent()).includes('FOREIGN TAIL'));

    await fixture(page);
    await page.locator('#btnRecoverJob').click();
    await page.locator('[data-mode="agent-windows"]').click();
    await page.waitForSelector('#cxRecoverTask', { state: 'visible' });
    await page.locator('#cxRecoverTask').click();
    assert.equal(await page.evaluate(() => calls.length), 1, 'both desktop entry points join one recovery request');
    await settle(page, { ...recovered, status: 'paused' });
    await page.waitForFunction(() => AiPanel.getState().currentJobId === 'recovered' && CodexiaAgentView._internal.getState().selectedId === 'recovered');

    await fixture(page);
    await page.locator('#cxRecoverTask').click();
    await page.evaluate(job => {
      const state = CodexiaAgentView._internal.getState();
      state.selectedId = 'another-job';
      state.selectedId = job.id;
      CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project' }], jobs: [job] });
    }, original);
    await settle(page);
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(() => CodexiaAgentView._internal.getState().selectedId), original.id, 'workbench A → B → A invalidates pending recovery');

    await fixture(page);

    await page.waitForSelector('#cxRecoverTask', { state: 'visible' });
    await page.locator('#cxAgentPrompt').fill('workbench draft');
    await page.locator('#cxRecoverTask').click();
    await page.locator('#cxRecoverTask').dispatchEvent('click');
    assert.equal(await page.evaluate(() => calls.length), 1);
    await settle(page, { ...recovered, status: 'paused' });
    await page.waitForFunction(() => CodexiaAgentView._internal.getState().selectedId === 'recovered');
    assert.equal(await page.locator('#cxResumeTask').isVisible(), true);
    assert.equal(await page.locator('#cxAgentPrompt').inputValue(), 'workbench draft');
    assert.equal((await state(page)).id, 'recovered', 'workbench synchronizes the already-open original conversation');
    assert.equal(await page.evaluate(() => calls.length), 1, 'paused recovery never invokes resume');

    // Terminal detail cache keeps events, but server summaries own eligibility.
    await fixture(page);
    await page.evaluate(async job => {
      const s = CodexiaAgentView._internal.getState();
      s.debugData = null; s.jobDetails.set(job.id, { ...job, can_recover: false, events: [{ id: 19, type: 'text', text: 'cached' }] });
      await CodexiaAgentView.refresh();
    }, original);
    assert.equal(await page.locator('#cxRecoverTask').isVisible(), true);
    assert.equal(await page.evaluate(() => CodexiaAgentView._internal.getState().jobs[0].events[0].id), 19);
    await page.evaluate(async job => {
      AiPanel.client.jobs = async () => ({ jobs: [{ ...job, can_recover: false, recovery_job_id: 'recovered' }] });
      await CodexiaAgentView.refresh();
    }, original);
    assert.equal(await page.locator('#cxRecoverTask').textContent(), '查看恢复任务');
    assert.deepEqual(errors, []);
    console.log('job-recovery.test: OK (both UIs, authoritative eligibility, duplicates, paused/terminal recovery, errors, binding/account isolation, subscriptions, canonical cache)');
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
