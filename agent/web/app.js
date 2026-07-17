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
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    serverUrl: $("serverUrl"),
    apiToken: $("apiToken"),
    btnConnect: $("btnConnect"),
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
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data.serverUrl) els.serverUrl.value = data.serverUrl;
      // 自用默认不恢复 Token，始终走 local
    } catch (_) {
      /* ignore */
    }
  }

  function savePrefs() {
    localStorage.setItem(
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

  function hideSettings() {
    els.connectPanel.hidden = true;
  }

  function showSettings() {
    els.connectPanel.hidden = false;
    els.connectStatus.textContent = state.connected
      ? `当前用户 ${state.userId || "local"}（Token 可留空）`
      : "填写服务地址后重新连接";
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

    if (!project) {
      els.projectTitle.textContent = "选择项目";
      els.projectMeta.textContent = "从左侧选择或创建一个项目";
      els.jobHistory.innerHTML = '<option value="">最近任务</option>';
      return;
    }

    els.projectTitle.textContent = project.name || project.id;
    els.projectMeta.textContent = `${project.package || project.package_name || "—"} · ${project.id}`;

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

  function bindTabs() {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        tab.classList.add("active");
        const panel = document.getElementById(
          tab.dataset.tab === "summary"
            ? "tabSummary"
            : tab.dataset.tab === "changes"
              ? "tabChanges"
              : "tabLog",
        );
        panel?.classList.add("active");
      });
    });
  }

  function bindEvents() {
    els.btnConnect.addEventListener("click", () => connect().catch((e) => toast(e.message)));
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
    // 自用默认不带 Token，直接连本机 local 用户
    if (!els.apiToken.value.trim()) {
      els.apiToken.value = "";
    }
    bindEvents();
    connect().catch((e) => toast(e.message));
  }

  init();
})();
