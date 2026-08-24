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
    sidebar: document.getElementById("sidebar"),
    conversationList: document.getElementById("conversationList"),
    jobList: document.getElementById("jobList"),
    approvalInbox: document.getElementById("approvalInbox"),
    approvalInboxCount: document.getElementById("approvalInboxCount"),
    pendingApprovalBadge: document.getElementById("pendingApprovalBadge"),
    btnRefreshApprovals: document.getElementById("btnRefreshApprovals"),
    themeSelect: document.getElementById("themeSelect"),
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
    approvalInboxDialog: document.getElementById("approvalInboxDialog"),
    approvalDetailSource: document.getElementById("approvalDetailSource"),
    approvalDetailTitle: document.getElementById("approvalDetailTitle"),
    approvalDetailRisk: document.getElementById("approvalDetailRisk"),
    approvalDetailIntent: document.getElementById("approvalDetailIntent"),
    approvalDetailMeta: document.getElementById("approvalDetailMeta"),
    approvalDetailPayload: document.getElementById("approvalDetailPayload"),
    btnInboxReject: document.getElementById("btnInboxReject"),
    btnInboxApprove: document.getElementById("btnInboxApprove"),
  };

  const approvalSubmitting = new Set();
  let approvalDetailItem = null;
  let approvalLastLoadedAt = 0;
  let approvalLoadPromise = null;

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

  function initActivityBar() {
    document.querySelectorAll(".activity-btn[data-view]").forEach((button) => {
      button.addEventListener("click", () => selectSidebarView(button.dataset.view));
    });
  }

  function selectSidebarView(view) {
    if (!view) return;
    dispatch({ type: "LAYOUT_SIDEBAR_VIEW", view });
    if (els.sidebar?.classList.contains("collapsed")) editor().toggleSidebar?.();
    renderSidebarTabs();
    renderSidebarView();
    if (view === "search") requestAnimationFrame(() => els.searchInput?.focus());
    if (view === "approvals") loadPendingApprovals({ force: true });
  }

  function renderSidebarTabs() {
    const view = state().sidebarView || "explorer";
    const tabs = els.sidebarTabs
      ? els.sidebarTabs.querySelectorAll(".sidebar-tab")
      : document.querySelectorAll(".activity-btn[data-view]");
    for (const tab of tabs) {
      tab.classList.toggle("active", tab.dataset.view === view);
      tab.setAttribute("aria-current", tab.dataset.view === view ? "page" : "false");
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
    if (view === "approvals") renderApprovalInbox();
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

  // —— Cross-project approval inbox (mirrors the Android "待处理" destination) ——
  async function loadPendingApprovals({ force = false } = {}) {
    const aiState = ai().getState?.() || {};
    if (!aiState.connected) {
      dispatch({ type: "SET_INBOX_APPROVALS", approvals: [] });
      renderApprovalInbox({ message: "连接 Agent 后可查看待处理审批" });
      return [];
    }
    const now = Date.now();
    if (!force && now - approvalLastLoadedAt < 6000) return state().inboxApprovals || [];
    if (approvalLoadPromise) return approvalLoadPromise;

    approvalLoadPromise = (async () => {
      try {
        const jobsData = await api().jobs();
        const jobs = jobsData.jobs || [];
        const activeJobs = jobs.filter((job) => window.ApprovalInbox.ACTIVE_STATUSES.has(job.status));
        const approvalResults = await Promise.all(
          activeJobs.map(async (job) => {
            try {
              const data = await api().listApprovals(job.id);
              return [job.id, data.approvals || []];
            } catch (_) {
              return [job.id, []];
            }
          }),
        );
        const approvalsByJob = Object.fromEntries(approvalResults);
        const projectData = aiState.projects?.length ? { projects: aiState.projects } : await api().projects();
        const conversations = [...(aiState.conversations || [])];
        const knownConversationIds = new Set(conversations.map((conversation) => conversation.id));
        const missingConversationIds = [...new Set(
          activeJobs
            .map((job) => job.conversation_id || job.conversationId)
            .filter((id) => id && !knownConversationIds.has(id)),
        )];
        const conversationResults = await Promise.all(
          missingConversationIds.map(async (id) => {
            try {
              return await api().getConversation(id);
            } catch (_) {
              return null;
            }
          }),
        );
        conversations.push(...conversationResults.filter(Boolean));
        const items = window.ApprovalInbox.buildApprovalInbox({
          jobs: activeJobs,
          approvalsByJob,
          projects: projectData.projects || [],
          conversations,
        });
        dispatch({ type: "SET_INBOX_APPROVALS", approvals: items });
        approvalLastLoadedAt = Date.now();
        renderApprovalInbox();
        return items;
      } catch (err) {
        renderApprovalInbox({ message: `加载失败：${err.message}` });
        return state().inboxApprovals || [];
      } finally {
        approvalLoadPromise = null;
      }
    })();
    return approvalLoadPromise;
  }

  function renderApprovalInbox({ message = "" } = {}) {
    if (!els.approvalInbox) return;
    const items = state().inboxApprovals || [];
    const count = items.length;
    els.approvalInboxCount.textContent = `${count} 项`;
    els.pendingApprovalBadge.textContent = count > 99 ? "99+" : String(count);
    els.pendingApprovalBadge.hidden = count === 0;
    const activityButton = els.pendingApprovalBadge.closest(".activity-btn");
    if (activityButton) activityButton.title = count ? `待处理审批（${count}）` : "待处理审批";

    els.approvalInbox.textContent = "";
    if (!count) {
      const empty = document.createElement("div");
      empty.className = "sidebar-empty";
      const title = document.createElement("strong");
      title.textContent = message ? "暂时无法加载" : "没有待处理操作";
      const hint = document.createElement("span");
      hint.textContent = message || "Agent 需要确认时会集中显示在这里";
      empty.append(title, hint);
      els.approvalInbox.appendChild(empty);
      return;
    }

    for (const item of items) {
      const card = document.createElement("article");
      card.className = "inbox-approval";

      const heading = document.createElement("div");
      heading.className = "inbox-approval-heading";
      const kind = document.createElement("strong");
      kind.textContent = window.ApprovalInbox.kindLabel(item);
      const age = document.createElement("span");
      age.className = "muted";
      age.textContent = window.ApprovalInbox.relativeTime(item.createdAt);
      heading.append(kind, age);

      const source = document.createElement("div");
      source.className = "inbox-approval-source";
      source.textContent = [item.projectName, item.conversationTitle].filter(Boolean).join(" · ");

      const intent = document.createElement("code");
      intent.className = "inbox-approval-intent";
      intent.textContent = window.ApprovalInbox.intent(item);

      const footer = document.createElement("div");
      footer.className = "inbox-approval-footer";
      if (window.ApprovalInbox.isDestructive(item)) {
        const risk = document.createElement("span");
        risk.className = "approval-risk";
        risk.textContent = "高风险 · 需查看详情";
        footer.appendChild(risk);
      }
      const spacer = document.createElement("span");
      spacer.className = "spacer";
      footer.appendChild(spacer);
      if (!window.ApprovalInbox.isDestructive(item)) {
        const reject = document.createElement("button");
        reject.type = "button";
        reject.className = "ghost-btn sm";
        reject.textContent = "拒绝";
        reject.addEventListener("click", () => decideInboxApproval(item, false));
        const approve = document.createElement("button");
        approve.type = "button";
        approve.className = "primary-btn sm";
        approve.textContent = "允许本次";
        approve.addEventListener("click", () => decideInboxApproval(item, true));
        footer.append(reject, approve);
      }
      const detail = document.createElement("button");
      detail.type = "button";
      detail.className = "ghost-btn sm";
      detail.textContent = "查看";
      detail.addEventListener("click", () => openApprovalDetail(item));
      footer.appendChild(detail);

      card.append(heading, source, intent, footer);
      els.approvalInbox.appendChild(card);
    }
  }

  function openApprovalDetail(item) {
    if (!els.approvalInboxDialog) return;
    approvalDetailItem = item;
    const destructive = window.ApprovalInbox.isDestructive(item);
    const payload = item.approval?.payload || {};
    els.approvalDetailSource.textContent = [item.projectName, item.conversationTitle].filter(Boolean).join(" · ");
    els.approvalDetailTitle.textContent = window.ApprovalInbox.kindLabel(item);
    els.approvalDetailIntent.textContent = window.ApprovalInbox.intent(item);
    els.approvalDetailRisk.hidden = !destructive;
    els.approvalDetailMeta.textContent = [
      payload.cwd ? `工作目录：${payload.cwd}` : "",
      payload.reason ? `原因：${payload.reason}` : "",
      item.prompt ? `任务：${item.prompt}` : "",
    ].filter(Boolean).join("\n");
    els.approvalDetailPayload.textContent = JSON.stringify(payload, null, 2);
    els.btnInboxApprove.className = destructive ? "danger-btn" : "primary-btn";
    els.approvalInboxDialog.showModal();
  }

  async function decideInboxApproval(item, approved) {
    if (!item || approvalSubmitting.has(item.id)) return;
    approvalSubmitting.add(item.id);
    const buttons = els.approvalInbox?.querySelectorAll("button") || [];
    buttons.forEach((button) => { button.disabled = true; });
    els.btnInboxApprove.disabled = true;
    els.btnInboxReject.disabled = true;
    try {
      await api().resolveApproval(item.jobId, item.approval.id, approved);
      dispatch({
        type: "SET_INBOX_APPROVALS",
        approvals: (state().inboxApprovals || []).filter((candidate) => candidate.id !== item.id),
      });
      els.approvalInboxDialog?.close("resolved");
      editor().toast?.(approved ? "已允许本次操作" : "已拒绝操作");
      renderApprovalInbox();
      approvalLastLoadedAt = 0;
    } catch (err) {
      if ([403, 404, 409].includes(err.status)) {
        dispatch({
          type: "SET_INBOX_APPROVALS",
          approvals: (state().inboxApprovals || []).filter((candidate) => candidate.id !== item.id),
        });
        els.approvalInboxDialog?.close("handled-elsewhere");
        editor().toast?.("此操作已在其他端处理");
        renderApprovalInbox();
      } else {
        editor().toast?.(`提交失败：${err.message}`);
      }
    } finally {
      approvalSubmitting.delete(item.id);
      buttons.forEach((button) => { button.disabled = false; });
      els.btnInboxApprove.disabled = false;
      els.btnInboxReject.disabled = false;
    }
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
      if (meta && e.shiftKey && e.key.toLowerCase() === "a") {
        e.preventDefault();
        selectSidebarView("approvals");
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
    await loadPendingApprovals();
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
    initActivityBar();
    initBottomPanel();
    initKeyboard();
    renderSidebarTabs();
    renderSidebarView();
    renderBottomTabs();

    window.DesktopState?.subscribe?.((_next, action) => {
      if (action?.type === "LAYOUT_SIDEBAR_VIEW") {
        renderSidebarTabs();
        renderSidebarView();
      }
    });

    if (els.themeSelect) {
      els.themeSelect.value = window.ThemeManager?.getMode?.() || "dark";
      els.themeSelect.addEventListener("change", () => window.ThemeManager?.setMode?.(els.themeSelect.value));
    }
    els.btnRefreshApprovals?.addEventListener("click", () => loadPendingApprovals({ force: true }));
    els.btnInboxReject?.addEventListener("click", () => decideInboxApproval(approvalDetailItem, false));
    els.btnInboxApprove?.addEventListener("click", () => decideInboxApproval(approvalDetailItem, true));

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
