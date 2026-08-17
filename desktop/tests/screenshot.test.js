const assert = require("assert");
const { chromium } = require("playwright");
const http = require("http");
const fs = require("fs");
const path = require("path");
const { promisify } = require("util");

const readFile = promisify(fs.readFile);
const desktopDir = path.join(__dirname, "..");

const mime = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
};

const server = http.createServer(async (req, res) => {
  let urlPath = decodeURIComponent(req.url || "/").split("?")[0];
  if (urlPath === "/") urlPath = "/src/index.html";
  const filePath = path.join(desktopDir, urlPath);
  if (!filePath.startsWith(desktopDir)) {
    res.writeHead(403);
    res.end();
    return;
  }
  try {
    const data = await readFile(filePath);
    const ext = path.extname(filePath);
    res.writeHead(200, { "Content-Type": mime[ext] || "application/octet-stream" });
    res.end(data);
  } catch (err) {
    res.writeHead(404);
    res.end(String(err.message));
  }
});

// —— Deterministic fixtures (fixed ids / timestamps / text, no network) ——

function sceneEvents(name) {
  // Self-contained: this function is serialized and eval'd inside the page.
  const T0 = 1767225600; // fixed epoch for stable timestamps
  const ev = (id, type, fields = {}) => ({ id, type, ts: T0 + id, ...fields });
  switch (name) {
    case "empty":
      return [];
    case "markdown":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "把登录页改成 Material 3 风格，并说明改动点" }],
        }),
        ev(2, "assistant_message", {
          message_id: "m1",
          is_final: true,
          text_blocks: [
            {
              type: "text",
              text:
                "已完成登录页改造，要点如下：\n\n" +
                "## 改动概览\n\n" +
                "- 使用 `MaterialButton` 替换原生按钮\n" +
                "- 颜色令牌迁移到 `res/values/colors.xml`\n" +
                "- 布局文件 `res/layout/activity_login.xml:12` 已重写\n\n" +
                "```kotlin\nclass LoginActivity : AppCompatActivity() {\n  override fun onCreate(b: Bundle?) {\n    super.onCreate(b)\n  }\n}\n```\n\n" +
                "> 注意：不要直接提交未构建的分支。\n\n" +
                "1. 先运行 `./gradlew assembleDebug`\n2. 再人工走查登录流程",
            },
          ],
        }),
      ];
    case "markdown-table":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "对比三种缓存方案的优劣" }],
        }),
        ev(2, "assistant_message", {
          message_id: "m1",
          is_final: true,
          text_blocks: [
            {
              type: "text",
              text:
                "## 缓存方案对比\n\n" +
                "| 方案 | 命中延迟 | 内存占用 | 适用场景 |\n" +
                "| --- | ---: | ---: | --- |\n" +
                "| `LruCache` | 低 | 中 | 图片/位图缓存 |\n" +
                "| `Room` | 中 | 高 | 结构化数据持久化 |\n" +
                "| 文件缓存 | 高 | 低 | 大文件、离线资源 |\n\n" +
                "**结论**：优先使用 `LruCache` + `Room` 组合。\n\n" +
                "- 优点\n  - 实现简单\n  - 命中率高\n- 缺点\n  - 需要手动失效策略",
            },
          ],
        }),
      ];
    case "streaming-delta":
      // Simulates the real protocol: many text_delta fragments of ONE output.
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "解释一下这段构建为什么失败" }],
        }),
        ...[
          "构建失败的原因是 ",
          "**依赖冲突**。",
          "\n\n`app/build.gradle` 中 ",
          "implementation 行",
          "引入了两个版本。",
          "\n\n```groovy\nimplementation 'x:y:1.0'\nimplementation 'x:y:2.0'\n```",
          "\n\n删除旧版本即可。",
        ].map((d, i) => ev(2 + i, "text_delta", { delta: d, message_id: "m1", stream_id: "s1" })),
      ];
    case "running-turn":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "给设置页加上深色模式开关" }],
        }),
        ev(2, "status", { message: "正在检查项目结构" }),
        ev(3, "status", { message: "正在搜索文件" }),
        ev(4, "text_delta", { delta: "我先查看设置页的当前实现，", message_id: "m1" }),
        ev(5, "text_delta", { delta: "再决定开关放在哪里。", message_id: "m1" }),
        ev(6, "tool_call", {
          tool_call_id: "c1",
          name: "read_file",
          input: { path: "app/src/main/res/layout/activity_settings.xml" },
        }),
      ];
    case "plan-tools":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "新增一个关于页面" }],
        }),
        ev(2, "plan", { message: "理解需求 -> 定位/修改代码 -> 需要时再 assembleDebug" }),
        ev(3, "tool_call", {
          tool_call_id: "c1",
          name: "list_files",
          input: { path: "app/src/main/java" },
        }),
        ev(4, "tool_result", {
          tool_call_id: "c1",
          name: "list_files",
          ok: true,
          duration_ms: 38,
          model_output: "MainActivity.kt\nSettingsActivity.kt",
        }),
        ev(5, "tool_call", {
          tool_call_id: "c2",
          name: "write_file",
          input: { path: "app/src/main/java/com/example/AboutActivity.kt", content: "package com.example\n// ..." },
        }),
        ev(6, "tool_result", {
          tool_call_id: "c2",
          name: "write_file",
          ok: true,
          duration_ms: 12,
          model_output: "已写入 AboutActivity.kt",
        }),
      ];
    case "approval-command":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "清理构建产物并重新构建" }],
        }),
        ev(2, "tool_call", {
          tool_call_id: "c1",
          name: "run_command",
          input: { argv: ["./gradlew", "clean", "assembleDebug"], cwd: "." },
        }),
        ev(3, "approval_required", {
          approval_id: "ap1",
          tool_call_id: "c1",
          job_id: "j1",
          kind: "process",
          risk: "destructive",
          reason: "workspace 模式下网络、进程或破坏性操作需要审批",
          requested_capability: "process",
          tool_name: "run_command",
          message: "请求执行 run_command（请在对话确认卡片中选择允许或拒绝）",
          command: "./gradlew clean assembleDebug",
          argv: ["./gradlew", "clean", "assembleDebug"],
          cwd: ".",
        }),
      ];
    case "approval-network":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "下载新的应用图标" }],
        }),
        ev(2, "tool_call", {
          tool_call_id: "c1",
          name: "download_file",
          input: { url: "https://cdn.example.com/icon.png", path: "downloads/icon.png" },
        }),
        ev(3, "approval_required", {
          approval_id: "ap2",
          tool_call_id: "c1",
          job_id: "j1",
          kind: "download_file",
          risk: "destructive",
          reason: "下载文件会写入工作区并访问外部网络",
          requested_capability: "download_file",
          tool_name: "download_file",
          message: "请求下载文件到 downloads/icon.png",
          url: "https://cdn.example.com/icon.png",
          path: "downloads/icon.png",
          max_bytes: 52428800,
          domains: ["cdn.example.com"],
          target_paths: ["downloads/icon.png"],
        }),
      ];
    case "tool-failed":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "运行单元测试" }],
        }),
        ev(2, "tool_call", {
          tool_call_id: "c1",
          name: "run_gradle",
          input: { task: "testDebugUnitTest" },
        }),
        ev(3, "tool_result", {
          tool_call_id: "c1",
          name: "run_gradle",
          ok: false,
          duration_ms: 8421,
          error_type: "NonZeroExitCode",
          model_output: "FAILED: SettingsViewModelTest.toggle saves state\nExpected: true\nActual: false",
        }),
        ev(4, "assistant_message", {
          message_id: "m1",
          text_blocks: [{ type: "text", text: "测试 `SettingsViewModelTest` 失败，原因是状态未持久化。我将修复后重跑。" }],
        }),
      ];
    case "failed-turn":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "升级 AGP 到 8.5" }],
        }),
        ev(2, "tool_call", {
          tool_call_id: "c1",
          name: "run_gradle",
          input: { task: "assembleDebug" },
        }),
        ev(3, "tool_result", {
          tool_call_id: "c1",
          name: "run_gradle",
          ok: false,
          duration_ms: 15200,
          error_type: "NonZeroExitCode",
          model_output: "FAILURE: Build failed with an exception.\n* What went wrong:\nCould not determine the dependencies of task ':app:assembleDebug'.",
        }),
        ev(4, "failed", { error: "构建失败：依赖解析错误，任务终止" }),
      ];
    case "changes":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "把首页标题改成“欢迎使用”" }],
        }),
        ev(2, "tool_call", {
          tool_call_id: "c1",
          name: "str_replace",
          input: { path: "app/src/main/res/values/strings.xml" },
        }),
        ev(3, "tool_result", { tool_call_id: "c1", name: "str_replace", ok: true, duration_ms: 9 }),
        ev(4, "changes", {
          turn_id: "t1",
          message: "改动 2 个文件",
          diff_status: "ready",
          files: [
            { path: "app/src/main/res/values/strings.xml", change: "modified" },
            { path: "app/src/main/AndroidManifest.xml", change: "added" },
          ],
        }),
        ev(5, "checkpoint", { checkpoint_id: "cp9", kind: "after_turn", file_count: 42 }),
        ev(6, "completed", { message: "任务完成", diff_status: "ready" }),
      ];
    case "review-ready":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "修复启动页的空指针崩溃" }],
        }),
        ev(2, "tool_call", {
          tool_call_id: "c1",
          name: "str_replace",
          input: { path: "app/src/main/java/com/example/SplashActivity.kt" },
        }),
        ev(3, "tool_result", { tool_call_id: "c1", name: "str_replace", ok: true, duration_ms: 11 }),
        ev(4, "changes", {
          turn_id: "t1",
          diff_status: "ready",
          files: [
            { path: "app/src/main/java/com/example/SplashActivity.kt", change: "modified" },
            { path: "app/src/main/java/com/example/util/Nulls.kt", change: "added" },
            { path: "app/legacy/OldSplash.kt", change: "deleted" },
          ],
        }),
        ev(5, "assistant_message", {
          message_id: "m1",
          is_final: true,
          text_blocks: [{ type: "text", text: "已修复：对 `intent.extras` 做了空判断，并抽出 `Nulls.kt` 工具类。" }],
        }),
        ev(6, "completed", { message: "任务完成", diff_status: "ready" }),
      ];
    case "review-preparing":
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "重命名应用图标资源" }],
        }),
        ev(2, "changes", {
          turn_id: "t1",
          diff_status: "preparing",
          files: [{ path: "app/src/main/res/drawable/ic_launcher.xml", change: "modified" }],
        }),
      ];
    case "long-output": {
      const lines = [];
      for (let i = 1; i <= 120; i += 1) {
        lines.push(`[gradle] task :app:compileDebugKotlin — progress ${i}/120, processing source set ${i}`);
      }
      return [
        ev(1, "user_message", {
          message_id: "u1",
          content: [{ type: "text", text: "执行一次完整构建" }],
        }),
        ev(2, "tool_call", {
          tool_call_id: "c1",
          name: "run_command",
          input: { argv: ["./gradlew", "assembleDebug", "--console=plain"], cwd: "." },
        }),
        ev(3, "tool_result", {
          tool_call_id: "c1",
          name: "run_command",
          ok: true,
          duration_ms: 95432,
          model_output: lines.join("\n"),
        }),
      ];
    }
    case "three-turn-history": {
      // Three finished turns; each has a user prompt, commentary, tools,
      // changes and a final answer. Fixed ids/timestamps for stability.
      const out = [];
      let id = 1;
      for (let turn = 1; turn <= 3; turn += 1) {
        const tid = `t${turn}`;
        const e = (type, fields = {}) => ev(id++, type, { turn_id: tid, ...fields });
        out.push(
          e("user_message", {
            message_id: `u${turn}`,
            content: [{ type: "text", text: `第 ${turn} 轮：调整页面 ${turn} 的样式与文案` }],
          }),
          e("assistant_message", {
            message_id: `c${turn}`,
            text_blocks: [{ type: "text", text: `先查看页面 ${turn} 的现有布局。` }],
          }),
          e("tool_call", { tool_call_id: `c${turn}a`, name: "read_file", input: { path: `app/src/main/res/layout/page_${turn}.xml` } }),
          e("tool_result", { tool_call_id: `c${turn}a`, name: "read_file", ok: true, duration_ms: 7 }),
          e("tool_call", { tool_call_id: `c${turn}b`, name: "str_replace", input: { path: `app/src/main/res/layout/page_${turn}.xml` } }),
          e("tool_result", { tool_call_id: `c${turn}b`, name: "str_replace", ok: true, duration_ms: 12 }),
          e("changes", {
            diff_status: "ready",
            files: [{ path: `app/src/main/res/layout/page_${turn}.xml`, change: "modified" }],
          }),
          e("assistant_message", {
            message_id: `f${turn}`,
            is_final: true,
            text_blocks: [{ type: "text", text: `第 ${turn} 轮已完成：修改了对应布局与字符串资源。` }],
          }),
          e("completed", { message: "任务完成", diff_status: "ready" }),
        );
      }
      return out;
    }
    case "history": {
      const out = [];
      let id = 1;
      for (let turn = 1; turn <= 3; turn += 1) {
        out.push(
          ev(id++, "user_message", {
            message_id: `u${turn}`,
            content: [{ type: "text", text: `第 ${turn} 轮需求：调整页面 ${turn} 的样式与文案` }],
          }),
          ev(id++, "assistant_message", {
            message_id: `m${turn}`,
            text_blocks: [{ type: "text", text: `第 ${turn} 轮已完成：修改了对应布局与字符串资源。` }],
          }),
          ev(id++, "changes", {
            files: [{ path: `app/src/main/res/layout/page_${turn}.xml`, change: "modified" }],
          }),
        );
      }
      return out;
    }
    default:
      return [];
  }
}

