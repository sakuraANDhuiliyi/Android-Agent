/** Real Electron message receipts with an isolated API, local model stub, and dropped POST response. */
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

const AGENT_PORT = Number(process.env.AGENT_MESSAGES_SMOKE_PORT || 8151);
const STUB_PORT = Number(process.env.AGENT_MESSAGES_SMOKE_STUB_PORT || 9503);
const REG_TOKEN = "messages-smoke-reg-token-123";
const SERVER_URL = `http://127.0.0.1:${AGENT_PORT}`;
const ACCOUNT_EMAIL = "desktop-messages-smoke@example.com";
const ACCOUNT_PASSWORD = "secure-scenario-123";

const SMOKE_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "agent-messages-data-"));
const SMOKE_PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "agent-messages-prof-"));

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
  const scenarios = path.join(SMOKE_DATA, 'scenarios'); fs.mkdirSync(scenarios);
  fs.writeFileSync(path.join(scenarios, 'parent.json'), JSON.stringify({ id: 'desktop_messages_parent', steps: [
    { type: 'tool', delay_ms: 1500, calls: [{ name: 'read_file', arguments: { path: 'app/src/main/AndroidManifest.xml' } }] },
    { type: 'final', text: 'MESSAGE_RECEIPTS_PARENT_DONE', requires_user_occurrences: { '消息重复验证标记': 2 } },
  ] }));
  fs.writeFileSync(path.join(scenarios, 'child.json'), JSON.stringify({ id: 'desktop_messages_child', steps: [
    { type: 'final', delay_ms: 2500, text: 'MESSAGE_RECEIPTS_CHILD_DONE' },
  ] }));
  track(spawn('python3', [SHARED_STUB], { cwd: repoRoot,
    env: isolatedSmokeEnv({ AGENT_E2E_STUB_PORT: String(STUB_PORT), AGENT_E2E_SCENARIO_DIR: scenarios }), stdio: 'ignore' }));
  await waitForTcp(STUB_PORT, 15000); startService(); await waitForTcp(AGENT_PORT, 30000);
  const reg = await httpJson('POST', '/api/auth/register', { body: { email: ACCOUNT_EMAIL, password: ACCOUNT_PASSWORD,
    display_name: 'Messages Smoke', device: { device_id: 'messages-smoke', device_name: 'Messages Smoke', device_type: 'desktop' } } });
  assert.equal(reg.status, 201); const token = reg.json.token;
  const project = await httpJson('POST', '/api/projects', { token, body: { name: 'messages-smoke' } });
  assert.equal(project.status, 201, JSON.stringify(project.json)); const projectId = project.json.project?.id || project.json.id;
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
    await settleComposer(page, 'messages project ready');
    const source = await page.evaluate(async () => {
      const reply = await AiPanel.client.askConversation(AiPanel.getState().conversationId, { prompt: '只读查看清单并总结 [[desktop_messages_parent]]', run_mode: 'read_only' });
      await AiPanel.client.pauseJob(reply.job.id); AiPanel.adoptJob(reply.job); return reply.job.id;
    });
    await waitUntil(async () => (await httpJson('GET', `/api/jobs/${source}`, { token })).json.job.status === 'paused', 20000, 'parent paused at a safe boundary');
    await page.evaluate(id => AiPanel.openJob(id), source);

    // Execute the real POST on the server, then drop its response. A reload
    // must retain the original UUID/body and GET before the explicit retry.
    let dropped = false, originalBody, firstReceipt;
    await page.route('**/api/jobs/*/messages', async route => {
      if (route.request().method() !== 'POST' || dropped) return route.continue();
      dropped = true; originalBody = route.request().postDataJSON();
      const response = await route.fetch(); firstReceipt = (await response.json()).message;
      assert.equal(response.status(), 201); await route.abort('failed');
    });
    await page.fill('#promptInput', 'password=synthetic_message_receipt_secret'); await page.click('#btnSend');
    await page.locator('#aiMessageReceipts [data-state="unconfirmed"]').waitFor();
    assert.ok(dropped); assert.ok(originalBody.message_key);
    await page.reload(); await page.waitForFunction(() => window.AiPanel?.getState().connected);
    await page.evaluate(id => AiPanel.openJob(id), source);
    await page.locator('#aiMessageReceipts [data-state="unconfirmed"]').waitFor();
    const beforeRetry = await httpJson('GET', `/api/jobs/${source}/messages?include_consumed=true`, { token });
    const beforeSteers = beforeRetry.json.messages.filter(row => row.type === 'steer');
    assert.equal(beforeSteers.length, 1);
    assert.equal(beforeSteers[0].payload.text, 'password=[REDACTED]');
    await page.locator('#aiMessageReceipts').getByRole('button', { name: '核对并重试' }).click();
    await page.locator('#aiMessageReceipts [data-state="pending"]').waitFor();
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(JobMessages.STORAGE_KEY)).length), 0);
    console.log('ok - real accepted POST with lost response survives reload; explicit retry reconciles original UUID/body despite redaction');

    for (let index = 0; index < 2; index++) {
      await page.fill('#promptInput', '消息重复验证标记'); await page.click('#btnSend');
      await page.waitForFunction(count => document.querySelectorAll('#aiMessageReceipts [data-state="pending"]').length === count, index + 2);
    }
    await page.locator('[data-mode="agent-windows"]').click(); await page.evaluate(() => CodexiaAgentView.refresh());
    await page.locator('.cx-project-task').filter({ hasText: '只读查看清单' }).click();
    await page.locator('#cxMessageModes [data-message-mode="follow_up"]').click();
    await page.fill('#cxAgentPrompt', '只读总结后续任务 [[desktop_messages_child]]'); await page.click('#cxSendAgent');
    await page.waitForFunction(() => document.querySelectorAll('#cxMessageReceipts [data-state="pending"]').length === 4);
    await page.click('#cxResumeTask');
    const parent = await waitUntil(async () => {
      const job = (await httpJson('GET', `/api/jobs/${source}`, { token })).json.job;
      return ['succeeded', 'failed', 'canceled'].includes(job.status) ? job : null;
    }, 30000, 'source completed with both identical steers');
    assert.equal(parent.status, 'succeeded', parent.error_message);
    assert.ok(parent.final_message.includes('MESSAGE_RECEIPTS_PARENT_DONE'), parent.final_message);
    const messages = await waitUntil(async () => {
      const rows = (await httpJson('GET', `/api/jobs/${source}/messages?include_consumed=true`, { token })).json.messages.filter(row => ['steer', 'follow_up'].includes(row.type));
      return rows.some(row => row.delivery_state === 'follow_up_created') ? rows : null;
    }, 20000, 'authoritative follow-up child receipt');
    assert.equal(messages.length, 4); assert.equal(new Set(messages.map(row => row.message_key)).size, 4);
    assert.equal(messages.filter(row => row.delivery_state === 'consumed').length, 3);
    const repeated = await httpJson('POST', `/api/jobs/${source}/messages`, { token, body: originalBody });
    assert.equal(repeated.status, 200); assert.equal(repeated.json.message.id, firstReceipt.id);
    const conflict = await httpJson('POST', `/api/jobs/${source}/messages`, { token, body: { ...originalBody, payload: { text: 'different body' } } });
    assert.equal(conflict.status, 409);
    const history = await httpJson('GET', `/api/conversations/${parent.conversation_id}/events`, { token });
    for (const message of messages.filter(row => row.type === 'steer')) {
      const canonical = history.json.events.filter(event => event.payload?.task_message_id === message.id);
      assert.equal(canonical.length, 1); assert.equal(canonical[0].payload.message_id, message.context_message_id);
    }
    const follow = messages.find(row => row.type === 'follow_up');
    const child = (await httpJson('GET', `/api/jobs/${follow.follow_up_job_id}`, { token })).json.job;
    assert.equal(child.conversation_id, parent.conversation_id); assert.equal(child.turn_id, follow.follow_up_turn_id);
    await page.waitForFunction(() => document.querySelector('#cxMessageReceipts [data-state="follow_up_created"]'));
    await page.locator('#cxMessageReceipts').getByRole('button', { name: '查看后续任务' }).click();
    await page.waitForFunction(id => CodexiaAgentView._internal.getState().selectedId === id, child.id);
    await page.locator('#cxSidebarDownload').click(); await page.evaluate(id => AiPanel.openJob(id), source);
    await page.locator('#aiMessageReceipts').getByRole('button', { name: '查看后续任务' }).click();
    await page.waitForFunction(id => AiPanel.getState().currentJobId === id, child.id);
    assert.deepEqual(errors, []);
    console.log('ok - both real desktop entries, repeated text as distinct intents, one canonical receipt per steer, terminal idempotency, and authoritative child navigation');
    console.log('electron-messages-smoke.test: OK');
  } finally { await app.close().catch(() => {}); }
}
main().catch(error => { console.error(error); console.error(svcLog.slice(-9000)); process.exitCode = 1; }).finally(cleanup);
