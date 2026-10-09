/**
 * Real end-to-end smoke test: stub model server + real agent service +
 * real Electron app. Isolated via AGENT_DATA_DIR and AGENT_DESKTOP_USER_DATA
 * so the user's database / credentials are never touched.
 *
 * Run: node tests/electron-smoke.test.js
 */
const assert = require("assert");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { _electron } = require("playwright");
const { isolatedSmokeEnv, latestToolResult } = require("./smoke/environment");

const desktopDir = path.join(__dirname, "..");
const repoRoot = path.join(desktopDir, "..");
const AGENT_PORT = Number(process.env.AGENT_SMOKE_PORT || 8123);
const STUB_PORT = Number(process.env.AGENT_SMOKE_STUB_PORT || 9477);
const REG_TOKEN = "smoke-reg-token-123";
const SERVER_URL = `http://127.0.0.1:${AGENT_PORT}`;
const ACCOUNT_EMAIL = "desktop-smoke@example.com";
const ACCOUNT_PASSWORD = "secure-smoke-123";

const SMOKE_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "agent-smoke-data-"));
const SMOKE_PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "agent-smoke-prof-"));

let svcLog = "";
let diagnosticPage = null;
const ARTIFACT_DIR = process.env.AGENT_SMOKE_ARTIFACT_DIR || path.join(repoRoot, ".artifacts", "electron-smoke-failure", `${Date.now()}-${process.pid}`);

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

