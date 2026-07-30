const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("agentDesktop", {
  confirmDownload: (payload) => ipcRenderer.invoke("dialog:confirm-download", payload),
  getCredential: (baseUrl) => ipcRenderer.invoke("credentials:get", baseUrl),
  setCredential: (baseUrl, token) =>
    ipcRenderer.invoke("credentials:set", baseUrl, token),
  getDefaultWorkspace: () => ipcRenderer.invoke("app:get-default-workspace"),
  openFolderDialog: () => ipcRenderer.invoke("dialog:open-folder"),
  openFileDialog: () => ipcRenderer.invoke("dialog:open-file"),
  saveFileDialog: (defaultPath) => ipcRenderer.invoke("dialog:save-file", defaultPath),
  readTree: (rootDir) => ipcRenderer.invoke("fs:read-tree", rootDir),
  listFiles: (rootDir) => ipcRenderer.invoke("fs:list-files", rootDir),
  readFile: (filePath) => ipcRenderer.invoke("fs:read-file", filePath),
  writeFile: (filePath, content) => ipcRenderer.invoke("fs:write-file", filePath, content),
  exists: (filePath) => ipcRenderer.invoke("fs:exists", filePath),
  stat: (filePath) => ipcRenderer.invoke("fs:stat", filePath),
  basename: (filePath) => ipcRenderer.invoke("path:basename", filePath),
  dirname: (filePath) => ipcRenderer.invoke("path:dirname", filePath),
  joinPath: (...parts) => ipcRenderer.invoke("path:join", ...parts),
  relative: (from, to) => ipcRenderer.invoke("path:relative", from, to),
  normalize: (filePath) => ipcRenderer.invoke("path:normalize", filePath),
  agentStatus: () => ipcRenderer.invoke("agent:status"),
  agentStart: () => ipcRenderer.invoke("agent:start"),
  agentStop: () => ipcRenderer.invoke("agent:stop"),
  onAgentServerExit: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("agent:server-exit", listener);
    return () => ipcRenderer.removeListener("agent:server-exit", listener);
  },
  onMenu: (channel, handler) => {
    const map = {
      "open-folder": "menu:open-folder",
      "open-file": "menu:open-file",
      "new-file": "menu:new-file",
      save: "menu:save",
      "save-as": "menu:save-as",
      "save-all": "menu:save-all",
      "close-tab": "menu:close-tab",
      "command-palette": "menu:command-palette",
      "quick-open": "menu:quick-open",
      "toggle-sidebar": "menu:toggle-sidebar",
      "toggle-ai": "menu:toggle-ai",
    };
    const ipcChannel = map[channel];
    if (!ipcChannel) return () => {};
    const listener = () => handler();
    ipcRenderer.on(ipcChannel, listener);
    return () => ipcRenderer.removeListener(ipcChannel, listener);
  },
});
