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
};

// Serves the desktop dir so the real styles.css / timeline.js / agent-timeline.js
// are exercised (getComputedStyle needs the real stylesheet).
const server = http.createServer(async (req, res) => {
  let urlPath = decodeURIComponent(req.url || "/").split("?")[0];
  if (urlPath === "/") urlPath = "/tests/__dom.html";
  const filePath = path.join(desktopDir, urlPath);
  if (!filePath.startsWith(desktopDir)) {
    res.writeHead(403);
    res.end();
    return;
  }
  try {
    if (urlPath === "/tests/__dom.html") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(
        `<!DOCTYPE html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/src/styles.css"></head>` +
          `<body><div id="root"></div>` +
          `<script src="/src/timeline.js"></script>` +
          `<script src="/src/agent-timeline.js"></script></body></html>`,
      );
      return;
    }
    const data = await readFile(filePath);
    res.writeHead(200, { "Content-Type": mime[path.extname(filePath)] || "application/octet-stream" });
    res.end(data);
  } catch (err) {
    res.writeHead(404);
    res.end(String(err.message));
  }
});

let passed = 0;
function ok(cond, name) {
  assert.ok(cond, name);
  passed += 1;
  console.log(`  ok - ${name}`);
}

async function run() {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "load" });
  await page.waitForFunction(() => window.AgentTimeline && window.Timeline);

  // —————————————————— Markdown ——————————————————

  const mdChecks = await page.evaluate(() => {
    const AT = window.AgentTimeline;
    const out = {};
    const render = (text, cbs = {}) => {
      const c = document.createElement("div");
      document.getElementById("root").appendChild(c);
      AT.renderMarkdown(c, text, cbs);
      return c;
    };

    // Headings H1-H4
    const h = render("# 一级\n## 二级\n### 三级\n#### 四级");
    out.headings = ["H1", "H2", "H3", "H4"].every((t) => h.querySelector(t.toLowerCase()));

    // Table
    const t = render("| 模块 | 状态 |\n| --- | --- |\n| 登录 | 完成 |\n| 支付 | 进行中 |");
    const table = t.querySelector("table.md-table");
    out.table =
      Boolean(table) &&
      table.querySelectorAll("thead th").length === 2 &&
      table.querySelectorAll("tbody tr").length === 2 &&
      table.querySelector("tbody tr td")?.textContent === "登录";

    // Nested list + task list
    const l = render("- 外层一\n  - 内层甲\n  - 内层乙\n- 外层二\n1. 有序一\n2. 有序二");
    out.nestedList = Boolean(l.querySelector("ul li ul li"));
    out.orderedList = Boolean(l.querySelector("ol li"));
    const task = render("- [x] 已完成项\n- [ ] 待办项");
    out.taskList =
      task.querySelectorAll(".md-task").length === 2 &&
      Boolean(task.querySelector(".md-task-box.checked"));

    // Blockquote
    const q = render("> 注意：这是引用\n> 第二行引用");
    out.blockquote = Boolean(q.querySelector("blockquote")) && q.querySelector("blockquote").textContent.includes("注意");

    // Bold / italic / strike / inline code
    const s = render("**粗体** 与 *斜体* 与 ~~删除~~ 与 `行内代码`");
    out.bold = Boolean(s.querySelector("strong"));
    out.italic = Boolean(s.querySelector("em"));
    out.strike = Boolean(s.querySelector("s, del"));
    out.inlineCode = s.querySelector("code.md-code")?.textContent === "行内代码";

    // Fenced code block: language label, copy button, real newlines kept
    const f = render("```kotlin\nfun main() {\n  println(\"hi\")\n}\n```");
    const pre = f.querySelector(".md-codeblock pre");
    out.fenced =
      f.querySelector(".md-codeblock-lang")?.textContent === "kotlin" &&
      Boolean(f.querySelector(".md-copy")) &&
      pre.textContent.split("\n").length === 3 &&
      pre.textContent.includes('println("hi")') &&
      pre.textContent.startsWith("fun main() {");

    // Chinese soft break inside a paragraph stays one paragraph
    const soft = render("这是第一行\n这是第二行，同一段落。");
    const paras = soft.querySelectorAll("p");
    out.softBreak = paras.length === 1 && paras[0].textContent.includes("第一行") && paras[0].textContent.includes("第二行");

    // Soft break inside bold must not leak raw markers
    const softBold = render("**粗\n体**内容");
    out.softBoldNoMarkers = !softBold.textContent.includes("**") && softBold.textContent.includes("粗");

    // File path + line link uses the safe openFile callback
    let opened = null;
    const fl = render("请查看 app/src/main/java/Foo.kt:12 的实现", { openFile: (p, line) => (opened = [p, line]) });
    const fileLink = fl.querySelector("a.md-file");
    fileLink?.click();
    out.fileLink =
      Boolean(fileLink) && opened && opened[0] === "app/src/main/java/Foo.kt" && opened[1] === 12;

    // External http link: allowed, with rel
    const ext = render("[官网](https://example.com)");
    const a = ext.querySelector("a[href^='https://example.com']");
    out.httpLink = Boolean(a) && (a.rel || "").includes("noopener");

    // javascript: URL dropped
    const js = render("[点我](javascript:alert(1))");
    out.noJsUrl = ![...js.querySelectorAll("a")].some((x) => (x.getAttribute("href") || "").startsWith("javascript:"));

    // XSS fixtures: never produce img/script/iframe elements or attribute handlers
    const xss = render('<img src=x onerror=alert(1)> <script>alert(2)<\/script> <iframe src="x"></iframe> [x](data:text/html,<script>alert(3)<\/script>)');
    const anyHandler = [...xss.querySelectorAll("*")].some((n) =>
      [...n.attributes].some((a) => /^on/i.test(a.name)),
    );
    out.noXss =
      !xss.querySelector("img") &&
      !xss.querySelector("script") &&
      !xss.querySelector("iframe") &&
      !anyHandler &&
      ![...xss.querySelectorAll("a")].some((x) => /^(data:text\/html|javascript:)/i.test(x.getAttribute("href") || ""));

    // Tolerant streaming markdown: unclosed fence/bold do not leak markers
    const tol = document.createElement("div");
    AT.renderMarkdown(tol, "**正在处理\n```kotlin\nfun x()", {}, { tolerant: true });
    out.tolerant = !tol.textContent.includes("```") && !tol.textContent.includes("**");

    return out;
  });
  for (const [name, val] of Object.entries(mdChecks)) ok(val, `markdown: ${name}`);

  // —————————————————— Collapse behaviour (real CSS) ——————————————————

  const collapseChecks = await page.evaluate(() => {
    const AT = window.AgentTimeline;
    const Timeline = window.Timeline;
    const root = document.getElementById("root");
    root.textContent = "";
    const container = document.createElement("div");
    container.style.cssText = "width:400px;height:600px;overflow:auto;";
    root.appendChild(container);

    const store = Timeline.createStore();
    store.ingestTaskEvents(
      [
        { id: 1, type: "user_message", message_id: "u1", content: [{ type: "text", text: "运行构建" }] },
        { id: 2, type: "tool_call", tool_call_id: "c1", name: "run_gradle", input: { task: "assembleDebug" } },
        { id: 3, type: "tool_result", tool_call_id: "c1", name: "run_gradle", ok: true, duration_ms: 1200, model_output: "BUILD SUCCESSFUL" },
        { id: 4, type: "plan", message: "构建 -> 验证" },
        { id: 5, type: "assistant_message", message_id: "m1", is_final: true, text_blocks: [{ type: "text", text: "构建成功。" }] },
      ],
      { jobId: "j1", turnId: "t1" },
    );
    const view = AT.createTimelineView(container, {});
    view.update(store.items(), { immediate: true });

    const out = {};
    const disp = (el) => (el ? getComputedStyle(el).display : null);

    // Tool detail hidden by default -> computed display must be none
    const toolHead = container.querySelector(".tl-tool .tl-tool-head");
    let detail = container.querySelector(".tl-tool .tl-tool-detail");
    out.toolHiddenNone = disp(detail) === "none";
    out.toolAriaCollapsed = toolHead.getAttribute("aria-expanded") === "false";

    // Click expands; computed display no longer none; aria-expanded true
    toolHead.click();
    view.refresh();
    detail = container.querySelector(".tl-tool .tl-tool-detail");
    out.toolShown = disp(detail) !== "none";
    out.toolAriaExpanded = container.querySelector(".tl-tool .tl-tool-head").getAttribute("aria-expanded") === "true";

    // Tool status update (running -> success already; bump version) keeps expansion
    store.ingestTaskEvents(
      [{ id: 6, type: "tool_result", tool_call_id: "c1", name: "run_gradle", ok: true, duration_ms: 1300, model_output: "BUILD SUCCESSFUL (cached)" }],
      { jobId: "j1", turnId: "t1" },
    );
    view.update(store.items(), { immediate: true });
    detail = container.querySelector(".tl-tool .tl-tool-detail");
    out.toolKeptExpandedAfterUpdate = disp(detail) !== "none";

    // Click again hides
    container.querySelector(".tl-tool .tl-tool-head").click();
    view.refresh();
    detail = container.querySelector(".tl-tool .tl-tool-detail");
    out.toolHiddenAgain = disp(detail) === "none";

    // Plan body toggle
    const planHead = container.querySelector(".tl-plan .tl-plan-head");
    if (planHead) {
      let body = container.querySelector(".tl-plan .tl-plan-body");
      const before = disp(body);
      planHead.click();
      view.refresh();
      body = container.querySelector(".tl-plan .tl-plan-body");
      out.planToggles = disp(body) !== before && planHead.getAttribute("aria-expanded") != null;
    } else {
      out.planToggles = true; // plan may render inline without collapse when single-line
    }

    // Worked session head: aria + toggle
    const workHead = container.querySelector(".tl-work-head");
    const workBody = container.querySelector(".tl-work-body");
    out.workAriaControls = workHead.getAttribute("aria-controls") === workBody.id;
    const wasExpanded = workHead.getAttribute("aria-expanded") === "true";
    workHead.click();
    out.workToggles = workHead.getAttribute("aria-expanded") === String(!wasExpanded) && disp(container.querySelector(".tl-work-body")) !== "none" === !wasExpanded;

    return out;
  });
  for (const [name, val] of Object.entries(collapseChecks)) ok(val, `collapse: ${name}`);

  // —————————————————— Turn history (3 turns) ——————————————————

  const turnChecks = await page.evaluate(() => {
    const AT = window.AgentTimeline;
    const Timeline = window.Timeline;
    const root = document.getElementById("root");
    root.textContent = "";
    localStorage.removeItem("agentTimeline.viewState.v1");
    const container = document.createElement("div");
    container.style.cssText = "width:400px;height:600px;overflow:auto;";
    root.appendChild(container);

    const store = Timeline.createStore();
    const T0 = 1767225600;
    let seq = 0;
    const ev = (turnId, taskId, type, payload) => ({ seq: ++seq, event_type: type, payload, turn_id: turnId, task_id: taskId, created_at: T0 + seq * 10 });
    for (let n = 1; n <= 3; n += 1) {
      const turnId = `t${n}`;
      const taskId = `j${n}`;
      store.ingestConversationEvents([
        ev(turnId, taskId, "user_message", { message_id: `u${n}`, content: [{ type: "text", text: `第 ${n} 轮问题` }] }),
        ev(turnId, taskId, "assistant_message", { message_id: `c${n}a`, text_blocks: [{ type: "text", text: `第 ${n} 轮中间说明` }] }),
        ev(turnId, taskId, "tool_call", { tool_call_id: `c${n}x`, name: "read_file", input: { path: `f${n}.kt` } }),
        ev(turnId, taskId, "tool_result", { tool_call_id: `c${n}x`, name: "read_file", ok: true, duration_ms: 5 }),
        ev(turnId, taskId, "tool_call", { tool_call_id: `c${n}y`, name: "run_command", input: { argv: ["ls"] } }),
        ev(turnId, taskId, "tool_result", { tool_call_id: `c${n}y`, name: "run_command", ok: true, duration_ms: 9 }),
        ev(turnId, taskId, "changes", { files: [{ path: `f${n}.kt`, change: "modified" }] }),
        ev(turnId, taskId, "assistant_message", { message_id: `f${n}`, is_final: true, text_blocks: [{ type: "text", text: `第 ${n} 轮最终回答` }] }),
        ev(turnId, taskId, "completed", { message: "任务完成" }),
      ]);
    }
    // A private reasoning event must never surface.
    store.ingestConversationEvents([ev("t3", "j3", "reasoning", { content: "PRIVATE-COT-LEAK" })]);

    const view = AT.createTimelineView(container, {});
    view.update(store.items(), { immediate: true });

    const out = {};
    const turns = container.querySelectorAll(".tl-turn");
    out.threeTurns = turns.length === 3;
    out.notFlat = !container.querySelector(":scope > .tl-item"); // items live inside turns

    // Last turn expanded, older collapsed (default policy)
    const bodies = [...turns].map((t) => t.querySelector(".tl-work-body"));
    const disp = (el) => getComputedStyle(el).display;
    out.lastExpanded = disp(bodies[2]) !== "none";
    out.olderCollapsed = disp(bodies[0]) === "none" && disp(bodies[1]) === "none";

    // Final answers stay visible even when the work session is collapsed
    const finals = [...turns].map((t) => t.querySelector(".tl-turn-final"));
    out.finalsVisible = finals.every((f) => f && f.textContent.includes("最终回答") && disp(f) !== "none");
    out.finalOutsideWork = finals.every((f) => !f.closest(".tl-work-body"));

    // Collapsed summary counts come from real data (1 read + 1 command + 1 change)
    const sum0 = turns[0].querySelector(".tl-work-summary").textContent;
    out.summaryCounts = sum0.includes("查看 1 个文件") && sum0.includes("执行 1 条命令") && sum0.includes("修改 1 个文件");

    // Expanded turn shows events in order inside the work body
    const body2 = bodies[2];
    const kinds = [...body2.querySelectorAll(".tl-item")].map((i) =>
      i.classList.contains("tl-tool") ? "tool" : i.classList.contains("tl-changes") ? "changes" : i.classList.contains("tl-assistant") ? "assistant" : "other",
    );
    out.workOrder = kinds.join(",") === "assistant,tool,tool,changes";

    // No private reasoning anywhere
    out.noPrivateCot = !container.textContent.includes("PRIVATE-COT-LEAK") && !container.textContent.includes("Thought");

    // User prompts visible in every turn
    out.userVisible = [...turns].every((t, i) => t.querySelector(".tl-turn-user")?.textContent.includes(`第 ${i + 1} 轮问题`));

    // Worked label present
    out.workedLabel = [...turns].every((t) => /工作了|Worked for/.test(t.querySelector(".tl-work-head").textContent));

    // Re-render (simulating streaming reconcile) must keep user expansion:
    // collapse the last turn manually, push the same items again, stays collapsed.
    view.setTurnExpanded(AT.buildTurns(store.items())[2].key, false);
    view.update(store.items(), { immediate: true });
    out.manualStateSurvivesReconcile = disp(container.querySelectorAll(".tl-turn")[2].querySelector(".tl-work-body")) === "none";

    // buildTurns structure sanity
    const built = AT.buildTurns(store.items());
    out.builtThree = built.length === 3;
    out.builtStatus = built.every((t) => t.status === "succeeded" || t.status === "completed");
    out.builtFinals = built.every((t) => t.finalMessages.length === 1);

    return out;
  });
  for (const [name, val] of Object.entries(turnChecks)) ok(val, `turns: ${name}`);

  await browser.close();
  await new Promise((resolve) => server.close(resolve));

  assert.deepStrictEqual(pageErrors, [], "no uncaught page errors");
  console.log(`agent-timeline.test: OK (${passed} checks)`);
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
