/** Real Electron delivery evidence from controlled test processes and JUnit reports. */
const assert = require("assert");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { _electron } = require("playwright");
const { isolatedSmokeEnv } = require("./smoke/environment");

const desktopDir = path.join(__dirname, "..");
const repoRoot = path.join(desktopDir, "..");
const SHARED_STUB = path.join(repoRoot, "tests", "e2e", "stub_scenario_model.py");
const SHARED_SCENARIOS = path.join(repoRoot, "tests", "e2e", "scenarios");

const AGENT_PORT = Number(process.env.AGENT_DELIVERY_SMOKE_PORT || 8141);
const STUB_PORT = Number(process.env.AGENT_DELIVERY_SMOKE_STUB_PORT || 9493);
const REG_TOKEN = "delivery-smoke-reg-token-123";
const SERVER_URL = `http://127.0.0.1:${AGENT_PORT}`;
const ACCOUNT_EMAIL = "desktop-delivery-smoke@example.com";
const ACCOUNT_PASSWORD = "secure-scenario-123";

const SMOKE_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "agent-delivery-data-"));
const SMOKE_PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "agent-delivery-prof-"));

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
      ANDROID_HOME: path.join(SMOKE_DATA, 'sdk'), ANDROID_SDK_ROOT: path.join(SMOKE_DATA, 'sdk'),
    }), stdio: ['ignore', 'pipe', 'pipe'],
  }));
  svc.stdout.on('data', data => { svcLog += data; });
  svc.stderr.on('data', data => { svcLog += data; });
  return svc;
}
async function main() {
  await assertPortFree(AGENT_PORT); await assertPortFree(STUB_PORT);
  fs.mkdirSync(path.join(SMOKE_DATA, 'sdk/platforms'), { recursive: true });
  track(spawn('python3', [SHARED_STUB], { cwd: repoRoot,
    env: isolatedSmokeEnv({ AGENT_E2E_STUB_PORT: String(STUB_PORT), AGENT_E2E_SCENARIO_DIR: SHARED_SCENARIOS }), stdio: 'ignore' }));
  await waitForTcp(STUB_PORT, 15000); startService(); await waitForTcp(AGENT_PORT, 30000);
  const reg = await httpJson('POST', '/api/auth/register', { body: { email: ACCOUNT_EMAIL, password: ACCOUNT_PASSWORD,
    display_name: 'Delivery Smoke', device: { device_id: 'delivery-smoke', device_name: 'Delivery Smoke', device_type: 'desktop' } } });
  assert.equal(reg.status, 201); const token = reg.json.token;
  const project = await httpJson('POST', '/api/projects', { token, body: { name: 'delivery-smoke' } });
  assert.equal(project.status, 201, JSON.stringify(project.json));
  const projectId = project.json.project?.id || project.json.id;
  const workspace = path.join(SMOKE_DATA, 'workspaces', reg.json.user_id, projectId);
  fs.copyFileSync(path.join(__dirname, 'fixtures/delivery-gradlew.sh'), path.join(workspace, 'gradlew'));
  fs.chmodSync(path.join(workspace, 'gradlew'), 0o755);
  const settings = await httpJson('PUT', `/api/projects/${projectId}/feedback/settings`, { token,
    body: { build_after_changes: false, run_tests: true, fix_failures: false } });
  assert.equal(settings.status, 200);
  const app = await _electron.launch({ args: ['.'], cwd: desktopDir,
    env: isolatedSmokeEnv({ AGENT_DESKTOP_USER_DATA: SMOKE_PROFILE, ANDROID_AGENT_SERVER_URL: SERVER_URL }) });
  track(app.process()); const page = await app.firstWindow();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  try {
    await page.waitForFunction(() => window.AiPanel?.openSettings);
    await page.evaluate(() => AiPanel.openSettings());
    await page.fill('#accountEmail', ACCOUNT_EMAIL); await page.fill('#accountPassword', ACCOUNT_PASSWORD); await page.click('#btnAccountLogin');
    await page.waitForFunction(() => AiPanel.getState().connected);
    await page.evaluate(async id => { await AiPanel.refreshProjects(); await AiPanel.selectProject(id); }, projectId);
    await page.evaluate(() => document.getElementById('settingsDialog').close());
    await settleComposer(page, 'verification project ready');
    const modes = [['passed', 'passed'], ['failed', 'failed'], ['zero', 'no_tests'], ['skipped', 'skipped'], ['missing_report', 'unknown'], ['canceled', 'canceled']];
    for (const [mode, expected] of modes) {
      fs.writeFileSync(path.join(workspace, 'delivery-mode.txt'), mode);
      fs.rmSync(path.join(workspace, 'app/build/test-started'), { force: true });
      await page.evaluate(async () => { await AiPanel.createConversation(AiPanel.getState().selectedProjectId); });
      const source = await page.evaluate(async mode => {
        const data = await AiPanel.client.askConversation(AiPanel.getState().conversationId, { prompt: `Run controlled verification ${mode}`, feedback_requested: true });
        AiPanel.adoptJob(data.job); return data.job.id;
      }, mode);
      if (mode === 'canceled') {
        await waitUntil(() => fs.existsSync(path.join(workspace, 'app/build/test-started')), 20000, 'test process started before cancellation');
        await page.click('#btnHeaderStop');
      }
      const detail = await waitUntil(async () => {
        const response = await httpJson('GET', `/api/jobs/${source}`, { token });
        return ['succeeded', 'failed', 'canceled', 'interrupted'].includes(response.json.job.status) ? response.json.job : null;
      }, 35000, `${mode} task finished`);
      assert.equal(detail.verification?.schema_version, 1, JSON.stringify(detail.verification));
      assert.equal(detail.verification.job_id, source);
      assert.equal(detail.verification.build.state, 'passed');
      assert.equal(detail.verification.unit_tests.state, expected, JSON.stringify(detail.verification));
      if (mode === 'passed') assert.deepEqual(detail.verification.unit_tests.counts, { total: 3, passed: 2, failed: 0, skipped: 1 });
      if (mode === 'failed') assert.deepEqual(detail.verification.unit_tests.counts, { total: 3, passed: 1, failed: 1, skipped: 1 });
      if (mode === 'zero') assert.deepEqual(detail.verification.unit_tests.counts, { total: 0, passed: 0, failed: 0, skipped: 0 });
      await page.evaluate(id => AiPanel.openJob(id), source);
      await page.locator('#aiDelivery > summary').waitFor();
      if ((await page.locator('#aiDelivery').getAttribute('open')) === null) await page.locator('#aiDelivery > summary').click();
      const check = page.locator('#aiDelivery [data-check="unit_tests"]');
      assert.equal(await check.getAttribute('data-state'), expected);
      assert.equal(await page.locator('#aiDelivery [data-check="installation"]').getAttribute('data-state'), 'unknown');
      assert.equal(await page.locator('#aiDelivery [data-check="build"]').getAttribute('data-source-match'), detail.verification.build.source_match);
      await page.locator('[data-mode="agent-windows"]').click();
      await page.evaluate(() => CodexiaAgentView.refresh());
      await page.locator('.cx-project-task').filter({ hasText: `Run controlled verification ${mode}` }).click();
      const row = page.locator(`.cx-thread-card[data-job-id="${source}"] [data-check="unit_tests"]`);
      await row.waitFor(); assert.equal(await row.getAttribute('data-state'), expected);
      await page.locator('#cxSidebarDownload').click();
      console.log(`ok - ${mode}: real test process → JUnit → job verification → both desktop entries`);
    }
    assert.deepEqual(errors, []); console.log('electron-delivery-smoke.test: OK');
  } finally { await app.close().catch(() => {}); }
}
main().catch(error => { console.error(error); console.error(svcLog.slice(-7000)); process.exitCode = 1; }).finally(cleanup);
