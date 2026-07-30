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
    conversationSelect: document.getElementById("conversationSelect"),
    aiContext: document.getElementById("aiContext"),
    aiPlan: document.getElementById("aiPlan"),
    planBody: document.getElementById("planBody"),
    btnTogglePlan: document.getElementById("btnTogglePlan"),
    aiTools: document.getElementById("aiTools"),
    toolsBody: document.getElementById("toolsBody"),
    approvalDock: document.getElementById("approvalDock"),
    aiMeta: document.getElementById("aiMeta"),
    tokenMeta: document.getElementById("tokenMeta"),
    timingMeta: document.getElementById("timingMeta"),
    modelMeta: document.getElementById("modelMeta"),
    fallbackMeta: document.getElementById("fallbackMeta"),
    aiActions: document.getElementById("aiActions"),
    btnSteer: document.getElementById("btnSteer"),
    btnFollowUp: document.getElementById("btnFollowUp"),
    btnPause: document.getElementById("btnPause"),
    btnResume: document.getElementById("btnResume"),
    btnCancel: document.getElementById("btnCancel"),
    promptInput: document.getElementById("promptInput"),
    btnUseCurrentFile: document.getElementById("btnUseCurrentFile"),
    btnUseSelection: document.getElementById("btnUseSelection"),
    btnUseFolder: document.getElementById("btnUseFolder"),
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
        refreshConversationSelect();
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
      refreshConversationSelect();
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
      refreshConversationSelect();
    } catch (err) {
      editor().toast?.(`恢复失败: ${err.message}`);
    }
  }

  function selectConversation(id) {
    ai().dispatch?.({ patch: { conversationId: id } });
  }

  function refreshConversationSelect() {
    if (!els.conversationSelect) return;
    const s = ai().getState?.() || {};
    const list = s.conversations || [];
    els.conversationSelect.innerHTML = '<option value="">切换对话…</option>';
    for (const c of list) {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.title || c.id;
      if (c.id === s.conversationId) opt.selected = true;
      els.conversationSelect.appendChild(opt);
    }
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
    try {
      const data = await api().job(jobId);
      const job = data.job;
      ai().dispatch?.({ patch: { currentJobId: job.id, jobStatus: job.status } });
      dispatch({ type: "SET_CURRENT_JOB", jobId: job.id, status: job.status });
      dispatch({
        type: "JOB_EVENTS",
        events: job.events || [],
        status: job.status,
        plan: job.plan || [],
        approvals: job.approvals || [],
      });
      syncFromJob(job);
      renderPlan();
      renderTools();
      renderApprovals();
      renderMeta();
      renderActions();
    } catch (_) {}
  }

  function syncFromJob(job) {
    if (!job) return;
    const toolCalls = [];
    const toolResults = [];
    for (const ev of job.events || []) {
      if (ev.type === "tool_call") toolCalls.push(ev);
      if (ev.type === "tool_result") toolResults.push(ev);
    }
    dispatch({
      type: "JOB_EVENTS",
      events: job.events || [],
      status: job.status,
      plan: job.plan || [],
      approvals: job.approvals || [],
      toolCalls,
      toolResults,
      tokenEstimate: job.token_estimate,
      timingMs: job.duration_ms,
      providerModel: job.provider ? `${job.provider}/${job.model || ""}` : null,
      fallbackUsed: job.fallback_used,
      recovery: job.recovery_mode,
    });
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

  // —— Context chips ——
  function updateContextChips() {
    if (!els.aiContext) return;
    const chips = [];
    const tab = editor().getActiveTab?.();
    if (tab?.path) {
      chips.push({ key: "file", kind: "file", label: tab.path.split(/[/\\]/).pop() || tab.path, path: tab.path });
    }
    const root = editor().getRoot?.();
    if (root) {
      // No folder chip by default; user clicks @folder to add.
    }
    const s = state();
    for (const c of s.contextChips || []) {
      if (!chips.find((x) => x.key === c.key)) chips.push(c);
    }
    dispatch({ type: "PATCH", patch: { contextChips: chips } });
    renderContextChips();
  }

  function renderContextChips() {
    if (!els.aiContext) return;
    const chips = state().contextChips || [];
    els.aiContext.hidden = !chips.length;
    els.aiContext.innerHTML = "";
    for (const chip of chips) {
      const el = document.createElement("span");
      el.className = "context-chip";
      el.innerHTML = `<span class="kind">${chip.kind}</span>
        <span class="label" title="${escapeHtml(chip.path || chip.label || "")}">${escapeHtml(chip.label || "")}</span>
        <span class="remove" data-key="${chip.key}">×</span>`;
      el.querySelector(".remove")?.addEventListener("click", () => {
        dispatch({ type: "REMOVE_CONTEXT_CHIP", key: chip.key });
        renderContextChips();
      });
      els.aiContext.appendChild(el);
    }
  }

  function addContextChip(kind) {
    const tab = editor().getActiveTab?.();
    if (kind === "file" && tab?.path) {
      dispatch({
        type: "ADD_CONTEXT_CHIP",
        chip: { key: `file:${tab.path}`, kind: "file", label: tab.path.split(/[/\\]/).pop() || tab.path, path: tab.path },
      });
    } else if (kind === "folder") {
      const root = editor().getRoot?.();
      if (root) {
        dispatch({
          type: "ADD_CONTEXT_CHIP",
          chip: { key: `folder:${root}`, kind: "folder", label: root.split(/[/\\]/).pop() || root, path: root },
        });
      }
    } else if (kind === "selection") {
      const sel = editor().getSelection?.();
      if (sel?.text) {
        dispatch({
          type: "ADD_CONTEXT_CHIP",
          chip: { key: `selection:${Date.now()}`, kind: "selection", label: `${sel.path || ""} 选区`, text: sel.text },
        });
      }
    }
    renderContextChips();
  }

  // —— Plan ——
  function renderPlan() {
    if (!els.aiPlan || !els.planBody) return;
    const plan = state().plan || [];
    if (!plan.length) {
      els.aiPlan.hidden = true;
      return;
    }
    els.aiPlan.hidden = false;
    els.planBody.innerHTML = "";
    for (const item of plan) {
      const el = document.createElement("div");
      el.className = "todo-item" + (item.status ? " " + item.status : "");
      el.innerHTML = `<input type="checkbox" disabled ${item.status === "done" ? "checked" : ""} />
        <span>${escapeHtml(item.title || item.text || "")}</span>`;
      els.planBody.appendChild(el);
    }
  }

  // —— Tools ——
  function renderTools() {
    if (!els.aiTools || !els.toolsBody) return;
    const calls = state().toolCalls || [];
    const results = state().toolResults || [];
    if (!calls.length && !results.length) {
      els.aiTools.hidden = true;
      return;
    }
    els.aiTools.hidden = false;
    els.toolsBody.innerHTML = "";
    const resultByCall = new Map();
    for (const r of results) resultByCall.set(r.tool_call_id, r);
    for (const call of calls) {
      const el = document.createElement("div");
      el.className = "tool-call";
      const risk = call.risk || "read";
      el.innerHTML = `<div class="tool-header">
          <span>${escapeHtml(call.name || call.tool || "tool")}</span>
          <span class="risk ${risk}">${risk}</span>
        </div>
        <div class="tool-body">${escapeHtml(JSON.stringify(call.input || call.arguments || {}, null, 2))}</div>`;
      el.querySelector(".tool-header").addEventListener("click", () => el.classList.toggle("open"));
      els.toolsBody.appendChild(el);
      const result = resultByCall.get(call.tool_call_id || call.id);
      if (result) {
        const rel = document.createElement("div");
        rel.className = "tool-result" + (result.ok ? " ok" : " error");
        rel.innerHTML = `<div class="tool-header"><span>result</span></div>
          <div class="tool-body">${escapeHtml(JSON.stringify(result.output || result.result || result, null, 2))}</div>`;
        rel.querySelector(".tool-header").addEventListener("click", () => rel.classList.toggle("open"));
        els.toolsBody.appendChild(rel);
      }
    }
  }

  // —— Approvals ——
  function renderApprovals() {
    if (!els.approvalDock) return;
    const approvals = state().approvals || [];
    const pending = approvals.filter((a) => a.status === "pending");
    els.approvalDock.hidden = !pending.length;
    els.approvalDock.innerHTML = "";
    for (const a of pending) {
      const card = document.createElement("div");
      card.className = "approval-card";
      const risk = a.risk || "process";
      card.innerHTML = `<div class="approval-header">
          <span class="risk ${risk}">${risk}</span>
          <span class="muted">${escapeHtml(a.kind || a.approval_kind || "approval")}</span>
        </div>
        <div class="approval-body">${escapeHtml(JSON.stringify(a.payload || a, null, 2))}</div>
        <div class="approval-actions">
          <button class="ghost-btn sm" data-action="reject" data-id="${a.id}">拒绝</button>
          <button class="primary-btn sm" data-action="approve" data-id="${a.id}">批准</button>
        </div>`;
      card.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-action]");
        if (!btn) return;
        resolveApproval(a.id, btn.dataset.action === "approve");
      });
      els.approvalDock.appendChild(card);
    }
  }

  async function resolveApproval(approvalId, approved) {
    const jobId = (ai().getState?.() || {}).currentJobId;
    if (!jobId) return;
    try {
      await api().resolveApproval(jobId, approvalId, approved);
      editor().toast?.(approved ? "已批准" : "已拒绝");
      loadJob(jobId);
    } catch (err) {
      editor().toast?.(`审批失败: ${err.message}`);
    }
  }

  // —— Meta ——
  function renderMeta() {
    if (!els.aiMeta) return;
    const s = state();
    const any = s.tokenEstimate || s.timingMs || s.providerModel || s.fallbackUsed || s.recovery;
    els.aiMeta.hidden = !any;
    if (els.tokenMeta) els.tokenMeta.textContent = s.tokenEstimate ? `~${s.tokenEstimate} tokens` : "—";
    if (els.timingMeta) els.timingMeta.textContent = s.timingMs ? `${s.timingMs}ms` : "—";
    if (els.modelMeta) els.modelMeta.textContent = s.providerModel || "—";
    if (els.fallbackMeta) els.fallbackMeta.textContent = s.fallbackUsed ? "已降级" : s.recovery ? "恢复中" : "—";
  }

  // —— Actions ——
  function renderActions() {
    if (!els.aiActions) return;
    const s = state();
    const running = s.running || s.awaitingApproval || s.jobStatus === "paused";
    els.aiActions.hidden = !running;
    if (els.btnPause) els.btnPause.hidden = s.jobStatus !== "running";
    if (els.btnResume) els.btnResume.hidden = s.jobStatus !== "paused";
  }

  async function doAction(action) {
    const jobId = (ai().getState?.() || {}).currentJobId;
    if (!jobId) return;
    try {
      if (action === "pause") await api().pauseJob(jobId);
      else if (action === "resume") await api().resumeJob(jobId);
      else if (action === "cancel") await api().cancel(jobId);
      else if (action === "steer") {
        const text = els.promptInput?.value?.trim();
        if (!text) return;
        await api().steerJob(jobId, text);
      } else if (action === "follow_up") {
        const text = els.promptInput?.value?.trim();
        if (!text) return;
        await api().followUpJob(jobId, text);
      }
      loadJob(jobId);
    } catch (err) {
      editor().toast?.(`${action} 失败: ${err.message}`);
    }
  }

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
      // Cmd/Ctrl+Enter to send
      if (meta && e.key === "Enter" && els.promptInput && document.activeElement === els.promptInput) {
        e.preventDefault();
        els.btnSend?.click();
      }
      // Escape closes dialogs and stops if running
      if (e.key === "Escape") {
        const openDialog = document.querySelector("dialog[open]");
        if (openDialog) {
          e.preventDefault();
          openDialog.close("cancel");
          return;
        }
        if (state().running && els.btnCancel) {
          e.preventDefault();
          doAction("cancel");
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
    if (aiState.currentJobId && aiState.currentJobId !== state().currentJobId) {
      dispatch({ type: "SET_CURRENT_JOB", jobId: aiState.currentJobId, status: aiState.jobStatus });
    }
    if (state().sidebarView === "jobs" || state().bottomView === "terminal") {
      await loadJobs();
    }
    const currentJob = aiState.currentJobId;
    if (currentJob && (state().running || state().awaitingApproval)) {
      try {
        const data = await api().job(currentJob);
        syncFromJob(data.job);
        renderPlan();
        renderTools();
        renderApprovals();
        renderMeta();
        renderActions();
      } catch (_) {}
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
      succeeded: "成功",
      failed: "失败",
      canceled: "已取消",
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
    if (els.btnUseCurrentFile) {
      els.btnUseCurrentFile.addEventListener("click", () => addContextChip("file"));
    }
    if (els.btnUseSelection) {
      els.btnUseSelection.addEventListener("click", () => addContextChip("selection"));
    }
    if (els.btnUseFolder) {
      els.btnUseFolder.addEventListener("click", () => addContextChip("folder"));
    }
    if (els.btnTogglePlan) {
      els.btnTogglePlan.addEventListener("click", () => {
        els.planBody?.classList.toggle("hidden");
      });
    }
    if (els.btnSteer) els.btnSteer.addEventListener("click", () => doAction("steer"));
    if (els.btnFollowUp) els.btnFollowUp.addEventListener("click", () => doAction("follow_up"));
    if (els.btnPause) els.btnPause.addEventListener("click", () => doAction("pause"));
    if (els.btnResume) els.btnResume.addEventListener("click", () => doAction("resume"));
    if (els.btnCancel) els.btnCancel.addEventListener("click", () => doAction("cancel"));

    // Sync context chips on active tab change.
    window.addEventListener("editor-tab-changed", () => updateContextChips());
    updateContextChips();

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
