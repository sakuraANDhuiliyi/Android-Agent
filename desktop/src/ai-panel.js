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
    aiMessages: document.getElementById("aiMessages"),
    aiEmpty: document.getElementById("aiEmpty"),
    aiContext: document.getElementById("aiContext"),
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
    running: false,
    sawTextForJob: false,
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
        succeeded: "已完成",
        failed: "失败",
        canceled: "已停止",
      }[status] || status || "—"
    );
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
      case "model_switch":
      case "provider_switch":
        appendStatusChip(event.message || event.type);
        break;
      case "text":
        state.sawTextForJob = true;
        appendAssistantText(event.content || event.message || "");
        break;
      case "tool_call":
      case "tool_result":
        appendToolCard(event);
        break;
      case "usage": {
        const u = event.usage || {};
        appendStatusChip(
          `Token ${u.input_tokens ?? "?"} → ${u.output_tokens ?? "?"} (Σ ${u.total_tokens ?? "?"})`,
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
    setRunning(true);
    state.watcher = client.watchJob(jobId, async (payload) => {
      if (payload.kind === "event" && payload.event) {
        handleEvent(payload.event);
      }
      if (payload.kind === "job" && payload.job) {
        /* stats reserved */
      }
      if (payload.kind === "done") {
        // Ignore stale done callbacks after the user switched conversations
        if (state.currentJobId !== jobId) return;
        stopWatcher();
        setRunning(false);
        const status = payload.status;
        try {
          const data = await client.job(jobId);
          if (state.currentJobId !== jobId) return;
          if (data.job?.changed_files?.length) appendChanges(data.job.changed_files);
          const finalText = data.job?.result || data.job?.final_message || payload.result;
          if (finalText && status === "succeeded" && !state.sawTextForJob) {
            appendAssistantText(finalText);
          }
          if (status === "succeeded") {
            appendStatusChip("任务成功", "ok");
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
          if (status === "succeeded") appendStatusChip("任务成功", "ok");
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
      const active = (data.jobs || []).find((j) => j.status === "queued" || j.status === "running");
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
      if (job.status === "queued" || job.status === "running") {
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
    if (els.btnNewConversation) els.btnNewConversation.disabled = !state.connected || !state.selectedProjectId || state.running;
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
  };
})();
