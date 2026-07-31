(() => {
  "use strict";

  const STORAGE_KEY = "android-agent-desktop";
  const desktop = window.agentDesktop;
  const client = new window.AgentApi();

  const els = {
    connPill: document.getElementById("connPill"),
    statusConn: document.getElementById("statusConn"),
    projectSelect: document.getElementById("projectSelect"),
    modelSelect: document.getElementById("modelSelect"),
    autoFallback: document.getElementById("autoFallback"),
    jobHistory: document.getElementById("jobHistory"),
    conversationSelect: document.getElementById("conversationSelect"),
    btnNewConversation: document.getElementById("btnNewConversation"),
    btnSidebarNewConversation: document.getElementById("btnSidebarNewConversation"),
    aiMessages: document.getElementById("aiMessages"),
    aiEmpty: document.getElementById("aiEmpty"),
    aiContext: document.getElementById("aiContext"),
    approvalDock: document.getElementById("approvalDock"),
    promptInput: document.getElementById("promptInput"),
    btnSend: document.getElementById("btnSend"),
    btnStop: document.getElementById("btnStop"),
    btnNewChat: document.getElementById("btnNewChat"),
    btnAiSettings: document.getElementById("btnAiSettings"),
    btnCloseAi: document.getElementById("btnCloseAi"),
    btnUseCurrentFile: document.getElementById("btnUseCurrentFile"),
    btnStartServer: document.getElementById("btnStartServer"),
    btnStopServer: document.getElementById("btnStopServer"),
    btnCopyPhoneUrl: document.getElementById("btnCopyPhoneUrl"),
    serverPortLabel: document.getElementById("serverPortLabel"),
    phoneUrl: document.getElementById("phoneUrl"),
    settingsDialog: document.getElementById("settingsDialog"),
    settingsForm: document.getElementById("settingsForm"),
    serverUrl: document.getElementById("serverUrl"),
    apiToken: document.getElementById("apiToken"),
    registrationToken: document.getElementById("registrationToken"),
    btnPair: document.getElementById("btnPair"),
    settingsHint: document.getElementById("settingsHint"),
    createProjectDialog: document.getElementById("createProjectDialog"),
    createProjectForm: document.getElementById("createProjectForm"),
  };

  const state = {
    connected: false,
    userId: "",
    projects: [],
    selectedProjectId: null,
    conversations: [],
    conversationId: null,
    currentJobId: null,
    watcher: null,
    liveWatching: false,
    running: false,
    sawTextForJob: false,
    streamActive: false,
    assistantMsgEl: null,
    assistantTextEl: null,
    contextFile: null,
    seenEventIds: new Set(),
    serverManaged: false,
    serverRunning: false,
    phoneUrl: "",
    serverBusy: false,
  };

  function loadPrefs() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data.serverUrl) els.serverUrl.value = data.serverUrl;
      if (data.autoFallback) els.autoFallback.checked = true;
    } catch (_) {
      /* ignore */
    }
  }

  function savePrefs() {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        serverUrl: els.serverUrl.value.trim(),
        autoFallback: els.autoFallback.checked,
      }),
    );
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setConn(stateName, label) {
    els.connPill.dataset.state = stateName;
    els.connPill.textContent = label;
    els.statusConn.textContent =
      stateName === "ok"
        ? `Agent · ${state.userId || "local"}`
        : stateName === "busy"
          ? "Agent · 连接中"
          : stateName === "err"
            ? "Agent · 失败"
            : "Agent · 未连接";
  }

  function applyServerStatus(status) {
    state.serverRunning = Boolean(status.running);
    state.serverManaged = Boolean(status.managed);
    const port = status.port || 8000;
    const phone = status.phoneUrl || (status.lanIp ? `http://${status.lanIp}:${port}` : "");
    state.phoneUrl = phone;

    els.serverPortLabel.textContent = state.serverRunning
      ? `端口 ${port} · 运行中`
      : `端口 ${port} · 未运行`;
    els.phoneUrl.textContent = phone || "等待获取局域网地址…";
    els.phoneUrl.title = phone
      ? `手机端服务器地址：${phone}`
      : "启动服务后显示局域网地址";

    els.btnStopServer.hidden = !state.serverManaged;
    els.btnStartServer.disabled =
      state.serverBusy || (state.serverRunning && state.serverManaged);
    els.btnStopServer.disabled = state.serverBusy || !state.serverManaged;

    if (state.serverBusy) {
      els.btnStartServer.textContent = "启动中…";
    } else if (state.serverRunning && state.serverManaged) {
      els.btnStartServer.textContent = "服务已启动";
    } else if (state.serverRunning) {
      els.btnStartServer.textContent = "重新连接";
    } else {
      els.btnStartServer.textContent = "启动服务";
    }
  }

  async function refreshServerStatus() {
    try {
      const status = await desktop.agentStatus();
      applyServerStatus(status);
      return status;
    } catch (err) {
      els.serverPortLabel.textContent = "端口 —";
      els.phoneUrl.textContent = "状态获取失败";
      return null;
    }
  }

  async function startServer() {
    if (state.serverBusy) return;
    state.serverBusy = true;
    applyServerStatus({
      running: state.serverRunning,
      managed: state.serverManaged,
      port: Number(String(els.serverPortLabel.textContent).match(/\d+/)?.[0] || 8000),
      phoneUrl: state.phoneUrl,
    });
    els.btnStartServer.textContent = "启动中…";
    try {
      const result = await desktop.agentStart();
      applyServerStatus(result);
      if (!result.ok) {
        toast(result.error || "启动失败");
        return;
      }
      if (result.alreadyRunning && !result.managed) {
        toast("服务已在运行，正在连接…");
      } else {
        toast(`服务已启动 · 端口 ${result.port}`);
      }
      els.serverUrl.value = result.localUrl || `http://127.0.0.1:${result.port || 8000}`;
      try {
        await connect({ silent: true });
        toast(result.phoneUrl ? `手机可连 ${result.phoneUrl}` : "已连接本地服务");
      } catch (err) {
        toast(`服务已起，连接失败: ${err.message}`);
      }
    } catch (err) {
      toast(`启动失败: ${err.message}`);
      await refreshServerStatus();
    } finally {
      state.serverBusy = false;
      await refreshServerStatus();
    }
  }

  async function stopServer() {
    if (state.serverBusy) return;
    state.serverBusy = true;
    els.btnStopServer.disabled = true;
    try {
      const result = await desktop.agentStop();
      applyServerStatus(result);
      if (result.stopped) {
        state.connected = false;
        setConn("idle", "未连接");
        updateComposerEnabled();
        toast("已停止桌面端拉起的服务");
      } else {
        toast("当前服务不是由桌面端启动的，未强制停止");
      }
    } catch (err) {
      toast(`停止失败: ${err.message}`);
    } finally {
      state.serverBusy = false;
      await refreshServerStatus();
    }
  }

  async function copyPhoneUrl() {
    const text = state.phoneUrl || els.phoneUrl.textContent;
    if (!text || text.includes("等待") || text.includes("失败") || text === "—") {
      toast("暂无手机连接地址");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制手机连接地址");
    } catch (_) {
      toast("复制失败，请手动选择地址");
    }
  }

  function toast(msg) {
    window.EditorApp?.toast?.(msg);
  }

  function clearMessages() {
    els.aiMessages.innerHTML = "";
    els.aiMessages.appendChild(els.aiEmpty);
    els.aiEmpty.hidden = false;
    state.assistantMsgEl = null;
    state.assistantTextEl = null;
    state.seenEventIds = new Set();
  }

  function hideEmpty() {
    els.aiEmpty.hidden = true;
  }

  function appendUserMessage(text) {
    hideEmpty();
    const msg = document.createElement("div");
    msg.className = "msg user";
    msg.innerHTML = `<div class="msg-role">You</div><div class="msg-bubble">${escapeHtml(text)}</div>`;
    els.aiMessages.appendChild(msg);
    scrollMessages();
  }

  function ensureAssistantMessage() {
    if (state.assistantMsgEl) return state.assistantMsgEl;
    hideEmpty();
    const msg = document.createElement("div");
    msg.className = "msg assistant";
    msg.innerHTML = `<div class="msg-role">Agent</div>`;
    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    msg.appendChild(bubble);
    els.aiMessages.appendChild(msg);
    state.assistantMsgEl = msg;
    state.assistantTextEl = bubble;
    return msg;
  }

  function appendAssistantText(text) {
    if (!text) return;
    ensureAssistantMessage();
    const current = state.assistantTextEl.textContent || "";
    state.assistantTextEl.textContent = current ? `${current}\n${text}` : text;
    state.streamActive = false;
    scrollMessages();
  }

  function appendAssistantDelta(delta) {
    if (!delta) return;
    ensureAssistantMessage();
    state.streamActive = true;
    state.sawTextForJob = true;
    state.assistantTextEl.textContent = (state.assistantTextEl.textContent || "") + delta;
    scrollMessages();
  }

  function appendStatusChip(text, kind = "") {
    hideEmpty();
    const chip = document.createElement("div");
    chip.className = `status-chip ${kind}`;
    chip.textContent = text;
    els.aiMessages.appendChild(chip);
    state.assistantMsgEl = null;
    state.assistantTextEl = null;
    scrollMessages();
    return chip;
  }

  function appendToolCard(event) {
    hideEmpty();
    state.assistantMsgEl = null;
    state.assistantTextEl = null;
    const details = document.createElement("details");
    details.className = "tool-card";
    if (event.type === "tool_result") {
      details.dataset.ok = event.ok === false ? "false" : "true";
    }
    const isCall = event.type === "tool_call";
    const title = isCall
      ? event.message || `调用 ${event.name || "tool"}`
      : event.message ||
        `${event.name || "tool"} → ${event.ok === false ? "失败" : "成功"}`;
    const body = isCall
      ? JSON.stringify(event.input || {}, null, 2)
      : event.preview || "";
    details.innerHTML = `
      <summary>
        <span class="tool-badge">${isCall ? "tool" : "result"}</span>
        <span class="tool-name">${escapeHtml(event.name || "")}</span>
        <span>${escapeHtml(title)}</span>
      </summary>
      ${body ? `<div class="tool-body">${escapeHtml(body)}</div>` : ""}
    `;
    els.aiMessages.appendChild(details);
    scrollMessages();
  }

  function appendChanges(files) {
    if (!files?.length) return;
    hideEmpty();
    state.assistantMsgEl = null;
    state.assistantTextEl = null;
    const block = document.createElement("div");
    block.className = "changes-block";
    block.innerHTML = `<h4>改动文件</h4>`;
    for (const file of files) {
      const path = typeof file === "string" ? file : file.path || "";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "change-file";
      btn.textContent = path;
      btn.addEventListener("click", async () => {
        const root = window.EditorApp?.getRoot?.();
        const project = state.projects.find((p) => p.id === state.selectedProjectId);
        const base = project?.workspace || root;
        if (!base || !path) return;
        const abs = await desktop.joinPath(base, path);
        await window.EditorApp?.openPath?.(abs);
      });
      block.appendChild(btn);
    }
    els.aiMessages.appendChild(block);
    scrollMessages();
  }

  function scrollMessages() {
    els.aiMessages.scrollTop = els.aiMessages.scrollHeight;
  }

  function statusLabel(status) {
    return (
      {
        queued: "排队中",
        running: "运行中",
        awaiting_approval: "等待确认",
        succeeded: "已完成",
        failed: "失败",
        canceled: "已停止",
      }[status] || status || "—"
    );
  }

  const pendingApprovalPrompts = new Set();
  const dockedApprovals = new Map(); // approvalId -> { jobId, payload }

  function clearApprovalDock() {
    dockedApprovals.clear();
    pendingApprovalPrompts.clear();
    if (!els.approvalDock) return;
    els.approvalDock.innerHTML = "";
    els.approvalDock.hidden = true;
  }

  function renderApprovalDock() {
    if (!els.approvalDock) return;
    els.approvalDock.innerHTML = "";
    if (!dockedApprovals.size) {
      els.approvalDock.hidden = true;
      return;
    }
    els.approvalDock.hidden = false;

    for (const [approvalId, item] of dockedApprovals) {
      const card = document.createElement("div");
      card.className = "approval-dock-card";
      card.dataset.approvalId = approvalId;

      const title = document.createElement("div");
      title.className = "approval-title";
      title.textContent = "需要你确认下载";
      card.appendChild(title);

      const meta = document.createElement("div");
      meta.className = "approval-meta";
      meta.innerHTML =
        `<div><span>URL</span><code>${escapeHtml(item.url || "")}</code></div>` +
        `<div><span>保存到</span><code>${escapeHtml(item.path || "")}</code></div>`;
      card.appendChild(meta);

      const hint = document.createElement("p");
      hint.className = "approval-hint";
      hint.textContent = "任务已暂停。请选择允许或拒绝后继续。";
      card.appendChild(hint);

      const actions = document.createElement("div");
      actions.className = "approval-actions";
      const btnAllow = document.createElement("button");
      btnAllow.type = "button";
      btnAllow.className = "primary-btn";
      btnAllow.textContent = "允许下载";
      const btnDeny = document.createElement("button");
      btnDeny.type = "button";
      btnDeny.className = "ghost-btn";
      btnDeny.textContent = "拒绝";

      const setBusy = (busy) => {
        btnAllow.disabled = busy;
        btnDeny.disabled = busy;
      };

      const decide = async (approved) => {
        setBusy(true);
        try {
          await client.resolveApproval(item.jobId, approvalId, approved);
          dockedApprovals.delete(approvalId);
          pendingApprovalPrompts.delete(`${item.jobId}:${approvalId}`);
          renderApprovalDock();
          appendStatusChip(approved ? "已允许下载" : "已拒绝下载", approved ? "ok" : "err");
          markApprovalCardResolved({
            approval_id: approvalId,
            decision: approved ? "approved" : "rejected",
          });
        } catch (err) {
          setBusy(false);
          appendStatusChip(`确认失败: ${err.message}`, "err");
          toast(err.message);
        }
      };

      btnAllow.addEventListener("click", () => decide(true));
      btnDeny.addEventListener("click", () => decide(false));
      actions.appendChild(btnAllow);
      actions.appendChild(btnDeny);
      card.appendChild(actions);
      els.approvalDock.appendChild(card);
    }
  }

  function upsertPendingApproval(event) {
    const approvalId = event.approval_id;
    const jobId = event.job_id || state.currentJobId;
    if (!approvalId || !jobId) return;
    const key = `${jobId}:${approvalId}`;
    if (dockedApprovals.has(approvalId)) {
      renderApprovalDock();
      return;
    }
    dockedApprovals.set(approvalId, {
      jobId,
      url: event.url || (event.payload && event.payload.url) || "",
      path: event.path || (event.payload && event.payload.path) || "",
      max_bytes: event.max_bytes,
      kind: event.kind || "download_file",
    });
    pendingApprovalPrompts.add(key);
    renderApprovalDock();

    // Also leave a marker in the chat transcript (non-interactive; actions are in the dock)
    if (!els.aiMessages.querySelector(`.approval-card[data-approval-id="${approvalId}"]`)) {
      appendApprovalCard(event, { interactive: false });
    }
    appendStatusChip("请在下方确认条选择：允许 / 拒绝下载", "running");
    scrollMessages();
    els.approvalDock?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  function appendApprovalCard(event, { interactive }) {
    clearEmpty();
    const approvalId = event.approval_id;
    const jobId = event.job_id || state.currentJobId;
    const card = document.createElement("div");
    card.className = "approval-card";
    card.dataset.approvalId = approvalId || "";
    card.dataset.jobId = jobId || "";

    const title = document.createElement("div");
    title.className = "approval-title";
    title.textContent = interactive ? "需要你确认：允许下载文件？" : "下载确认请求";
    card.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "approval-meta";
    meta.innerHTML =
      `<div><span>URL</span><code>${escapeHtml(event.url || "")}</code></div>` +
      `<div><span>保存到</span><code>${escapeHtml(event.path || "")}</code></div>`;
    card.appendChild(meta);

    const hint = document.createElement("p");
    hint.className = "approval-hint";
    hint.textContent = interactive
      ? "也可使用下方固定确认条操作。"
      : "请到输入框上方的黄色确认条操作。";
    card.appendChild(hint);

    if (interactive && approvalId && jobId) {
      const actions = document.createElement("div");
      actions.className = "approval-actions";
      const btnAllow = document.createElement("button");
      btnAllow.type = "button";
      btnAllow.className = "primary-btn";
      btnAllow.textContent = "允许下载";
      const btnDeny = document.createElement("button");
      btnDeny.type = "button";
      btnDeny.className = "ghost-btn";
      btnDeny.textContent = "拒绝";
      const decide = async (approved) => {
        btnAllow.disabled = true;
        btnDeny.disabled = true;
        try {
          await client.resolveApproval(jobId, approvalId, approved);
          dockedApprovals.delete(approvalId);
          renderApprovalDock();
          card.classList.add(approved ? "approved" : "rejected");
          hint.textContent = approved ? "已允许" : "已拒绝";
          actions.remove();
        } catch (err) {
          btnAllow.disabled = false;
          btnDeny.disabled = false;
          toast(err.message);
        }
      };
      btnAllow.addEventListener("click", () => decide(true));
      btnDeny.addEventListener("click", () => decide(false));
      actions.appendChild(btnAllow);
      actions.appendChild(btnDeny);
      card.appendChild(actions);
    } else {
      card.classList.add("historical");
    }

    els.aiMessages.appendChild(card);
    scrollMessages();
  }

  function markApprovalCardResolved(event) {
    const approvalId = event.approval_id;
    if (!approvalId) return;
    dockedApprovals.delete(approvalId);
    renderApprovalDock();
    const card = els.aiMessages.querySelector(`.approval-card[data-approval-id="${approvalId}"]`);
    if (!card) return;
    const decision = event.decision || "";
    card.classList.add(decision === "approved" ? "approved" : "rejected");
    const hint = card.querySelector(".approval-hint");
    if (hint) {
      const labels = {
        approved: "已允许下载",
        rejected: "已拒绝下载",
        timeout: "确认超时，未下载",
        canceled: "任务取消，下载中止",
      };
      hint.textContent = labels[decision] || `结果: ${decision}`;
    }
    card.querySelector(".approval-actions")?.remove();
  }

  async function syncPendingApprovals(jobId) {
    if (!jobId || !state.connected) return;
    try {
      const data = await client.listApprovals(jobId);
      const pending = data.approvals || [];
      for (const item of pending) {
        upsertPendingApproval({
          approval_id: item.id,
          job_id: jobId,
          kind: item.kind,
          ...(item.payload || {}),
        });
      }
      const liveIds = new Set(pending.map((p) => p.id));
      for (const id of [...dockedApprovals.keys()]) {
        if (!liveIds.has(id)) dockedApprovals.delete(id);
      }
      renderApprovalDock();

      // Orphaned wait: job says awaiting_approval but server has no live approval
      if (!pending.length) {
        const jobData = await client.job(jobId);
        const job = jobData.job;
        if (job?.status === "awaiting_approval") {
          showOrphanApprovalDock(jobId, job);
        }
      }
    } catch (_) {
      /* ignore */
    }
  }

  function showOrphanApprovalDock(jobId, job) {
    if (!els.approvalDock) return;
    const events = job.events || [];
    let lastReq = null;
    for (let i = events.length - 1; i >= 0; i -= 1) {
      if (events[i].type === "approval_required") {
        lastReq = events[i];
        break;
      }
      if (events[i].type === "approval_resolved") break;
    }
    els.approvalDock.hidden = false;
    els.approvalDock.innerHTML = "";
    const card = document.createElement("div");
    card.className = "approval-dock-card";
    card.innerHTML =
      `<div class="approval-title">下载确认已失效</div>` +
      `<p class="approval-hint">任务仍在等待确认，但确认通道已断开（常见于服务重启）。` +
      `${lastReq?.path ? `<br>上次请求保存到：<code>${escapeHtml(lastReq.path)}</code>` : ""}` +
      `</p>`;
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    const btnStop = document.createElement("button");
    btnStop.type = "button";
    btnStop.className = "danger-btn";
    btnStop.textContent = "停止并重试";
    btnStop.addEventListener("click", async () => {
      try {
        await client.cancel(jobId);
        clearApprovalDock();
        appendStatusChip("已停止失效任务，请重新发送需求", "err");
      } catch (err) {
        toast(err.message);
      }
    });
    actions.appendChild(btnStop);
    card.appendChild(actions);
    els.approvalDock.appendChild(card);
  }

  function eventKey(event) {
    if (event.id != null) return `id:${event.id}`;
    return `${event.type}:${event.ts}:${event.message || ""}:${event.content || ""}:${event.name || ""}`;
  }

  function handleEvent(event) {
    if (!event || !event.type) return;
    const key = eventKey(event);
    if (state.seenEventIds.has(key)) return;
    state.seenEventIds.add(key);

    switch (event.type) {
      case "started":
        appendStatusChip("任务开始", "running");
        break;
      case "plan":
      case "turn":
      case "auto_continue":
        // Cursor-like: keep the transcript continuous — skip turn banners
        break;
      case "model_switch":
      case "provider_switch":
        appendStatusChip(event.message || event.type);
        break;
      case "text_delta":
        appendAssistantDelta(event.content || event.delta || event.message || "");
        break;
      case "text":
        state.sawTextForJob = true;
        if (event.streamed || state.streamActive) {
          // Already rendered via text_delta; just finalize the bubble
          state.streamActive = false;
        } else {
          appendAssistantText(event.content || event.message || "");
        }
        break;
      case "tool_call":
      case "tool_result":
        state.streamActive = false;
        appendToolCard(event);
        break;
      case "usage": {
        const u = event.usage || {};
        window.EditorApp?.setStatus?.(
          `Token 输入 ${u.input_tokens ?? "?"} · 输出 ${u.output_tokens ?? "?"} · 合计 ${u.total_tokens ?? "?"}`,
        );
        break;
      }
      case "changes":
        appendChanges(event.files || []);
        break;
      case "completed":
        // Final text is applied once in watchJob's done handler to avoid duplicates
        break;
      case "failed":
        appendStatusChip(`失败: ${event.error || event.message || "未知错误"}`, "err");
        break;
      case "canceled":
      case "cancel_requested":
        appendStatusChip(event.message || "已停止", "err");
        break;
      case "honesty_nudge":
        appendStatusChip(event.message || "系统要求真实改文件", "err");
        break;
      case "approval_required":
        if (event.kind === "download_file" || !event.kind) {
          upsertPendingApproval(event);
        } else {
          appendStatusChip(event.message || "等待用户确认", "running");
        }
        break;
      case "approval_resolved":
        markApprovalCardResolved(event);
        appendStatusChip(event.message || `确认结果: ${event.decision || ""}`);
        break;
      default:
        if (event.message) appendStatusChip(event.message);
        break;
    }
  }

  function setRunning(running) {
    state.running = running;
    els.btnStop.disabled = !running;
    const canSend = state.connected && state.selectedProjectId && !running;
    els.btnSend.disabled = !canSend;
    els.promptInput.disabled = !state.connected || !state.selectedProjectId;
    els.btnUseCurrentFile.disabled = !state.selectedProjectId;
    window.EditorApp?.setStatus?.(running ? "Agent 运行中…" : "就绪");
  }

  function stopWatcher() {
    if (state.watcher) {
      state.watcher.close();
      state.watcher = null;
    }
    state.liveWatching = false;
  }

  async function refreshOpenFilesAfterJob(job) {
    const files = job?.changed_files || [];
    const project = state.projects.find((p) => p.id === state.selectedProjectId);
    const base = project?.workspace || window.EditorApp?.getRoot?.();
    if (base) {
      await window.EditorApp?.refreshTree?.({ silent: true });
    }
    for (const file of files) {
      const rel = typeof file === "string" ? file : file.path;
      if (!rel || !base) continue;
      const abs = await desktop.joinPath(base, rel);
      await window.EditorApp?.reloadPathIfOpen?.(abs);
    }
  }

  function watchJob(jobId) {
    stopWatcher();
    state.currentJobId = jobId;
    state.sawTextForJob = false;
    state.liveWatching = true;
    setRunning(true);
    // Immediately pull any pending approvals (covers missed WS events / resume)
    syncPendingApprovals(jobId);
    state.watcher = client.watchJob(jobId, async (payload) => {
      if (payload.kind === "event" && payload.event) {
        handleEvent(payload.event);
      }
      if (payload.kind === "job" && payload.job) {
        if (payload.job.status === "awaiting_approval") {
          await syncPendingApprovals(jobId);
        }
      }
      if (payload.kind === "done") {
        // Ignore stale done callbacks after the user switched conversations
        if (state.currentJobId !== jobId) return;
        stopWatcher();
        setRunning(false);
        clearApprovalDock();
        const status = payload.status;
        try {
          const data = await client.job(jobId);
          if (state.currentJobId !== jobId) return;
          if (data.job?.changed_files?.length) appendChanges(data.job.changed_files);
          const finalText = data.job?.result || data.job?.final_message || payload.result;
          if (finalText && status === "succeeded") {
            if (!state.sawTextForJob) {
              appendAssistantText(finalText);
            } else {
              const noteMatch = String(finalText).match(/【系统(?:校验|说明)】[\s\S]*/);
              if (noteMatch) {
                appendAssistantText(noteMatch[0].trim());
              }
            }
          }
          if (status === "succeeded") {
            appendStatusChip("本轮结束", "ok");
          } else if (status === "failed") {
            appendStatusChip(`任务失败: ${payload.error || data.job?.error || data.job?.error_message || ""}`, "err");
          } else if (status === "canceled") {
            appendStatusChip("任务已停止", "err");
          }
          await refreshOpenFilesAfterJob(data.job);
          await loadJobHistory(state.selectedProjectId, state.conversationId);
          if (state.selectedProjectId) {
            await loadConversations(state.selectedProjectId, {
              preferId: state.conversationId,
              loadHistory: false,
            });
          }
        } catch (_) {
          if (state.currentJobId !== jobId) return;
          if (status === "succeeded") appendStatusChip("本轮结束", "ok");
          else if (status === "failed") appendStatusChip(`任务失败: ${payload.error || ""}`, "err");
          else if (status === "canceled") appendStatusChip("任务已停止", "err");
        }
      }
      if (payload.kind === "error") {
        if (state.currentJobId === jobId) {
          appendStatusChip(`连接异常: ${payload.error}`, "err");
        }
      }
    });
  }

  async function connect({ silent = false } = {}) {
    const baseUrl = (els.serverUrl.value || "http://127.0.0.1:8000").trim().replace(/\/+$/, "");
    const token = els.apiToken.value.trim();
    els.serverUrl.value = baseUrl;
    client.configure({ baseUrl, token });
    savePrefs();
    if (!token) {
      state.connected = false;
      setConn("err", "需要 Token");
      els.settingsHint.textContent = "请输入服务端生成的访问 Token";
      if (!silent) els.settingsDialog.showModal();
      return;
    }
    setConn("busy", "连接中");
    try {
      const health = await client.health();
      state.connected = true;
      state.userId = health.user_id || "local";
      setConn("ok", "已连接");
      els.settingsHint.textContent = `已连接 ${state.userId} · ${health.provider}/${health.model} · Key ${health.api_key_configured ? "OK" : "缺失"}`;
      if (health.port || health.lan_ip) {
        applyServerStatus({
          running: true,
          managed: state.serverManaged,
          port: health.port || 8000,
          lanIp: health.lan_ip,
          phoneUrl: health.lan_ip ? `http://${health.lan_ip}:${health.port || 8000}` : state.phoneUrl,
        });
      } else {
        await refreshServerStatus();
      }
      await loadModels();
      await refreshProjects({ silent: true });
      await maybeAutoSelectProject();
      updateComposerEnabled();
      if (desktop?.setCredential) {
        try {
          await desktop.setCredential(baseUrl, token);
        } catch (_) {
          els.settingsHint.textContent += " · 凭证仅用于当前会话";
        }
      }
      if (!silent) toast("已连接 Agent");
    } catch (err) {
      state.connected = false;
      setConn("err", "连接失败");
      els.settingsHint.textContent = err.message;
      updateComposerEnabled();
      if (!silent) toast(`连接失败: ${err.message}`);
      throw err;
    }
  }

  async function pair() {
    const baseUrl = (
      els.serverUrl.value || "http://127.0.0.1:8000"
    ).trim().replace(/\/+$/, "");
    const secret = els.registrationToken.value.trim();
    if (!secret) {
      toast("请输入服务端配对密钥");
      return;
    }
    client.configure({ baseUrl, token: "" });
    const account = await client.pair(secret);
    els.apiToken.value = account.token;
    els.registrationToken.value = "";
    await connect();
    toast(`配对成功: ${account.user_id}`);
  }

  async function loadModels() {
    try {
      const data = await client.models();
      els.modelSelect.innerHTML = "";
      const def = document.createElement("option");
      def.value = "";
      def.textContent = "默认模型";
      els.modelSelect.appendChild(def);
      for (const m of data.models || []) {
        const opt = document.createElement("option");
        opt.value = m.id || m.provider || "";
        opt.textContent = m.label || `${m.provider}/${m.model}`;
        if (m.is_default) opt.selected = true;
        els.modelSelect.appendChild(opt);
      }
      els.modelSelect.disabled = false;
    } catch (_) {
      els.modelSelect.disabled = true;
    }
  }

  async function refreshProjects({ silent = false } = {}) {
    const data = await client.projects();
    state.projects = data.projects || [];
    renderProjectSelect();
    if (!silent) toast(`已加载 ${state.projects.length} 个项目`);
  }

  function renderProjectSelect() {
    const prev = state.selectedProjectId;
    els.projectSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = state.projects.length ? "选择项目…" : "暂无项目";
    els.projectSelect.appendChild(placeholder);

    for (const p of state.projects) {
      const opt = document.createElement("option");
      opt.value = p.id;
      const badge = p.latest_status ? ` · ${statusLabel(p.latest_status)}` : "";
      opt.textContent = `${p.name || p.id}${badge}`;
      els.projectSelect.appendChild(opt);
    }

    const create = document.createElement("option");
    create.value = "__create__";
    create.textContent = "＋ 新建项目…";
    els.projectSelect.appendChild(create);

    if (prev && state.projects.some((p) => p.id === prev)) {
      els.projectSelect.value = prev;
    }
  }

  async function maybeAutoSelectProject() {
    const root = window.EditorApp?.getRoot?.();
    if (!root || !state.projects.length) return;
    for (const p of state.projects) {
      if (!p.workspace) continue;
      const rel = await desktop.relative(p.workspace, root).catch(() => null);
      const rel2 = await desktop.relative(root, p.workspace).catch(() => null);
      if (root === p.workspace || rel === "" || rel2 === "" || (rel && !rel.startsWith(".."))) {
        await selectProject(p.id, { openWorkspace: false });
        return;
      }
      // match by path segments workspaces/<user>/<project>
      const parts = root.split(/[/\\]/);
      const idx = parts.lastIndexOf("workspaces");
      if (idx >= 0 && parts[idx + 2] === p.id) {
        await selectProject(p.id, { openWorkspace: false });
        return;
      }
    }
  }

  async function selectProject(projectId, { openWorkspace = false } = {}) {
    state.selectedProjectId = projectId || null;
    if (projectId) els.projectSelect.value = projectId;
    updateComposerEnabled();

    if (!projectId) {
      state.conversations = [];
      state.conversationId = null;
      renderConversationSelect();
      els.jobHistory.innerHTML = '<option value="">本轮任务</option>';
      els.jobHistory.disabled = true;
      return;
    }

    const project = state.projects.find((p) => p.id === projectId);
    if (openWorkspace && project?.workspace) {
      const exists = await desktop.exists(project.workspace);
      if (exists) await window.EditorApp?.openFolder?.(project.workspace);
    }

    await loadConversations(projectId);
  }

  function formatConversationLabel(conv) {
    const title = (conv.title || "对话").slice(0, 28);
    const turns = conv.turn_count || (conv.turns || []).length || 0;
    return turns ? `${title} · ${turns} 轮` : title;
  }

  function renderConversationSelect() {
    const select = els.conversationSelect;
    if (!select) return;
    select.innerHTML = "";
    if (!state.conversations.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "暂无对话";
      select.appendChild(opt);
      select.disabled = true;
      return;
    }
    select.disabled = false;
    for (const conv of state.conversations) {
      const opt = document.createElement("option");
      opt.value = conv.id;
      opt.textContent = formatConversationLabel(conv);
      select.appendChild(opt);
    }
    if (state.conversationId) select.value = state.conversationId;
  }

  async function loadConversations(projectId, { preferId = null, loadHistory = true } = {}) {
    try {
      const data = await client.conversations(projectId);
      state.conversations = data.conversations || [];
      if (!state.conversations.length) {
        const created = await client.createConversation(projectId, "默认对话");
        state.conversations = [created];
      }
      let preferred =
        preferId && state.conversations.some((c) => c.id === preferId)
          ? preferId
          : state.conversationId && state.conversations.some((c) => c.id === state.conversationId)
            ? state.conversationId
            : null;
      // Avoid landing on an empty "新对话" when older chats with history exist
      if (!preferred) {
        const withTurns = state.conversations.find(
          (c) => (c.turn_count || (c.turns || []).length || 0) > 0,
        );
        preferred = (withTurns || state.conversations[0]).id;
      }

      // Soft refresh (e.g. after send): update titles only — do not kill the live job watcher
      if (!loadHistory && preferred === state.conversationId) {
        renderConversationSelect();
        return;
      }
      await selectConversation(preferred, { loadHistory });
    } catch (err) {
      state.conversations = [];
      state.conversationId = null;
      renderConversationSelect();
      toast(err.message);
    }
  }

  async function selectConversation(conversationId, { loadHistory = true } = {}) {
    if (!conversationId) return;
    const switching = conversationId !== state.conversationId;
    if (switching || loadHistory) {
      stopWatcher();
      setRunning(false);
      state.currentJobId = null;
      state.sawTextForJob = false;
    }
    state.conversationId = conversationId;
    renderConversationSelect();
    if (loadHistory) {
      await renderConversationHistory(conversationId);
    }
    if (switching || loadHistory) {
      await loadJobHistory(state.selectedProjectId, conversationId);
      await resumeActiveJobForConversation(conversationId);
    }
    updateComposerEnabled();
  }

  async function resumeActiveJobForConversation(conversationId) {
    if (!conversationId || !state.selectedProjectId || !state.connected) return;
    try {
      const data = await client.jobs(state.selectedProjectId, conversationId);
      const active = (data.jobs || []).find(
        (j) => j.status === "queued" || j.status === "running" || j.status === "awaiting_approval",
      );
      if (!active) return;
      state.seenEventIds = new Set();
      state.assistantMsgEl = null;
      state.assistantTextEl = null;
      state.sawTextForJob = false;
      appendStatusChip(`恢复进行中的任务 ${active.id}`, "running");
      watchJob(active.id);
      if (els.jobHistory) els.jobHistory.value = active.id;
    } catch (_) {
      /* ignore */
    }
  }

  async function renderConversationHistory(conversationId) {
    clearMessages();
    try {
      const conv = await client.getConversation(conversationId);
      const turns = conv.turns || [];
      if (!turns.length) {
        appendStatusChip("新对话 — 发送第一条消息开始");
        return;
      }
      for (const turn of turns) {
        if (turn.user) appendUserMessage(turn.user);
        if (turn.assistant) {
          state.assistantMsgEl = null;
          state.assistantTextEl = null;
          appendAssistantText(turn.assistant);
        }
        if (turn.changed_files?.length) appendChanges(turn.changed_files);
        state.assistantMsgEl = null;
        state.assistantTextEl = null;
      }
      appendStatusChip(`已加载 ${turns.length} 轮历史`);
    } catch (err) {
      toast(err.message);
    }
  }

  async function createNewConversation() {
    const projectId = state.selectedProjectId;
    if (!projectId || !state.connected) {
      toast("请先连接并选择项目");
      return;
    }
    stopWatcher();
    setRunning(false);
    try {
      const conv = await client.createConversation(projectId, "新对话");
      state.conversations = [conv, ...state.conversations.filter((c) => c.id !== conv.id)];
      await selectConversation(conv.id, { loadHistory: true });
      toast("已开新对话");
    } catch (err) {
      toast(err.message);
    }
  }

  async function loadJobHistory(projectId, conversationId = state.conversationId) {
    try {
      const data = await client.jobs(projectId, conversationId || undefined);
      const jobs = data.jobs || [];
      els.jobHistory.innerHTML = '<option value="">本轮任务</option>';
      for (const job of jobs.slice(0, 30)) {
        const opt = document.createElement("option");
        opt.value = job.id;
        const prompt = (job.prompt || "").slice(0, 24);
        opt.textContent = `${statusLabel(job.status)} · ${prompt || job.id}`;
        els.jobHistory.appendChild(opt);
      }
      els.jobHistory.disabled = false;
    } catch (_) {
      els.jobHistory.disabled = true;
    }
  }

  async function loadHistoricalJob(jobId) {
    if (!jobId) return;
    stopWatcher();
    try {
      const data = await client.job(jobId);
      const job = data.job;
      if (job.conversation_id && job.conversation_id !== state.conversationId) {
        await selectConversation(job.conversation_id, { loadHistory: true });
      }
      appendStatusChip(`回看任务 ${job.id}`);
      appendUserMessage(job.prompt || "(无提示词)");
      state.assistantMsgEl = null;
      state.assistantTextEl = null;
      for (const event of job.events || []) handleEvent(event);
      if (job.result || job.final_message) appendAssistantText(job.result || job.final_message);
      if (job.changed_files?.length) appendChanges(job.changed_files);
      if (job.status === "queued" || job.status === "running" || job.status === "awaiting_approval") {
        watchJob(job.id);
      } else {
        appendStatusChip(statusLabel(job.status), job.status === "succeeded" ? "ok" : "err");
        setRunning(false);
      }
      state.currentJobId = job.id;
    } catch (err) {
      toast(err.message);
    }
  }

  function updateComposerEnabled() {
    const ok = state.connected && state.selectedProjectId && state.conversationId && !state.running;
    els.promptInput.disabled = !state.connected || !state.selectedProjectId || !state.conversationId;
    els.btnSend.disabled = !ok;
    els.btnUseCurrentFile.disabled = !state.selectedProjectId;
    const convDisabled = !state.connected || !state.selectedProjectId || state.running;
    if (els.btnNewConversation) els.btnNewConversation.disabled = convDisabled;
    if (els.btnSidebarNewConversation) els.btnSidebarNewConversation.disabled = convDisabled;
  }

  function buildPrompt() {
    let prompt = els.promptInput.value.trim();
    if (!prompt) return "";
    if (state.contextFile) {
      prompt = `当前聚焦文件: ${state.contextFile}\n\n${prompt}`;
    }
    return prompt;
  }

  async function sendAsk() {
    const projectId = state.selectedProjectId;
    let conversationId = state.conversationId;
    const prompt = buildPrompt();
    if (!projectId || !prompt) {
      toast("请选择项目并输入问题");
      return;
    }

    if (!conversationId) {
      try {
        const conv = await client.createConversation(projectId, "新对话");
        state.conversations = [conv, ...state.conversations];
        state.conversationId = conv.id;
        conversationId = conv.id;
        renderConversationSelect();
      } catch (err) {
        toast(err.message);
        return;
      }
    }

    const body = {
      prompt,
      auto_fallback: els.autoFallback.checked,
    };
    const provider = els.modelSelect.value;
    if (provider) body.provider = provider;

    // Continuous stream: do not clear the panel between turns in the same conversation
    state.assistantMsgEl = null;
    state.assistantTextEl = null;
    state.seenEventIds = new Set();
    appendUserMessage(prompt);
    els.promptInput.value = "";
    autosizePrompt();
    setRunning(true);

    try {
      const data = await client.askConversation(conversationId, body);
      const job = data.job;
      state.currentJobId = job.id;
      if (data.conversation_id) state.conversationId = data.conversation_id;
      els.jobHistory.value = job.id;
      appendStatusChip(`任务 ${job.id}`, "running");
      watchJob(job.id);
      await loadJobHistory(projectId, state.conversationId);
    } catch (err) {
      setRunning(false);
      appendStatusChip(err.message, "err");
      toast(err.message);
    }
  }

  async function stopJob() {
    if (!state.currentJobId) return;
    els.btnStop.disabled = true;
    try {
      await client.cancel(state.currentJobId);
      appendStatusChip("已请求停止…");
      toast("已请求停止");
    } catch (err) {
      els.btnStop.disabled = false;
      toast(err.message);
    }
  }

  function setContextFile(relOrName) {
    state.contextFile = relOrName;
    if (!relOrName) {
      els.aiContext.hidden = true;
      els.aiContext.innerHTML = "";
      return;
    }
    els.aiContext.hidden = false;
    els.aiContext.innerHTML = `<span class="chip">@ ${escapeHtml(relOrName)} <button type="button" class="icon-btn" id="btnClearContext" title="移除">×</button></span>`;
    document.getElementById("btnClearContext")?.addEventListener("click", () => setContextFile(null));
  }

  async function useCurrentFile() {
    const tab = window.EditorApp?.getActiveTab?.();
    if (!tab?.path) {
      toast("当前没有打开的文件");
      return;
    }
    const root = window.EditorApp?.getRoot?.();
    let label = tab.title;
    if (root) {
      try {
        label = await desktop.relative(root, tab.path);
      } catch (_) {
        /* keep title */
      }
    }
    setContextFile(label);
    els.promptInput.focus();
  }

  function autosizePrompt() {
    const el = els.promptInput;
    el.style.height = "auto";
    el.style.height = `${Math.min(180, Math.max(72, el.scrollHeight))}px`;
  }

  function openSettings() {
    els.settingsDialog.showModal();
  }

  function openCreateProject() {
    els.createProjectDialog.showModal();
  }

  function focusComposer() {
    window.EditorApp?.showAi?.();
    els.promptInput.focus();
  }

  function onActiveFileChanged(tab) {
    /* keep context sticky unless cleared */
  }

  async function onWorkspaceChanged(rootDir) {
    if (state.connected) {
      await maybeAutoSelectProject();
    }
  }

  function bind() {
    loadPrefs();

    els.btnAiSettings.addEventListener("click", () => openSettings());
    els.statusConn.addEventListener("click", () => openSettings());
    els.btnCloseAi.addEventListener("click", () => window.EditorApp?.toggleAi?.());
    els.btnStartServer.addEventListener("click", () => startServer());
    els.btnStopServer.addEventListener("click", () => stopServer());
    els.btnCopyPhoneUrl.addEventListener("click", () => copyPhoneUrl());
    els.btnPair?.addEventListener("click", () =>
      pair().catch((err) => toast(`配对失败: ${err.message}`)));
    els.phoneUrl.addEventListener("click", () => copyPhoneUrl());
    desktop.onAgentServerExit?.(async () => {
      state.serverManaged = false;
      state.connected = false;
      setConn("idle", "未连接");
      updateComposerEnabled();
      await refreshServerStatus();
      toast("Agent 服务已退出");
    });
    els.btnNewChat.addEventListener("click", () => createNewConversation());
    els.btnNewConversation?.addEventListener("click", () => createNewConversation());
    els.btnSidebarNewConversation?.addEventListener("click", () => createNewConversation());
    els.conversationSelect?.addEventListener("change", async () => {
      const id = els.conversationSelect.value;
      if (id && id !== state.conversationId) await selectConversation(id);
    });
    els.btnSend.addEventListener("click", () => sendAsk());
    els.btnStop.addEventListener("click", () => stopJob());
    els.btnUseCurrentFile.addEventListener("click", () => useCurrentFile());
    els.autoFallback.addEventListener("change", savePrefs);

    els.promptInput.addEventListener("input", autosizePrompt);
    els.promptInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        if (!els.btnSend.disabled) sendAsk();
      }
    });

    els.projectSelect.addEventListener("change", async () => {
      const value = els.projectSelect.value;
      if (value === "__create__") {
        els.projectSelect.value = state.selectedProjectId || "";
        openCreateProject();
        return;
      }
      await selectProject(value, { openWorkspace: true });
    });

    els.jobHistory.addEventListener("change", () => {
      const id = els.jobHistory.value;
      if (id) loadHistoricalJob(id);
    });

    els.settingsForm.addEventListener("submit", async (ev) => {
      const submitter = ev.submitter;
      const value = submitter?.value || "connect";
      if (value === "cancel") return;
      ev.preventDefault();
      try {
        await connect();
        els.settingsDialog.close();
      } catch (_) {
        /* keep open */
      }
    });

    els.createProjectForm.addEventListener("submit", async (ev) => {
      const submitter = ev.submitter;
      if (submitter?.value === "cancel") return;
      ev.preventDefault();
      const fd = new FormData(els.createProjectForm);
      const name = String(fd.get("name") || "").trim();
      const packageName = String(fd.get("package") || "").trim();
      if (!name) return;
      try {
        if (!state.connected) await connect({ silent: true });
        const body = { name };
        if (packageName) body.package = packageName;
        const project = await client.createProject(body);
        await refreshProjects({ silent: true });
        await selectProject(project.id, { openWorkspace: true });
        els.createProjectDialog.close();
        els.createProjectForm.reset();
        toast(`已创建 ${project.name}`);
      } catch (err) {
        toast(err.message);
      }
    });
  }

  async function init() {
    bind();
    clearMessages();
    setRunning(false);
    await refreshServerStatus();
    if (desktop?.getCredential) {
      const baseUrl = (
        els.serverUrl.value || "http://127.0.0.1:8000"
      ).trim().replace(/\/+$/, "");
      els.apiToken.value = await desktop.getCredential(baseUrl);
    }
    try {
      await connect({ silent: true });
    } catch (_) {
      setConn("err", "未连接");
    }
    setInterval(() => {
      if (!state.serverBusy) refreshServerStatus();
    }, 5000);
  }

  window.AiPanel = {
    init,
    openSettings,
    openCreateProject,
    focusComposer,
    onActiveFileChanged,
    onWorkspaceChanged,
    client,
    getState: () => state,
    dispatch: (action) => {
      if (typeof action === "object" && action) {
        Object.assign(state, action.patch || {});
        if (action.patch && action.patch.conversationId !== undefined) {
          selectConversation(action.patch.conversationId).catch(() => {});
        }
      }
    },
  };
})();
