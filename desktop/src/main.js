const { app, BrowserWindow, dialog, ipcMain, Menu, safeStorage, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawn } = require("child_process");
const fs = require("fs/promises");
const fssync = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const IGNORE_NAMES = new Set([
  ".git",
  "node_modules",
  ".DS_Store",
  "__pycache__",
  ".gradle",
  "build",
  "dist",
  ".idea",
  ".cursor",
]);

let mainWindow = null;
/** @type {import('child_process').ChildProcess | null} */
let agentProcess = null;
let agentLogTail = "";
const approvedRoots = new Set();

function configureSecureUpdates() {
  const feedUrl = String(process.env.ANDROID_AGENT_UPDATE_URL || "").trim();
  if (!app.isPackaged || !feedUrl) return;
  let parsed;
  try {
    parsed = new URL(feedUrl);
  } catch (_) {
    console.error("Ignoring invalid ANDROID_AGENT_UPDATE_URL");
    return;
  }
  if (parsed.protocol !== "https:") {
    console.error("Ignoring non-HTTPS update feed");
    return;
  }
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.setFeedURL({ provider: "generic", url: parsed.toString() });
  autoUpdater.checkForUpdates().catch((error) => {
    console.error("Signed update check failed:", error.message);
  });
}

function credentialFile() {
  return path.join(app.getPath("userData"), "agent-credentials.json");
}

async function loadCredentials() {
  try {
    return JSON.parse(await fs.readFile(credentialFile(), "utf8"));
  } catch (_) {
    return {};
  }
}

async function getCredential(baseUrl) {
  if (!safeStorage.isEncryptionAvailable()) return "";
  const items = await loadCredentials();
  const encoded = items[String(baseUrl || "")];
  if (!encoded) return "";
  try {
    return safeStorage.decryptString(Buffer.from(encoded, "base64"));
  } catch (_) {
    return "";
  }
}

async function setCredential(baseUrl, token) {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("系统安全凭证库不可用");
  }
  const key = String(baseUrl || "").slice(0, 2048);
  const items = await loadCredentials();
  if (token) {
    items[key] = safeStorage.encryptString(String(token)).toString("base64");
  } else {
    delete items[key];
  }
  await fs.mkdir(path.dirname(credentialFile()), { recursive: true });
  await fs.writeFile(
    credentialFile(),
    JSON.stringify(items),
    { encoding: "utf8", mode: 0o600 },
  );
  return true;
}

function rememberApprovedRoot(rootDir) {
  if (!rootDir) return null;
  const resolved = fssync.realpathSync(rootDir);
  approvedRoots.add(resolved);
  return resolved;
}

function isInsideRoot(candidate, root) {
  const rel = path.relative(root, candidate);
  return rel === "" || (!rel.startsWith(`..${path.sep}`) && rel !== ".." && !path.isAbsolute(rel));
}

function assertApprovedPath(filePath) {
  if (!filePath || typeof filePath !== "string") {
    throw new Error("文件路径无效");
  }
  const absolute = path.resolve(filePath);
  let existing = absolute;
  while (!fssync.existsSync(existing)) {
    const parent = path.dirname(existing);
    if (parent === existing) break;
    existing = parent;
  }
  const realExisting = fssync.realpathSync(existing);
  const suffix = path.relative(existing, absolute);
  const candidate = path.resolve(realExisting, suffix);
  for (const root of approvedRoots) {
    if (isInsideRoot(candidate, root)) return candidate;
  }
  throw new Error("拒绝访问未授权的文件路径");
}

function guessLanIp() {
  try {
    const nets = os.networkInterfaces();
    for (const entries of Object.values(nets)) {
      for (const entry of entries || []) {
        const v4 = entry.family === "IPv4" || entry.family === 4;
        if (v4 && !entry.internal) {
          return entry.address;
        }
      }
    }
  } catch (_) {
    /* ignore */
  }
  return null;
}

function readConfiguredPort() {
  const candidates = [
    path.join(repoRoot(), "config.yaml"),
    path.join(repoRoot(), "config.yml"),
  ];
  for (const file of candidates) {
    try {
      const text = fssync.readFileSync(file, "utf8");
      const match = text.match(/^\s*server_port:\s*(\d+)\s*$/m);
      if (match) return Number(match[1]);
    } catch (_) {
      /* ignore */
    }
  }
  return 8000;
}