function httpJson(method, urlPath, { token, body, headers = {} } = {}) {
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
          ...headers,
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

// The panel's conversation-switch chain (selectConversation) clears
// #promptInput mid-flight and sendAsk() silently drops an empty prompt, so
// typing immediately after 新对话/项目创建 races that chain: the fill is wiped
// before 发送 is clicked and the ask never reaches the server. Wait until no
// switch is in flight (loadToken stable) and the composer is ready.
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
  // 0) refuse to run against leftover services from a previous failed run
  await assertPortFree(STUB_PORT);
  await assertPortFree(AGENT_PORT);

  // 1) stub model server
  track(
    spawn("python3", [path.join(__dirname, "smoke", "stub_model_server.py")], {
      env: isolatedSmokeEnv({ AGENT_SMOKE_STUB_PORT: String(STUB_PORT) }),
      stdio: "ignore",
    }),
  );
  await waitForTcp(STUB_PORT, 15000);

  // 2) real agent service, isolated data dir, stub provider
  const svc = track(
    spawn("python3", ["-m", "agent", "serve", "--host", "127.0.0.1", "--port", String(AGENT_PORT)], {
      cwd: repoRoot,
      env: isolatedSmokeEnv({
        AGENT_DATA_DIR: SMOKE_DATA,
        AGENT_CONFIG_PATH: path.join(SMOKE_DATA, "test-config.yaml"),
        AGENT_WORKSPACES_DIR: path.join(SMOKE_DATA, "workspaces"),
        AGENT_BUILDS_DIR: path.join(SMOKE_DATA, "builds"),
        AGENT_BASE_URL: `http://127.0.0.1:${STUB_PORT}`,
        AGENT_API_KEY: "sk-smoke-stub",
        AGENT_PROVIDER: "deepseek",
        AGENT_REGISTRATION_ENABLED: "1",
        AGENT_REGISTRATION_TOKEN: REG_TOKEN,
        PYTHONUNBUFFERED: "1",
      }),
      stdio: ["ignore", "pipe", "pipe"],
    }),
  );
  svc.stdout.on("data", (d) => (svcLog += d.toString()));
  svc.stderr.on("data", (d) => (svcLog += d.toString()));
  await waitForTcp(AGENT_PORT, 30000);

  // 3) register a smoke user
  const reg = await httpJson("POST", "/api/auth/register", {
    body: {
      email: ACCOUNT_EMAIL,
      password: ACCOUNT_PASSWORD,
      display_name: "Desktop Smoke",
      device: {
        device_id: "smoke-bootstrap",
        device_name: "Smoke Bootstrap",
        device_type: "desktop",
        platform: process.platform,
        app_version: "0.1.0"
      }
    },
  });
  assert.strictEqual(reg.status, 201, `register failed: ${JSON.stringify(reg.json)}`);
  console.log("ok - registered smoke user", reg.json.user_id);

  // 4) launch real Electron with isolated profile
  const launchApp = () =>
    _electron.launch({
      args: ["."],
      cwd: desktopDir,
      env: isolatedSmokeEnv({
        AGENT_DESKTOP_USER_DATA: SMOKE_PROFILE,
        ANDROID_AGENT_SERVER_URL: SERVER_URL,
      }),
    });

  let app = await launchApp();
  track(app.process());
  let page = await app.firstWindow();
  diagnosticPage = page;
  const pageErrors = [];
  let closing = false;
  const wireErrors = (p) => {
    p.on("pageerror", (e) => pageErrors.push(`pageerror: ${e && e.message ? e.message : e}`));
    p.on("console", (m) => {
      if (m.type() === "error" && !closing) pageErrors.push(`console: ${m.text()}`);
    });
  };
  wireErrors(page);
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
  assert.strictEqual(userId, reg.json.account.user_id, "desktop logged into the registered account");
  assert.ok(/^usr_/.test(userId), `desktop reports user id: ${userId}`);
  const panelConnectionStatus = await page.evaluate(
    () => document.getElementById("aiStatusText").textContent.trim(),
  );
  assert.strictEqual(
    panelConnectionStatus,
    "已连接 · 空闲",
    "Agent panel connection label matches the global connection pill",
  );
  console.log("ok - desktop connected to smoke service as", userId);

  // 6) create a project (template copy under workspaces/{user})
  await page.evaluate(() => window.AiPanel.openCreateProject());
  await page.fill('#createProjectForm input[name="name"]', "smoke");
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
  // Project creation kicks off loadConversations -> selectConversation; let it
  // settle before typing turn 1 or the fill is wiped mid-switch.
  await settleComposer(page, "after project creation");
  console.log("ok - project created", projectId);

  const send = async (text) => {
    await page.fill("#promptInput", text);
    await page.click("#btnSend");
  };

  // 7) turn 1: streaming markdown; deltas must merge into ONE bubble
  await send("用表格对比 LruCache 和 Room 缓存方案 ALPHA-UNIQUE-7");
  await waitUntil(
    () =>
      page.evaluate(() => {
        const state = window.AiPanel.getState();
        const visible = (id) =>
          getComputedStyle(document.getElementById(id)).display !== "none";
        return (
          state.running &&
          ["queued", "running"].includes(state.jobStatus) &&
          visible("btnPauseJob") &&
          visible("btnHeaderStop") &&
          visible("btnStop")
        );
      }),
    10000,
    "active task controls visible",
  );
  await page.waitForSelector(".tl-assistant", { timeout: 20000 });
  const len1 = await page.evaluate(
    () => document.querySelector(".tl-assistant").textContent.length,
  );
  await page.waitForTimeout(400);
  const len2 = await page.evaluate(
    () => document.querySelector(".tl-assistant").textContent.length,
  );
  assert.ok(len2 > len1, `streaming text grows (${len1} -> ${len2})`);
  const streamShape = await page.evaluate(() => {
    const statuses = [...document.querySelectorAll(".tl-status")].map((n) =>
      n.textContent.trim(),
    );
    return {
      assistants: document.querySelectorAll(".tl-assistant").length,
      statuses,
      // A status line that is a fragment of the streamed markdown means raw
      // text_delta events leaked into status rows instead of the bubble.
      deltaLeakInStatus: statuses.some((s) => s.includes("LruCache")),
    };
  });
  assert.strictEqual(streamShape.assistants, 1, "exactly one streaming bubble");
  assert.ok(!streamShape.deltaLeakInStatus, `no delta fragments in status: ${streamShape.statuses}`);
  console.log("ok - streaming merges deltas into one bubble");

  await waitUntil(
    () =>
      page.evaluate(() => {
        const heads = [...document.querySelectorAll(".tl-work-head")];
        const state = window.AiPanel.getState();
        return (
          heads.some((h) => h.textContent.includes("已完成")) &&
          !state.running &&
          state.jobStatus === "succeeded"
        );
      }),
    30000,
    "turn 1 finished",
  );
  // The "已完成" head can appear a frame before the final message re-renders
  // from its streaming partial into full markdown, so poll for content, not a
  // one-shot snapshot.
  await waitUntil(
    () =>
      page.evaluate(() => {
        const a = document.querySelector(".tl-assistant");
        if (!a) return false;
        return Boolean(
          a.querySelector("table") &&
            a.querySelector("strong") &&
            a.querySelector("pre code, pre") &&
            a.querySelector("code:not(pre code)"),
        );
      }),
    15000,
    "markdown table/bold/code rendered",
  );
  console.log("ok - markdown rendered (table/bold/code)");
  await page.screenshot({ path: path.join(__dirname, "smoke-1-streaming.png") });

  // 8) turn 2: real file modification via write_file tool
  await send("把 strings.xml 的 smoke_label 修改为 hello-smoke-v2");
  await waitUntil(
    () =>
      page.evaluate(() => {
        const state = window.AiPanel.getState();
        const btns = [...document.querySelectorAll(".tl-changes button.primary-btn")];
        return !state.running && state.jobStatus === "succeeded"
          && btns.some((b) => b.textContent.includes("审查改动") && !b.disabled);
      }),
    60000,
    "review button ready",
  );
  const wsFile = path.join(
    SMOKE_DATA,
    "workspaces",
    userId,
    projectId,
    "app/src/main/res/values/strings.xml",
  );
  const written = fs.readFileSync(wsFile, "utf8");
  assert.ok(written.includes("hello-smoke-v2"), "real file modified by agent");
  console.log("ok - agent wrote real file in isolated workspace");

  // 9) review changes: real click -> Monaco diff from checkpoint blobs
  const templateBefore = fs.readFileSync(
    path.join(repoRoot, "template/app/src/main/res/values/strings.xml"),
    "utf8",
  );
  await page.click(".tl-changes button:has-text('审查改动')");
  await waitUntil(
    () => page.evaluate(() => !document.getElementById("monacoDiffHost").hidden),
    20000,
    "diff host visible",
  ).catch(async error => {
    const diagnostics = await page.evaluate(() => ({
      changes: [...document.querySelectorAll(".tl-changes")].map(node => node.outerHTML),
      toasts: [...document.querySelectorAll(".toast")].map(node => node.textContent),
      state: { project: AiPanel.getState().selectedProjectId, conversation: AiPanel.getState().conversationId, job: AiPanel.getState().currentJobId },
    }));
    throw new Error(`${error.message}: ${JSON.stringify(diagnostics)}`);
  });
  await page.waitForTimeout(800);
  const review = await page.evaluate(() => {
    const models = window.monaco.editor.getModels().map((m) => m.getValue());
    return {
      title: document.getElementById("diffTitle").textContent,
      models,
      acceptHidden: getComputedStyle(document.getElementById("btnAcceptDiff")).display === "none",
    };
  });
  assert.ok(review.title.includes("审查改动"), "diff title");
  assert.ok(
    review.models.some((v) => v.includes("hello-smoke-v2")),
    "modified content from after checkpoint",
  );
  assert.ok(
    review.models.some((v) => v.trim() === templateBefore.trim()),
    "before content equals checkpoint blob (not reverse-engineered)",
  );
  assert.ok(review.acceptHidden, "review mode hides accept/reject");
  console.log("ok - Monaco diff shows exact before/after checkpoint blobs");
  await page.screenshot({ path: path.join(__dirname, "smoke-2-review.png") });
  await page.click("#btnCloseDiff");
  await waitUntil(
    () => page.evaluate(() => document.getElementById("monacoDiffHost").hidden),
    10000,
    "diff closed",
  );
  const modelsAfterClose = await page.evaluate(() =>
    window.monaco.editor.getModels().map((m) => `${m.uri} ${m.getLanguageId()}`),
  );
  assert.deepStrictEqual(
    modelsAfterClose,
    [],
    `diff models disposed on close; leaked: ${JSON.stringify(modelsAfterClose)}`,
  );
  console.log("ok - diff models disposed");

  // 10) turn 3 -> three turns; older collapsed, last expanded
  await send("第三轮：总结一下冒烟测试 BRAVO-THIRD");
  await waitUntil(
    () => page.evaluate(() => document.querySelectorAll(".tl-turn").length >= 3),
    30000,
    "three turns",
  );
  await waitUntil(
    () =>
      page.evaluate(() => {
        const turns = [...document.querySelectorAll(".tl-turn")];
        const bodies = turns.map((t) => t.querySelector(".tl-work-body"));
        const disp = (el) => (el ? getComputedStyle(el).display : "missing");
        return (
          turns.length === 3 &&
          disp(bodies[0]) === "none" &&
          disp(bodies[1]) === "none" &&
          disp(bodies[2]) !== "none"
        );
      }),
    20000,
    "history folding defaults",
  );
  console.log("ok - three turns, older collapsed, current expanded");
  await page.screenshot({ path: path.join(__dirname, "smoke-3-turns.png") });

  // 11) restart Electron: history, folding and credentials persist
  closing = true;
  await app.close();
  closing = false;
  app = await launchApp();
  track(app.process());
  page = await app.firstWindow();
  diagnosticPage = page;
  wireErrors(page);
  await page.waitForSelector("#promptInput", { timeout: 20000 });
  await page.waitForFunction(() => window.AiPanel?.getState, null, { timeout: 20000 });
  await waitUntil(
    () => page.evaluate(() => document.getElementById("connPill").dataset.state === "ok"),
    20000,
    "auto reconnect after restart",
  );
  await waitUntil(
    () =>
      page.evaluate(() => {
        const sel = document.getElementById("conversationSelect");
        return sel && sel.options.length > 0 && sel.value;
      }),
    20000,
    "conversation restored after restart",
  );
  await waitUntil(
    () => page.evaluate(() => document.querySelectorAll(".tl-turn").length === 3),
    20000,
    "three turns after restart",
  );
  const afterRestart = await page.evaluate(() => {
    const turns = [...document.querySelectorAll(".tl-turn")];
    const bodies = turns.map((t) => t.querySelector(".tl-work-body"));
    const disp = (el) => (el ? getComputedStyle(el).display : "missing");
    return {
      collapsed: disp(bodies[0]) === "none" && disp(bodies[1]) === "none",
      lastExpanded: disp(bodies[2]) !== "none",
      finals: turns.every(
        (t) => t.querySelector(".tl-turn-final") && getComputedStyle(t.querySelector(".tl-turn-final")).display !== "none",
      ),
      text: document.getElementById("aiMessages").textContent,
    };
  });
  assert.ok(afterRestart.collapsed, "older turns collapsed after restart");
  assert.ok(afterRestart.lastExpanded, "last turn expanded after restart");
  assert.ok(afterRestart.finals, "final answers always visible");
  assert.ok(afterRestart.text.includes("ALPHA-UNIQUE-7"), "history contains turn 1 prompt");
  console.log("ok - restart restores history/folding/credentials");
  await page.screenshot({ path: path.join(__dirname, "smoke-4-restart.png") });

  // 12) fast conversation switching must not cross wires
  await page.click("#btnNewChat");
  // createNewConversation -> selectConversation clears the input mid-flight;
  // wait for the new conversation to settle before typing.
  await settleComposer(page, "after new chat");
  await send("新会话 CHARLIE-UNIQUE-99：简单回复即可");
  await page.waitForSelector(".tl-assistant", { timeout: 20000 });
  const convB = await page.evaluate(() => window.AiPanel.getState().conversationId);
  // switch back to A quickly, then to B, then A again
  const convA = await page.evaluate((b) => {
    const sel = document.getElementById("conversationSelect");
    return [...sel.options].map((o) => o.value).find((v) => v && v !== b);
  }, convB);
  for (const target of [convA, convB, convA]) {
    await page.selectOption("#conversationSelect", target);
    await page.waitForTimeout(350);
  }
  const domA = await page.evaluate(() => document.getElementById("aiMessages").textContent);
  assert.ok(domA.includes("ALPHA-UNIQUE-7"), "conversation A shows its own events");
  assert.ok(!domA.includes("CHARLIE-UNIQUE-99"), "no B events leaked into A");
  await page.selectOption("#conversationSelect", convB);
  await waitUntil(
    () =>
      page.evaluate(() =>
        document.getElementById("aiMessages").textContent.includes("CHARLIE-UNIQUE-99"),
      ),
    10000,
    "conversation B shows its own events",
  );
  const domB = await page.evaluate(() => document.getElementById("aiMessages").textContent);
  assert.ok(!domB.includes("ALPHA-UNIQUE-7"), "no A events leaked into B");
  console.log("ok - fast switching keeps conversations isolated");
  await page.screenshot({ path: path.join(__dirname, "smoke-5-switch.png") });

  // 12b) command approval: card shows human-readable command, approve -> runs
  const approvePending = async (titleText, bodyText, shotName) => {
    const card = page.locator(".tl-approval.is-pending").last();
    await card.waitFor({ state: "visible", timeout: 30000 });
    const info = await card.evaluate((n) => ({
      title: n.querySelector(".tl-approval-name")?.textContent || "",
      body: n.textContent || "",
    }));
    assert.ok(info.title.includes(titleText), `approval title ${titleText}: ${info.title}`);
    assert.ok(info.body.includes(bodyText), `approval body includes ${bodyText}`);
    const approvalId = await card.getAttribute("data-approval-id");
    const jobId = await page.evaluate(() => window.AiPanel.getState().currentJobId);
    assert.ok(approvalId && jobId, "approval is bound to a real job and request");
    await page.screenshot({ path: path.join(__dirname, shotName) });
    await card.locator("button", { hasText: "允许一次" }).click();
    // Completion may collapse the work section between browser samples. Verify
    // the stable server decision, then reveal this exact approval's work section.
    await waitUntil(() => page.evaluate(async ({ jobId, approvalId }) => {
      const { job } = await window.AiPanel.client.job(jobId);
      return job.events.some(event => event.type === "approval_resolved" && event.approval_id === approvalId && event.decision === "approved");
    }, { jobId, approvalId }), 30000, "approval resolved on the server");
    await page.evaluate(id => window.AiPanel.debug.getView().focusApproval(id), approvalId);
    const resolved = page.locator(`[data-approval-id="${approvalId}"].is-approved`);
    await resolved.waitFor({ state: "visible", timeout: 10000 });
    const stamp = await resolved.locator(".tl-approval-stamp").textContent();
    assert.ok(stamp.includes("已允许"), `approved stamp: ${stamp}`);
  };

  await send("运行命令 RUN-CMD-SMOKE-8 打印一行信息");
  await approvePending("运行命令", "python3", "smoke-6-approval-command.png");
  await waitUntil(
    () =>
      page.evaluate(() => {
        const nodes = [...document.querySelectorAll(".tl-assistant")];
        const last = nodes[nodes.length - 1];
        const state = window.AiPanel.getState();
        return Boolean(
          last &&
            last.textContent.includes("smoke-command-ok-9") &&
            !state.running &&
            state.jobStatus === "succeeded"
        );
      }),
    30000,
    "command turn final answer",
  );
  const commandResult = await latestToolResult(page, "run_command");
  assert.strictEqual(await page.locator("#aiMessages .tl-turn").count(), 2, "live status events remain in their two real conversation turns");
  assert.strictEqual(commandResult?.ok, true, `command actually succeeded: ${JSON.stringify(commandResult)}`);
  assert.ok(JSON.stringify(commandResult).includes("smoke-command-ok-9"), "actual tool output contains expected marker");
  console.log("ok - command approval card -> approve -> tool executed");

  // 12c) network approval: web_search asks, approve -> tool runs
  await send("访问网络 NET-SMOKE-5 搜索测试");
  await approvePending("访问网络", "android agent smoke test", "smoke-7-approval-network.png");
  await waitUntil(
    () =>
      page.evaluate(() => {
        const nodes = [...document.querySelectorAll(".tl-assistant")];
        const last = nodes[nodes.length - 1];
        return Boolean(last && last.textContent.includes("审批链路"));
      }),
    30000,
    "network turn final answer",
  );
  const searchResult = await latestToolResult(page, "web_search");
  assert.strictEqual(searchResult?.ok, false, "search remains offline without test credentials");
  assert.ok(JSON.stringify(searchResult).includes("未配置 Tavily API Key"), "approved search reaches the tool credential check");
  console.log("ok - network approval card -> approve -> isolated credential check");

  // 13) no uncaught exceptions
  closing = true;
  await app.close();
  const realErrors = pageErrors.filter(
    (e) => !/net::|Failed to load resource|WebSocket is closed|ECONNREFUSED/i.test(e),
  );
  assert.deepStrictEqual(realErrors, [], `uncaught errors: ${realErrors.join(" | ")}`);
  console.log("ok - no uncaught exceptions");

  svc.kill("SIGKILL");
  console.log("electron-smoke: OK");
  cleanup();
  process.exit(0);
}

