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
const AGENT_PORT = Number(process.env.AGENT_SMOKE_PORT || 8123);
const STUB_PORT = Number(process.env.AGENT_SMOKE_STUB_PORT || 9477);
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

async function main() {
  // 0) refuse to run against leftover services from a previous failed run
  await assertPortFree(STUB_PORT);
  await assertPortFree(AGENT_PORT);

  // 1) stub model server
  track(
    spawn("python3", [path.join(__dirname, "smoke", "stub_model_server.py")], {
      env: { ...process.env, AGENT_SMOKE_STUB_PORT: String(STUB_PORT) },
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
        AGENT_REGISTRATION_ENABLED: "1",
        AGENT_REGISTRATION_TOKEN: REG_TOKEN,
        // macOS can expose a system proxy through urllib/httpx even when no
        // proxy variables are printed in the shell. The model stub is local.
        NO_PROXY: "*",
        no_proxy: "*",
        HTTP_PROXY: "",
        HTTPS_PROXY: "",
        ALL_PROXY: "",
        http_proxy: "",
        https_proxy: "",
        all_proxy: "",
        // This host blocks sandbox_apply, which would fail every run_command
        // with exit 71 before the stub's expected output is produced.
        AGENT_CMD_SANDBOX: "0",
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
  console.log("ok - registered smoke user", reg.json.user_id);

  // 4) launch real Electron with isolated profile
  const launchApp = () =>
    _electron.launch({
      // Restricted CI sandboxes cannot start Chromium's own sandbox/GPU
      // subprocesses; without these flags the app FATALs on startup.
      args: [".", "--no-sandbox", "--disable-gpu"],
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
  await page.waitForFunction(() => window.AiPanel?.openSettings, null, { timeout: 20000 });

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
  // The desktop pairs via /api/pair, which registers its OWN user account —
  // distinct from the user created by the /api/register probe above. All
  // workspace assertions must use the account the desktop actually uses.
  const userId = await page.evaluate(() => window.AiPanel.getState().userId);
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
  page = await app.firstWindow();
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
  const lastApproval = () =>
    page.evaluate(() => {
      const cards = [...document.querySelectorAll(".tl-approval")];
      const last = cards[cards.length - 1];
      if (!last) return null;
      return {
        pending: last.classList.contains("is-pending"),
        approved: last.classList.contains("is-approved"),
        title: last.querySelector(".tl-approval-name")?.textContent || "",
        body: last.textContent || "",
      };
    });
  const approvePending = async (titleText, bodyText, shotName) => {
    const card = page.locator(".tl-approval.is-pending").last();
    await card.waitFor({ state: "visible", timeout: 30000 });
    const info = await card.evaluate((n) => ({
      title: n.querySelector(".tl-approval-name")?.textContent || "",
      body: n.textContent || "",
    }));
    assert.ok(info.title.includes(titleText), `approval title ${titleText}: ${info.title}`);
    assert.ok(info.body.includes(bodyText), `approval body includes ${bodyText}`);
    await page.screenshot({ path: path.join(__dirname, shotName) });
    await card.locator("button", { hasText: "允许一次" }).click();
    await waitUntil(async () => (await lastApproval())?.approved, 30000, "approval resolved");
    const stamp = await page.evaluate(() => {
      const cards = [...document.querySelectorAll(".tl-approval")];
      const last = cards[cards.length - 1];
      return last?.querySelector(".tl-approval-stamp")?.textContent || "";
    });
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
  console.log("ok - network approval card -> approve -> tool executed");

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

main().catch((err) => {
  console.error("electron-smoke FAILED:", err && err.message ? err.message : err);
  if (svcLog) console.error("service log tail:\n" + svcLog.slice(-3000));
  // Children keep the event loop alive, so the "exit" handler would never
  // fire. Kill them here and exit explicitly or this process leaks forever.
  cleanup();
  process.exit(1);
});