function agentEnv() {
  const env = { ...process.env, PYTHONUNBUFFERED: "1" };
  const jbr = "/Applications/Android Studio.app/Contents/jbr/Contents/Home";
  if (!env.JAVA_HOME && fssync.existsSync(jbr)) {
    env.JAVA_HOME = jbr;
  }
  const sdk = path.join(os.homedir(), "Library/Android/sdk");
  if (!env.ANDROID_HOME && fssync.existsSync(sdk)) {
    env.ANDROID_HOME = sdk;
    env.ANDROID_SDK_ROOT = sdk;
  }
  if (env.JAVA_HOME) {
    env.PATH = `${path.join(env.JAVA_HOME, "bin")}${path.delimiter}${env.PATH || ""}`;
  }
  if (env.ANDROID_HOME) {
    env.PATH = `${path.join(env.ANDROID_HOME, "platform-tools")}${path.delimiter}${env.PATH || ""}`;
  }
  return env;
}

function probeHealth(port, timeoutMs = 800) {
  return new Promise((resolve) => {
    const req = http.get(
      {
        host: "127.0.0.1",
        port,
        path: "/api/health",
        timeout: timeoutMs,
      },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          body += chunk;
        });
        res.on("end", () => {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            try {
              resolve(JSON.parse(body));
            } catch (_) {
              resolve({ status: "ok" });
            }
          } else {
            resolve(null);
          }
        });
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.on("error", () => resolve(null));
  });
}

async function getAgentStatus() {
  const port = readConfiguredPort();
  const lanIp = guessLanIp();
  const health = await probeHealth(port);
  const managed = Boolean(agentProcess && !agentProcess.killed);
  return {
    running: Boolean(health),
    managed,
    pid: managed ? agentProcess.pid : null,
    port,
    lanIp: (health && health.lan_ip) || lanIp,
    localUrl: `http://127.0.0.1:${port}`,
    phoneUrl: ((health && health.lan_ip) || lanIp)
      ? `http://${(health && health.lan_ip) || lanIp}:${port}`
      : null,
    health,
    logTail: agentLogTail.slice(-2000),
  };
}

function stopAgentProcess() {
  if (!agentProcess || agentProcess.killed) {
    agentProcess = null;
    return { stopped: false, reason: "not_managed" };
  }
  const child = agentProcess;
  agentProcess = null;
  try {
    child.kill("SIGTERM");
  } catch (_) {
    /* ignore */
  }
  setTimeout(() => {
    try {
      if (!child.killed) child.kill("SIGKILL");
    } catch (_) {
      /* ignore */
    }
  }, 2000);
  return { stopped: true, pid: child.pid };
}

async function startAgentProcess() {
  const port = readConfiguredPort();
  const existing = await probeHealth(port);
  if (existing) {
    return {
      ok: true,
      alreadyRunning: true,
      ...(await getAgentStatus()),
    };
  }
  if (agentProcess && !agentProcess.killed) {
    return {
      ok: true,
      alreadyRunning: true,
      ...(await getAgentStatus()),
    };
  }

  agentLogTail = "";
  const child = spawn("python3", ["-m", "agent", "serve"], {
    cwd: repoRoot(),
    env: agentEnv(),
    stdio: ["ignore", "pipe", "pipe"],
  });
  agentProcess = child;

  const appendLog = (chunk) => {
    agentLogTail = (agentLogTail + chunk.toString("utf8")).slice(-8000);
  };
  child.stdout.on("data", appendLog);
  child.stderr.on("data", appendLog);
  child.on("exit", (code, signal) => {
    if (agentProcess === child) agentProcess = null;
    agentLogTail += `\n[exit code=${code} signal=${signal || ""}]\n`;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("agent:server-exit", { code, signal });
    }
  });

  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (child.exitCode != null) {
      return {
        ok: false,
        error: `服务进程已退出（code=${child.exitCode}）`,
        logTail: agentLogTail.slice(-2000),
        ...(await getAgentStatus()),
      };
    }
    const health = await probeHealth(port, 500);
    if (health) {
      return {
        ok: true,
        alreadyRunning: false,
        ...(await getAgentStatus()),
      };
    }
    await new Promise((r) => setTimeout(r, 300));
  }

  return {
    ok: false,
    error: "启动超时：未能连上 /api/health",
    logTail: agentLogTail.slice(-2000),
    ...(await getAgentStatus()),
  };
}

