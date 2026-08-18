(() => {
  "use strict";

  const api = window.agentDesktop;

  const els = {
    app: document.getElementById("app"),
    windowTitle: document.getElementById("windowTitle"),
    fileTree: document.getElementById("fileTree"),
    explorerRootName: document.getElementById("explorerRootName"),
    tabs: document.getElementById("tabs"),
    breadcrumbs: document.getElementById("breadcrumbs"),
    monacoHost: document.getElementById("monacoHost"),
    editorHosts: document.getElementById("editorHosts"),
    monacoDiffHost: document.getElementById("monacoDiffHost"),
    diffMonaco: document.getElementById("diffMonaco"),
    diffTitle: document.getElementById("diffTitle"),
    btnCloseDiff: document.getElementById("btnCloseDiff"),
    btnAcceptDiff: document.getElementById("btnAcceptDiff"),
    btnRejectDiff: document.getElementById("btnRejectDiff"),
    previewPane: document.getElementById("previewPane"),
    previewScreen: document.getElementById("previewScreen"),
    previewMeta: document.getElementById("previewMeta"),
    sashPreview: document.getElementById("sashPreview"),
    btnRefreshPreview: document.getElementById("btnRefreshPreview"),
    btnClosePreview: document.getElementById("btnClosePreview"),
    emptyState: document.getElementById("emptyState"),
    welcomeRecent: document.getElementById("welcomeRecent"),
    btnWelcomeOpen: document.getElementById("btnWelcomeOpen"),
    btnWelcomeNewProject: document.getElementById("btnWelcomeNewProject"),
    btnWelcomeConnect: document.getElementById("btnWelcomeConnect"),
    focusSwitch: document.getElementById("focusSwitch"),
    sidebar: document.getElementById("sidebar"),
    aiPane: document.getElementById("aiPane"),
    sashSidebar: document.getElementById("sashSidebar"),
    sashAi: document.getElementById("sashAi"),
    paletteOverlay: document.getElementById("paletteOverlay"),
    paletteInput: document.getElementById("paletteInput"),
    paletteList: document.getElementById("paletteList"),
    statusCursor: document.getElementById("statusCursor"),
    statusLang: document.getElementById("statusLang"),
    statusErrors: document.getElementById("statusErrors"),
    toast: document.getElementById("toast"),
    btnNewFile: document.getElementById("btnNewFile"),
    btnRefreshTree: document.getElementById("btnRefreshTree"),
    btnCollapseTree: document.getElementById("btnCollapseTree"),
    btnCommandPalette: document.getElementById("btnCommandPalette"),
    btnToggleAiActivity: document.getElementById("btnToggleAiActivity"),
  };

  /** @type {any} */
  let monaco;
  /** @type {any} */
  let editor = null;

  const state = {
    root: null,
    tabs: [],
    activeId: null,
    nextId: 1,
    fileIndex: [],
    sidebarCollapsed: false,
    aiCollapsed: false,
    paletteMode: "command", // command | file
    paletteItems: [],
    paletteIndex: 0,
    previewOpen: false,
    previewPinned: false,
    previewTimer: null,
    previewStrings: {},
    previewStringsPath: null,
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
    properties: "ini",
    toml: "ini",
  };

  const COMMANDS = [
    { id: "open-folder", label: "文件: 打开文件夹", run: () => openFolderDialog() },
    { id: "open-file", label: "文件: 打开文件", run: () => openFileDialog() },
    { id: "new-file", label: "文件: 新建文件", run: () => createTab({ content: "", title: "未命名" }) },
    { id: "save", label: "文件: 保存", run: () => saveActive() },
    { id: "save-as", label: "文件: 另存为", run: () => saveActive({ saveAs: true }) },
    { id: "save-all", label: "文件: 全部保存", run: () => saveAll() },
    { id: "close-tab", label: "文件: 关闭编辑器", run: () => closeTab(state.activeId) },
    { id: "quick-open", label: "转到文件…", run: () => openPalette("file") },
    { id: "toggle-sidebar", label: "查看: 切换侧边栏", run: () => toggleSidebar() },
    { id: "toggle-ai", label: "查看: 切换 AI 面板", run: () => toggleAi() },
    { id: "toggle-preview", label: "查看: 切换布局预览", run: () => togglePreview() },
    { id: "refresh-preview", label: "查看: 刷新布局预览", run: () => refreshPreview({ force: true }) },
    { id: "refresh-tree", label: "资源管理器: 刷新", run: () => refreshTree() },
    { id: "ai-settings", label: "Agent: 连接设置", run: () => window.AiPanel?.openSettings() },
    { id: "ai-focus", label: "Agent: 聚焦对话输入", run: () => { showAi(); window.AiPanel?.focusComposer(); } },
    { id: "new-project", label: "Agent: 新建项目", run: () => window.AiPanel?.openCreateProject() },
  ];

  function languageForPath(filePath) {
    if (!filePath) return "plaintext";
    const base = filePath.split(/[/\\]/).pop() || "";
    if (base.endsWith(".gradle.kts") || base.endsWith(".kts")) return "kotlin";
    const ext = base.includes(".") ? base.split(".").pop().toLowerCase() : "";
    return LANGUAGE_BY_EXT[ext] || "plaintext";
  }

  function languageLabel(id) {
    const map = {
      javascript: "JavaScript",
      typescript: "TypeScript",
      kotlin: "Kotlin",
      java: "Java",
      xml: "XML",
      json: "JSON",
      markdown: "Markdown",
      python: "Python",
      groovy: "Groovy",
      yaml: "YAML",
      shell: "Shell",
      html: "HTML",
      css: "CSS",
      plaintext: "Plain Text",
      ini: "INI",
    };
    return map[id] || id;
  }

  function iconClassForName(name, isDir) {
    if (isDir) return "dir";
    if (name.endsWith(".kt") || name.endsWith(".kts")) return "kt";
    if (name.endsWith(".java")) return "java";
    if (name.endsWith(".xml")) return "xml";
    if (name.endsWith(".gradle") || name.includes("gradle")) return "gradle";
    if (name.endsWith(".json")) return "json";
    if (name.endsWith(".md")) return "md";
    return "";
  }

  function iconGlyph(name, isDir) {
    if (isDir) return "▾";
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (["kt", "kts", "java"].includes(ext)) return "K";
    if (ext === "xml") return "X";
    if (["json", "yaml", "yml"].includes(ext)) return "{}";
    if (ext === "md") return "M";
    return "·";
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      els.toast.hidden = true;
    }, 2400);
  }

  function activeTab() {
    return state.tabs.find((t) => t.id === state.activeId) || null;
  }

  function updateEmpty() {
    // The welcome overlay must never cover an open diff review.
    const diffOpen = els.monacoDiffHost && !els.monacoDiffHost.hidden;
    els.emptyState.classList.toggle("hidden", state.tabs.length > 0 || diffOpen);
  }

  function updateWindowTitle() {
    const tab = activeTab();
    if (tab) {
      const dirty = tab.dirty ? "● " : "";
      els.windowTitle.textContent = `${dirty}${tab.title}${state.root ? ` — ${basenameSync(state.root)}` : ""}`;
    } else if (state.root) {
      els.windowTitle.textContent = basenameSync(state.root);
    } else {
      els.windowTitle.textContent = "欢迎";
    }
  }

  function basenameSync(p) {
    return (p || "").split(/[/\\]/).filter(Boolean).pop() || p || "";
  }

  function renderTabs() {
    els.tabs.innerHTML = "";
    for (const tab of state.tabs) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "tab" +
        (tab.id === state.activeId ? " active" : "") +
        (tab.dirty ? " dirty" : "");
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
      btn.addEventListener("auxclick", (ev) => {
        if (ev.button === 1) {
          ev.preventDefault();
          closeTab(tab.id);
        }
      });
      els.tabs.appendChild(btn);
    }
    updateWindowTitle();
    renderBreadcrumbs();
    updateStatusFromEditor();
  }

  async function renderBreadcrumbs() {
    const tab = activeTab();
    els.breadcrumbs.innerHTML = "";
    if (!tab) {
      els.breadcrumbs.innerHTML = '<span class="crumb muted">打开文件开始编辑</span>';
      syncPreviewVisibility();
      return;
    }

    const appendToggle = () => {
      if (!window.LayoutPreview?.isLayoutPath?.(tab.path)) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ghost-btn sm btn-preview-toggle";
      btn.textContent = state.previewOpen ? "关闭预览" : "预览";
      btn.title = "切换 Android 布局预览";
      btn.addEventListener("click", () => togglePreview());
      els.breadcrumbs.appendChild(btn);
    };

    if (!tab.path || !state.root) {
      const crumb = document.createElement("span");
      crumb.className = "crumb";
      crumb.textContent = tab.title;
      els.breadcrumbs.appendChild(crumb);
      appendToggle();
      syncPreviewVisibility();
      return;
    }
    let rel = tab.path;
    try {
      rel = await api.relative(state.root, tab.path);
    } catch (_) {
      /* keep absolute */
    }
    const parts = rel.split(/[/\\]/).filter(Boolean);
    parts.forEach((part, i) => {
      if (i > 0) {
        const sep = document.createElement("span");
        sep.className = "crumb-sep";
        sep.textContent = "›";
        els.breadcrumbs.appendChild(sep);
      }
      const crumb = document.createElement("span");
      crumb.className = "crumb" + (i === parts.length - 1 ? "" : " muted");
      crumb.textContent = part;
      els.breadcrumbs.appendChild(crumb);
    });
    appendToggle();
    syncPreviewVisibility();
  }

  function syncPreviewVisibility() {
    const tab = activeTab();
    const isLayout = window.LayoutPreview?.isLayoutPath?.(tab?.path);
    if (!isLayout) {
      setPreviewOpen(false, { silent: true, skipRefresh: true });
      return;
    }
    if (!state.previewPinned && !state.previewOpen) {
      // Auto-open when opening a layout file (until user explicitly closes)
      state.previewOpen = true;
    }
    setPreviewOpen(state.previewOpen, { silent: true });
  }

  function setPreviewOpen(open, { silent = false, skipRefresh = false } = {}) {
    state.previewOpen = Boolean(open);
    els.previewPane.hidden = !state.previewOpen;
    els.sashPreview.hidden = !state.previewOpen;
    els.editorHosts?.classList.toggle("preview-open", state.previewOpen);
    if (state.previewOpen && !skipRefresh) schedulePreviewRefresh({ immediate: true });
    if (!silent) renderBreadcrumbs();
  }

  function togglePreview() {
    const tab = activeTab();
    if (!window.LayoutPreview?.isLayoutPath?.(tab?.path)) {
      toast("请先打开 res/layout 下的 XML 布局文件");
      return;
    }
    const next = !state.previewOpen;
    // Closing pins off auto-open; opening clears the pin
    state.previewPinned = !next;
    setPreviewOpen(next);
  }

  function schedulePreviewRefresh({ immediate = false, force = false } = {}) {
    if (state.previewTimer) {
      clearTimeout(state.previewTimer);
      state.previewTimer = null;
    }
    if (immediate) {
      refreshPreview({ force });
      return;
    }
    state.previewTimer = setTimeout(() => refreshPreview({ force }), 280);
  }

  function invalidatePreviewStrings(filePath) {
    if (!filePath) {
      state.previewStringsPath = null;
      state.previewStrings = {};
      return;
    }
    const norm = String(filePath).replace(/\\/g, "/");
    if (/\/res\/values[^/]*\/.+\.xml$/i.test(norm)) {
      state.previewStringsPath = null;
      state.previewStrings = {};
      if (state.previewOpen) schedulePreviewRefresh({ force: true });
    }
  }

  async function refreshPreview({ force = false } = {}) {
    if (!state.previewOpen || !els.previewScreen) return;
    const tab = activeTab();
    const tabId = tab?.id;
    const tabPath = tab?.path;
    if (!window.LayoutPreview?.isLayoutPath?.(tabPath)) {
      els.previewScreen.innerHTML = "";
      els.previewMeta.textContent = "当前不是布局文件";
      return;
    }

    try {
      if (force || state.previewStringsPath !== tabPath) {
        const strings = await window.LayoutPreview.loadStringsNearLayout(
          tabPath,
          (p) => api.readFile(p),
          (...parts) => api.joinPath(...parts),
        );
        if (activeTab()?.id !== tabId) return;
        state.previewStrings = strings;
        state.previewStringsPath = tabPath;
      }
      if (activeTab()?.id !== tabId) return;
      const xml = tab.model.getValue();
      const result = window.LayoutPreview.renderXml(xml, { strings: state.previewStrings });
      if (activeTab()?.id !== tabId) return;
      els.previewScreen.innerHTML = "";
      if (!result.ok) {
        const err = document.createElement("div");
        err.className = "ap-unknown";
        err.style.margin = "12px";
        err.textContent = result.error || "预览失败";
        els.previewScreen.appendChild(err);
        els.previewMeta.textContent = "XML 解析失败";
        return;
      }
      if (result.root) els.previewScreen.appendChild(result.root);
      const warns = result.warnings || [];
      els.previewMeta.textContent = warns.length
        ? `近似预览 · ${warns.slice(0, 2).join("；")}`
        : "近似预览 · 编辑后自动刷新";
    } catch (err) {
      if (activeTab()?.id !== tabId) return;
      els.previewScreen.innerHTML = "";
      els.previewMeta.textContent = err.message || "预览失败";
    }
  }

  function syncEditorFromTab(tab) {
    if (!editor || !tab) return;
    editor.setModel(tab.model);
    if (tab.viewState) editor.restoreViewState(tab.viewState);
    editor.focus();
    updateStatusFromEditor();
  }

  function activateTab(id) {
    const current = activeTab();
    if (current && editor) current.viewState = editor.saveViewState();
    state.activeId = id;
    const tab = activeTab();
    renderTabs();
    syncEditorFromTab(tab);
    window.dispatchEvent(new CustomEvent("editor-tab-changed", { detail: { tab } }));
    updateEmpty();
    window.AiPanel?.onActiveFileChanged(tab);
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
      title: title || (path ? basenameSync(path) : "未命名"),
      dirty: false,
      model,
      viewState: null,
      savedVersionId: model.getAlternativeVersionId(),
    };
    model.onDidChangeContent(() => {
      tab.dirty = model.getAlternativeVersionId() !== tab.savedVersionId;
      renderTabs();
      if (tab.id === state.activeId && window.LayoutPreview?.isLayoutPath?.(tab.path)) {
        schedulePreviewRefresh();
      }
    });
    state.tabs.push(tab);
    activateTab(id);
    return tab;
  }

  async function openPath(filePath, { reveal = true } = {}, line = 0) {
    const existing = state.tabs.find((t) => t.path === filePath);
    if (existing) {
      activateTab(existing.id);
      if (line && editor) {
        editor.revealLineInCenter(line);
        editor.setPosition({ lineNumber: line, column: 1 });
      }
      return existing;
    }
    const data = await api.readFile(filePath);
    const title = await api.basename(filePath);
    const tab = createTab({ path: filePath, content: data.content, title });
    if (line && editor) {
      setTimeout(() => {
        editor.revealLineInCenter(line);
        editor.setPosition({ lineNumber: line, column: 1 });
      }, 50);
    }
    if (reveal) highlightTreePath(filePath);
    return tab;
  }

  async function reloadPathIfOpen(filePath) {
    invalidatePreviewStrings(filePath);
    const tab = state.tabs.find((t) => t.path === filePath);
    if (!tab) {
      // strings.xml may have changed while a layout tab is active
      if (state.previewOpen && window.LayoutPreview?.isLayoutPath?.(activeTab()?.path)) {
        schedulePreviewRefresh({ force: true });
      }
      return;
    }
    if (tab.dirty) return;
    const data = await api.readFile(filePath);
    const pos = editor && tab.id === state.activeId ? editor.getPosition() : null;
    tab.model.setValue(data.content);
    tab.savedVersionId = tab.model.getAlternativeVersionId();
    tab.dirty = false;
    if (pos && editor && tab.id === state.activeId) editor.setPosition(pos);
    renderTabs();
    if (tab.id === state.activeId && window.LayoutPreview?.isLayoutPath?.(tab.path)) {
      schedulePreviewRefresh({ immediate: true, force: true });
    }
  }

  async function closeTab(id) {
    if (id == null) return;
    const index = state.tabs.findIndex((t) => t.id === id);
    if (index < 0) return;
    const tab = state.tabs[index];
    if (tab.dirty) {
      const choice = window.confirm(
        `「${tab.title}」有未保存的更改。\n确定关闭而不保存吗？`,
      );
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
    window.AiPanel?.onActiveFileChanged(activeTab());
  }

  async function saveActive({ saveAs = false } = {}) {
    const tab = activeTab();
    if (!tab) return;
    let target = tab.path;
    if (saveAs || !target) {
      target = await api.saveFileDialog(
        tab.path || (state.root ? `${state.root}/untitled.txt` : undefined),
      );
      if (!target) return;
    }
    const content = tab.model.getValue();
    await api.writeFile(target, content);
    tab.path = target;
    tab.title = await api.basename(target);
    tab.savedVersionId = tab.model.getAlternativeVersionId();
    tab.dirty = false;
    monaco.editor.setModelLanguage(tab.model, languageForPath(target));
    renderTabs();
    toast("已保存");
    if (state.root) await refreshTree({ silent: true });
  }

  async function saveAll() {
    for (const tab of [...state.tabs]) {
      if (!tab.dirty) continue;
      state.activeId = tab.id;
      syncEditorFromTab(tab);
      await saveActive();
    }
  }

  function highlightTreePath(absPath) {
    document.querySelectorAll(".tree-item.active").forEach((el) => el.classList.remove("active"));
    if (!state.root || !absPath) return;
    api.relative(state.root, absPath).then((rel) => {
      const row = els.fileTree.querySelector(`.tree-item[data-path="${cssEscape(rel)}"]`);
      if (row) {
        row.classList.add("active");
        // expand parents
        let parent = row.parentElement;
        while (parent && parent !== els.fileTree) {
          if (parent.classList.contains("tree-children")) {
            parent.hidden = false;
            const prev = parent.previousElementSibling;
            if (prev?.classList.contains("tree-item")) {
              const twistie = prev.querySelector(".tree-twistie");
              if (twistie) twistie.textContent = "▾";
            }
          }
          parent = parent.parentElement;
        }
        row.scrollIntoView({ block: "nearest" });
      }
    });
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return CSS.escape(value);
    return String(value).replace(/"/g, '\\"');
  }

  function renderTreeNodes(nodes, container, root) {
    for (const node of nodes) {
      const row = document.createElement("div");
      row.className = "tree-item";
      row.dataset.path = node.path;
      row.dataset.type = node.type;

      if (node.type === "dir") {
        const kids = document.createElement("div");
        kids.className = "tree-children";
        kids.hidden = true;

        const twistie = document.createElement("span");
        twistie.className = "tree-twistie";
        twistie.textContent = "▸";

        const icon = document.createElement("span");
        icon.className = `tree-icon dir`;
        icon.textContent = "▸";

        const name = document.createElement("span");
        name.className = "tree-name";
        name.textContent = node.name;

        row.append(twistie, icon, name);
        row.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const open = kids.hidden;
          kids.hidden = !open;
          twistie.textContent = open ? "▾" : "▸";
          icon.textContent = open ? "▾" : "▸";
        });

        container.appendChild(row);
        container.appendChild(kids);
        if (node.children?.length) renderTreeNodes(node.children, kids, root);
      } else {
        const twistie = document.createElement("span");
        twistie.className = "tree-twistie";
        twistie.textContent = "";

        const icon = document.createElement("span");
        icon.className = `tree-icon ${iconClassForName(node.name, false)}`;
        icon.textContent = iconGlyph(node.name, false);

        const name = document.createElement("span");
        name.className = "tree-name";
        name.textContent = node.name;

        row.append(twistie, icon, name);
        row.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          document.querySelectorAll(".tree-item.active").forEach((el) => el.classList.remove("active"));
          row.classList.add("active");
          const abs = await api.joinPath(root, node.path);
          await openPath(abs, { reveal: false });
        });
        container.appendChild(row);
      }
    }
  }

  async function openFolder(rootDir) {
    if (!rootDir) return;
    state.root = rootDir;
    els.explorerRootName.textContent = basenameSync(rootDir);
    await refreshTree({ silent: true });
    updateWindowTitle();
    pushRecentWorkspace(rootDir);
    window.AiPanel?.onWorkspaceChanged(rootDir);
    toast(`已打开 ${basenameSync(rootDir)}`);
  }

  // —— Recent workspaces (welcome page "继续工作") ——

  const RECENT_KEY = "androidAgentDesktop.recentWorkspaces";

  function loadRecentWorkspaces() {
    try {
      const arr = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(arr) ? arr.filter((e) => e && typeof e.path === "string") : [];
    } catch (_) {
      return [];
    }
  }

  function pushRecentWorkspace(dir) {
    let list = loadRecentWorkspaces().filter((e) => e.path !== dir);
    list.unshift({ path: dir, at: Date.now() });
    list = list.slice(0, 6);
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(list));
    } catch (_) {
      /* storage unavailable */
    }
    renderWelcomeRecent();
  }

  function formatRecentTime(at) {
    if (!at) return "";
    const diff = Date.now() - at;
    if (diff < 60000) return "刚刚";
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
    const d = new Date(at);
    if (d.toDateString() === new Date().toDateString()) return "今天";
    return `${d.getMonth() + 1}/${d.getDate()}`;
  }

  function renderWelcomeRecent() {
    const host = els.welcomeRecent;
    if (!host) return;
    host.textContent = "";
    const list = loadRecentWorkspaces();
    if (!list.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "暂无最近项目";
      host.appendChild(p);
      return;
    }
    for (const entry of list) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "welcome-recent-item";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = basenameSync(entry.path);
      const desc = document.createElement("span");
      desc.className = "desc";
      desc.textContent = `${entry.path} · ${formatRecentTime(entry.at)}`;
      desc.title = entry.path;
      btn.append(name, desc);
      btn.addEventListener("click", () => openFolder(entry.path));
      host.appendChild(btn);
    }
  }

  async function refreshTree({ silent = false } = {}) {
    if (!state.root) return;
    const tree = await api.readTree(state.root);
    els.fileTree.innerHTML = "";
    renderTreeNodes(tree.children || [], els.fileTree, state.root);
    state.fileIndex = await api.listFiles(state.root);
    if (!silent) toast("资源管理器已刷新");
  }

  function collapseTree() {
    els.fileTree.querySelectorAll(".tree-children").forEach((el) => {
      el.hidden = true;
    });
    els.fileTree.querySelectorAll(".tree-item[data-type='dir'] .tree-twistie").forEach((el) => {
      el.textContent = "▸";
    });
  }

  async function openFolderDialog() {
    const dir = await api.openFolderDialog();
    if (dir) await openFolder(dir);
  }

  async function openFileDialog() {
    const file = await api.openFileDialog();
    if (file) await openPath(file);
  }

  function toggleSidebar() {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    els.sidebar.classList.toggle("collapsed", state.sidebarCollapsed);
    els.sashSidebar.style.display = state.sidebarCollapsed ? "none" : "";
  }

  function toggleAi() {
    state.aiCollapsed = !state.aiCollapsed;
    els.aiPane.classList.toggle("collapsed", state.aiCollapsed);
    els.sashAi.style.display = state.aiCollapsed ? "none" : "";
    els.btnToggleAiActivity.classList.toggle("active", !state.aiCollapsed);
  }

  function showAi() {
    if (state.aiCollapsed) toggleAi();
  }

  function updateStatusFromEditor() {
    const tab = activeTab();
    if (!tab || !editor) {
      els.statusCursor.textContent = "Ln —, Col —";
      els.statusLang.textContent = "Plain Text";
      return;
    }
    const pos = editor.getPosition();
    if (pos) {
      els.statusCursor.textContent = `Ln ${pos.lineNumber}, Col ${pos.column}`;
    }
    const lang = tab.model.getLanguageId();
    els.statusLang.textContent = languageLabel(lang);
  }

  /* —— Palette —— */
  function openPalette(mode = "command") {
    state.paletteMode = mode;
    els.paletteOverlay.hidden = false;
    els.paletteInput.value = "";
    els.paletteInput.placeholder =
      mode === "file" ? "搜索工作区文件…" : "输入命令…";
    renderPalette("");
    els.paletteInput.focus();
  }

  function closePalette() {
    els.paletteOverlay.hidden = true;
  }

  function fuzzyScore(query, text) {
    const q = query.toLowerCase();
    const t = text.toLowerCase();
    if (!q) return 1;
    if (t.includes(q)) return 100 - t.indexOf(q);
    let qi = 0;
    for (let i = 0; i < t.length && qi < q.length; i++) {
      if (t[i] === q[qi]) qi++;
    }
    return qi === q.length ? 10 : 0;
  }

  function renderPalette(query) {
    let items = [];
    if (state.paletteMode === "file") {
      items = (state.fileIndex || [])
        .map((p) => ({
          id: p,
          label: basenameSync(p),
          meta: p,
          score: Math.max(fuzzyScore(query, basenameSync(p)), fuzzyScore(query, p) * 0.8),
          run: async () => {
            const abs = await api.joinPath(state.root, p);
            await openPath(abs);
          },
        }))
        .filter((x) => x.score > 0 || !query)
        .sort((a, b) => b.score - a.score)
        .slice(0, 50);
    } else {
      items = COMMANDS.map((c) => ({
        id: c.id,
        label: c.label,
        meta: c.id,
        score: fuzzyScore(query, c.label),
        run: c.run,
      }))
        .filter((x) => x.score > 0 || !query)
        .sort((a, b) => b.score - a.score);
    }
    state.paletteItems = items;
    state.paletteIndex = 0;
    paintPaletteList();
  }

  function paintPaletteList() {
    els.paletteList.innerHTML = "";
    if (!state.paletteItems.length) {
      const li = document.createElement("li");
      li.className = "palette-item";
      li.textContent = "无匹配项";
      els.paletteList.appendChild(li);
      return;
    }
    state.paletteItems.forEach((item, i) => {
      const li = document.createElement("li");
      li.className = "palette-item" + (i === state.paletteIndex ? " active" : "");
      li.innerHTML = `<span>${escapeHtml(item.label)}</span><span class="meta">${escapeHtml(item.meta || "")}</span>`;
      li.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        runPaletteItem(item);
      });
      els.paletteList.appendChild(li);
    });
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function runPaletteItem(item) {
    closePalette();
    await item.run();
  }

  /* —— Resizable panes —— */
  function bindSashes() {
    const bind = (sash, cssVar, min, max) => {
      let startX = 0;
      let startW = 0;
      const onMove = (ev) => {
        const dx = ev.clientX - startX;
        let next = startW + (cssVar === "--ai-w" ? -dx : dx);
        next = Math.max(min, Math.min(max, next));
        document.documentElement.style.setProperty(cssVar, `${next}px`);
      };
      const onUp = () => {
        sash.classList.remove("dragging");
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      sash.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        startX = ev.clientX;
        startW = parseInt(getComputedStyle(document.documentElement).getPropertyValue(cssVar), 10);
        sash.classList.add("dragging");
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
      });
    };
    bind(els.sashSidebar, "--sidebar-w", 160, 480);
    bind(els.sashAi, "--ai-w", 360, 640);
    if (els.sashPreview) {
      const sash = els.sashPreview;
      let startX = 0;
      let startW = 0;
      const onMove = (ev) => {
        const dx = ev.clientX - startX;
        let next = startW - dx;
        next = Math.max(260, Math.min(520, next));
        document.documentElement.style.setProperty("--preview-w", `${next}px`);
      };
      const onUp = () => {
        sash.classList.remove("dragging");
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      sash.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        startX = ev.clientX;
        startW =
          parseInt(getComputedStyle(document.documentElement).getPropertyValue("--preview-w"), 10) ||
          els.previewPane.getBoundingClientRect().width ||
          360;
        sash.classList.add("dragging");
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
      });
    }
  }

  function bindUi() {
    els.btnNewFile.addEventListener("click", () => createTab({ content: "", title: "未命名" }));
    els.btnRefreshTree.addEventListener("click", () => refreshTree());
    els.btnCollapseTree.addEventListener("click", () => collapseTree());
    els.btnCommandPalette.addEventListener("click", () => openPalette("command"));
    els.btnToggleAiActivity.addEventListener("click", () => toggleAi());
    els.btnRefreshPreview?.addEventListener("click", () => refreshPreview({ force: true }));
    els.btnClosePreview?.addEventListener("click", () => {
      state.previewPinned = true;
      setPreviewOpen(false);
    });

    document.querySelectorAll(".activity-btn[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".activity-btn[data-view]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        if (btn.dataset.view === "search") openPalette("file");
        if (btn.dataset.view === "explorer" && state.sidebarCollapsed) toggleSidebar();
      });
    });

    els.paletteInput.addEventListener("input", () => renderPalette(els.paletteInput.value));
    els.paletteInput.addEventListener("keydown", async (ev) => {
      if (ev.key === "Escape") {
        closePalette();
        return;
      }
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        state.paletteIndex = Math.min(state.paletteIndex + 1, state.paletteItems.length - 1);
        paintPaletteList();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        state.paletteIndex = Math.max(state.paletteIndex - 1, 0);
        paintPaletteList();
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        const item = state.paletteItems[state.paletteIndex];
        if (item) await runPaletteItem(item);
      }
    });
    els.paletteOverlay.addEventListener("mousedown", (ev) => {
      if (ev.target === els.paletteOverlay) closePalette();
    });

    api.onMenu("open-folder", () => openFolderDialog());
    api.onMenu("open-file", () => openFileDialog());
    api.onMenu("new-file", () => createTab({ content: "", title: "未命名" }));
    api.onMenu("save", () => saveActive());
    api.onMenu("save-as", () => saveActive({ saveAs: true }));
    api.onMenu("save-all", () => saveAll());
    api.onMenu("close-tab", () => closeTab(state.activeId));
    api.onMenu("command-palette", () => openPalette("command"));
    api.onMenu("quick-open", () => openPalette("file"));
    api.onMenu("toggle-sidebar", () => toggleSidebar());
    api.onMenu("toggle-ai", () => toggleAi());

    // Welcome page actions + recent workspaces
    els.btnWelcomeOpen?.addEventListener("click", () => openFolderDialog());
    els.btnWelcomeNewProject?.addEventListener("click", () => window.AiPanel?.openCreateProject?.());
    els.btnWelcomeConnect?.addEventListener("click", () => window.AiPanel?.openSettings?.());
    renderWelcomeRecent();

    // Focus mode: 代码 / Agent / 审阅 (narrow-window single-pane switch)
    if (els.focusSwitch) {
      let focusMode = "code";
      const applyFocusMode = (mode, { quiet = false } = {}) => {
        focusMode = mode;
        els.focusSwitch.querySelectorAll(".focus-switch-btn").forEach((btn) => {
          const active = btn.dataset.mode === mode;
          btn.classList.toggle("active", active);
          btn.setAttribute("aria-selected", String(active));
        });
        document.body.dataset.focusMode = mode;
        const narrow = window.matchMedia("(max-width: 900px)").matches;
        if (mode === "agent") {
          els.aiPane.classList.remove("collapsed");
          if (narrow) els.sidebar.classList.add("collapsed");
          if (!quiet) window.AiPanel?.focusComposer?.();
        } else if (mode === "code") {
          if (narrow) els.aiPane.classList.add("collapsed");
          else els.aiPane.classList.remove("collapsed");
          els.sidebar.classList.remove("collapsed");
        } else {
          // 审阅: maximal editor/diff space
          els.aiPane.classList.add("collapsed");
          if (!narrow) els.sidebar.classList.remove("collapsed");
        }
      };
      els.focusSwitch.addEventListener("click", (ev) => {
        const btn = ev.target.closest(".focus-switch-btn");
        if (btn) applyFocusMode(btn.dataset.mode);
      });
      let resizeTimer = null;
      window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => applyFocusMode(focusMode, { quiet: true }), 150);
      });
      applyFocusMode("code", { quiet: true });
    }

    window.addEventListener("keydown", (ev) => {
      const mod = ev.metaKey || ev.ctrlKey;
      if (mod && ev.key.toLowerCase() === "s") {
        ev.preventDefault();
        if (ev.altKey) saveAll();
        else saveActive({ saveAs: ev.shiftKey });
      }
      if (mod && ev.key.toLowerCase() === "w") {
        ev.preventDefault();
        closeTab(state.activeId);
      }
      if (mod && ev.key.toLowerCase() === "p") {
        ev.preventDefault();
        openPalette(ev.shiftKey ? "command" : "file");
      }
      if (mod && ev.key.toLowerCase() === "b") {
        ev.preventDefault();
        toggleSidebar();
      }
      if (mod && ev.key.toLowerCase() === "l") {
        ev.preventDefault();
        toggleAi();
        if (!state.aiCollapsed) window.AiPanel?.focusComposer();
      }
      if (mod && ev.key.toLowerCase() === "o" && !ev.shiftKey) {
        ev.preventDefault();
        openFolderDialog();
      }
      if (ev.key === "Escape" && !els.paletteOverlay.hidden) {
        closePalette();
      }
    });

    bindSashes();
  }

  function bootMonaco() {
    const amdRequire = window.require;
    amdRequire.config({
      paths: { vs: "../node_modules/monaco-editor/min/vs" },
    });
    // file:// pages cannot create cross-file workers directly: the worker URL
    // would resolve against the filesystem root. Proxy through a blob URL so
    // Monaco's worker loads its chunks from the real vs/ directory.
    window.MonacoEnvironment = {
      getWorkerUrl() {
        const url = amdRequire.toUrl("vs/base/worker/workerMain.js");
        const base = url.replace(/workerMain\.js.*$/, "");
        return URL.createObjectURL(
          new Blob(
            [`self.MonacoEnvironment={baseUrl:'${base}'};importScripts('${url}');`],
            { type: "text/javascript" },
          ),
        );
      },
    };

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
        renderLineHighlight: "all",
        padding: { top: 8 },
        smoothScrolling: true,
        cursorBlinking: "smooth",
        bracketPairColorization: { enabled: true },
        guides: { bracketPairs: true, indentation: true },
        find: { addExtraSpaceOnTop: false },
      });

      editor.onDidChangeCursorPosition(() => updateStatusFromEditor());

      // monaco.editor.create() without a `model` option auto-creates an
      // anonymous plaintext model (inmemory://model/1). Once the first tab
      // calls setModel(), that default is orphaned but stays alive in
      // monaco.editor.getModels() forever. Detach and dispose it now.
      const defaultModel = editor.getModel();
      if (defaultModel) {
        editor.setModel(null);
        defaultModel.dispose();
      }

      bindUi();
      updateEmpty();

      let diffEditor = null;
      let diffOriginal = "";
      let diffModified = "";
      let diffPath = "";

      /** Dispose the diff editor AND its original/modified models (no leaks). */
      function disposeDiffEditor() {
        if (!diffEditor) return;
        let pair = null;
        try {
          pair = diffEditor.getModel();
        } catch (_) {}
        // Monaco requires the widget to release its model reference BEFORE
        // the TextModels are disposed, otherwise it logs
        // "TextModel got disposed before DiffEditorWidget model got reset".
        try {
          diffEditor.setModel(null);
        } catch (_) {}
        try {
          diffEditor.dispose();
        } catch (_) {}
        if (pair) {
          try {
            if (pair.original && !pair.original.isDisposed()) pair.original.dispose();
            if (pair.modified && !pair.modified.isDisposed()) pair.modified.dispose();
          } catch (_) {}
        }
        diffEditor = null;
      }

      function hideDiffNotice() {
        const notice = document.getElementById("diffNotice");
        if (notice) notice.hidden = true;
        els.diffMonaco.style.display = "";
      }

      function openDiff({ original, modified, path, language = "plaintext", title, review = false }) {
        const perf = window.DesktopPerf || {};
        const gate =
          typeof perf.shouldUseDiffNotice === "function"
            ? perf.shouldUseDiffNotice({ original, modified, path })
            : { notice: false };
        if (gate.notice) {
          const message =
            typeof perf.diffNoticeMessage === "function"
              ? perf.diffNoticeMessage({ reason: gate.reason, path })
              : "该文件无法以文本 Diff 显示。";
          openDiffNotice({
            title: title || (path ? `Diff: ${basenameSync(path)}` : "Diff"),
            message,
          });
          return;
        }
        els.monacoDiffHost.hidden = false;
        updateEmpty();
        hideDiffNotice();
        els.diffMonaco.style.display = "";
        // Review mode (checkpoint diffs) is read-only by contract: the
        // accept/reject actions do not apply and must not be offered.
        if (els.btnAcceptDiff) els.btnAcceptDiff.hidden = review;
        if (els.btnRejectDiff) els.btnRejectDiff.hidden = review;
        els.diffTitle.textContent =
          title || (path ? `Diff: ${basenameSync(path)}` : "Diff");
        diffOriginal = original || "";
        diffModified = modified || "";
        diffPath = path || "";
        // Release the previous editor + models before creating new ones so
        // reopening the same file never accumulates models.
        disposeDiffEditor();
        diffEditor = monaco.editor.createDiffEditor(els.diffMonaco, {
          theme: "vs-dark",
          automaticLayout: true,
          renderSideBySide: true,
          readOnly: true,
          scrollBeyondLastLine: false,
        });
        diffEditor.setModel({
          original: monaco.editor.createModel(diffOriginal, language),
          modified: monaco.editor.createModel(diffModified, language),
        });
      }

      /** Metadata-only notice (e.g. binary files) shown instead of a text diff. */
      function openDiffNotice({ title, message }) {
        els.monacoDiffHost.hidden = false;
        updateEmpty();
        els.diffTitle.textContent = title || "Diff";
        disposeDiffEditor();
        els.diffMonaco.style.display = "none";
        let notice = document.getElementById("diffNotice");
        if (!notice) {
          notice = document.createElement("div");
          notice.id = "diffNotice";
          notice.className = "diff-notice";
          els.monacoDiffHost.appendChild(notice);
        }
        notice.hidden = false;
        notice.textContent = "";
        const pre = document.createElement("pre");
        pre.className = "diff-notice-text";
        pre.textContent = message || "";
        notice.appendChild(pre);
      }

      function closeDiff() {
        els.monacoDiffHost.hidden = true;
        disposeDiffEditor();
        hideDiffNotice();
        updateEmpty();
      }

      function acceptDiff() {
        if (diffPath && diffModified !== undefined) {
          openPath(diffPath, diffModified);
        }
        closeDiff();
      }

      if (els.btnCloseDiff) els.btnCloseDiff.addEventListener("click", closeDiff);
      if (els.btnRejectDiff) els.btnRejectDiff.addEventListener("click", closeDiff);
      if (els.btnAcceptDiff) els.btnAcceptDiff.addEventListener("click", acceptDiff);

      // Expose editor bridge for AI panel
      window.EditorApp = {
        getRoot: () => state.root,
        getActiveTab: () => activeTab(),
        getSelection: () => {
          if (!editor) return null;
          const sel = editor.getSelection();
          const model = editor.getModel();
          if (!sel || !model) return null;
          const text = model.getValueInRange(sel);
          const tab = activeTab();
          return { path: tab?.path, text, startLine: sel.startLineNumber, endLine: sel.endLineNumber };
        },
        openPath,
        reloadPathIfOpen,
        refreshTree,
        openFolder,
        toast,
        showAi,
        toggleAi,
        toggleSidebar,
        togglePreview,
        refreshPreview,
        openDiff,
        openDiffNotice,
        closeDiff,
        acceptDiff,
        setStatus: (text) => {
          els.statusErrors.textContent = text;
        },
      };

      window.AiPanel?.init?.();

      const defaultWs = await api.getDefaultWorkspace();
      if (defaultWs) await openFolder(defaultWs);
    });
  }

  bootMonaco();
})();
