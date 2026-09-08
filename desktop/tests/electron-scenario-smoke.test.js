/**
 * Scenario-driven Electron smoke test sharing the backend E2E fixture:
 *
 *   stub model  = tests/e2e/stub_scenario_model.py   (same script as backend E2E)
 *   scenarios   = tests/e2e/scenarios/*.json          (same JSON scripts)
 *
 * The user prompt embeds a "[[scenario_id]]" marker so the stub replays the
 * scripted tool calls / final answers, and the real Electron UI, real agent
 * service and real tool execution drive the whole chain:
 *
 *   user asks -> tool execution -> approval -> file change -> conversation
 *
 * Run: node tests/electron-scenario-smoke.test.js
 */
const assert = require("assert");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { _electron } = require("playwright");

const desktopDir = path.join(__dirname, "..");
const repoRoot = path.join(desktopDir, "..");
const SHARED_STUB = path.join(repoRoot, "tests", "e2e", "stub_scenario_model.py");
const SHARED_SCENARIOS = path.join(repoRoot, "tests", "e2e", "scenarios");

const AGENT_PORT = Number(process.env.AGENT_SCENARIO_SMOKE_PORT || 8127);
const STUB_PORT = Number(process.env.AGENT_SCENARIO_SMOKE_STUB_PORT || 9479);
const REG_TOKEN = "scenario-smoke-reg-token-123";
const SERVER_URL = `http://127.0.0.1:${AGENT_PORT}`;
const ACCOUNT_EMAIL = "desktop-scenario-smoke@example.com";
const ACCOUNT_PASSWORD = "secure-scenario-123";

const SMOKE_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "agent-scenario-data-"));
const SMOKE_PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "agent-scenario-prof-"));

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

