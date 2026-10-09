/** Real Electron recovery after service crash, using isolated model/data/profile. */
const assert = require("assert");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { _electron } = require("playwright");
const { isolatedSmokeEnv, latestToolResult } = require("./smoke/environment");

const desktopDir = path.join(__dirname, "..");
const repoRoot = path.join(desktopDir, "..");
const SHARED_STUB = path.join(repoRoot, "tests", "e2e", "stub_scenario_model.py");
const SHARED_SCENARIOS = path.join(repoRoot, "tests", "e2e", "scenarios");

const AGENT_PORT = Number(process.env.AGENT_RECOVERY_SMOKE_PORT || 8131);
const STUB_PORT = Number(process.env.AGENT_RECOVERY_SMOKE_STUB_PORT || 9483);
const REG_TOKEN = "recovery-smoke-reg-token-123";
const SERVER_URL = `http://127.0.0.1:${AGENT_PORT}`;
const ACCOUNT_EMAIL = "desktop-recovery-smoke@example.com";
const ACCOUNT_PASSWORD = "secure-scenario-123";

const SMOKE_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "agent-recovery-data-"));
const SMOKE_PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "agent-recovery-prof-"));

let svcLog = "";

const children = [];
function track(child) {
  children.push(child);
  return child;
}
function cleanup() {
  // SIGKILL: smoke services are disposable; graceful SIGTERM can hang on
  // non-daemon worker threads and leak ports that poison the next run.
  for (const child of children) {
    try {
      if (!child.killed) child.kill("SIGKILL");
    } catch (_) {}
  }
}
process.on("exit", cleanup);

function assertPortFree(port) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 600 }, (res) => {
      res.resume();
      reject(new Error(`port ${port} is already in use (stale smoke service?): kill it first`));
    });
    req.on("error", () => resolve());
    req.on("timeout", () => {
      req.destroy();
      resolve();
    });
  });
}

function waitForTcp(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 700 }, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() > deadline) reject(new Error(`port ${port} never opened`));
        else setTimeout(tryOnce, 400);
      });
      req.on("timeout", () => {
        req.destroy();
        if (Date.now() > deadline) reject(new Error(`port ${port} timeout`));
        else setTimeout(tryOnce, 400);
      });
    };
    tryOnce();
  });
}

function httpJson(method, urlPath, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(
      {
        host: "127.0.0.1",
        port: AGENT_PORT,
        path: urlPath,
        method,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      },
      (res) => {
        let raw = "";
        res.setEncoding("utf8");
        res.on("data", (c) => (raw += c));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, json: raw ? JSON.parse(raw) : null });
          } catch (e) {
            resolve({ status: res.statusCode, json: raw });
          }
        });
      },
    );
    req.on("error", reject);
    req.end(data || undefined);
  });
}

async function waitUntil(fn, timeoutMs, desc) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await fn();
    if (last) return last;
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`timeout waiting for: ${desc}`);
}

// The panel's conversation-switch chain clears #promptInput mid-flight and
// sendAsk() drops an empty prompt, so wait until no switch is in flight and
// the composer is ready before typing (same contract as electron-smoke).
async function settleComposer(page, desc) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const before = await page.evaluate(() => {
      const s = window.AiPanel.getState();
      return { token: s.loadToken, conv: s.conversationId };
    });
    await page.waitForTimeout(400);
    const after = await page.evaluate(() => {
      const s = window.AiPanel.getState();
      return {
        token: s.loadToken,
        conv: s.conversationId,
        ready: s.connected && s.selectedProjectId && s.conversationId && !s.running,
      };
    });
    if (after.ready && after.token === before.token && after.conv === before.conv) return;
  }
  throw new Error(`composer never settled: ${desc}`);
}

