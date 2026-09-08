(() => {
  "use strict";

  const { Terminal } = window.Terminal;
  const { FitAddon } = window.FitAddon;
  const { WebLinksAddon } = window.WebLinksAddon;

  const els = {
    bottomPanel: document.getElementById("bottomPanel"),
    bottomTabs: document.getElementById("bottomTabs"),
    bottomContent: document.getElementById("bottomContent"),
    terminalTabs: document.getElementById("terminalTabs"),
    terminalStack: document.getElementById("terminalStack"),
    btnNewTerminal: document.getElementById("btnNewTerminal"),
  };

  const projectId = () => window.DesktopState?.getState()?.selectedProjectId || "";

  const api = () => window.AiPanel?.client || new window.AgentApi();

  const terminals = new Map();
  let nextId = 1;

  function terminalTheme() {
    if (window.ThemeManager?.getResolved?.() === "light") {
      return { background: "#ffffff", foreground: "#171a1f", cursor: "#6f49b1", selectionBackground: "#e7ddf5" };
    }
    return { background: "#121212", foreground: "#f5f5f5", cursor: "#b17fe8", selectionBackground: "#3d2a5c" };
  }

  function ensureVisible() {
    window.DesktopState?.dispatch({ type: "LAYOUT_BOTTOM_VIEW", view: "terminal" });
    els.bottomPanel.hidden = false;
  }

  function createTerminalId() {
    return `term-${Date.now()}-${nextId++}`;
  }

  function fitAll() {
    for (const t of terminals.values()) {
      try {
        t.fitAddon.fit();
      } catch (_) {}
    }
  }

  async function newTerminal(shell, existing = null) {
    const pid = projectId();
    if (!pid) {
      window.renderer?.toast("请先选择项目");
      return null;
    }
    ensureVisible();
    const id = createTerminalId();
    const source = api();
    const terminalApi = new window.AgentApi();
    terminalApi.configure({ baseUrl: source.baseUrl, token: source.token });
    const ownerMatches = () => api().baseUrl === terminalApi.baseUrl && api().token === terminalApi.token;
    const term = new Terminal({
      fontFamily: "'SF Mono', Menlo, Monaco, Consolas, monospace",
      fontSize: 12,
      cursorBlink: true,
      scrollback: 5000,
      theme: terminalTheme(),
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());

    const el = document.createElement("div");
    el.className = "terminal-instance";
    els.terminalStack.appendChild(el);

    term.open(el);
    try {
      fitAddon.fit();
    } catch (_) {}

    const cols = term.cols || 80;
    const rows = term.rows || 24;
    let backendId = null;
    let watcher = null;
    let cursor = 0;
    let connected = false;
    let alive = true;

    try {
      const info = existing || await terminalApi.createTerminal(pid, {
        shell: shell || "/bin/bash",
        cols,
        rows,
      });
      backendId = info.id;
      term.write(`\r\n\x1b[1;32mTerminal ${info.id.slice(0, 8)} started\x1b[0m\r\n`);
    } catch (err) {
      term.write(`\r\n\x1b[1;31mStart failed: ${err.message}\x1b[0m\r\n`);
    }

    if (backendId) {
      watcher = terminalApi.watchTerminal(backendId, (msg) => {
        if (!alive || !ownerMatches()) return;
        if (msg.kind === "output") {
          if (msg.seq) cursor = msg.seq;
          term.write(msg.data || "");
        } else if (msg.kind === "done") {
          term.write(`\r\n\x1b[1;33m[session ${msg.status}]\x1b[0m\r\n`);
          connected = false;
          updateTabState(id, false);
        } else if (msg.kind === "error") {
          term.write(`\r\n\x1b[1;31m[stream error] ${msg.error}\x1b[0m\r\n`);
          connected = false;
          updateTabState(id, false);
        }
      });
      connected = true;
    }

    term.onData((data) => {
      if (!backendId || !alive || !ownerMatches() || projectId() !== pid) return;
      terminalApi.terminalInput(backendId, data).catch((err) => window.renderer?.toast(err.message));
    });

    term.onResize((size) => {
      if (!backendId || !alive) return;
      if (ownerMatches()) terminalApi.terminalResize(backendId, size.cols, size.rows).catch(() => {});
    });

    terminals.set(id, {
      id,
      backendId,
      term,
      fitAddon,
      el,
      watcher,
      cursor,
      connected,
      alive,
      projectId: pid,
      terminalApi,
      ownerMatches,
      detach: () => { alive = false; watcher?.close(); },
    });

    renderTabs();
    if (projectId() === pid && ownerMatches()) selectTerminal(id);
    return id;
  }

  function updateTabState(id, isConnected) {
    const tab = document.querySelector(`.terminal-tab[data-id="${id}"]`);
    if (tab) tab.classList.toggle("disconnected", !isConnected);
  }

  function renderTabs() {
    els.terminalTabs.innerHTML = "";
    for (const t of terminals.values()) {
      if (t.projectId !== projectId() || !t.ownerMatches()) continue;
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "terminal-tab" + (t.id === activeId() ? " active" : "");
      tab.dataset.id = t.id;
      tab.title = `Terminal ${t.backendId || t.id.slice(-6)}`;
      const label = document.createElement("span");
      label.className = "title";
      label.textContent = tab.title;
      const close = document.createElement("span");
      close.className = "close";
      close.title = "关闭";
      close.textContent = "×";
      tab.append(label, close);
      tab.addEventListener("click", (e) => {
        if (e.target.classList.contains("close")) {
          closeTerminal(t.id);
        } else {
          selectTerminal(t.id);
        }
      });
      els.terminalTabs.appendChild(tab);
    }
  }

  function activeId() {
    return window.DesktopState?.getState()?.activeTerminalId || "";
  }

  function selectTerminal(id) {
    for (const t of terminals.values()) {
      t.el.classList.toggle("active", t.id === id && t.projectId === projectId() && t.ownerMatches());
    }
    window.DesktopState?.dispatch({ type: "SELECT_TERMINAL", terminalId: id });
    renderTabs();
    const t = terminals.get(id);
    if (t && t.projectId === projectId() && t.ownerMatches()) {
      setTimeout(() => {
        try {
          t.fitAddon.fit();
          t.term.focus();
        } catch (_) {}
      }, 10);
    }
  }

  async function closeTerminal(id) {
    const t = terminals.get(id);
    if (!t) return;
    if (t.backendId && t.ownerMatches()) {
      try { await t.terminalApi.deleteTerminal(t.backendId); }
      catch (err) { window.renderer?.toast(err.message); return; }
    }
    t.alive = false;
    t.detach();
    try {
      t.watcher?.close();
    } catch (_) {}
    try {
      t.term.dispose();
    } catch (_) {}
    t.el.remove();
    terminals.delete(id);
    const remaining = Array.from(terminals.keys());
    if (activeId() === id) {
      selectTerminal(remaining[0] || "");
    }
    renderTabs();
  }

  function inputToActive(data) {
    const id = activeId();
    if (!id) return;
    const t = terminals.get(id);
    if (!t || !t.backendId || t.projectId !== projectId() || !t.ownerMatches()) return;
    t.terminalApi.terminalInput(t.backendId, data).catch((err) => window.renderer?.toast(err.message));
  }

  function resizeAll() {
    for (const t of terminals.values()) {
      try {
        t.fitAddon.fit();
        if (t.ownerMatches()) t.terminalApi.terminalResize(t.backendId, t.term.cols, t.term.rows).catch(() => {});
      } catch (_) {}
    }
  }

  function setTheme() {
    const theme = terminalTheme();
    for (const terminal of terminals.values()) terminal.term.options.theme = theme;
  }

  if (els.btnNewTerminal) {
    els.btnNewTerminal.addEventListener("click", () => newTerminal());
  }

  function activeOutput() {
    const t = terminals.get(activeId());
    if (!t || t.projectId !== projectId() || !t.ownerMatches()) return null;
    const buffer = t.term.buffer.active;
    const lines = [];
    for (let i = Math.max(0, buffer.length - 300); i < buffer.length; i++) lines.push(buffer.getLine(i)?.translateToString(true) || "");
    return { terminal: t, text: (t.term.getSelection() || lines.join("\n")).slice(-16000) };
  }
  document.getElementById("btnStopTerminal")?.addEventListener("click", () => inputToActive("\x03"));
  document.getElementById("btnCopyTerminal")?.addEventListener("click", async () => {
    const value = activeOutput();
    if (value) try { await navigator.clipboard.writeText(value.text); } catch (err) { window.renderer?.toast(err.message); }
  });
  document.getElementById("btnAskTerminal")?.addEventListener("click", () => {
    const value = activeOutput();
    if (value?.text.trim()) window.AiPanel?.addTerminalContext(value.terminal.projectId, value.terminal.backendId, value.text);
  });
  document.getElementById("btnReconnectTerminals")?.addEventListener("click", async () => {
    const pid = projectId();
    if (!pid) return window.renderer?.toast("请先选择项目");
    try {
      const source = api();
      const baseUrl = source.baseUrl;
      const token = source.token;
      const response = await source.listTerminals(pid);
      if (projectId() !== pid || api().baseUrl !== baseUrl || api().token !== token) return;
      for (const session of response.terminals || []) {
        if (projectId() !== pid || api().baseUrl !== baseUrl || api().token !== token) break;
        if (![...terminals.values()].some((t) => t.backendId === session.id && t.ownerMatches())) await newTerminal(null, session);
      }
    } catch (err) { window.renderer?.toast(err.message); }
  });
  window.DesktopState?.subscribe((state, action) => {
    if (action.type !== "SELECT_PROJECT") return;
    const next = [...terminals.values()].find((t) => t.projectId === projectId() && t.ownerMatches());
    selectTerminal(next?.id || "");
  });

  window.TerminalManager = {
    new: newTerminal,
    close: closeTerminal,
    select: selectTerminal,
    input: inputToActive,
    resizeAll,
    fitAll,
    setTheme,
  };

  window.addEventListener("android-agent-theme-change", setTheme);

  // Resize on layout change
  window.addEventListener("resize", () => {
    setTimeout(resizeAll, 50);
  });
})();