async function main() {
  assert.ok(fs.existsSync(SHARED_STUB), `shared stub model not found: ${SHARED_STUB}`);
  assert.ok(
    fs.existsSync(path.join(SHARED_SCENARIOS, "01_simple_answer.json")),
    `shared scenario fixture not found: ${SHARED_SCENARIOS}`,
  );

  // 0) refuse to run against leftover services from a previous failed run
  await assertPortFree(STUB_PORT);
  await assertPortFree(AGENT_PORT);

  // 1) the SAME scenario stub model the backend E2E runner uses
  track(
    spawn("python3", [SHARED_STUB], {
      env: {
        ...process.env,
        AGENT_E2E_STUB_PORT: String(STUB_PORT),
        AGENT_E2E_SCENARIO_DIR: SHARED_SCENARIOS,
      },
      stdio: "ignore",
    }),
  );
  await waitForTcp(STUB_PORT, 15000);
  console.log("ok - shared scenario stub model up on", STUB_PORT);

  // 2) real agent service, isolated data dir, stub provider
  const svc = track(
    spawn("python3", ["-m", "agent", "serve", "--host", "127.0.0.1", "--port", String(AGENT_PORT)], {
      cwd: repoRoot,
      env: {
        ...process.env,
        AGENT_DATA_DIR: SMOKE_DATA,
        AGENT_BASE_URL: `http://127.0.0.1:${STUB_PORT}`,
        AGENT_API_KEY: "sk-scenario-smoke-stub",
        AGENT_REGISTRATION_ENABLED: "1",
        AGENT_REGISTRATION_TOKEN: REG_TOKEN,
        NO_PROXY: "*",
        no_proxy: "*",
        HTTP_PROXY: "",
        HTTPS_PROXY: "",
        ALL_PROXY: "",
        http_proxy: "",
        https_proxy: "",
        all_proxy: "",
        // This host blocks sandbox_apply, which would fail every run_command.
        AGENT_CMD_SANDBOX: "0",
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    }),
  );
  svc.stdout.on("data", (d) => (svcLog += d.toString()));
  svc.stderr.on("data", (d) => (svcLog += d.toString()));
  await waitForTcp(AGENT_PORT, 30000);
  console.log("ok - agent service up on", AGENT_PORT);

  // 3) register a scenario-smoke user
  const reg = await httpJson("POST", "/api/auth/register", {
    body: {
      email: ACCOUNT_EMAIL,
      password: ACCOUNT_PASSWORD,
      display_name: "Desktop Scenario Smoke",
      device: {
        device_id: "scenario-smoke-bootstrap",
        device_name: "Scenario Smoke Bootstrap",
        device_type: "desktop",
        platform: process.platform,
        app_version: "0.1.0",
      },
    },
  });
  assert.strictEqual(reg.status, 201, `register failed: ${JSON.stringify(reg.json)}`);
  console.log("ok - registered smoke user", reg.json.user_id);

  // 4) launch real Electron with isolated profile
  const app = await _electron.launch({
    args: [".", "--no-sandbox", "--disable-gpu"],
    cwd: desktopDir,
    env: {
      ...process.env,
      AGENT_DESKTOP_USER_DATA: SMOKE_PROFILE,
      ANDROID_AGENT_SERVER_URL: SERVER_URL,
    },
  });
  const page = await app.firstWindow();
  const pageErrors = [];
  let closing = false;
  page.on("pageerror", (e) => pageErrors.push(`pageerror: ${e && e.message ? e.message : e}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !closing) pageErrors.push(`console: ${m.text()}`);
  });
  await page.waitForSelector("#promptInput", { timeout: 20000 });
  await page.waitForFunction(() => window.AiPanel?.openSettings, null, { timeout: 20000 });

  // 5) log in using only email + password against the configured service
  await page.evaluate(() => window.AiPanel.openSettings());
  await page.fill("#accountEmail", ACCOUNT_EMAIL);
  await page.fill("#accountPassword", ACCOUNT_PASSWORD);
  await page.click("#btnAccountLogin");
  await waitUntil(
    () => page.evaluate(() => document.getElementById("connPill").dataset.state === "ok"),
    20000,
    "connection ok",
  );
  const userId = await page.evaluate(() => window.AiPanel.getState().userId);
  console.log("ok - desktop connected to service as", userId);

  // 6) create a project (template copy under workspaces/{user})
  await page.evaluate(() => window.AiPanel.openCreateProject());
  await page.fill('#createProjectForm input[name="name"]', "scenario-smoke");
  await page.click('#createProjectForm button.primary-btn');
  await waitUntil(
    () =>
      page.evaluate(() => {
        const sel = document.getElementById("projectSelect");
        return sel && sel.options.length > 0 && sel.value;
      }),
    20000,
    "project selected",
  );
  const projectId = await page.evaluate(() => window.AiPanel.getState().selectedProjectId);
  await settleComposer(page, "after project creation");
  console.log("ok - project created", projectId);

  const send = async (text) => {
    await page.fill("#promptInput", text);
    await page.click("#btnSend");
  };
  const lastAssistantText = () =>
    page.evaluate(() => {
      const nodes = [...document.querySelectorAll(".tl-assistant")];
      return nodes.length ? nodes[nodes.length - 1].textContent : "";
    });
  const jobDone = () =>
    page.evaluate(() => {
      const s = window.AiPanel.getState();
      return !s.running && s.jobStatus === "succeeded";
    });

  // 7) scenario 01: pure streamed answer, zero tool cards
  await send("[[01_simple_answer]] Android 图片缓存用什么方案？");
  await waitUntil(
    () => page.evaluate(() => document.querySelectorAll(".tl-assistant").length >= 1),
    20000,
    "scenario 01 bubble",
  );
  await waitUntil(async () => (await lastAssistantText()).includes("LruCache"), 30000, "scenario 01 final text");
  await waitUntil(jobDone, 30000, "scenario 01 job succeeded");
  const shape01 = await page.evaluate(() => ({
    toolCards: document.querySelectorAll(".tl-tool").length,
    approvals: document.querySelectorAll(".tl-approval").length,
  }));
  assert.strictEqual(shape01.toolCards, 0, "scenario 01 must not render tool cards");
  assert.strictEqual(shape01.approvals, 0, "scenario 01 must not render approval cards");
  console.log("ok - scenario 01_simple_answer replayed in desktop UI");
  await page.screenshot({ path: path.join(__dirname, "scenario-smoke-1-answer.png") });

  // 8) scenario 03: scripted write_file -> real file change + review card
  await send("[[03_edit_one_file]] 把 app_name 改成 E2E-EDITED");
  await waitUntil(
    () =>
      page.evaluate(() => {
        const btns = [...document.querySelectorAll(".tl-changes button")];
        return btns.some((b) => b.textContent.includes("审查改动") && !b.disabled);
      }),
    60000,
    "scenario 03 review button",
  );
  const wsFile = path.join(
    repoRoot,
    "workspaces",
    userId,
    projectId,
    "app/src/main/res/values/strings.xml",
  );
  const written = fs.readFileSync(wsFile, "utf8");
  assert.ok(written.includes("E2E-EDITED"), `real file modified by scenario 03: ${written}`);
  await waitUntil(async () => (await lastAssistantText()).includes("E2E-EDITED"), 30000, "scenario 03 final text");
  await waitUntil(jobDone, 30000, "scenario 03 job succeeded");
  console.log("ok - scenario 03_edit_one_file wrote real file in isolated workspace");
  await page.screenshot({ path: path.join(__dirname, "scenario-smoke-2-edit.png") });

  // 9) scenario 08: scripted run_command -> approval card -> approve -> executed
  await send("[[08_approval]] 运行一条命令打印 E2E-APPROVAL-OK");
  const card = page.locator(".tl-approval.is-pending").last();
  await card.waitFor({ state: "visible", timeout: 30000 });
  const info = await card.evaluate((n) => ({
    title: n.querySelector(".tl-approval-name")?.textContent || "",
    body: n.textContent || "",
  }));
  assert.ok(info.title.includes("运行命令"), `approval title: ${info.title}`);
  assert.ok(info.body.includes("E2E-APPROVAL-OK"), `approval body shows command: ${info.body}`);
  await page.screenshot({ path: path.join(__dirname, "scenario-smoke-3-approval.png") });
  await card.locator("button", { hasText: "允许一次" }).click();
  await waitUntil(
    () =>
      page.evaluate(() => {
        const cards = [...document.querySelectorAll(".tl-approval")];
        const last = cards[cards.length - 1];
        return Boolean(last && last.classList.contains("is-approved"));
      }),
    30000,
    "approval resolved",
  );
  await waitUntil(
    async () => (await lastAssistantText()).includes("E2E-APPROVAL-OK"),
    30000,
    "scenario 08 final text",
  );
  await waitUntil(jobDone, 30000, "scenario 08 job succeeded");
  console.log("ok - scenario 08_approval full approval chain in desktop UI");

  // 10) no uncaught exceptions
  closing = true;
  await app.close();
  const realErrors = pageErrors.filter(
    (e) => !/net::|Failed to load resource|WebSocket is closed|ECONNREFUSED/i.test(e),
  );
  assert.deepStrictEqual(realErrors, [], `uncaught errors: ${realErrors.join(" | ")}`);
  console.log("ok - no uncaught exceptions");

  svc.kill("SIGKILL");
  console.log("electron-scenario-smoke: OK");
  cleanup();
  process.exit(0);
}

main().catch((err) => {
  console.error("electron-scenario-smoke FAILED:", err && err.message ? err.message : err);
  if (svcLog) console.error("service log tail:\n" + svcLog.slice(-3000));
  cleanup();
  process.exit(1);
});
