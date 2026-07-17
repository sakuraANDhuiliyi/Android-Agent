(() => {
  "use strict";

  const api = window.agentDesktop;
  const els = {
    folderStatus: document.getElementById("folderStatus"),
    fileTree: document.getElementById("fileTree"),
    tabs: document.getElementById("tabs"),
    monacoHost: document.getElementById("monacoHost"),
    emptyState: document.getElementById("emptyState"),
    btnOpenFolder: document.getElementById("btnOpenFolder"),
    btnOpenFile: document.getElementById("btnOpenFile"),
    btnSave: document.getElementById("btnSave"),
  };

  /** @type {import('monaco-editor')} */
  let monaco;
  /** @type {import('monaco-editor').editor.IStandaloneCodeEditor | null} */
  let editor = null;

  const state = {
    root: null,
    tabs: [],
    activeId: null,
    nextId: 1,
  };

  const LANGUAGE_BY_EXT = {
    js: "javascript",
    jsx: "javascript",
    ts: "typescript",
    tsx: "typescript",
    json: "json",
    md: "markdown",
    py: "python",
    kt: "kotlin",
    kts: "kotlin",
    java: "java",
    xml: "xml",
    html: "html",
    css: "css",
    scss: "scss",
    gradle: "groovy",
    groovy: "groovy",
    yaml: "yaml",
    yml: "yaml",
    sh: "shell",
    bash: "shell",
    txt: "plaintext",
  };

  function languageForPath(filePath) {
    if (!filePath) return "plaintext";
    const base = filePath.split(/[/\\]/).pop() || "";
    if (base.endsWith(".gradle.kts") || base.endsWith(".kts")) return "kotlin";
    const ext = base.includes(".") ? base.split(".").pop().toLowerCase() : "";
    return LANGUAGE_BY_EXT[ext] || "plaintext";
  }

  function activeTab() {
    return state.tabs.find((t) => t.id === state.activeId) || null;
  }

  function updateEmpty() {
    els.emptyState.classList.toggle("hidden", state.tabs.length > 0);
  }

  function renderTabs() {
    els.tabs.innerHTML = "";
    for (const tab of state.tabs) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tab" + (tab.id === state.activeId ? " active" : "") + (tab.dirty ? " dirty" : "");
      btn.title = tab.path || "未命名";

      const name = document.createElement("span");
      name.className = "name";
      name.textContent = tab.title;
      btn.appendChild(name);

      const close = document.createElement("span");
      close.className = "close";
      close.textContent = "×";
      close.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeTab(tab.id);
      });
      btn.appendChild(close);

      btn.addEventListener("click", () => activateTab(tab.id));
      els.tabs.appendChild(btn);
    }
  }

  function syncEditorFromTab(tab) {
    if (!editor || !tab) return;
    const model = tab.model;
    editor.setModel(model);
    if (tab.viewState) {
      editor.restoreViewState(tab.viewState);
    }
    editor.focus();
  }

  function activateTab(id) {
    const current = activeTab();
    if (current && editor) {
      current.viewState = editor.saveViewState();
    }
    state.activeId = id;
    const tab = activeTab();
    renderTabs();
    syncEditorFromTab(tab);
    updateEmpty();
  }

  function createTab({ path, content, title }) {
    const id = state.nextId++;
    const uri = path
      ? monaco.Uri.file(path)
      : monaco.Uri.parse(`untitled:untitled-${id}`);
    const existing = monaco.editor.getModel(uri);
    if (existing) existing.dispose();
    const model = monaco.editor.createModel(content || "", languageForPath(path), uri);
    const tab = {
      id,
      path: path || null,
      title: title || (path ? path.split(/[/\\]/).pop() : "未命名"),
      dirty: false,
      model,
      viewState: null,
    };
    model.onDidChangeContent(() => {
      tab.dirty = true;
      renderTabs();
    });
    state.tabs.push(tab);
    activateTab(id);
    return tab;
  }

  async function openPath(filePath) {
    const existing = state.tabs.find((t) => t.path === filePath);
    if (existing) {
      activateTab(existing.id);
      return;
    }
    const data = await api.readFile(filePath);
    const title = await api.basename(filePath);
    createTab({ path: filePath, content: data.content, title });
  }

  async function closeTab(id) {
    const index = state.tabs.findIndex((t) => t.id === id);
    if (index < 0) return;
    const tab = state.tabs[index];
    if (tab.dirty) {
      const choice = window.confirm(`「${tab.title}」有未保存的更改。\n确定关闭而不保存吗？\n（点取消可先去保存）`);
      if (!choice) return;
    }
    tab.model.dispose();
    state.tabs.splice(index, 1);
    if (state.activeId === id) {
      const next = state.tabs[index] || state.tabs[index - 1] || null;
      state.activeId = next ? next.id : null;
      if (next) syncEditorFromTab(next);
      else if (editor) editor.setModel(null);
    }
    renderTabs();
    updateEmpty();
  }

  async function saveActive({ saveAs = false } = {}) {
    const tab = activeTab();
    if (!tab) return;
    let target = tab.path;
    if (saveAs || !target) {
      target = await api.saveFileDialog(tab.path || (state.root ? `${state.root}/untitled.txt` : undefined));
      if (!target) return;
    }
    const content = tab.model.getValue();
    await api.writeFile(target, content);
    tab.path = target;
    tab.title = await api.basename(target);
    tab.dirty = false;
    // Remap model language if extension changed
    monaco.editor.setModelLanguage(tab.model, languageForPath(target));
    renderTabs();
  }

  function renderTreeNodes(nodes, container, root) {
    for (const node of nodes) {
      const row = document.createElement("div");
      row.className = "tree-item";
      row.dataset.path = node.path;

      if (node.type === "dir") {
        const kids = document.createElement("div");
        kids.className = "tree-children";
        kids.hidden = true;

        const twistie = document.createElement("span");
        twistie.className = "tree-twistie";
        twistie.textContent = "▸";

        const icon = document.createElement("span");
        icon.className = "tree-icon";
        icon.textContent = "📁";

        const name = document.createElement("span");
        name.className = "tree-name";
        name.textContent = node.name;

        row.append(twistie, icon, name);
        row.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const open = kids.hidden;
          kids.hidden = !open;
          twistie.textContent = open ? "▾" : "▸";
        });

        container.appendChild(row);
        container.appendChild(kids);
        if (node.children?.length) {
          renderTreeNodes(node.children, kids, root);
        }
      } else {
        const twistie = document.createElement("span");
        twistie.className = "tree-twistie";
        twistie.textContent = "";

        const icon = document.createElement("span");
        icon.className = "tree-icon";
        icon.textContent = "📄";

        const name = document.createElement("span");
        name.className = "tree-name";
        name.textContent = node.name;

        row.append(twistie, icon, name);
        row.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          document.querySelectorAll(".tree-item.active").forEach((el) => el.classList.remove("active"));
          row.classList.add("active");
          const abs = await api.joinPath(root, node.path);
          await openPath(abs);
        });
        container.appendChild(row);
      }
    }
  }

  async function openFolder(rootDir) {
    if (!rootDir) return;
    state.root = rootDir;
    els.folderStatus.textContent = rootDir;
    const tree = await api.readTree(rootDir);
    els.fileTree.innerHTML = "";
    renderTreeNodes(tree.children || [], els.fileTree, rootDir);
  }

  function bindUi() {
    els.btnOpenFolder.addEventListener("click", async () => {
      const dir = await api.openFolderDialog();
      if (dir) await openFolder(dir);
    });
    els.btnOpenFile.addEventListener("click", async () => {
      const file = await api.openFileDialog();
      if (file) await openPath(file);
    });
    els.btnSave.addEventListener("click", () => saveActive());

    api.onMenu("open-folder", async () => {
      const dir = await api.openFolderDialog();
      if (dir) await openFolder(dir);
    });
    api.onMenu("open-file", async () => {
      const file = await api.openFileDialog();
      if (file) await openPath(file);
    });
    api.onMenu("new-file", () => createTab({ content: "", title: "未命名" }));
    api.onMenu("save", () => saveActive());
    api.onMenu("save-as", () => saveActive({ saveAs: true }));

    window.addEventListener("keydown", (ev) => {
      const mod = ev.metaKey || ev.ctrlKey;
      if (mod && ev.key.toLowerCase() === "s") {
        ev.preventDefault();
        saveActive({ saveAs: ev.shiftKey });
      }
    });
  }

  function bootMonaco() {
    const amdRequire = window.require;
    amdRequire.config({
      paths: {
        vs: "../node_modules/monaco-editor/min/vs",
      },
    });

    amdRequire(["vs/editor/editor.main"], async () => {
      monaco = window.monaco;
      editor = monaco.editor.create(els.monacoHost, {
        theme: "vs-dark",
        automaticLayout: true,
        fontSize: 13,
        fontFamily: 'Menlo, Monaco, "SF Mono", Consolas, monospace',
        minimap: { enabled: true },
        scrollBeyondLastLine: false,
        tabSize: 4,
        renderLineHighlight: "line",
        padding: { top: 8 },
      });

      bindUi();
      updateEmpty();

      const defaultWs = await api.getDefaultWorkspace();
      if (defaultWs) {
        await openFolder(defaultWs);
      }
    });
  }

  bootMonaco();
})();
