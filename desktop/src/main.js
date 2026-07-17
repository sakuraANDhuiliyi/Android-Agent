const { app, BrowserWindow, dialog, ipcMain, Menu } = require("electron");
const fs = require("fs/promises");
const path = require("path");

const IGNORE_NAMES = new Set([
  ".git",
  "node_modules",
  ".DS_Store",
  "__pycache__",
  ".gradle",
  "build",
]);

let mainWindow = null;

function repoRoot() {
  return path.resolve(__dirname, "..", "..");
}

function defaultWorkspace() {
  const workspaces = path.join(repoRoot(), "workspaces");
  try {
    if (require("fs").statSync(workspaces).isDirectory()) {
      return workspaces;
    }
  } catch (_) {
    /* ignore */
  }
  return null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: "Android Agent",
    backgroundColor: "#151920",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "index.html"));

  const template = [
    ...(process.platform === "darwin"
      ? [{ role: "appMenu" }]
      : []),
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
        { type: "separator" },
        process.platform === "darwin" ? { role: "close" } : { role: "quit" },
      ],
    },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function readDirTree(rootDir, rel = ".") {
  const abs = path.join(rootDir, rel);
  const entries = await fs.readdir(abs, { withFileTypes: true });
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
        children: await readDirTree(rootDir, childRel),
      });
    } else if (entry.isFile()) {
      nodes.push({ name: entry.name, path: childRel, type: "file" });
    }
  }
  return nodes;
}

function registerIpc() {
  ipcMain.handle("app:get-default-workspace", () => defaultWorkspace());

  ipcMain.handle("dialog:open-folder", async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"],
    });
    if (result.canceled || !result.filePaths[0]) {
      return null;
    }
    return result.filePaths[0];
  });

  ipcMain.handle("dialog:open-file", async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openFile"],
    });
    if (result.canceled || !result.filePaths[0]) {
      return null;
    }
    return result.filePaths[0];
  });

  ipcMain.handle("dialog:save-file", async (_event, defaultPath) => {
    const result = await dialog.showSaveDialog(mainWindow, {
      defaultPath: defaultPath || undefined,
    });
    if (result.canceled || !result.filePath) {
      return null;
    }
    return result.filePath;
  });

  ipcMain.handle("fs:read-tree", async (_event, rootDir) => {
    return {
      root: rootDir,
      children: await readDirTree(rootDir),
    };
  });

  ipcMain.handle("fs:read-file", async (_event, filePath) => {
    const content = await fs.readFile(filePath, "utf8");
    return { path: filePath, content };
  });

  ipcMain.handle("fs:write-file", async (_event, filePath, content) => {
    await fs.writeFile(filePath, content, "utf8");
    return { ok: true, path: filePath };
  });

  ipcMain.handle("path:basename", (_event, filePath) => path.basename(filePath));
  ipcMain.handle("path:join", (_event, ...parts) => path.join(...parts));
}

app.whenReady().then(() => {
  registerIpc();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
