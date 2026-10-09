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
const run = { state: 'passed', task: 'assembleDebug', run_id: 'build-run', evidence_time: 1791500000, duration_ms: 1250, source_match: 'match' };
const job = { id: 'delivery-job', project_id: 'project', conversation_id: 'conversation', status: 'succeeded', prompt: 'verify this job', has_apk: true,
  verification: { schema_version: 1, job_id: 'delivery-job', scope: 'job', build: run,
    unit_tests: { ...run, task: 'testDebugUnitTest', run_id: 'tests-run', counts: { total: 3, passed: 2, failed: 0, skipped: 1 } } } };
async function fixture(page, value = job) {
  await page.evaluate(value => {
    const api = AiPanel.client;
    api.configure({ baseUrl: location.origin, token: 'delivery-test' });
    window.watchers = [];
    api.job = async () => ({ job: value }); api.jobs = async () => ({ jobs: [value] });
    api.projects = async () => ({ projects: [{ id: 'project', name: 'Project' }] }); api.models = async () => ({ models: [] });
    api.listApprovals = async () => ({ approvals: [] }); api.conversationEvents = async () => ({ events: [], has_more: false });
    api.jobMessages = async id => ({ schema_version: 1, job_id: id, messages: [] });
    api.watchJob = (id, callback) => { watchers.push({ id, callback }); return { close() {} }; };
    AiPanel.debug.setState({ connected: true, selectedProjectId: 'project', conversationId: 'conversation', currentJobId: null, currentJob: null, running: false, jobStatus: null });
    AiPanel.adoptJob(value);
    const state = CodexiaAgentView._internal.getState();
    state.selectedProjectId = 'project'; state.selectedConversationId = 'conversation'; state.selectedId = value.id;
    CodexiaAgentView._internal.setDebugData({ projects: [{ id: 'project', name: 'Project' }], jobs: [value] });
  }, value);
}
async function assertCheck(page, selector, key, state, source) {
  const row = page.locator(`${selector} [data-check="${key}"]`);
  assert.equal(await row.getAttribute('data-state'), state);
  if (source) assert.equal(await row.getAttribute('data-source-match'), source);
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1300, height: 950 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.agentDesktop = {
        getDefaultWorkspace: async () => null, getRepoRoot: async () => '/', readTree: async () => ({ entries: [] }), listFiles: async () => [],
        readFile: async () => ({ content: '' }), writeFile: async () => {}, exists: async () => false, stat: async () => ({}),
        basename: async value => value?.split('/').pop() || value, dirname: async value => value?.split('/').slice(0, -1).join('/') || '/',
        joinPath: async (...parts) => parts.join('/'), relative: async (_from, to) => to, normalize: async value => value,
        agentStatus: async () => ({ running: false, managed: false, port: 8000 }), onAgentServerExit: () => () => {}, onMenu: () => () => {},
      };
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/src/index.html?__agent_windows_fixture=1`);
    await page.waitForFunction(() => window.AiPanel?.debug && window.EditorApp?.openDiff);
    await fixture(page);
    await page.locator('#aiDelivery > summary').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('#aiDelivery').getAttribute('open'), '', 'keyboard opens verification details');
    await assertCheck(page, '#aiDelivery', 'build', 'passed', 'match');
    const successColor = await page.locator('#aiDelivery [data-check="build"]').evaluate(node => getComputedStyle(node).color);
    assert.equal(await page.locator('#aiDelivery [data-check="build"] .delivery-check-status > span').textContent(), '✓');
    await assertCheck(page, '#aiDelivery', 'unit_tests', 'passed', 'match');
    await assertCheck(page, '#aiDelivery', 'installation', 'unknown');
    assert.match(await page.locator('#aiDelivery').textContent(), /2 通过 \/ 0 失败 \/ 1 跳过/);
    assert.match(await page.locator('#aiDelivery').textContent(), /运行时间：2026/);
    assert.match(await page.locator('#aiDelivery').textContent(), /不代表之后的工作区/);

    for (const [state, counts] of [['failed', { total: 2, passed: 1, failed: 1, skipped: 0 }], ['no_tests', { total: 0, passed: 0, failed: 0, skipped: 0 }], ['skipped', { total: 2, passed: 0, failed: 0, skipped: 2 }], ['canceled', null], ['interrupted', null], ['unknown', null]]) {
      const value = { ...job, verification: { ...job.verification, build: { ...run, source_match: 'changed' }, unit_tests: { ...job.verification.unit_tests, state, counts } } };
      await fixture(page, value);
      await assertCheck(page, '#aiDelivery', 'build', 'passed', 'changed');
      assert.equal(await page.locator('#aiDelivery [data-check="build"] .delivery-check-status > span').textContent(), '○');
      assert.notEqual(await page.locator('#aiDelivery [data-check="build"]').evaluate(node => getComputedStyle(node).color), successColor);
      assert.match(await page.locator('#aiDelivery [data-check="build"]').textContent(), /此前执行通过/);
      await assertCheck(page, '#aiDelivery', 'unit_tests', state);
      await page.locator('[data-mode="agent-windows"]').click();
      await assertCheck(page, '.cx-thread-card', 'build', 'passed', 'changed');
      assert.equal(await page.locator('.cx-thread-card [data-check="build"] .delivery-check-status > span').textContent(), '○');
      assert.notEqual(await page.locator('.cx-thread-card [data-check="build"]').evaluate(node => getComputedStyle(node).color), successColor);
      await assertCheck(page, '.cx-thread-card', 'unit_tests', state);
      await page.locator('#cxSidebarDownload').click();
    }
    for (const verification of [undefined, { ...job.verification, schema_version: 9 }, { ...job.verification, job_id: 'other-job' }, { ...job.verification, unit_tests: { ...job.verification.unit_tests, counts: { total: -1, passed: -1, failed: 0, skipped: 0 } } }]) {
      await fixture(page, { ...job, verification });
      await assertCheck(page, '#aiDelivery', 'unit_tests', 'unknown');
      await page.locator('[data-mode="agent-windows"]').click();
      await assertCheck(page, '.cx-thread-card', 'unit_tests', 'unknown');
      await page.locator('#cxSidebarDownload').click();
    }
    await fixture(page, { ...job, verification: { ...job.verification, build: { ...run, source_match: 'unknown' } } });
    assert.equal(await page.locator('#aiDelivery [data-check="build"] .delivery-check-status > span').textContent(), '○');
    assert.notEqual(await page.locator('#aiDelivery [data-check="build"]').evaluate(node => getComputedStyle(node).color), successColor);
    assert.match(await page.locator('#aiDelivery [data-check="build"]').textContent(), /此前执行通过/);
    await page.locator('[data-mode="agent-windows"]').click();
    assert.equal(await page.locator('.cx-thread-card [data-check="build"] .delivery-check-status > span').textContent(), '○');
    assert.notEqual(await page.locator('.cx-thread-card [data-check="build"]').evaluate(node => getComputedStyle(node).color), successColor);
    await page.locator('#cxSidebarDownload').click();
    const dangerous = '<img src=x onerror="window.deliveryXss=1">';
    await fixture(page, { ...job, id: dangerous, verification: { ...job.verification, job_id: dangerous, build: { ...run, reason: dangerous } } });
    assert.ok(await page.locator('#aiDelivery').textContent().then(text => text.includes('<img')));
    assert.equal(await page.locator('#aiDelivery img').count(), 0);
    assert.equal(await page.locator('.cx-thread-card .delivery-card img').count(), 0);
    assert.equal(await page.evaluate(() => window.deliveryXss), undefined);

    // Deferred terminal reconciliation for old A must not put evidence in B or
    // a later binding of A. This uses the production watcher callback path.
    await fixture(page, { ...job, status: 'running', verification: null });
    await page.evaluate(() => {
      AiPanel.client.job = () => new Promise(resolve => { window.finishOldJob = resolve; });
      window.oldDone = watchers[0].callback({ kind: 'done', status: 'succeeded' });
    });
    assert.equal(await page.locator('#aiDelivery').isHidden(), true, 'wait for authoritative terminal evidence');
    await page.evaluate(async () => { await AiPanel.selectConversation('other', { loadHistory: false }); });
    await page.evaluate(value => { finishOldJob({ job: value }); }, job);
    await page.evaluate(() => oldDone);
    assert.equal(await page.locator('#aiDelivery').isHidden(), true, 'old job cannot populate another chat');
    await fixture(page, { ...job, status: 'running', verification: null });
    await page.evaluate(() => {
      AiPanel.client.job = () => new Promise(resolve => { window.finishOldJob = resolve; });
      window.oldDone = watchers[0].callback({ kind: 'done', status: 'succeeded' });
      AiPanel.client.configure({ token: 'new-account' });
    });
    await page.evaluate(value => finishOldJob({ job: value }), job); await page.evaluate(() => oldDone);
    assert.equal(await page.locator('#aiDelivery').isHidden(), true, 'old account cannot populate evidence');

    await fixture(page);
    await page.locator('[data-mode="agent-windows"]').click();
    // A summary from an older server omitting verification must clear cached
    // success instead of retaining it via object spread.
    await page.evaluate(async value => {
      const state = CodexiaAgentView._internal.getState(); state.debugData = null; state.jobDetails.set(value.id, value);
      AiPanel.client.jobs = async () => ({ jobs: [{ id: value.id, project_id: 'project', conversation_id: 'conversation', status: 'succeeded' }] });
      await CodexiaAgentView.refresh();
    }, job);
    await assertCheck(page, '.cx-thread-card', 'build', 'unknown');
    await fixture(page);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const selector of ['.cx-thread-card .delivery-card']) {
      const bounds = await page.locator(selector).evaluate(node => ({ width: node.getBoundingClientRect().width, scroll: node.scrollWidth, client: node.clientWidth }));
      assert.ok(bounds.width <= 390 && bounds.scroll <= bounds.client + 1, 'narrow verification card does not overflow');
    }
    await page.setViewportSize({ width: 1300, height: 950 });
    await page.locator('#cxSidebarDownload').click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#aiDelivery > summary').isVisible(), true);
    const overflow = await page.locator('#aiDelivery').evaluate(node => node.scrollWidth > node.clientWidth + 1);
    assert.equal(overflow, false, 'narrow AI details remain scrollable without horizontal overflow');
    const output = path.resolve(root, '../.artifacts/iteration-003');
    fs.mkdirSync(output, { recursive: true });
    await page.screenshot({ path: path.join(output, 'delivery-ai-narrow.png') });
    await page.setViewportSize({ width: 1300, height: 950 });
    await page.locator('[data-mode="agent-windows"]').click();
    await page.screenshot({ path: path.join(output, 'delivery-workbench.png') });
    assert.deepEqual(errors, []);
    console.log('delivery-ui-browser.test: OK (both entries, schema states, input boundary, keyboard, escaping, stale responses/cache, narrow layout)');
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