function repoRoot() {
  return path.resolve(__dirname, "..", "..");
}

function defaultWorkspace() {
  const workspaces = path.join(repoRoot(), "workspaces");
  try {
    if (fssync.statSync(workspaces).isDirectory()) {
      return workspaces;
    }
  } catch (_) {
    /* ignore */
  }
  return null;
}

function createWindow() {
  const workspace = defaultWorkspace();
  if (workspace) rememberApprovedRoot(workspace);
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    title: "Android Agent",
    backgroundColor: "#1e1e1e",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: { x: 14, y: 14 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith("file:")) event.preventDefault();
  });
  mainWindow.loadFile(path.join(__dirname, "index.html"));

  const template = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    {
      label: "文件",
      submenu: [
        {
          label: "打开文件夹…",
          accelerator: "CmdOrCtrl+O",
          click: () => mainWindow.webContents.send("menu:open-folder"),
        },
        {
          label: "打开文件…",
          accelerator: "CmdOrCtrl+Shift+O",
          click: () => mainWindow.webContents.send("menu:open-file"),
        },
        {
          label: "新建文件",
          accelerator: "CmdOrCtrl+N",
          click: () => mainWindow.webContents.send("menu:new-file"),
        },
        { type: "separator" },
        {
          label: "保存",
          accelerator: "CmdOrCtrl+S",
          click: () => mainWindow.webContents.send("menu:save"),
        },
        {
          label: "另存为…",
          accelerator: "CmdOrCtrl+Shift+S",
          click: () => mainWindow.webContents.send("menu:save-as"),
        },
        {
          label: "全部保存",
          accelerator: "Alt+CmdOrCtrl+S",
          click: () => mainWindow.webContents.send("menu:save-all"),
        },
        { type: "separator" },
        {
          label: "关闭编辑器",
          accelerator: "CmdOrCtrl+W",
          click: () => mainWindow.webContents.send("menu:close-tab"),
        },
        { type: "separator" },
        process.platform === "darwin" ? { role: "close" } : { role: "quit" },
      ],
    },
    { role: "editMenu" },
    {
      label: "查看",
      submenu: [
        {
          label: "命令面板…",
          accelerator: "CmdOrCtrl+Shift+P",
          click: () => mainWindow.webContents.send("menu:command-palette"),
        },
        {
          label: "快速打开…",
          accelerator: "CmdOrCtrl+P",
          click: () => mainWindow.webContents.send("menu:quick-open"),
        },
        { type: "separator" },
        {
          label: "切换侧边栏",
          accelerator: "CmdOrCtrl+B",
          click: () => mainWindow.webContents.send("menu:toggle-sidebar"),
        },
        {
          label: "切换 AI 面板",
          accelerator: "CmdOrCtrl+L",
          click: () => mainWindow.webContents.send("menu:toggle-ai"),
        },
        { type: "separator" },
        { role: "reload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
    {
      label: "帮助",
      submenu: [
        {
          label: "打开 Agent API 文档",
          click: async () => {
            await shell.openExternal("http://127.0.0.1:8000/docs");
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function readDirTree(rootDir, rel = ".", depth = 0, maxDepth = 12) {
  if (depth > maxDepth) return [];
  const abs = path.join(rootDir, rel);
  let entries;
  try {
    entries = await fs.readdir(abs, { withFileTypes: true });
  } catch (_) {
    return [];
  }
  const nodes = [];
  for (const entry of entries.sort((a, b) => {
    if (a.isDirectory() !== b.isDirectory()) {
      return a.isDirectory() ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  })) {
    if (IGNORE_NAMES.has(entry.name) || entry.name.startsWith(".")) {
      continue;
    }
    const childRel = rel === "." ? entry.name : path.join(rel, entry.name);
    if (entry.isDirectory()) {
      nodes.push({
        name: entry.name,
        path: childRel,
        type: "dir",
        children: await readDirTree(rootDir, childRel, depth + 1, maxDepth),
      });
    } else if (entry.isFile()) {
      nodes.push({ name: entry.name, path: childRel, type: "file" });
    }
  }
  return nodes;
}

function flattenFiles(nodes, out = []) {
  for (const node of nodes) {
    if (node.type === "file") out.push(node.path);
    if (node.children) flattenFiles(node.children, out);
  }
  return out;
}

function registerIpc() {
  ipcMain.handle("app:get-default-workspace", () => defaultWorkspace());
  ipcMain.handle("credentials:get", (_event, baseUrl) => getCredential(baseUrl));
  ipcMain.handle("credentials:set", (_event, baseUrl, token) =>
    setCredential(baseUrl, token));
  ipcMain.handle("dialog:confirm-download", async (_event, payload = {}) => {
    const url = payload.url || "";
    const savePath = payload.path || "";
    const maxBytes = payload.max_bytes;
    const sizeHint =
      typeof maxBytes === "number"
        ? `\n大小上限: ${(maxBytes / (1024 * 1024)).toFixed(0)} MB`
        : "";
    const result = await dialog.showMessageBox(mainWindow, {
      type: "warning",
      buttons: ["允许下载", "拒绝"],
      defaultId: 1,
      cancelId: 1,
      title: "确认下载文件",
      message: "Agent 请求从网络下载文件",
      detail: `URL:\n${url}\n\n保存到工程:\n${savePath}${sizeHint}\n\n默认拒绝。仅在你确认来源可信时选择「允许下载」。`,
      noLink: true,
    });
    return result.response === 0;
  });

  ipcMain.handle("dialog:open-folder", async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"],
    });
    if (result.canceled || !result.filePaths[0]) return null;
    return rememberApprovedRoot(result.filePaths[0]);
  });

  ipcMain.handle("dialog:open-file", async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openFile"],
    });
    if (result.canceled || !result.filePaths[0]) return null;
    const selected = fssync.realpathSync(result.filePaths[0]);
    rememberApprovedRoot(path.dirname(selected));
    return selected;
  });

  ipcMain.handle("dialog:save-file", async (_event, defaultPath) => {
    const result = await dialog.showSaveDialog(mainWindow, {
      defaultPath: defaultPath || undefined,
    });
    if (result.canceled || !result.filePath) return null;
    rememberApprovedRoot(path.dirname(result.filePath));
    return result.filePath;
  });

  ipcMain.handle("fs:read-tree", async (_event, rootDir) => ({
    root: assertApprovedPath(rootDir),
    children: await readDirTree(assertApprovedPath(rootDir)),
  }));

  ipcMain.handle("fs:list-files", async (_event, rootDir) => {
    const children = await readDirTree(assertApprovedPath(rootDir));
    return flattenFiles(children);
  });

  ipcMain.handle("fs:read-file", async (_event, filePath) => {
    const approved = assertApprovedPath(filePath);
    const content = await fs.readFile(approved, "utf8");
    return { path: approved, content };
  });

  ipcMain.handle("fs:write-file", async (_event, filePath, content) => {
    const approved = assertApprovedPath(filePath);
    await fs.mkdir(path.dirname(approved), { recursive: true });
    await fs.writeFile(approved, content, "utf8");
    return { ok: true, path: approved };
  });

  ipcMain.handle("fs:exists", async (_event, filePath) => {
    try {
      await fs.access(assertApprovedPath(filePath));
      return true;
    } catch (_) {
      return false;
    }
  });

  ipcMain.handle("fs:stat", async (_event, filePath) => {
    try {
      const st = await fs.stat(assertApprovedPath(filePath));
      return {
        exists: true,
        isFile: st.isFile(),
        isDirectory: st.isDirectory(),
        size: st.size,
        mtimeMs: st.mtimeMs,
      };
    } catch (_) {
      return { exists: false };
    }
  });

  ipcMain.handle("path:basename", (_event, filePath) => path.basename(filePath));
  ipcMain.handle("path:dirname", (_event, filePath) => path.dirname(filePath));
  ipcMain.handle("path:join", (_event, ...parts) => path.join(...parts));
  ipcMain.handle("path:relative", (_event, from, to) => path.relative(from, to));
  ipcMain.handle("path:normalize", (_event, filePath) => path.normalize(filePath));

  ipcMain.handle("agent:status", async () => getAgentStatus());
  ipcMain.handle("agent:start", async () => startAgentProcess());
  ipcMain.handle("agent:stop", async () => {
    const result = stopAgentProcess();
    // Give the port a moment to free if we killed our process
    await new Promise((r) => setTimeout(r, 400));
    return { ...result, ...(await getAgentStatus()) };
  });
}

app.whenReady().then(() => {
  registerIpc();
  createWindow();
  configureSecureUpdates();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("before-quit", () => {
  stopAgentProcess();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