main().catch(async (err) => {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
  fs.writeFileSync(path.join(ARTIFACT_DIR, "service.log"), svcLog);
  fs.writeFileSync(path.join(ARTIFACT_DIR, "failure.txt"), String(err?.stack || err));
  fs.writeFileSync(path.join(ARTIFACT_DIR, "isolated-paths.json"), JSON.stringify({ data: SMOKE_DATA, profile: SMOKE_PROFILE }, null, 2));
  if (diagnosticPage && !diagnosticPage.isClosed()) {
    try {
      const snapshot = await diagnosticPage.evaluate(() => {
        const state = window.AiPanel?.getState();
        return {
          state: state && Object.fromEntries(["connected", "selectedProjectId", "conversationId", "currentJobId", "jobStatus", "running", "loadToken", "controlBusy"].map(key => [key, state[key]])),
          timeline: document.getElementById("aiMessages")?.innerText,
          approvals: [...document.querySelectorAll(".tl-approval")].map(card => ({ className: card.className, text: card.textContent })),
          items: window.AiPanel?.debug.timeline.items(),
        };
      });
      fs.writeFileSync(path.join(ARTIFACT_DIR, "ui-state.json"), JSON.stringify(snapshot, null, 2));
      await diagnosticPage.screenshot({ path: path.join(ARTIFACT_DIR, "failure.png") });
    } catch (captureError) { console.error("failure capture:", captureError.message); }
  }
  if (svcLog) console.error("service log tail:\n" + svcLog.slice(-3000));
  // Keep the actual exception last: release runners may retain only stderr's tail.
  console.error("electron-smoke FAILED:", err?.stack || err);
  console.error("failure evidence:", ARTIFACT_DIR);
  // Children keep the event loop alive, so the "exit" handler would never
  // fire. Kill them here and exit explicitly or this process leaks forever.
  cleanup();
  process.exit(1);
});
