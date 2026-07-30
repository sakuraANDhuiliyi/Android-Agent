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

  async function newTerminal(shell) {
    const pid = projectId();
    if (!pid) {
      window.renderer?.toast("请先选择项目");
      return null;
    }
    ensureVisible();
    const id = createTerminalId();
    const term = new Terminal({
      fontFamily: "'SF Mono', Menlo, Monaco, Consolas, monospace",
      fontSize: 12,
      cursorBlink: true,
      theme: {
        background: "#1e1e1e",
        foreground: "#cccccc",
        cursor: "#cccccc",
      },
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
      const info = await api().createTerminal(pid, {
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
      watcher = api().watchTerminal(backendId, (msg) => {
        if (!alive) return;
        if (msg.kind === "output") {
          if (msg.seq) cursor = msg.seq;
          term.write(msg.data || "");
        } else if (msg.kind === "done") {
          term.write(`\r\n\x1b[1;33m[session ${msg.status}]\x1b[0m\r\n`);
          connected = false;
          updateTabState(id, false);
        }
      });
      connected = true;
    }

    term.onData((data) => {
      if (!backendId || !alive) return;
      api().terminalInput(backendId, data).catch(() => {});
    });

    term.onResize((size) => {
      if (!backendId || !alive) return;
      api().terminalResize(backendId, size.cols, size.rows).catch(() => {});
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
    });

    renderTabs();
    selectTerminal(id);
    return id;
  }

  function updateTabState(id, isConnected) {
    const tab = document.querySelector(`.terminal-tab[data-id="${id}"]`);
    if (tab) tab.classList.toggle("disconnected", !isConnected);
  }

  function renderTabs() {
    els.terminalTabs.innerHTML = "";
    for (const t of terminals.values()) {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "terminal-tab" + (t.id === activeId() ? " active" : "");
      tab.dataset.id = t.id;
      tab.title = `Terminal ${t.backendId || t.id.slice(-6)}`;
      tab.innerHTML = `<span class="title">${tab.title}</span><span class="close" title="关闭">×</span>`;
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
      t.el.classList.toggle("active", t.id === id);
    }
    window.DesktopState?.dispatch({ type: "SELECT_TERMINAL", terminalId: id });
    renderTabs();
    const t = terminals.get(id);
    if (t) {
      setTimeout(() => {
        try {
          t.fitAddon.fit();
          t.term.focus();
        } catch (_) {}
      }, 10);
    }
  }

  function closeTerminal(id) {
    const t = terminals.get(id);
    if (!t) return;
    t.alive = false;
    try {
      t.watcher?.close();
    } catch (_) {}
    if (t.backendId) {
      api().deleteTerminal(t.backendId).catch(() => {});
    }
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
    if (!t || !t.backendId) return;
    api().terminalInput(t.backendId, data).catch(() => {});
  }

  function resizeAll() {
    for (const t of terminals.values()) {
      try {
        t.fitAddon.fit();
        api().terminalResize(t.backendId, t.term.cols, t.term.rows).catch(() => {});
      } catch (_) {}
    }
  }

  if (els.btnNewTerminal) {
    els.btnNewTerminal.addEventListener("click", () => newTerminal());
  }

  window.TerminalManager = {
    new: newTerminal,
    close: closeTerminal,
    select: selectTerminal,
    input: inputToActive,
    resizeAll,
    fitAll,
  };

  // Resize on layout change
  window.addEventListener("resize", () => {
    setTimeout(resizeAll, 50);
  });
})();
