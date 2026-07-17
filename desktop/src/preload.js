const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("agentDesktop", {
  getDefaultWorkspace: () => ipcRenderer.invoke("app:get-default-workspace"),
  openFolderDialog: () => ipcRenderer.invoke("dialog:open-folder"),
  openFileDialog: () => ipcRenderer.invoke("dialog:open-file"),
  saveFileDialog: (defaultPath) => ipcRenderer.invoke("dialog:save-file", defaultPath),
  readTree: (rootDir) => ipcRenderer.invoke("fs:read-tree", rootDir),
  readFile: (filePath) => ipcRenderer.invoke("fs:read-file", filePath),
  writeFile: (filePath, content) => ipcRenderer.invoke("fs:write-file", filePath, content),
  basename: (filePath) => ipcRenderer.invoke("path:basename", filePath),
  joinPath: (...parts) => ipcRenderer.invoke("path:join", ...parts),
  onMenu: (channel, handler) => {
    const map = {
      "open-folder": "menu:open-folder",
      "open-file": "menu:open-file",
      "new-file": "menu:new-file",
      save: "menu:save",
      "save-as": "menu:save-as",
    };
    const ipcChannel = map[channel];
    if (!ipcChannel) return () => {};
    const listener = () => handler();
    ipcRenderer.on(ipcChannel, listener);
    return () => ipcRenderer.removeListener(ipcChannel, listener);
  },
});
