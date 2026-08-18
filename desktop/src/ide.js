(() => {
  "use strict";

  const state = () => window.DesktopState?.getState() || {};
  const dispatch = (action) => window.DesktopState?.dispatch?.(action);
  const ai = () => window.AiPanel || {};
  const api = () => ai().client || new window.AgentApi();
  const editor = () => window.EditorApp || {};

  const els = {
    sidebarTabs: document.getElementById("sidebarTabs"),
    sidebarContent: document.getElementById("sidebarContent"),
    conversationList: document.getElementById("conversationList"),
    jobList: document.getElementById("jobList"),
    searchInput: document.getElementById("searchInput"),
    searchResults: document.getElementById("searchResults"),
    btnSearch: document.getElementById("btnSearch"),
    btnRefreshJobs: document.getElementById("btnRefreshJobs"),
    btnArchiveConversation: document.getElementById("btnArchiveConversation"),
    bottomTabs: document.getElementById("bottomTabs"),
    bottomContent: document.getElementById("bottomContent"),
    bottomPanel: document.getElementById("bottomPanel"),
    bottomResize: document.getElementById("bottomResize"),
    problemList: document.getElementById("problemList"),
    outputLog: document.getElementById("outputLog"),
    buildLog: document.getElementById("buildLog"),
    renameConversationDialog: document.getElementById("renameConversationDialog"),
    renameConversationForm: document.getElementById("renameConversationForm"),
    renameConversationId: document.getElementById("renameConversationId"),
    renameConversationTitle: document.getElementById("renameConversationTitle"),
  };

  // —— Sidebar tabs ——
  function initSidebarTabs() {
    if (!els.sidebarTabs) return;
    els.sidebarTabs.addEventListener("click", (e) => {
      const tab = e.target.closest(".sidebar-tab");
      if (!tab) return;
      const view = tab.dataset.view;
      dispatch({ type: "LAYOUT_SIDEBAR_VIEW", view });
      renderSidebarTabs();
      renderSidebarView();
    });
  }

  function renderSidebarTabs() {
    if (!els.sidebarTabs) return;
    const view = state().sidebarView || "explorer";
    for (const tab of els.sidebarTabs.querySelectorAll(".sidebar-tab")) {
      tab.classList.toggle("active", tab.dataset.view === view);
    }
  }

  function renderSidebarView() {
    if (!els.sidebarContent) return;
    const view = state().sidebarView || "explorer";
    for (const v of els.sidebarContent.querySelectorAll(".sidebar-view")) {
      v.hidden = v.dataset.view !== view;
      v.classList.toggle("active", v.dataset.view === view);
    }
    if (view === "conversations") renderConversationList();
    if (view === "jobs") renderJobList();
  }

  // —— Conversations ——
  function renderConversationList() {
    if (!els.conversationList) return;
    const s = ai().getState?.() || {};
    const list = s.conversations || [];
    const current = s.conversationId;
    els.conversationList.innerHTML = "";
    if (!list.length) {
      els.conversationList.innerHTML = '<div class="muted" style="padding:10px">暂无对话</div>';
      return;
    }
    for (const conv of list) {
      const item = document.createElement("div");
      item.className = "conversation-item" + (conv.id === current ? " active" : "");
      item.innerHTML = `<span class="title">${escapeHtml(conv.title || conv.id)}</span>
        <div class="actions">
          <button class="icon-btn" title="重命名" data-action="rename" data-id="${conv.id}">✎</button>
          <button class="icon-btn" title="归档" data-action="archive" data-id="${conv.id}">▤</button>
        </div>`;
      item.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-action]");
        if (btn) {
          const action = btn.dataset.action;
          const id = btn.dataset.id;
          if (action === "rename") openRenameConversation(id, conv.title);
          if (action === "archive") archiveConversation(id);
        } else {
          selectConversation(conv.id);
        }
      });
      els.conversationList.appendChild(item);
    }
  }

  function openRenameConversation(id, title) {
    if (!els.renameConversationDialog) return;
    els.renameConversationId.value = id;
    els.renameConversationTitle.value = title || "";
    els.renameConversationDialog.showModal();
  }

  async function onRenameSubmit(form) {
    if (!form || form.dataset.submitted) return;
    form.dataset.submitted = "1";
    const id = els.renameConversationId.value;
    const title = els.renameConversationTitle.value.trim();
    if (id && title) {
      try {
        await api().renameConversation(id, title);
        const s = ai().getState?.() || {};
        const updated = (s.conversations || []).map((c) => (c.id === id ? { ...c, title } : c));
        ai().dispatch?.({ patch: { conversations: updated } });
        renderConversationList();
      } catch (err) {
        editor().toast?.(`重命名失败: ${err.message}`);
      }
    }
    setTimeout(() => delete form.dataset.submitted, 100);
  }

  async function archiveConversation(id) {
    try {
      await api().archiveConversation(id);
      const s = ai().getState?.() || {};
      const conv = (s.conversations || []).find((c) => c.id === id);
      if (conv) {
        ai().dispatch?.({
          patch: {
            conversations: (s.conversations || []).filter((c) => c.id !== id),
            archivedConversations: [...(s.archivedConversations || []), conv],
          },
        });
      }
      renderConversationList();
    } catch (err) {
      editor().toast?.(`归档失败: ${err.message}`);
    }
  }

  async function restoreConversation(id) {
    try {
      await api().restoreConversation(id);
      const s = ai().getState?.() || {};
      const conv = (s.archivedConversations || []).find((c) => c.id === id);
      if (conv) {
        ai().dispatch?.({
          patch: {
            conversations: [conv, ...(s.conversations || [])],
            archivedConversations: (s.archivedConversations || []).filter((c) => c.id !== id),
          },
        });
      }
      renderConversationList();
    } catch (err) {
      editor().toast?.(`恢复失败: ${err.message}`);
    }
  }

  function selectConversation(id) {
    ai().dispatch?.({ patch: { conversationId: id } });
  }

  // —— Jobs ——
  async function loadJobs() {
    const pid = state().selectedProjectId;
    const cid = (ai().getState?.() || {}).conversationId;
    if (!pid) return;
    try {
      const data = await api().jobs(pid, cid);
      dispatch({ type: "SET_JOBS", jobs: data.jobs || [] });
    } catch (_) {}
  }

  function renderJobList() {
    if (!els.jobList) return;
    const jobs = state().jobs || [];
    els.jobList.innerHTML = "";
    if (!jobs.length) {
      els.jobList.innerHTML = '<div class="muted" style="padding:10px">暂无任务</div>';
      return;
    }
    for (const job of jobs) {
      const item = document.createElement("div");
      item.className = "job-item" + (job.id === (ai().getState?.() || {}).currentJobId ? " active" : "");
      item.innerHTML = `<span class="status ${job.status}"></span>
        <span class="title">${escapeHtml(job.id.slice(0, 8))}</span>
        <span class="muted">${statusLabel(job.status)}</span>`;
      item.addEventListener("click", () => loadJob(job.id));
      els.jobList.appendChild(item);
    }
  }

  async function loadJob(jobId) {
    // Delegate to the AI panel — the timeline is its single rendering authority.
    await ai().openJob?.(jobId);
  }

  // —— Search ——
  async function runSearch() {
    const pid = state().selectedProjectId;
    if (!pid || !els.searchInput) return;
    const q = els.searchInput.value.trim();
    if (!q) return;
    try {
      const data = await api().search(pid, q);
      dispatch({ type: "SET_SEARCH_RESULTS", results: data.results || [] });
      renderSearchResults();
    } catch (err) {
      editor().toast?.(`搜索失败: ${err.message}`);
    }
  }

  function renderSearchResults() {
    if (!els.searchResults) return;
    const results = state().searchResults || [];
    els.searchResults.innerHTML = "";
    if (!results.length) {
      els.searchResults.innerHTML = '<div class="muted" style="padding:10px">无结果</div>';
      return;
    }
    for (const r of results) {
      const item = document.createElement("div");
      item.className = "search-result";
      item.innerHTML = `<span class="path">${escapeHtml(r.path || "")}</span>
        <span class="line">L${r.line || 0}</span>`;
      item.addEventListener("click", () => {
        editor().openPath?.(r.path, undefined, r.line);
      });
      els.searchResults.appendChild(item);
    }
  }

  // —— Context chips live in the AI panel composer now ——

  // —— Bottom panel ——
  function initBottomPanel() {
    if (!els.bottomTabs) return;
    els.bottomTabs.addEventListener("click", (e) => {
      const tab = e.target.closest(".bottom-tab");
      if (!tab) return;
      dispatch({ type: "LAYOUT_BOTTOM_VIEW", view: tab.dataset.view });
      renderBottomTabs();
    });
    if (els.bottomResize) {
      let dragging = false;
      els.bottomResize.addEventListener("mousedown", (e) => {
        dragging = true;
        e.preventDefault();
      });
      window.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        const h = Math.max(120, Math.min(600, window.innerHeight - e.clientY - 60));
        dispatch({ type: "LAYOUT_BOTTOM_HEIGHT", height: h });
        applyBottomHeight();
      });
      window.addEventListener("mouseup", () => {
        dragging = false;
      });
    }
    applyBottomHeight();
  }

  function renderBottomTabs() {
    if (!els.bottomTabs) return;
    const view = state().bottomView || "terminal";
    for (const tab of els.bottomTabs.querySelectorAll(".bottom-tab")) {
      tab.classList.toggle("active", tab.dataset.view === view);
    }
    if (els.bottomContent) {
      for (const v of els.bottomContent.querySelectorAll(".bottom-view")) {
        v.hidden = v.dataset.view !== view;
        v.classList.toggle("active", v.dataset.view === view);
      }
    }
  }

  function applyBottomHeight() {
    if (!els.bottomPanel) return;
    const h = state().bottomPanelHeight || 240;
    els.bottomPanel.style.height = `${h}px`;
  }

  function renderProblems() {
    if (!els.problemList) return;
    const problems = state().problems || [];
    els.problemList.innerHTML = "";
    if (!problems.length) {
      els.problemList.innerHTML = '<div class="muted" style="padding:10px">没有问题</div>';
      return;
    }
    for (const p of problems) {
      const item = document.createElement("div");
      item.className = "problem-item";
      item.innerHTML = `<span class="loc">${escapeHtml(p.path || "")}:${p.line || 0}</span>
        <span class="msg ${p.severity === "error" ? "sev-error" : "sev-warning"}">${escapeHtml(p.message || "")}</span>`;
      item.addEventListener("click", () => editor().openPath?.(p.path, undefined, p.line));
      els.problemList.appendChild(item);
    }
  }

  // —— Keyboard shortcuts ——
  function initKeyboard() {
    document.addEventListener("keydown", (e) => {
      const meta = e.metaKey || e.ctrlKey;
      // Escape closes dialogs and transient panels; it never cancels a task.
      if (e.key === "Escape") {
        const openDialog = document.querySelector("dialog[open]");
        if (openDialog) {
          e.preventDefault();
          openDialog.close("cancel");
          return;
        }
        if (els.bottomPanel && !els.bottomPanel.hidden) {
          els.bottomPanel.hidden = true;
        }
      }
      // View shortcuts
      if (meta && e.shiftKey && e.key.toLowerCase() === "e") {
        dispatch({ type: "LAYOUT_SIDEBAR_VIEW", view: "explorer" });
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "f") {
        dispatch({ type: "LAYOUT_SIDEBAR_VIEW", view: "search" });
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "c") {
        dispatch({ type: "LAYOUT_SIDEBAR_VIEW", view: "conversations" });
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "j") {
        dispatch({ type: "LAYOUT_SIDEBAR_VIEW", view: "jobs" });
      }
      if (meta && e.key.toLowerCase() === "l") {
        editor().showAi?.();
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "b") {
        dispatch({ type: "LAYOUT_TOGGLE_BOTTOM" });
      }
      if (meta && e.key.toLowerCase() === "j") {
        e.preventDefault();
        dispatch({ type: "LAYOUT_BOTTOM_VIEW", view: "terminal" });
        window.TerminalManager?.new?.();
      }
    });
  }

  // —— Polling and sync ——
  async function tick() {
    const aiState = ai().getState?.() || {};
    const pid = aiState.selectedProjectId;
    if (pid && pid !== state().selectedProjectId) {
      dispatch({ type: "SELECT_PROJECT", projectId: pid });
    }
    if (aiState.conversationId !== state().conversationId) {
      dispatch({ type: "SELECT_CONVERSATION", conversationId: aiState.conversationId });
    }
    if (state().sidebarView === "jobs" || state().bottomView === "terminal") {
      await loadJobs();
    }
    renderSidebarView();
    renderProblems();
  }

  // —— Utilities ——
  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function statusLabel(status) {
    const map = {
      queued: "排队中",
      running: "运行中",
      paused: "已暂停",
      awaiting_approval: "等待审批",
      cancel_requested: "正在停止",
      succeeded: "已完成",
      failed: "失败",
      canceled: "已停止",
      interrupted: "已中断",
    };
    return map[status] || status;
  }

  // —— Bindings ——
  function bind() {
    initSidebarTabs();
    initBottomPanel();
    initKeyboard();
    renderSidebarTabs();
    renderSidebarView();
    renderBottomTabs();

    if (els.btnArchiveConversation) {
      els.btnArchiveConversation.addEventListener("click", () => {
        const cid = (ai().getState?.() || {}).conversationId;
        if (cid) archiveConversation(cid);
      });
    }
    if (els.btnRefreshJobs) {
      els.btnRefreshJobs.addEventListener("click", async () => {
        await loadJobs();
        renderJobList();
      });
    }
    if (els.btnSearch) {
      els.btnSearch.addEventListener("click", runSearch);
    }
    if (els.searchInput) {
      els.searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") runSearch();
      });
    }
    if (els.renameConversationForm) {
      els.renameConversationForm.addEventListener("submit", (e) => {
        e.preventDefault();
        onRenameSubmit(e.currentTarget);
        els.renameConversationDialog?.close("ok");
      });
    }

    setInterval(tick, 2000);
    tick();
  }

  // Wait for other modules to load.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