function startService() {
  const svc = track(spawn('python3', ['-m', 'agent', 'serve', '--host', '127.0.0.1', '--port', String(AGENT_PORT)], {
    cwd: repoRoot,
    env: isolatedSmokeEnv({
      AGENT_DATA_DIR: SMOKE_DATA, AGENT_CONFIG_PATH: path.join(SMOKE_DATA, 'test-config.yaml'),
      AGENT_WORKSPACES_DIR: path.join(SMOKE_DATA, 'workspaces'), AGENT_BUILDS_DIR: path.join(SMOKE_DATA, 'builds'),
      AGENT_BASE_URL: `http://127.0.0.1:${STUB_PORT}`, AGENT_API_KEY: 'sk-recovery-stub', AGENT_PROVIDER: 'deepseek',
      AGENT_REGISTRATION_ENABLED: '1', AGENT_REGISTRATION_TOKEN: REG_TOKEN,
      AGENT_MAX_REQUESTS_PER_MINUTE: '100000', PYTHONUNBUFFERED: '1',
    }), stdio: ['ignore', 'pipe', 'pipe'],
  }));
  svc.stdout.on('data', data => { svcLog += data; });
  svc.stderr.on('data', data => { svcLog += data; });
  return svc;
}
async function main() {
  await assertPortFree(AGENT_PORT); await assertPortFree(STUB_PORT);
  track(spawn('python3', [SHARED_STUB], { cwd: repoRoot,
    env: isolatedSmokeEnv({ AGENT_E2E_STUB_PORT: String(STUB_PORT), AGENT_E2E_SCENARIO_DIR: SHARED_SCENARIOS }), stdio: 'ignore' }));
  await waitForTcp(STUB_PORT, 15000);
  let svc = startService();
  await waitForTcp(AGENT_PORT, 30000);
  const reg = await httpJson('POST', '/api/auth/register', { body: { email: ACCOUNT_EMAIL, password: ACCOUNT_PASSWORD,
    display_name: 'Recovery Smoke', device: { device_id: 'recovery-smoke', device_name: 'Recovery Smoke', device_type: 'desktop' } } });
  assert.equal(reg.status, 201);
  const token = reg.json.token;
  const project = await httpJson('POST', '/api/projects', { token, body: { name: 'recovery-smoke' } });
  assert.equal(project.status, 201, JSON.stringify(project.json));
  const projectId = project.json.project?.id || project.json.id;
  const app = await _electron.launch({ args: ['.'], cwd: desktopDir,
    env: isolatedSmokeEnv({ AGENT_DESKTOP_USER_DATA: SMOKE_PROFILE, ANDROID_AGENT_SERVER_URL: SERVER_URL }) });
  track(app.process());
  const page = await app.firstWindow();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  try {
    await page.waitForFunction(() => window.AiPanel?.openSettings);
    await page.evaluate(() => AiPanel.openSettings());
    await page.fill('#accountEmail', ACCOUNT_EMAIL); await page.fill('#accountPassword', ACCOUNT_PASSWORD);
    await page.click('#btnAccountLogin');
    await page.waitForFunction(() => AiPanel.getState().connected);
    await page.evaluate(async id => { await AiPanel.refreshProjects(); await AiPanel.selectProject(id); }, projectId);
    await page.evaluate(() => document.getElementById('settingsDialog').close());
    await settleComposer(page, 'initial connection');

    for (const [iteration, entry] of ['panel', 'workbench'].entries()) {
      if (iteration) {
        await page.evaluate(() => AiPanel.createConversation(AiPanel.getState().selectedProjectId));
        await settleComposer(page, 'second recovery conversation');
      }
      await page.fill('#promptInput', `[[18_service_recovery]] recover via ${entry}`);
      await page.click('#btnSend');
      await page.waitForFunction(() => AiPanel.getState().currentJobId && AiPanel.getState().running);
      const sourceId = await page.evaluate(() => AiPanel.getState().currentJobId);
      const conversationId = await page.evaluate(() => AiPanel.getState().conversationId);
      // Approve only the deterministic local command in this isolated fixture.
      await waitUntil(async () => {
        const approvals = await httpJson('GET', `/api/jobs/${sourceId}/approvals`, { token });
        const pending = approvals.json.approvals || [];
        for (const approval of pending) {
          const result = await httpJson('POST', `/api/jobs/${sourceId}/approvals/${approval.id}`, { token, body: { approved: true } });
          assert.equal(result.status, 200, JSON.stringify(result.json));
        }
        const detail = await httpJson('GET', `/api/jobs/${sourceId}`, { token });
        return (detail.json.job.events || []).some(event => {
          const payload = event.payload || event;
          return (event.type || event.event_type) === 'tool_result' && payload.ok === true
            && String(payload.output || payload.preview || '').includes('COMPLETED-BEFORE-CRASH');
        });
      }, 30000, 'durable successful tool before crash');
      const exited = new Promise(resolve => svc.once('exit', resolve));
      svc.kill('SIGKILL'); await exited;
      // Simulate only this dead worker's five-minute lease expiring. The
      // actual startup canonical interruption and recover API are exercised.
      const expire = spawnSync('python3', ['-c', "import sqlite3,sys,time\nwith sqlite3.connect(sys.argv[1]) as db:\n assert db.execute(\"UPDATE tasks SET lease_expires_at=? WHERE id=? AND status='running'\", (time.time()-1,sys.argv[2])).rowcount == 1", path.join(SMOKE_DATA, 'agent.db'), sourceId],
        { env: isolatedSmokeEnv(), encoding: 'utf8' });
      assert.equal(expire.status, 0, expire.stderr);
      svc = startService(); await waitForTcp(AGENT_PORT, 30000);
      await waitUntil(async () => (await httpJson('GET', `/api/jobs/${sourceId}`, { token })).json.job.can_recover === true, 15000, 'authoritative recovery eligibility');
      await page.evaluate(id => AiPanel.openJob(id), sourceId);
      if (entry === 'workbench') {
        await page.locator('[data-mode="agent-windows"]').click();
        await page.evaluate(() => CodexiaAgentView.refresh());
        await page.waitForFunction(id => CodexiaAgentView._internal.getState().selectedId === id, sourceId);
      }
      const selector = entry === 'panel' ? '#btnRecoverJob' : '#cxRecoverTask';
      await page.waitForSelector(selector, { state: 'visible' });
      assert.equal(await page.locator(selector).textContent(), '恢复任务');
      await page.locator(selector).click();
      // A queued second DOM click must join or be blocked while busy.
      await page.locator(selector).dispatchEvent('click');
      await page.waitForFunction(sourceId => AiPanel.getState().currentJobId !== sourceId, sourceId);
      const recoveryId = await page.evaluate(() => AiPanel.getState().currentJobId);
      assert.ok(recoveryId && recoveryId !== sourceId);
      assert.equal(await page.evaluate(() => AiPanel.getState().conversationId), conversationId);
      await page.waitForFunction(() => AiPanel.getState().jobStatus === 'succeeded' && !AiPanel.getState().running, null, { timeout: 30000 });
      const source = (await httpJson('GET', `/api/jobs/${sourceId}`, { token })).json.job;
      assert.equal(source.can_recover, false); assert.equal(source.recovery_job_id, recoveryId);
      const jobs = (await httpJson('GET', `/api/jobs?project_id=${projectId}&conversation_id=${conversationId}`, { token })).json.jobs;
      assert.equal(jobs.length, 2, 'double click must not create duplicate recovery jobs');
      assert.ok(jobs.every(job => job.conversation_id === conversationId));
      const events = (await httpJson('GET', `/api/conversations/${conversationId}/events`, { token })).json.events;
      assert.equal(events.filter(event => event.event_type === 'recovery_note').length, 1);
      const effects = fs.readFileSync(path.join(SMOKE_DATA, 'workspaces', reg.json.user_id, projectId, 'app/build/recovery-effects.log'), 'utf8').trim().split('\n');
      assert.deepEqual(effects, Array(iteration + 1).fill('once'), 'completed tools must never replay after recovery');
      if (entry === 'workbench') await page.locator('#cxSidebarDownload').click();
      await page.evaluate(id => AiPanel.openJob(id), sourceId);
      await page.waitForSelector('#btnRecoverJob', { state: 'visible' });
      assert.equal(await page.locator('#btnRecoverJob').textContent(), '查看恢复任务');
      const writes = []; const record = request => { if (request.method() === 'POST' && /\/recover|\/resume/.test(request.url())) writes.push(request.url()); };
      page.on('request', record);
      await page.locator('#btnRecoverJob').click();
      await page.waitForFunction(id => AiPanel.getState().currentJobId === id && !AiPanel.getState().controlBusy, recoveryId);
      page.off('request', record);
      assert.deepEqual(writes, [], 'view recovery is read-only');
      console.log(`ok - ${entry}: SIGKILL/restart, recovery, same conversation, no duplicate effects, read-only reopen`);
    }
    assert.deepEqual(errors, []);
    console.log('electron-recovery-smoke.test: OK');
  } finally { await app.close().catch(() => {}); }
}
main().catch(error => { console.error(error); console.error(svcLog.slice(-5000)); process.exitCode = 1; }).finally(cleanup);
