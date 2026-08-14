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

const desktopDir = path.join(__dirname, "..");
const repoRoot = path.join(desktopDir, "..");
const AGENT_PORT = 8123;
const STUB_PORT = 9477;
const REG_TOKEN = "smoke-reg-token-123";
const SERVER_URL = `http://127.0.0.1:${AGENT_PORT}`;

const SMOKE_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "agent-smoke-data-"));
const SMOKE_PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "agent-smoke-prof-"));

let svcLog = "";

const children = [];
function track(child) {
  children.push(child);
  return child;
}
function cleanup() {
  for (const child of children) {
    try {
      if (!child.killed) child.kill("SIGTERM");
    } catch (_) {}
  }
}
process.on("exit", cleanup);

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

async function main() {
  // 1) stub model server
  track(
    spawn("python3", [path.join(__dirname, "smoke", "stub_model_server.py")], {
      stdio: "ignore",
    }),
  );
  await waitForTcp(STUB_PORT, 15000);

  // 2) real agent service, isolated data dir, stub provider
  const svc = track(
    spawn("python3", ["-m", "agent", "serve", "--host", "127.0.0.1", "--port", String(AGENT_PORT)], {
      cwd: repoRoot,
      env: {
        ...process.env,
        AGENT_DATA_DIR: SMOKE_DATA,
        AGENT_BASE_URL: `http://127.0.0.1:${STUB_PORT}`,
        AGENT_API_KEY: "sk-smoke-stub",
        AGENT_REGISTRATION_TOKEN: REG_TOKEN,
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    }),
  );
  svc.stdout.on("data", (d) => (svcLog += d.toString()));
  svc.stderr.on("data", (d) => (svcLog += d.toString()));
  await waitForTcp(AGENT_PORT, 30000);

  // 3) register a smoke user
  const reg = await httpJson("POST", "/api/register", {
    headers: { "X-Registration-Token": REG_TOKEN },
  });
  assert.strictEqual(reg.status, 201, `register failed: ${JSON.stringify(reg.json)}`);
  const userToken = reg.json.token;
  const userId = reg.json.user_id;
  console.log("ok - registered smoke user", userId);

  // 4) launch real Electron with isolated profile
  const launchApp = () =>
    _electron.launch({
      args: ["."],
      cwd: desktopDir,
      env: { ...process.env, AGENT_DESKTOP_USER_DATA: SMOKE_PROFILE },
    });

  let app = await launchApp();
  let page = await app.firstWindow();
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

  // 5) pair against the smoke service (real register/connect flow)
  await page.evaluate(() => window.AiPanel.openSettings());
  await page.fill("#serverUrl", SERVER_URL);
  await page.fill("#registrationToken", REG_TOKEN);
  await page.click("#btnPair");
  await waitUntil(
    () => page.evaluate(() => document.getElementById("connPill").dataset.state === "ok"),
    20000,
    "connection ok",
  );
  console.log("ok - desktop connected to smoke service");

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
  console.log("ok - project created", projectId);

  const send = async (text) => {
    await page.fill("#promptInput", text);
    await page.click("#btnSend");
  };

  // 7) turn 1: streaming markdown; deltas must merge into ONE bubble
  await send("用表格对比 LruCache 和 Room 缓存方案 ALPHA-UNIQUE-7");
  await page.waitForSelector(".tl-assistant", { timeout: 20000 });
  const len1 = await page.evaluate(
    () => document.querySelector(".tl-assistant").textContent.length,
  );
  await page.waitForTimeout(400);
  const len2 = await page.evaluate(
    () => document.querySelector(".tl-assistant").textContent.length,
  );
  assert.ok(len2 > len1, `streaming text grows (${len1} -> ${len2})`);
  const streamShape = await page.evaluate(() => ({
    assistants: document.querySelectorAll(".tl-assistant").length,
    statuses: document.querySelectorAll(".tl-status").length,
    hasTableFragmentAsStatus: [...document.querySelectorAll(".tl-status")].some((n) =>
      n.textContent.includes("LruCache"),
    ),
  }));
  assert.strictEqual(streamShape.assistants, 1, "exactly one streaming bubble");
  assert.ok(streamShape.statuses <= 2, "deltas did not become status lines");
  assert.ok(!streamShape.hasTableFragmentAsStatus, "no table fragment in status");
  console.log("ok - streaming merges deltas into one bubble");

  await waitUntil(
    () =>
      page.evaluate(() => {
        const heads = [...document.querySelectorAll(".tl-work-head")];
        return heads.some((h) => h.textContent.includes("已完成"));
      }),
    30000,
    "turn 1 finished",
  );
  const md = await page.evaluate(() => {
    const a = document.querySelector(".tl-assistant");
    return {
      table: Boolean(a.querySelector("table")),
      bold: Boolean(a.querySelector("strong")),
      code: Boolean(a.querySelector("pre code, pre")),
      inline: Boolean(a.querySelector("code:not(pre code)")),
    };
  });
  assert.ok(md.table && md.bold && md.code && md.inline, "markdown table/bold/code rendered");
  console.log("ok - markdown rendered (table/bold/code)");
  await page.screenshot({ path: path.join(__dirname, "smoke-1-streaming.png") });

  // 8) turn 2: real file modification via write_file tool
  await send("把 strings.xml 的 smoke_label 修改为 hello-smoke-v2");
  await waitUntil(
    () =>
      page.evaluate(() => {
        const btns = [...document.querySelectorAll(".tl-changes button")];
        return btns.some((b) => b.textContent.includes("审查改动") && !b.disabled);
      }),
    60000,
    "review button ready",
  );
  const wsFile = path.join(
    repoRoot,
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
  );
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
  const modelsAfterClose = await page.evaluate(
    () => window.monaco.editor.getModels().length,
  );
  assert.strictEqual(modelsAfterClose, 0, "diff models disposed on close");
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
  page = await app.firstWindow();
  wireErrors(page);
  await page.waitForSelector("#promptInput", { timeout: 20000 });
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

  // 13) no uncaught exceptions
  closing = true;
  await app.close();
  const realErrors = pageErrors.filter(
    (e) => !/net::|Failed to load resource|WebSocket is closed|ECONNREFUSED/i.test(e),
  );
  assert.deepStrictEqual(realErrors, [], `uncaught errors: ${realErrors.join(" | ")}`);
  console.log("ok - no uncaught exceptions");

  svc.kill("SIGTERM");
  console.log("electron-smoke: OK");
}

main().catch((err) => {
  console.error("electron-smoke FAILED:", err && err.message ? err.message : err);
  if (svcLog) console.error("service log tail:\n" + svcLog.slice(-3000));
  process.exitCode = 1;
});
