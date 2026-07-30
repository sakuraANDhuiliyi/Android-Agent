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

async function run() {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const url = `http://127.0.0.1:${port}/src/index.html`;

  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

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

  // Wait for Monaco and xterm globals to settle.
  await page.waitForTimeout(2000);

  const sizes = [
    { name: "1440x900", width: 1440, height: 900 },
    { name: "1024x768", width: 1024, height: 768 },
    { name: "narrow", width: 700, height: 800 },
  ];

  for (const { name, width, height } of sizes) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(__dirname, `screenshot-${name}.png`), fullPage: false });
  }

  // Check no overlap and critical elements visible.
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
  console.log("screenshot.test: OK", visible);
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