const SCENES = [
  "empty",
  "markdown",
  "markdown-table",
  "streaming-delta",
  "running-turn",
  "plan-tools",
  "approval-command",
  "approval-network",
  "tool-failed",
  "failed-turn",
  "changes",
  "review-ready",
  "review-preparing",
  "long-output",
  "three-turn-history",
];

async function run() {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const url = `http://127.0.0.1:${port}/src/index.html`;

  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("pageerror", (err) => consoleErrors.push(String(err)));

  await page.addInitScript(() => {
    window.agentDesktop = {
      getDefaultWorkspace: () => Promise.resolve(null),
      getRepoRoot: () => Promise.resolve("/"),
      readTree: () => Promise.resolve({ entries: [] }),
      listFiles: () => Promise.resolve([]),
      readFile: () => Promise.resolve({ content: "" }),
      writeFile: () => Promise.resolve(),
      exists: () => Promise.resolve(false),
      stat: () => Promise.resolve({}),
      basename: (p) => Promise.resolve(p?.split(/[/\\]/).pop() || p),
      dirname: (p) => Promise.resolve(p?.split(/[/\\]/).slice(0, -1).join("/") || "/"),
      joinPath: (...parts) => Promise.resolve(parts.join("/")),
      relative: (f, t) => Promise.resolve(t),
      normalize: (p) => Promise.resolve(p),
      agentStatus: () => Promise.resolve({ running: false, managed: false, port: 8000, phoneUrl: null }),
      agentStart: () => Promise.resolve({ ok: true, running: true, port: 8000 }),
      agentStop: () => Promise.resolve({ stopped: true }),
      onAgentServerExit: () => () => {},
      onMenu: () => () => {},
    };
  });

  await page.goto(url, { waitUntil: "networkidle" });

  // Wait for Monaco + AiPanel init.
  await page.waitForFunction(() => window.AiPanel && window.AiPanel.debug, null, { timeout: 15000 });
  await page.waitForFunction(() => window.EditorApp && window.EditorApp.openDiff, null, { timeout: 20000 });
  await page.waitForTimeout(800);

  // Header status and task controls must reflect the real state. This catches
  // the regression where the global connection pill said "已连接" while the
  // Agent panel kept its static "未连接" label, and verifies that active-job
  // controls are discoverable without opening the overflow menu.
  const initialHeader = await page.evaluate(() => ({
    status: document.getElementById("aiStatusText").textContent.trim(),
    controlsHidden: document.getElementById("aiTaskControls").hidden,
  }));
  assert.deepStrictEqual(initialHeader, { status: "未连接", controlsHidden: true });

  const runningHeader = await page.evaluate(() => {
    window.AiPanel.debug.setState({
      connected: true,
      selectedProjectId: "p-control",
      conversationId: "c-control",
      currentJobId: "j-control",
      jobStatus: "running",
      running: true,
    });
    const visible = (id) => getComputedStyle(document.getElementById(id)).display !== "none";
    return {
      status: document.getElementById("aiStatusText").textContent.trim(),
      pause: visible("btnPauseJob"),
      resume: visible("btnResumeJob"),
      stop: visible("btnHeaderStop"),
      composerStop: visible("btnStop"),
    };
  });
  assert.deepStrictEqual(runningHeader, {
    status: "正在运行",
    pause: true,
    resume: false,
    stop: true,
    composerStop: true,
  });

  const pausedHeader = await page.evaluate(() => {
    window.AiPanel.debug.setState({
      running: true,
      jobStatus: "paused",
      pauseRequested: false,
    });
    const visible = (id) => getComputedStyle(document.getElementById(id)).display !== "none";
    return {
      status: document.getElementById("aiStatusText").textContent.trim(),
      pause: visible("btnPauseJob"),
      resume: visible("btnResumeJob"),
      stop: visible("btnHeaderStop"),
    };
  });
  assert.deepStrictEqual(pausedHeader, {
    status: "已暂停",
    pause: false,
    resume: true,
    stop: true,
  });

  await page.evaluate(() => {
    window.AiPanel.debug.setState({
      connected: false,
      selectedProjectId: null,
      conversationId: null,
      currentJobId: null,
      jobStatus: null,
      running: false,
      pauseRequested: false,
      cancelRequested: false,
      controlBusy: null,
    });
  });

  async function setScene(name) {
    await page.evaluate((sceneName) => {
      const debug = window.AiPanel.debug;
      debug.timeline.reset();
      // Mirror the real app: switching conversations resets the view's
      // node caches as well as the normalizer store.
      const view = debug.getView && debug.getView();
      if (view && view.reset) view.reset();
      debug.renderTimeline();
      const events = window.__sceneEvents(sceneName);
      if (events.length) debug.ingestTaskEvents(events, { jobId: "j1" });
      document.getElementById("aiMessages").scrollTop = 0;
    }, name);
    await page.waitForTimeout(350);
  }

  await page.evaluate((scenesSrc) => {
    // eslint-disable-next-line no-eval
    window.__sceneEvents = eval(`(${scenesSrc})`);
  }, sceneEvents.toString());

  // —— 1440x900: every scene ——
  for (const scene of SCENES) {
    await setScene(scene);
    await page.screenshot({
      path: path.join(__dirname, `screenshot-${scene}-1440x900.png`),
      fullPage: false,
    });
  }

  // Streaming regression: the delta scene must render ONE assistant bubble
  // (never one line per chunk), then finalize without a second bubble.
  await setScene("streaming-delta");
  const streamingChecks = await page.evaluate(() => {
    const debug = window.AiPanel.debug;
    const assistants = document.querySelectorAll(".tl-turn-final .tl-assistant, .tl-work-body .tl-assistant");
    const statusNodes = [...document.querySelectorAll(".tl-status")].map((n) => n.textContent);
    const oneBubble = assistants.length === 1;
    const hasCaret = Boolean(document.querySelector(".tl-stream-caret"));
    const text = assistants[0] ? assistants[0].textContent : "";
    const noDeltaStatus = !statusNodes.some((t) => t.includes("依赖冲突") || t.includes("implementation"));
    // Finalize with the authoritative message.
    debug.ingestTaskEvents(
      [
        {
          id: 90,
          type: "assistant_message",
          message_id: "m1",
          is_final: true,
          text_blocks: [{ type: "text", text: "构建失败的原因是 **依赖冲突**。\n\n`app/build.gradle` 中 implementation 行引入了两个版本。\n\n```groovy\nimplementation 'x:y:1.0'\nimplementation 'x:y:2.0'\n```\n\n删除旧版本即可。" }],
        },
        { id: 91, type: "completed", message: "任务完成" },
      ],
      { jobId: "j1", turnId: null },
    );
    return { oneBubble, hasCaret, noDeltaStatus, text };
  });
  assert.ok(streamingChecks.oneBubble, "streaming deltas render a single assistant bubble");
  assert.ok(streamingChecks.hasCaret, "streaming caret visible while streaming");
  assert.ok(streamingChecks.noDeltaStatus, "no text_delta leaked into status lines");
  assert.ok(streamingChecks.text.includes("依赖冲突"), "streamed text complete");
  await page.waitForTimeout(250);
  const afterFinal = await page.evaluate(() => {
    const assistants = document.querySelectorAll(".tl-assistant");
    return {
      count: assistants.length,
      caret: Boolean(document.querySelector(".tl-stream-caret")),
      codeBlock: Boolean(document.querySelector(".tl-assistant .md-codeblock")),
      bold: Boolean(document.querySelector(".tl-assistant strong")),
    };
  });
  assert.strictEqual(afterFinal.count, 1, "assistant_message finalizes the same bubble (no duplicate)");
  assert.ok(!afterFinal.caret, "caret removed after finalization");
  assert.ok(afterFinal.codeBlock && afterFinal.bold, "final markdown renders (code block + bold)");
  await page.screenshot({ path: path.join(__dirname, "screenshot-streaming-final-1440x900.png"), fullPage: false });

  // —— Turn structure & history folding ——
  await setScene("three-turn-history");
  const turnChecks = await page.evaluate(() => {
    const turns = [...document.querySelectorAll(".tl-turn")];
    const disp = (el) => (el ? getComputedStyle(el).display : "missing");
    const bodies = turns.map((t) => t.querySelector(".tl-work-body"));
    const heads = turns.map((t) => t.querySelector(".tl-work-head"));
    const finals = turns.map((t) => t.querySelector(".tl-turn-final"));
    return {
      three: turns.length === 3,
      olderCollapsed: disp(bodies[0]) === "none" && disp(bodies[1]) === "none",
      lastExpanded: disp(bodies[2]) !== "none",
      finalsAlwaysVisible: finals.every((f) => f && disp(f) !== "none" && f.textContent.includes("已完成")),
      workedLabels: heads.every((h) => /工作了/.test(h.textContent)),
      aria: heads.every((h) => h.getAttribute("aria-expanded") != null && h.getAttribute("aria-controls")),
      summaries: turns.map((t) => t.querySelector(".tl-work-summary").textContent),
      noYou: !document.querySelector(".tl-turn-user")?.textContent.includes("You"),
    };
  });
  assert.ok(turnChecks.three, "three turns rendered (not flat events)");
  assert.ok(turnChecks.olderCollapsed, "older finished turns collapsed by default");
  assert.ok(turnChecks.lastExpanded, "most recent turn expanded by default");
  assert.ok(turnChecks.finalsAlwaysVisible, "final answers stay visible when work session collapsed");
  assert.ok(turnChecks.workedLabels, "worked-for labels present");
  assert.ok(turnChecks.aria, "aria-expanded/aria-controls set on work heads");
  assert.ok(
    turnChecks.summaries.every((s) => s.includes("查看 1 个文件") && s.includes("修改 1 个文件")),
    "collapsed summaries computed from real tool/changes data",
  );
  assert.ok(turnChecks.noYou, "no You role label on user prompts");
  await page.screenshot({ path: path.join(__dirname, "screenshot-three-turn-history-collapsed-1440x900.png"), fullPage: false });

  // Expand an old turn manually; a streaming-style update must not reset it.
  const foldChecks = await page.evaluate(() => {
    const debug = window.AiPanel.debug;
    const turns = [...document.querySelectorAll(".tl-turn")];
    const head0 = turns[0].querySelector(".tl-work-head");
    head0.click(); // expand turn 1
    const disp = (el) => getComputedStyle(el).display;
    const expandedAfterClick = disp(turns[0].querySelector(".tl-work-body")) !== "none";
    // Simulate a live update (status bump) on the same conversation.
    debug.ingestTaskEvents([{ id: 900, type: "status", message: "补充状态", turn_id: "t3" }], { jobId: "j1" });
    const keptAfterUpdate = disp(document.querySelectorAll(".tl-turn")[0].querySelector(".tl-work-body")) !== "none";
    const ariaAfter = turns[0].querySelector(".tl-work-head").getAttribute("aria-expanded") === "true";
    return { expandedAfterClick, keptAfterUpdate, ariaAfter };
  });
  assert.ok(foldChecks.expandedAfterClick, "clicking a collapsed turn expands it");
  assert.ok(foldChecks.keptAfterUpdate, "expansion survives streaming/live updates");
  assert.ok(foldChecks.ariaAfter, "aria-expanded tracks state after update");
  await page.screenshot({ path: path.join(__dirname, "screenshot-history-expanded-1440x900.png"), fullPage: false });

  // —— Viewport matrix on key scenes ——
  const matrix = [
    { scene: "plan-tools", name: "1024x768", width: 1024, height: 768 },
    { scene: "approval-command", name: "1024x768", width: 1024, height: 768 },
    { scene: "changes", name: "narrow", width: 700, height: 800 },
    { scene: "approval-command", name: "narrow", width: 700, height: 800 },
    { scene: "three-turn-history", name: "narrow", width: 700, height: 800 },
  ];
  for (const { scene, name, width, height } of matrix) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(400);
    await setScene(scene);
    await page.screenshot({
      path: path.join(__dirname, `screenshot-${scene}-${name}.png`),
      fullPage: false,
    });
  }

  // Legacy-compatible baseline shots (populated, not blank).
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);
  await setScene("changes");
  await page.screenshot({ path: path.join(__dirname, "screenshot-1440x900.png"), fullPage: false });
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(__dirname, "screenshot-1024x768.png"), fullPage: false });
  await page.setViewportSize({ width: 700, height: 800 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(__dirname, "screenshot-narrow.png"), fullPage: false });

  // —— Interaction assertions: approvals ——
  await page.setViewportSize({ width: 1440, height: 900 });
  await setScene("approval-command");
  const checks = await page.evaluate(() => {
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => [...document.querySelectorAll(sel)];
    const approvalCards = $$(".tl-approval");
    const card = approvalCards[0];
    const dock = $("#approvalDock");
    const dockVisible = dock && !dock.hidden;
    const commandText = card ? card.textContent : "";
    return {
      singleApprovalCard: approvalCards.length === 1,
      dockVisible,
      dockLabel: dock ? dock.textContent : "",
      showsFullCommand: commandText.includes("./gradlew clean assembleDebug"),
      showsCwd: commandText.includes("工作目录"),
      showsRisk: commandText.includes("破坏性"),
      hasRejectThenAllow:
        card &&
        (() => {
          const btns = [...card.querySelectorAll(".tl-approval-actions button")].map((b) => b.textContent);
          return btns[0] === "拒绝" && btns[1] === "允许一次";
        })(),
      noRoleLabels: !$(".msg-role") && !$$(".tl-item").some((i) => i.textContent.trim().startsWith("You")),
      noUserBubbleGradient: (() => {
        const userBody = $(".tl-user-body");
        if (!userBody) return true;
        return !getComputedStyle(userBody).backgroundImage.includes("gradient");
      })(),
    };
  });

  assert.ok(checks.singleApprovalCard, "exactly one approval card in timeline");
  assert.ok(checks.dockVisible, "approval dock visible above composer");
  assert.ok(checks.dockLabel.includes("1 项操作等待确认"), "dock shows pending count");
  assert.ok(checks.showsFullCommand, "approval shows the full command");
  assert.ok(checks.showsCwd, "approval shows cwd");
  assert.ok(checks.showsRisk, "approval shows textual risk label");
  assert.ok(checks.hasRejectThenAllow, "reject comes before allow-once");
  assert.ok(checks.noRoleLabels, "no You/Agent role labels");
  assert.ok(checks.noUserBubbleGradient, "no gradient user bubble");

  // Dock click scrolls to and flashes the same card (single source of truth).
  await page.click("#approvalDock .tl-dock-btn");
  await page.waitForTimeout(300);
  const flash = await page.evaluate(() => Boolean(document.querySelector(".tl-approval.tl-flash")));
  assert.ok(flash, "dock jump focuses the timeline approval card");

  // Tool card expands and keeps buttons visible.
  await page.click(".tl-tool .tl-tool-head");
  await page.waitForTimeout(200);
  const expanded = await page.evaluate(() => {
    const detail = document.querySelector(".tl-tool .tl-tool-detail");
    const allow = document.querySelector(".tl-approval-actions .danger-btn, .tl-approval-actions .primary-btn");
    const rect = allow?.getBoundingClientRect();
    return {
      detailVisible: detail && !detail.hidden,
      allowVisible: Boolean(rect && rect.bottom <= window.innerHeight && rect.top >= 0),
    };
  });
  assert.ok(expanded.detailVisible, "tool detail expands");
  assert.ok(expanded.allowVisible, "approval buttons remain visible after expanding");

  // —— Review changes: preparing state shows a disabled busy button ——
  await setScene("review-preparing");
  const preparing = await page.evaluate(() => {
    const btn = document.querySelector(".tl-changes button.is-busy");
    return {
      busy: Boolean(btn),
      disabled: btn ? btn.disabled : false,
      label: btn ? btn.textContent : "",
      noPrimary: !document.querySelector(".tl-changes .primary-btn"),
    };
  });
  assert.ok(preparing.busy && preparing.disabled, "preparing shows disabled busy button");
  assert.ok(preparing.label.includes("正在准备改动审查"), "preparing label correct");
  assert.ok(preparing.noPrimary, "no clickable primary review button while preparing");

  // —— Review changes: ready -> real click -> Monaco diff with exact blobs ——
  await setScene("review-ready");
  await page.evaluate(() => {
    const client = window.AiPanel.client;
    window.__diffCalls = [];
    client.turnDiff = async (projectId, turnId) => {
      window.__diffCalls.push(["turnDiff", projectId, turnId]);
      return {
        ok: true,
        status: "ready",
        turn_id: turnId,
        truncated: false,
        files: [
          {
            path: "app/src/main/java/com/example/SplashActivity.kt",
            change: "modified",
            before_hash: "aa",
            after_hash: "bb",
            additions: 3,
            deletions: 1,
          },
          { path: "app/src/main/java/com/example/util/Nulls.kt", change: "added", additions: 8, deletions: 0 },
          { path: "app/legacy/OldSplash.kt", change: "deleted", additions: 0, deletions: 12 },
        ],
      };
    };
    client.turnDiffFile = async (projectId, turnId, p) => {
      window.__diffCalls.push(["turnDiffFile", p]);
      if (p.endsWith("SplashActivity.kt")) {
        return {
          ok: true,
          path: p,
          change: "modified",
          language: "kotlin",
          before_content: "class SplashActivity {\n  fun onCreate() {\n    val x = intent.extras.getString(\"k\")\n  }\n}",
          after_content: "class SplashActivity {\n  fun onCreate() {\n    val x = intent.extras?.getString(\"k\") ?: return\n  }\n}",
        };
      }
      if (p.endsWith("Nulls.kt")) {
        return { ok: true, path: p, change: "added", language: "kotlin", before_content: "", after_content: "object Nulls {\n  fun <T> T?.orDefault(d: T): T = this ?: d\n}" };
      }
      return { ok: true, path: p, change: "deleted", language: "kotlin", before_content: "class OldSplash // legacy\n", after_content: "" };
    };
    window.AiPanel.debug.setState({ selectedProjectId: "p1" });
  });

  const modelsBefore = await page.evaluate(() => window.monaco.editor.getModels().length);
  await page.click(".tl-changes .primary-btn");
  await page.waitForFunction(() => !document.getElementById("monacoDiffHost").hidden, null, { timeout: 10000 });
  await page.waitForTimeout(600);

  const reviewOpen = await page.evaluate(() => {
    const host = document.getElementById("monacoDiffHost");
    const title = document.getElementById("diffTitle").textContent;
    const switcher = document.getElementById("diffFileSwitcher");
    const toolbar = document.getElementById("diffToolbar");
    const models = window.monaco.editor.getModels().map((m) => m.getValue());
    const accept = document.getElementById("btnAcceptDiff");
    const reject = document.getElementById("btnRejectDiff");
    return {
      hostVisible: !host.hidden,
      title,
      switcherOptions: switcher ? [...switcher.options].map((o) => o.textContent) : [],
      reviewMode: toolbar.classList.contains("review-mode"),
      acceptHidden: getComputedStyle(accept).display === "none",
      rejectHidden: getComputedStyle(reject).display === "none",
      models,
    };
  });
  assert.ok(reviewOpen.hostVisible, "Monaco diff host opens on review click");
  assert.ok(reviewOpen.title.includes("审查改动") && reviewOpen.title.includes("1/3"), "diff title shows 审查改动 + position");
  assert.strictEqual(reviewOpen.switcherOptions.length, 3, "file switcher lists all files");
  assert.ok(reviewOpen.reviewMode, "toolbar in review mode");
  assert.ok(reviewOpen.acceptHidden && reviewOpen.rejectHidden, "accept/reject hidden in review mode");
  assert.ok(
    reviewOpen.models.some((v) => v.includes('intent.extras.getString')) &&
      reviewOpen.models.some((v) => v.includes('intent.extras?.getString')),
    "diff models contain exact before/after checkpoint blobs",
  );
  await page.screenshot({ path: path.join(__dirname, "screenshot-review-open-monaco-1440x900.png"), fullPage: false });

  // Switch to the ADDED file: original must be empty.
  await page.selectOption("#diffFileSwitcher", "1");
  await page.waitForTimeout(500);
  const addedFile = await page.evaluate(() => {
    const models = window.monaco.editor.getModels().map((m) => m.getValue());
    return {
      title: document.getElementById("diffTitle").textContent,
      hasEmptyOriginal: models.includes(""),
      hasAddedContent: models.some((v) => v.includes("object Nulls")),
    };
  });
  assert.ok(addedFile.title.includes("2/3"), "switcher updates position");
  assert.ok(addedFile.hasEmptyOriginal, "added file uses empty original");
  assert.ok(addedFile.hasAddedContent, "added file shows after content");

  // Switch to the DELETED file: modified must be empty.
  await page.selectOption("#diffFileSwitcher", "2");
  await page.waitForTimeout(500);
  const deletedFile = await page.evaluate(() => {
    const models = window.monaco.editor.getModels().map((m) => m.getValue());
    return {
      title: document.getElementById("diffTitle").textContent,
      hasOldContent: models.some((v) => v.includes("OldSplash")),
      emptyCount: models.filter((v) => v === "").length,
    };
  });
  assert.ok(deletedFile.title.includes("3/3"), "deleted file position correct");
  assert.ok(deletedFile.hasOldContent, "deleted file shows before content");
  assert.ok(deletedFile.emptyCount >= 1, "deleted file uses empty modified");

  // Close: host hidden, models disposed (no accumulation).
  await page.click("#btnCloseDiff");
  await page.waitForTimeout(300);
  const reviewClosed = await page.evaluate(() => ({
    hostHidden: document.getElementById("monacoDiffHost").hidden,
    switcherGone: !document.getElementById("diffFileSwitcher"),
    models: window.monaco.editor.getModels().length,
  }));
  assert.ok(reviewClosed.hostHidden, "diff host closes");
  assert.ok(reviewClosed.switcherGone, "review switcher removed on close");
  assert.ok(reviewClosed.models <= modelsBefore, "diff models disposed on close (no leak)");

  // Reopening the same file must not accumulate models.
  await page.click(".tl-changes .primary-btn");
  await page.waitForFunction(() => !document.getElementById("monacoDiffHost").hidden, null, { timeout: 10000 });
  await page.waitForTimeout(500);
  await page.click("#btnCloseDiff");
  await page.waitForTimeout(300);
  const reopenModels = await page.evaluate(() => window.monaco.editor.getModels().length);
  assert.ok(reopenModels <= modelsBefore, "reopen+close does not accumulate models");

  // Preparing -> ready retry: openTurnDiffReview polls until ready.
  await page.evaluate(() => {
    const client = window.AiPanel.client;
    let calls = 0;
    client.turnDiff = async () => {
      calls += 1;
      if (calls <= 2) return { ok: false, status: "preparing" };
      return {
        ok: true,
        status: "ready",
        turn_id: "t1",
        files: [{ path: "a.kt", change: "modified" }],
      };
    };
    client.turnDiffFile = async (p, t, path2) => ({ ok: true, path: path2, before_content: "1", after_content: "2" });
  });
  await page.evaluate(() => window.AiPanel.debug.openTurnDiffReview("p1", "t1"));
  await page.waitForFunction(() => !document.getElementById("monacoDiffHost").hidden, null, { timeout: 15000 });
  const retried = await page.evaluate(() => !document.getElementById("monacoDiffHost").hidden);
  assert.ok(retried, "preparing state retries until ready instead of failing");
  await page.click("#btnCloseDiff");
  await page.waitForTimeout(200);

  // API failure surfaces a visible error toast.
  await page.evaluate(() => {
    const client = window.AiPanel.client;
    client.turnDiff = async () => {
      throw new Error("checkpoint 读取失败");
    };
  });
  await page.evaluate(() => window.AiPanel.debug.openTurnDiffReview("p1", "t1"));
  await page.waitForTimeout(300);
  const errToast = await page.evaluate(() => {
    const t = document.getElementById("toast");
    return { visible: t && !t.hidden, text: t ? t.textContent : "" };
  });
  assert.ok(errToast.visible && errToast.text.includes("checkpoint 读取失败"), "diff API failure shows visible error");

  // Truncated diff shows a warning banner.
  await page.evaluate(() => {
    const client = window.AiPanel.client;
    client.turnDiff = async () => ({
      ok: true,
      status: "ready",
      turn_id: "t1",
      truncated: true,
      files: [{ path: "big.kt", change: "modified" }],
    });
    client.turnDiffFile = async (p, t, path2) => ({ ok: true, path: path2, before_content: "a", after_content: "b", truncated: true });
  });
  await page.evaluate(() => window.AiPanel.debug.openTurnDiffReview("p1", "t1"));
  await page.waitForFunction(() => !document.getElementById("monacoDiffHost").hidden, null, { timeout: 10000 });
  await page.waitForTimeout(300);
  const truncWarn = await page.evaluate(() => {
    const w = document.getElementById("diffTruncatedWarn");
    return { present: Boolean(w), text: w ? w.textContent : "" };
  });
  assert.ok(truncWarn.present && truncWarn.text.includes("截断"), "truncated diff shows warning");
  await page.click("#btnCloseDiff");
  await page.waitForTimeout(200);

  // —— Conversation switching: late response of A must never leak into B ——
  const switchCheck = await page.evaluate(async () => {
    const debug = window.AiPanel.debug;
    const client = window.AiPanel.client;
    const delay = (ms) => new Promise((r) => setTimeout(r, ms));
    const mkEvents = (tag) => [
      { seq: 1, event_type: "user_message", payload: { message_id: `${tag}-u1`, content: [{ type: "text", text: `会话${tag}的问题` }] }, turn_id: `${tag}t1`, task_id: `${tag}j1`, created_at: 1000 },
      { seq: 2, event_type: "assistant_message", payload: { message_id: `${tag}-m1`, is_final: true, text_blocks: [{ type: "text", text: `会话${tag}的最终回答` }] }, turn_id: `${tag}t1`, task_id: `${tag}j1`, created_at: 1010 },
    ];
    client.conversationEvents = async (convId) => {
      if (convId === "convA") {
        await delay(700); // A is slow
        return { events: mkEvents("A"), has_more: false };
      }
      return { events: mkEvents("B"), has_more: false };
    };
    client.jobs = async () => ({ jobs: [] });
    const pA = debug.selectConversation("convA");
    const pB = debug.selectConversation("convB");
    await pB;
    await pA;
    await delay(900); // A's late response arrives here and must be dropped
    const text = document.getElementById("aiMessages").textContent;
    return {
      hasB: text.includes("会话B的最终回答"),
      leakedA: text.includes("会话A"),
      convId: window.AiPanel.getState().conversationId,
    };
  });
  assert.ok(switchCheck.hasB, "conversation B content rendered");
  assert.ok(!switchCheck.leakedA, "late conversation-A events never leak into B");
  assert.strictEqual(switchCheck.convId, "convB", "state stays on conversation B");

  // Markdown table scene: real table element in the DOM.
  await setScene("markdown-table");
  const tableCheck = await page.evaluate(() => {
    const table = document.querySelector(".tl-assistant table.md-table");
    return {
      present: Boolean(table),
      headers: table ? [...table.querySelectorAll("thead th")].map((th) => th.textContent) : [],
      rows: table ? table.querySelectorAll("tbody tr").length : 0,
    };
  });
  assert.ok(tableCheck.present, "markdown table renders as a real table");
  assert.deepStrictEqual(tableCheck.headers, ["方案", "命中延迟", "内存占用", "适用场景"], "table headers correct");
  assert.strictEqual(tableCheck.rows, 3, "table body rows correct");

  // Markdown safety: no raw HTML injection from model text.
  await page.evaluate(() => {
    const debug = window.AiPanel.debug;
    debug.timeline.reset();
    debug.ingestTaskEvents(
      [
        {
          id: 1,
          type: "assistant_message",
          message_id: "mx",
          text_blocks: [{ type: "text", text: "safe <img src=x onerror=alert(1)> [x](javascript:alert(1)) `code`" }],
        },
      ],
      { jobId: "j1" },
    );
  });
  await page.waitForTimeout(200);
  const safety = await page.evaluate(() => ({
    noImg: !document.querySelector(".tl-assistant-body img"),
    noScript: !document.querySelector(".tl-assistant-body script"),
    noJsLink: ![...document.querySelectorAll(".tl-assistant-body a")].some((a) =>
      (a.getAttribute("href") || "").startsWith("javascript:"),
    ),
  }));
  assert.ok(safety.noImg, "markdown never injects raw HTML img");
  assert.ok(safety.noScript, "markdown never injects script");
  assert.ok(safety.noJsLink, "javascript: URLs are dropped");

  // Layout visibility checks across viewports.
  const visible = await page.evaluate(() => {
    const bottom = document.getElementById("bottomPanel");
    const ai = document.getElementById("aiPane");
    const editor = document.getElementById("editorHosts");
    const check = (el) => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight && r.right <= window.innerWidth;
    };
    return {
      aiPane: check(ai),
      editorHosts: check(editor),
      bottomPanel: bottom ? check(bottom) : true,
    };
  });

  await browser.close();
  await new Promise((resolve) => server.close(resolve));

  assert.ok(visible.aiPane, "AI pane visible");
  assert.ok(visible.editorHosts, "Editor area visible");

  // Only fail on uncaught errors originating from the timeline modules; ignore
  // pre-existing AMD-loader / xterm mock noise from the test harness.
  const KNOWN_NOISE = [
    "Can only have one anonymous define call per script file",
    "Cannot destructure property 'Terminal' of 'window.Terminal'",
  ];
  const realErrors = consoleErrors.filter((e) => !KNOWN_NOISE.some((k) => e.includes(k)));
  assert.deepStrictEqual(realErrors, [], "no uncaught timeline errors");
  console.log("screenshot.test: OK", visible);
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
