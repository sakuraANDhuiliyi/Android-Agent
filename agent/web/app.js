(() => {
  "use strict";

  const STORAGE_KEY = "android-agent-ui";

  const state = {
    baseUrl: "",
    token: "",
    userId: "",
    connected: false,
    projects: [],
    selectedProjectId: null,
    models: [],
    currentJobId: null,
    pollTimer: null,
    lastEventCount: 0,
    hasBuildLog: false,
    filePath: ".",
    fileEditPath: null,
    fileWritable: false,
    traceConversations: [],
    traceConversationId: null,
    traceTurnId: null,
    traceTurns: [],
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    serverUrl: $("serverUrl"),
    apiToken: $("apiToken"),
    btnConnect: $("btnConnect"),
    btnPair: $("btnPair"),
    registrationToken: $("registrationToken"),
    btnDisconnect: $("btnDisconnect"),
    btnSettings: $("btnSettings"),
    connectStatus: $("connectStatus"),
    connectPanel: $("connectPanel"),
    workspace: $("workspace"),
    connPill: $("connPill"),
    healthMeta: $("healthMeta"),
    projectList: $("projectList"),
    projectsEmpty: $("projectsEmpty"),
    btnRefreshProjects: $("btnRefreshProjects"),
    btnNewProject: $("btnNewProject"),
    projectTitle: $("projectTitle"),
    projectMeta: $("projectMeta"),
    btnBrowseFiles: $("btnBrowseFiles"),
    btnDownloadApk: $("btnDownloadApk"),
    btnDeleteProject: $("btnDeleteProject"),
    promptInput: $("promptInput"),
    modelSelect: $("modelSelect"),
    autoFallback: $("autoFallback"),
    btnSend: $("btnSend"),
    btnStop: $("btnStop"),
    jobStatus: $("jobStatus"),
    jobId: $("jobId"),
    statTurns: $("statTurns"),
    statTools: $("statTools"),
    statTokens: $("statTokens"),
    timeline: $("timeline"),
    jobHistory: $("jobHistory"),
    summaryText: $("summaryText"),
    changeList: $("changeList"),
    changesEmpty: $("changesEmpty"),
    logText: $("logText"),
    btnLoadLog: $("btnLoadLog"),
    traceConversation: $("traceConversation"),
    btnTraceRefresh: $("btnTraceRefresh"),
    traceTurnList: $("traceTurnList"),
    traceDetail: $("traceDetail"),
    traceMeta: $("traceMeta"),
    traceSteps: $("traceSteps"),
    traceEmpty: $("traceEmpty"),
    createDialog: $("createDialog"),
    createForm: $("createForm"),
    filesDialog: $("filesDialog"),
    filesPath: $("filesPath"),
    fileList: $("fileList"),
    fileTitle: $("fileTitle"),
    fileContent: $("fileContent"),
    btnSaveFile: $("btnSaveFile"),
    btnCloseFiles: $("btnCloseFiles"),
    toast: $("toast"),
  };

  function loadPrefs() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data.serverUrl) els.serverUrl.value = data.serverUrl;
    } catch (_) {
      /* ignore */
    }
  }

  function savePrefs() {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        serverUrl: els.serverUrl.value.trim(),
      }),
    );
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      els.toast.hidden = true;
    }, 2600);
  }

  function setConnPill(stateName, label) {
    els.connPill.dataset.state = stateName;
    els.connPill.textContent = label;
  }

  function normalizeBaseUrl(url) {
    return url.trim().replace(/\/+$/, "");
  }

  async function api(path, options = {}) {
    const headers = Object.assign(
      { Accept: "application/json" },
      options.headers || {},
    );
    if (state.token) {
      headers.Authorization = `Bearer ${state.token}`;
    }
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(`${state.baseUrl}${path}`, {
      ...options,
      headers,
      body:
        options.body && typeof options.body !== "string"
          ? JSON.stringify(options.body)
          : options.body,
    });
    if (res.status === 204) return null;
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail =
        (data && (data.detail || data.message)) || res.statusText || "请求失败";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function formatEvent(event) {
    const type = event.type || "";
    const message = event.message || "";
    switch (type) {
      case "turn":
        return message || `轮次 ${event.turn ?? "?"}`;
      case "text":
        return event.content || message;
      case "tool_call":
        return message || `工具: ${event.name || "?"}`;
      case "tool_result": {
        const base =
          message ||
          `结果: ${event.name || "?"} -> ${event.ok === false ? "失败" : "成功"}`;
        return event.duration_ms ? `${base} (${event.duration_ms}ms)` : base;
      }
      case "started":
        return "任务开始";
      case "completed":
        return message || "任务结束";
      case "failed":
        return `任务失败: ${event.error || message || "未知错误"}`;
      case "canceled":
        return message || "任务已停止";
      case "cancel_requested":
        return message || "已请求停止";
      case "usage": {
        const u = event.usage || {};
        return `Token: ${u.input_tokens ?? "?"} + ${u.output_tokens ?? "?"} = ${u.total_tokens ?? "?"}`;
      }
      case "changes":
        return message || "文件改动";
      case "plan":
        return message || "计划";
      case "auto_continue":
        return message || "Agent 自动继续下一批轮次";
      case "model_switch":
        return (
          message ||
          `切换模型: ${event.from_model || "?"} -> ${event.to_model || "?"}`
        );
      case "provider_switch":
        return (
          message ||
          `切换提供商: ${event.from_provider || "?"} -> ${event.to_provider || "?"}`
        );
      default:
        return message || type || JSON.stringify(event);
    }
  }

  function clearTimeline() {
    els.timeline.innerHTML = "";
    state.lastEventCount = 0;
  }

  function appendEvent(event) {
    const node = document.createElement("article");
    node.className = "event";
    node.dataset.kind = event.type || "event";
    if (event.type === "tool_result") {
      node.dataset.ok = event.ok === false ? "false" : "true";
    }
    node.innerHTML = `
      <div class="event-type">${escapeHtml(event.type || "event")}</div>
      <div class="event-body">${escapeHtml(formatEvent(event))}</div>
    `;
    els.timeline.appendChild(node);
    els.timeline.scrollTop = els.timeline.scrollHeight;
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function updateJobStats(job) {
    const events = job.events || [];
    const turns = events.filter((e) => e.type === "turn").length;
    const tools = events.filter((e) => e.type === "tool_call").length;
    els.jobStatus.textContent = statusLabel(job.status);
    els.jobId.textContent = job.id || "—";
    els.statTurns.textContent = String(turns);
    els.statTools.textContent = String(tools);
    if (job.total_tokens != null) {
      els.statTokens.textContent = `${job.input_tokens ?? "?"} / ${job.output_tokens ?? "?"} / ${job.total_tokens}`;
    } else {
      els.statTokens.textContent = "—";
    }
    state.hasBuildLog = Boolean(job.build_log_path || job.has_build_log);
    els.btnLoadLog.disabled = !state.hasBuildLog && job.status !== "succeeded" && job.status !== "failed";
    if (job.build_log_path) els.btnLoadLog.disabled = false;

    const lines = [];
    lines.push(`状态: ${statusLabel(job.status)}`);
    if (job.provider || job.model) {
      lines.push(`模型: ${job.provider || "?"}/${job.model || "?"}`);
    }
    if (job.prompt) lines.push(`提示词: ${job.prompt}`);
    if (job.result || job.final_message) {
      lines.push(`结果: ${job.result || job.final_message}`);
    }
    if (job.error || job.error_message) {
      lines.push(`错误: ${job.error || job.error_message}`);
    }
    if (job.total_tokens != null) {
      lines.push(
        `Token: 输入 ${job.input_tokens ?? "?"} / 输出 ${job.output_tokens ?? "?"} / 总计 ${job.total_tokens}`,
      );
    }
    els.summaryText.textContent = lines.join("\n");

    const changed = job.changed_files || [];
    els.changeList.innerHTML = "";
    els.changesEmpty.hidden = changed.length > 0;
    for (const file of changed) {
      const li = document.createElement("li");
      li.className = "change-item";
      const change = typeof file === "string" ? "M" : file.change || "M";
      const path = typeof file === "string" ? file : file.path || "";
      li.innerHTML = `<span class="change">${escapeHtml(change)}</span>${escapeHtml(path)}`;
      els.changeList.appendChild(li);
    }
  }

  function statusLabel(status) {
    const map = {
      queued: "排队中",
      running: "运行中",
      succeeded: "成功",
      failed: "失败",
      canceled: "已停止",
    };
    return map[status] || status || "—";
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function syncJob(job, { appendOnly = true } = {}) {
    const events = job.events || [];
    if (!appendOnly) {
      clearTimeline();
      events.forEach(appendEvent);
      state.lastEventCount = events.length;
    } else {
      while (state.lastEventCount < events.length) {
        appendEvent(events[state.lastEventCount]);
        state.lastEventCount += 1;
      }
    }
    updateJobStats(job);
    const active = job.status === "queued" || job.status === "running";
    els.btnStop.disabled = !active;
    els.btnSend.disabled = active || !state.selectedProjectId;
    if (!active) {
      stopPolling();
      await refreshProjects({ silent: true });
      syncTraceWithJob(job).catch(() => {});
    }
  }

  function startPolling(jobId) {
    stopPolling();
    state.currentJobId = jobId;
    state.pollTimer = setInterval(async () => {
      try {
        const data = await api(`/api/jobs/${jobId}`);
        await syncJob(data.job);
      } catch (err) {
        els.connectStatus.textContent = `轮询失败: ${err.message}`;
      }
    }, 1200);
  }

  async function connect() {
    const baseUrl = normalizeBaseUrl(els.serverUrl.value || window.location.origin);
    const token = els.apiToken.value.trim();
    state.baseUrl = baseUrl;
    state.token = token;
    els.serverUrl.value = baseUrl;
    savePrefs();
    if (!token) {
      state.connected = false;
      setConnPill("err", "需要 Token");
      els.connectStatus.textContent = "请输入服务端生成的访问 Token";
      els.connectPanel.hidden = false;
      els.workspace.hidden = true;
      return;
    }
    setConnPill("busy", "连接中");
    els.connectStatus.textContent = "正在连接…";
    try {
      const health = await api("/api/health");
      state.userId = health.user_id;
      state.connected = true;
      setConnPill("ok", "已连接");
      els.healthMeta.textContent = `${health.user_id} · ${health.provider}/${health.model} · Key ${health.api_key_configured ? "OK" : "缺失"}`;
      els.connectStatus.textContent = `已连接用户 ${health.user_id}`;
      els.connectPanel.hidden = true;
      els.workspace.hidden = false;
      await loadModels();
      await refreshProjects({ silent: true });
    } catch (err) {
      state.connected = false;
      setConnPill("err", "连接失败");
      els.healthMeta.textContent = err.message;
      els.connectStatus.textContent = err.message;
      els.connectPanel.hidden = false;
      toast("连接失败");
    }
  }

  async function pair() {
    const baseUrl = normalizeBaseUrl(
      els.serverUrl.value || window.location.origin,
    );
    const registrationToken = els.registrationToken.value.trim();
    if (!registrationToken) {
      throw new Error("请输入服务端配对密钥");
    }
    state.baseUrl = baseUrl;
    state.token = "";
    const account = await api("/api/pair", {
      method: "POST",
      headers: { "X-Registration-Token": registrationToken },
      body: {},
    });
    state.token = account.token;
    els.apiToken.value = account.token;
    els.registrationToken.value = "";
    await connect();
  }

  function hideSettings() {
    els.connectPanel.hidden = true;
  }

  function showSettings() {
    els.connectPanel.hidden = false;
    els.connectStatus.textContent = state.connected
      ? `当前用户 ${state.userId}`
      : "填写服务地址和访问 Token 后重新连接";
  }

  async function loadModels() {
    try {
      const data = await api("/api/models");
      state.models = data.models || [];
      els.modelSelect.innerHTML = "";
      const def = document.createElement("option");
      def.value = "";
      def.textContent = "默认配置";
      els.modelSelect.appendChild(def);
      for (const m of state.models) {
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
    const data = await api("/api/projects");
    state.projects = data.projects || [];
    renderProjects();
    if (!silent) {
      toast(`已加载 ${state.projects.length} 个项目`);
    }
    if (state.selectedProjectId) {
      const still = state.projects.find((p) => p.id === state.selectedProjectId);
      if (still) selectProject(still.id, { reloadJobs: false });
      else selectProject(null);
    }
  }

  function renderProjects() {
    els.projectList.innerHTML = "";
    els.projectsEmpty.hidden = state.projects.length > 0;
    for (const project of state.projects) {
      const li = document.createElement("li");
      li.className = "project-item" + (project.id === state.selectedProjectId ? " active" : "");
      li.dataset.id = project.id;
      const badges = [];
      if (project.has_apk) badges.push('<span class="badge apk">APK</span>');
      if (project.latest_status) {
        badges.push(
          `<span class="badge ${escapeHtml(project.latest_status)}">${escapeHtml(statusLabel(project.latest_status))}</span>`,
        );
      }
      li.innerHTML = `
        <div class="name">${escapeHtml(project.name || project.id)}</div>
        <div class="id">${escapeHtml(project.id)}</div>
        <div class="badges">${badges.join("")}</div>
      `;
      li.addEventListener("click", () => selectProject(project.id));
      els.projectList.appendChild(li);
    }
  }

  async function selectProject(projectId, { reloadJobs = true } = {}) {
    state.selectedProjectId = projectId;
    renderProjects();
    const project = state.projects.find((p) => p.id === projectId);
    const enabled = Boolean(project);
    els.promptInput.disabled = !enabled;
    els.btnSend.disabled = !enabled || Boolean(state.pollTimer);
    els.btnBrowseFiles.disabled = !enabled;
    els.btnDownloadApk.disabled = !enabled || !project?.has_apk;
    els.btnDeleteProject.disabled = !enabled;
    els.jobHistory.disabled = !enabled;
    els.traceConversation.disabled = !enabled;
    els.btnTraceRefresh.disabled = !enabled;

    if (!project) {
      els.projectTitle.textContent = "选择项目";
      els.projectMeta.textContent = "从左侧选择或创建一个项目";
      els.jobHistory.innerHTML = '<option value="">最近任务</option>';
      state.traceConversationId = null;
      state.traceTurnId = null;
      state.traceConversations = [];
      renderTraceEmpty("先选择项目。");
      return;
    }

    els.projectTitle.textContent = project.name || project.id;
    els.projectMeta.textContent = `${project.package || project.package_name || "—"} · ${project.id}`;

    loadTraceConversations().catch(() => renderTraceEmpty("对话列表加载失败。"));

    if (reloadJobs) {
      await loadJobHistory(project.id);
      if (project.latest_task_id) {
        await loadJob(project.latest_task_id, { startIfActive: true });
      } else {
        clearTimeline();
        updateJobStats({ status: "—", events: [] });
        els.summaryText.textContent = "该项目暂无任务。";
        els.changeList.innerHTML = "";
        els.changesEmpty.hidden = false;
        els.logText.textContent = "—";
      }
    }
  }

  async function loadJobHistory(projectId) {
    const data = await api(`/api/jobs?project_id=${encodeURIComponent(projectId)}`);
    const jobs = data.jobs || [];
    els.jobHistory.innerHTML = '<option value="">最近任务</option>';
    for (const job of jobs.slice(0, 20)) {
      const opt = document.createElement("option");
      opt.value = job.id;
      const prompt = (job.prompt || "").slice(0, 28);
      opt.textContent = `${job.id} · ${statusLabel(job.status)} · ${prompt}`;
      els.jobHistory.appendChild(opt);
    }
  }

  async function loadJob(jobId, { startIfActive = false } = {}) {
    const data = await api(`/api/jobs/${jobId}`);
    const job = data.job;
    state.currentJobId = job.id;
    els.jobHistory.value = job.id;
    await syncJob(job, { appendOnly: false });
    syncTraceWithJob(job).catch(() => {});
    const active = job.status === "queued" || job.status === "running";
    if (startIfActive && active) startPolling(job.id);
  }

  async function createProject(name, packageName) {
    const body = { name };
    if (packageName) body.package = packageName;
    const project = await api("/api/projects", { method: "POST", body });
    await refreshProjects({ silent: true });
    await selectProject(project.id);
    toast(`已创建 ${project.name}`);
  }

  async function deleteProject() {
    const id = state.selectedProjectId;
    if (!id) return;
    if (!confirm(`确认删除项目 ${id}？此操作不可恢复。`)) return;
    await api(`/api/projects/${id}`, { method: "DELETE" });
    state.selectedProjectId = null;
    await refreshProjects({ silent: true });
    selectProject(null);
    toast("项目已删除");
  }

  async function sendAsk() {
    const projectId = state.selectedProjectId;
    const prompt = els.promptInput.value.trim();
    if (!projectId || !prompt) {
      toast("请选择项目并填写提示词");
      return;
    }
    const body = {
      prompt,
      auto_fallback: els.autoFallback.checked,
    };
    const provider = els.modelSelect.value;
    if (provider) body.provider = provider;

    els.btnSend.disabled = true;
    clearTimeline();
    els.summaryText.textContent = "任务提交中…";
    try {
      const data = await api(`/api/projects/${projectId}/ask`, {
        method: "POST",
        body,
      });
      const job = data.job;
      state.currentJobId = job.id;
      els.promptInput.value = "";
      await loadJobHistory(projectId);
      els.jobHistory.value = job.id;
      await syncJob(job, { appendOnly: false });
      startPolling(job.id);
      toast(`任务已创建: ${job.id}`);
    } catch (err) {
      els.btnSend.disabled = false;
      toast(err.message);
      els.summaryText.textContent = err.message;
    }
  }

  async function stopJob() {
    if (!state.currentJobId) return;
    els.btnStop.disabled = true;
    try {
      const data = await api(`/api/jobs/${state.currentJobId}/cancel`, {
        method: "POST",
        body: {},
      });
      await syncJob(data.job);
      toast("已请求停止");
    } catch (err) {
      els.btnStop.disabled = false;
      toast(err.message);
    }
  }

  async function downloadApk() {
    const id = state.selectedProjectId;
    if (!id) return;
    try {
      const res = await fetch(`${state.baseUrl}/api/projects/${id}/apk`, {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "下载失败");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${id}.apk`;
      a.click();
      URL.revokeObjectURL(url);
      toast("APK 已开始下载");
    } catch (err) {
      toast(err.message);
    }
  }

  async function loadBuildLog() {
    if (!state.currentJobId) return;
    els.btnLoadLog.disabled = true;
    try {
      const data = await api(`/api/jobs/${state.currentJobId}/log`);
      els.logText.textContent = data.content || "（空日志）";
      document.querySelector('.tab[data-tab="log"]').click();
    } catch (err) {
      els.logText.textContent = err.message;
      toast(err.message);
    } finally {
      els.btnLoadLog.disabled = false;
    }
  }

  const TURN_STATUS_LABELS = {
    queued: "排队中",
    running: "运行中",
    awaiting_approval: "等待审批",
    succeeded: "成功",
    failed: "失败",
    canceled: "已取消",
    interrupted: "已中断",
    paused: "已暂停",
  };

  function formatClock(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
  }

  function formatMs(ms) {
    if (ms == null) return "—";
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`;
  }

  function renderTraceEmpty(message) {
    els.traceTurnList.innerHTML = "";
    els.traceDetail.hidden = true;
    els.traceEmpty.textContent = message || "选择对话后查看 Turn 执行追踪。";
    els.traceEmpty.hidden = false;
  }

  function markActiveTurn(turnId) {
    els.traceTurnList.querySelectorAll(".trace-turn").forEach((el) => {
      el.classList.toggle("active", el.dataset.id === turnId);
    });
  }

  async function loadTraceConversations({ keepSelection = false } = {}) {
    const projectId = state.selectedProjectId;
    if (!projectId) {
      renderTraceEmpty("先选择项目。");
      return;
    }
    const data = await api(`/api/projects/${projectId}/conversations`);
    state.traceConversations = data.conversations || [];
    const previous = state.traceConversationId;
    els.traceConversation.innerHTML = "";
    const def = document.createElement("option");
    def.value = "";
    def.textContent = "选择对话";
    els.traceConversation.appendChild(def);
    for (const conv of state.traceConversations) {
      const opt = document.createElement("option");
      opt.value = conv.id;
      opt.textContent = `${conv.title || conv.id} (${conv.turn_count ?? 0})`;
      els.traceConversation.appendChild(opt);
    }
    const stillThere =
      keepSelection &&
      previous &&
      state.traceConversations.some((c) => c.id === previous);
    const target = stillThere ? previous : state.traceConversations[0]?.id || "";
    state.traceConversationId = target || null;
    els.traceConversation.value = target;
    if (target) await loadTraceTurns();
    else renderTraceEmpty("该项目暂无对话。");
  }

  async function loadTraceTurns({ selectTaskId = null } = {}) {
    const conversationId = state.traceConversationId;
    if (!conversationId) return;
    const data = await api(`/api/conversations/${conversationId}/turns`);
    state.traceTurns = data.turns || [];
    if (!state.traceTurns.length) {
      renderTraceEmpty("该对话暂无 Turn。");
      return;
    }
    els.traceEmpty.hidden = true;
    els.traceTurnList.innerHTML = "";
    for (const turn of state.traceTurns) {
      const li = document.createElement("li");
      li.className = "trace-turn" + (turn.id === state.traceTurnId ? " active" : "");
      li.dataset.id = turn.id;
      const counts = turn.event_counts || {};
      const toolCalls = counts.tool_call || 0;
      const approvals = counts.approval_required || 0;
      const subParts = [`tools ${toolCalls}`];
      if (approvals) subParts.push(`审批 ${approvals}`);
      if (turn.task_id) subParts.push(turn.task_id.slice(0, 10));
      li.innerHTML = `
        <div class="trace-turn-head">
          <span class="badge turn-${escapeHtml(turn.status || "unknown")}">${escapeHtml(
            TURN_STATUS_LABELS[turn.status] || turn.status || "—",
          )}</span>
          <span class="mono trace-turn-time">${formatClock(turn.created_at)}</span>
        </div>
        <div class="trace-turn-preview">${escapeHtml(turn.user_preview || "（无输入）")}</div>
        <div class="trace-turn-sub mono">${subParts.map(escapeHtml).join(" · ")}</div>
      `;
      li.addEventListener("click", () => {
        state.traceTurnId = turn.id;
        markActiveTurn(turn.id);
        loadTurnTrace().catch((e) => toast(e.message));
      });
      els.traceTurnList.appendChild(li);
    }

    let wanted = null;
    if (selectTaskId) {
      const match = state.traceTurns.find((t) => t.task_id === selectTaskId);
      wanted = match ? match.id : null;
    }
    if (
      !wanted &&
      state.traceTurnId &&
      state.traceTurns.some((t) => t.id === state.traceTurnId)
    ) {
      wanted = state.traceTurnId;
    }
    wanted = wanted || state.traceTurns[0].id;
    state.traceTurnId = wanted;
    markActiveTurn(wanted);
    await loadTurnTrace();
  }

  async function loadTurnTrace() {
    const conversationId = state.traceConversationId;
    const turnId = state.traceTurnId;
    if (!conversationId || !turnId) return;
    const trace = await api(
      `/api/conversations/${conversationId}/turns/${turnId}/trace`,
    );

    const meta = [];
    const status = trace.status || "unknown";
    meta.push(
      `<span class="badge turn-${escapeHtml(status)}">${escapeHtml(
        TURN_STATUS_LABELS[trace.status] || trace.status || "—",
      )}</span>`,
    );
    if (trace.trace_id) {
      meta.push(`<span class="mono">trace ${escapeHtml(trace.trace_id.slice(0, 12))}</span>`);
    }
    if (trace.queue_ms != null) meta.push(`排队 ${formatMs(trace.queue_ms)}`);
    if (trace.total_ms != null) meta.push(`总耗时 ${formatMs(trace.total_ms)}`);
    if (trace.provider || trace.model) {
      meta.push(`${escapeHtml(trace.provider || "?")}/${escapeHtml(trace.model || "?")}`);
    }
    meta.push(`${(trace.steps || []).length} 步`);
    els.traceMeta.innerHTML = meta.join('<span class="trace-meta-sep">·</span>');

    els.traceSteps.innerHTML = "";
    for (const step of trace.steps || []) {
      const li = document.createElement("li");
      li.className = "trace-step";
      li.dataset.type = step.type || "";
      const metaParts = [];
      if (step.duration_ms > 0) metaParts.push(`+${formatMs(step.duration_ms)}`);
      if (step.tool_call_id) metaParts.push(`call ${step.tool_call_id.slice(0, 10)}`);
      if (step.approval_id) metaParts.push(`approval ${step.approval_id.slice(0, 10)}`);
      li.innerHTML = `
        <div class="trace-step-rail"><span class="trace-dot"></span></div>
        <div class="trace-step-body">
          <div class="trace-step-head">
            <span class="trace-step-label">${escapeHtml(step.label || step.type || "事件")}</span>
            <span class="mono trace-step-time">${formatClock(step.at)}</span>
          </div>
          ${step.detail ? `<div class="trace-step-detail">${escapeHtml(step.detail)}</div>` : ""}
          ${
            metaParts.length
              ? `<div class="trace-step-meta mono">${metaParts.map(escapeHtml).join(" · ")}</div>`
              : ""
          }
        </div>
      `;
      els.traceSteps.appendChild(li);
    }
    els.traceDetail.hidden = false;
  }

  async function syncTraceWithJob(job) {
    if (!state.connected) return;
    const conversationId = job.conversation_id;
    if (!conversationId) return;
    if (state.traceConversationId !== conversationId) {
      if (!state.traceConversations.some((c) => c.id === conversationId)) {
        await loadTraceConversations({ keepSelection: true });
      }
      state.traceConversationId = conversationId;
      els.traceConversation.value = conversationId;
    }
    await loadTraceTurns({ selectTaskId: job.id });
  }

  async function openFiles() {
    if (!state.selectedProjectId) return;
    state.filePath = ".";
    state.fileEditPath = null;
    els.fileContent.value = "";
    els.fileContent.disabled = true;
    els.btnSaveFile.disabled = true;
    els.fileTitle.textContent = "预览";
    els.filesDialog.showModal();
    await listFiles(".");
  }

  async function listFiles(path) {
    const projectId = state.selectedProjectId;
    const data = await api(
      `/api/projects/${projectId}/files?path=${encodeURIComponent(path)}`,
    );
    state.filePath = data.path || path;
    els.filesPath.textContent = state.filePath;
    els.fileList.innerHTML = "";

    if (state.filePath !== "." && state.filePath !== "") {
      const up = document.createElement("li");
      up.className = "file-item";
      up.textContent = "../";
      up.addEventListener("click", () => {
        const parts = state.filePath.split("/").filter(Boolean);
        parts.pop();
        listFiles(parts.length ? parts.join("/") : ".");
      });
      els.fileList.appendChild(up);
    }

    const entries = data.entries || [];
    const list = Array.isArray(entries) ? entries : [];
    for (const entry of list) {
      const name = entry.name || entry.path || String(entry);
      const type = entry.type || (String(name).endsWith("/") ? "dir" : "file");
      const entryPath = entry.path || (state.filePath === "." ? name : `${state.filePath}/${name}`);
      const li = document.createElement("li");
      li.className = "file-item";
      li.textContent = type === "dir" ? `${name.replace(/\/$/, "")}/` : name;
      li.addEventListener("click", async () => {
        if (type === "dir") await listFiles(entryPath.replace(/\/$/, ""));
        else await openFile(entryPath);
      });
      els.fileList.appendChild(li);
    }
  }

  async function openFile(path) {
    const projectId = state.selectedProjectId;
    const data = await api(
      `/api/projects/${projectId}/files/content?path=${encodeURIComponent(path)}`,
    );
    state.fileEditPath = data.path || path;
    state.fileWritable = Boolean(data.writable);
    els.fileTitle.textContent = state.fileEditPath;
    els.fileContent.value = data.content || "";
    els.fileContent.disabled = !state.fileWritable;
    els.btnSaveFile.disabled = !state.fileWritable;
    if (data.truncated) toast("文件内容已截断");
  }

  async function saveFile() {
    if (!state.fileEditPath || !state.fileWritable) return;
    await api(`/api/projects/${state.selectedProjectId}/files/content`, {
      method: "PUT",
      body: { path: state.fileEditPath, content: els.fileContent.value },
    });
    toast("已保存");
  }

  const TAB_PANELS = {
    summary: "tabSummary",
    changes: "tabChanges",
    log: "tabLog",
    trace: "tabTrace",
  };

  function bindTabs() {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        tab.classList.add("active");
        const panel = document.getElementById(TAB_PANELS[tab.dataset.tab] || "");
        panel?.classList.add("active");
      });
    });
  }

  function bindEvents() {
    els.btnConnect.addEventListener("click", () => connect().catch((e) => toast(e.message)));
    els.btnPair.addEventListener("click", () => pair().catch((e) => toast(e.message)));
    els.btnDisconnect.addEventListener("click", hideSettings);
    els.btnSettings.addEventListener("click", showSettings);
    els.btnRefreshProjects.addEventListener("click", () =>
      refreshProjects().catch((e) => toast(e.message)),
    );
    els.btnNewProject.addEventListener("click", () => els.createDialog.showModal());
    els.createForm.addEventListener("submit", async (ev) => {
      const submitter = ev.submitter;
      if (submitter && submitter.value === "cancel") return;
      ev.preventDefault();
      const fd = new FormData(els.createForm);
      const name = String(fd.get("name") || "").trim();
      const pkg = String(fd.get("package") || "").trim();
      if (!name) return;
      els.createDialog.close();
      els.createForm.reset();
      try {
        await createProject(name, pkg || null);
      } catch (err) {
        toast(err.message);
      }
    });
    els.btnDeleteProject.addEventListener("click", () =>
      deleteProject().catch((e) => toast(e.message)),
    );
    els.btnSend.addEventListener("click", () => sendAsk().catch((e) => toast(e.message)));
    els.btnStop.addEventListener("click", () => stopJob().catch((e) => toast(e.message)));
    els.btnDownloadApk.addEventListener("click", () =>
      downloadApk().catch((e) => toast(e.message)),
    );
    els.btnLoadLog.addEventListener("click", () =>
      loadBuildLog().catch((e) => toast(e.message)),
    );
    els.btnBrowseFiles.addEventListener("click", () =>
      openFiles().catch((e) => toast(e.message)),
    );
    els.btnCloseFiles.addEventListener("click", () => els.filesDialog.close());
    els.btnSaveFile.addEventListener("click", () =>
      saveFile().catch((e) => toast(e.message)),
    );
    els.traceConversation.addEventListener("change", () => {
      state.traceConversationId = els.traceConversation.value || null;
      state.traceTurnId = null;
      if (state.traceConversationId) {
        loadTraceTurns().catch((e) => toast(e.message));
      } else {
        renderTraceEmpty("选择对话后查看 Turn 执行追踪。");
      }
    });
    els.btnTraceRefresh.addEventListener("click", () =>
      loadTraceConversations({ keepSelection: true }).catch((e) => toast(e.message)),
    );
    els.jobHistory.addEventListener("change", () => {
      const id = els.jobHistory.value;
      if (id) loadJob(id, { startIfActive: true }).catch((e) => toast(e.message));
    });
    els.promptInput.addEventListener("keydown", (ev) => {
      if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
        ev.preventDefault();
        sendAsk().catch((e) => toast(e.message));
      }
    });
    bindTabs();
  }

  function init() {
    loadPrefs();
    if (!els.serverUrl.value) {
      els.serverUrl.value = window.location.origin;
    }
    bindEvents();
    connect().catch((e) => toast(e.message));
  }

  init();
})();
