(() => {
  "use strict";

  /**
   * AI panel controller. Owns connection, projects, conversations, composer,
   * job watching and approval resolution. All timeline rendering is delegated
   * to window.AgentTimeline (view) fed by window.Timeline (normalizer).
   */

  const STORAGE_KEY = "android-agent-desktop";
  const desktop = window.agentDesktop || {};
  const client = new window.AgentApi();

  const els = {
    connPill: document.getElementById("connPill"),
    statusConn: document.getElementById("statusConn"),
    aiStatusDot: document.getElementById("aiStatusDot"),
    aiStatusText: document.getElementById("aiStatusText"),
    aiMetaExtra: document.getElementById("aiMetaExtra"),
    aiTaskControls: document.getElementById("aiTaskControls"),
    projectSelect: document.getElementById("projectSelect"),
    modelSelect: document.getElementById("modelSelect"),
    runModeSelect: document.getElementById("runModeSelect"),
    autoFallback: document.getElementById("autoFallback"),
    jobHistory: document.getElementById("jobHistory"),
    conversationSelect: document.getElementById("conversationSelect"),
    aiMessages: document.getElementById("aiMessages"),
    aiEmpty: document.getElementById("aiEmpty"),
    aiContext: document.getElementById("aiContext"),
    approvalDock: document.getElementById("approvalDock"),
    promptInput: document.getElementById("promptInput"),
    btnSend: document.getElementById("btnSend"),
    btnStop: document.getElementById("btnStop"),
    btnSteer: document.getElementById("btnSteer"),
    btnFollowUp: document.getElementById("btnFollowUp"),
    composerModes: document.getElementById("composerModes"),
    btnNewChat: document.getElementById("btnNewChat"),
    btnAiMore: document.getElementById("btnAiMore"),
    aiMoreMenu: document.getElementById("aiMoreMenu"),
    btnCloseAi: document.getElementById("btnCloseAi"),
    btnAddContext: document.getElementById("btnAddContext"),
    contextMenu: document.getElementById("contextMenu"),
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
    settingsHint: document.getElementById("settingsHint"),
    btnPair: document.getElementById("btnPair"),
    btnOpenSettings: document.getElementById("btnOpenSettings"),
    btnPauseJob: document.getElementById("btnPauseJob"),
    btnResumeJob: document.getElementById("btnResumeJob"),
    btnHeaderStop: document.getElementById("btnHeaderStop"),
    btnSidebarNewConversation: document.getElementById("btnSidebarNewConversation"),
    createProjectDialog: document.getElementById("createProjectDialog"),
    createProjectForm: document.getElementById("createProjectForm"),
    diffToolbar: document.getElementById("diffToolbar"),
    diffTitle: document.getElementById("diffTitle"),
    btnAcceptDiff: document.getElementById("btnAcceptDiff"),
    btnRejectDiff: document.getElementById("btnRejectDiff"),
    btnCloseDiff: document.getElementById("btnCloseDiff"),
    monacoDiffHost: document.getElementById("monacoDiffHost"),
  };

  const state = {
    connected: false,
    userId: "",
    projects: [],
    selectedProjectId: null,
    conversations: [],
    conversationId: null,
    currentJobId: null,
    jobStatus: null,
    running: false,
    pauseRequested: false,
    cancelRequested: false,
    controlBusy: null,
    watcher: null,
    serverManaged: false,
    serverRunning: false,
    serverBusy: false,
    reconnectBusy: false,
    phoneUrl: "",
    runMode: "workspace",
    runInputMode: "steer",
    contextChips: [],
    // Monotonic token guarding every async conversation load: responses that
    // arrive after the user switched away (A→B→A) are dropped, never merged.
    loadToken: 0,
    // Per-conversation scroll positions restored on switch-back.
    scrollTops: new Map(),
    // Backward pagination cursor for the current conversation history.
    historyCursor: null, // { minSeq, hasMore }
  };

  // Last selection persisted before shutdown; applied once on the first
  // successful connect after launch so a restart restores the conversation.
  const restoredSelection = { projectId: null, conversationId: null };

  const HISTORY_PAGE_LIMIT = 300;
  const REVIEW_PREPARE_RETRIES = 8;
  const REVIEW_PREPARE_INTERVAL_MS = 750;
  const ACTIVE_JOB_STATUSES = new Set([
    "queued",
    "running",
    "awaiting_approval",
    "paused",
    "cancel_requested",
  ]);

  const timeline = window.Timeline.createStore();
  let view = null;
  let emptyNode = null;
  let loadEarlierBtn = null;
  let loadingEarlier = false;
  let reviewSession = null; // { files, index, turnId, projectId, truncated }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // —— Small helpers ——

  function toast(msg) {
    window.EditorApp?.toast?.(msg);
  }

  function loadPrefs() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data.serverUrl) els.serverUrl.value = data.serverUrl;
      if (data.autoFallback) els.autoFallback.checked = true;
      if (data.runMode && ["read_only", "workspace", "ask"].includes(data.runMode)) {
        state.runMode = data.runMode;
        if (els.runModeSelect) els.runModeSelect.value = data.runMode;
      }
      if (data.projectId) restoredSelection.projectId = data.projectId;
      if (data.conversationId) restoredSelection.conversationId = data.conversationId;
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
        runMode: state.runMode,
        projectId: state.selectedProjectId || "",
        conversationId: state.conversationId || "",
      }),
    );
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

  function statusLabel(status) {
    return (
      {
        queued: "排队中",
        running: "运行中",
        paused: "已暂停",
        awaiting_approval: "等待审批",
        cancel_requested: "正在停止",
        succeeded: "已完成",
        failed: "失败",
        canceled: "已停止",
        interrupted: "已中断",
      }[status] || status || "—"
    );
  }

  function hasActiveJob() {
    return Boolean(
      state.currentJobId &&
        (state.running || ACTIVE_JOB_STATUSES.has(state.jobStatus)),
    );
  }

  // —— Timeline rendering glue ——

  function showEmpty() {
    if (!emptyNode) return;
    if (!timeline.items().length && !els.aiMessages.contains(emptyNode)) {
      els.aiMessages.appendChild(emptyNode);
    }
  }

  function hideEmpty() {
    if (emptyNode && emptyNode.parentElement) emptyNode.parentElement.removeChild(emptyNode);
  }

  function renderTimeline(opts = {}) {
    if (!view) return;
    const items = timeline.items();
    if (items.length) hideEmpty();
    else showEmpty();
    view.update(items, { immediate: Boolean(opts.immediate) });
    renderApprovalDock();
    updateStatusDot();
  }

  function updateStatusDot() {
    if (!els.aiStatusDot) return;
    const pending = timeline.pendingApprovals().length;
    const status = state.cancelRequested ? "cancel_requested" : state.jobStatus;
    let name = "off";
    let label = "未连接";
    let title = "未连接";
    if (state.connected) {
      name = "idle";
      label = "已连接 · 空闲";
      title = "Agent 已连接，当前空闲";
      if (pending || status === "awaiting_approval") {
        name = "awaiting";
        label = "等待审批";
        title = "任务正在等待你的审批";
      } else if (state.cancelRequested) {
        name = "running";
        label = "正在停止";
        title = "已发送停止请求";
      } else if (state.pauseRequested && status === "running") {
        name = "running";
        label = "正在暂停";
        title = "任务将在安全检查点暂停";
      } else if (status === "paused") {
        name = "awaiting";
        label = "已暂停";
        title = "任务已暂停，可继续或停止";
      } else if (state.running || ACTIVE_JOB_STATUSES.has(status)) {
        name = "running";
        label = status === "queued" ? "排队中" : "正在运行";
        title = status === "queued" ? "任务正在排队" : "任务运行中";
      } else if (state.currentJobId && status) {
        label = statusLabel(status);
        title = `当前任务${statusLabel(status)}`;
      }
    }
    els.aiStatusDot.dataset.state = name;
    els.aiStatusDot.title = title;
    if (els.aiStatusText) {
      els.aiStatusText.textContent = label;
      els.aiStatusText.title = title;
    }
    if (els.aiMetaExtra) {
      els.aiMetaExtra.textContent = "";
    }
    updateJobControls();
  }

  function updateJobControls() {
    const active = state.connected && hasActiveJob();
    const status = state.jobStatus;
    const busy = Boolean(state.controlBusy);
    const canPause = active && !state.cancelRequested && (status === "queued" || status === "running");
    const canResume = active && !state.cancelRequested && status === "paused";
    const canStop = active;

    if (els.btnPauseJob) {
      els.btnPauseJob.hidden = !canPause;
      els.btnPauseJob.disabled = busy || state.pauseRequested;
      els.btnPauseJob.textContent = state.pauseRequested ? "暂停中…" : "暂停";
    }
    if (els.btnResumeJob) {
      els.btnResumeJob.hidden = !canResume;
      els.btnResumeJob.disabled = busy;
      els.btnResumeJob.textContent = state.controlBusy === "resume" ? "继续中…" : "继续";
    }
    if (els.btnHeaderStop) {
      els.btnHeaderStop.hidden = !canStop;
      els.btnHeaderStop.disabled = busy || state.cancelRequested;
      els.btnHeaderStop.textContent = state.cancelRequested ? "停止中…" : "停止";
    }
    if (els.aiTaskControls) {
      els.aiTaskControls.hidden = !(canPause || canResume || canStop);
    }
  }

  function renderApprovalDock() {
    const pending = timeline.pendingApprovals();
    if (!pending.length && state.jobStatus === "awaiting_approval" && state.currentJobId) {
      // Orphaned wait: server restarted mid-approval; offer an honest escape.
      els.approvalDock.textContent = "";
      els.approvalDock.hidden = false;
      const box = document.createElement("div");
      box.className = "tl-dock-orphan";
      const text = document.createElement("span");
      text.textContent = "任务仍在等待审批，但审批通道已失效（常见于服务重启）。";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "danger-btn sm";
      btn.textContent = "停止任务";
      btn.addEventListener("click", () => stopJob());
      box.appendChild(text);
      box.appendChild(btn);
      els.approvalDock.appendChild(box);
      return;
    }
    window.AgentTimeline.renderApprovalDock(els.approvalDock, pending, {
      onJump: (approvalId) => view?.focusApproval(approvalId),
    });
  }

  const callbacks = {
    copyText(text) {
      navigator.clipboard
        ?.writeText(String(text ?? ""))
        .then(() => toast("已复制"))
        .catch(() => toast("复制失败"));
    },

    async openFile(path, line) {
      if (!path) return;
      try {
        let abs = path;
        if (!/^([A-Za-z]:[\\/]|\/)/.test(path)) {
          const project = state.projects.find((p) => p.id === state.selectedProjectId);
          const base = project?.workspace || window.EditorApp?.getRoot?.();
          if (!base) {
            toast("无法定位文件：未打开工作区");
            return;
          }
          abs = await desktop.joinPath(base, path);
        }
        await window.EditorApp?.openPath?.(abs, undefined, line || 0);
      } catch (err) {
        toast(`打开文件失败: ${err.message}`);
      }
    },

    async resolveApproval(item, approved) {
      const jobId = item.jobId || state.currentJobId;
      if (!jobId) return;
      timeline.clearApprovalError(item.approvalId);
      renderTimeline();
      try {
        await client.resolveApproval(jobId, item.approvalId, approved);
        timeline.setApprovalDecision(item.approvalId, approved ? "approved" : "rejected");
      } catch (err) {
        // Keep the buttons; the approval is still pending server-side.
        timeline.markApprovalError(item.approvalId, err.message);
      }
      renderTimeline();
    },

    async reviewChanges(item, turn) {
      const projectId = state.selectedProjectId;
      const turnId = item.turnId || (turn && turn.turnId) || null;
      if (!projectId) {
        toast("请先选择项目");
        return;
      }
      if (!turnId) {
        toast("该改动记录缺少 turn 信息，无法审查");
        return;
      }
      await openTurnDiffReview(projectId, turnId);
    },

    async restoreCheckpoint(item) {
      const projectId = state.selectedProjectId;
      const checkpointId = item.content?.checkpointId;
      if (!projectId || !checkpointId) return;
      const ok = window.confirm("恢复检查点会覆盖当前工作区对应文件，确定继续吗？");
      if (!ok) return;
      try {
        await client.restoreCheckpoint(projectId, checkpointId);
        toast("已恢复到检查点");
        await window.EditorApp?.refreshTree?.({ silent: true });
      } catch (err) {
        toast(`恢复失败: ${err.message}`);
      }
    },
  };

  // —— Turn diff review (Monaco, checkpoint-blob based) ——
  //
  // The review NEVER reconstructs old content from the live workspace. The
  // backend serves exact before/after blobs captured by the before_turn and
  // after_turn checkpoints (GET /diff, GET /diff/file), so the diff stays
  // correct even if files kept changing after the turn finished.

  function languageFor(path) {
    const ext = (path.split(".").pop() || "").toLowerCase();
    return (
      {
        kt: "kotlin",
        kts: "kotlin",
        java: "java",
        xml: "xml",
        gradle: "groovy",
        json: "json",
        md: "markdown",
        js: "javascript",
        ts: "typescript",
        py: "python",
        sh: "shell",
        yaml: "yaml",
        yml: "yaml",
        properties: "ini",
        toml: "ini",
      }[ext] || "plaintext"
    );
  }

  /** Wait for the Monaco host instead of silently skipping via ?. chains. */
  async function ensureEditorReady(timeoutMs = 8000) {
    const ready = () =>
      window.EditorApp &&
      typeof window.EditorApp.openDiff === "function" &&
      typeof window.EditorApp.openDiffNotice === "function";
    if (ready()) return window.EditorApp;
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      await sleep(100);
      if (ready()) return window.EditorApp;
    }
    throw new Error("编辑器尚未准备好，无法打开 Diff");
  }

  /** Poll while the backend reports preparing (after_turn checkpoint pending). */
  async function fetchTurnDiffWithRetry(projectId, turnId) {
    let data = null;
    for (let attempt = 0; attempt <= REVIEW_PREPARE_RETRIES; attempt += 1) {
      data = await client.turnDiff(projectId, turnId);
      if (!data || data.status !== "preparing") return data;
      if (attempt === 0) toast("正在准备改动审查…");
      await sleep(REVIEW_PREPARE_INTERVAL_MS);
    }
    return data;
  }

  async function openTurnDiffReview(projectId, turnId) {
    let data;
    try {
      data = await fetchTurnDiffWithRetry(projectId, turnId);
    } catch (err) {
      toast(`审查改动失败: ${err.message}`);
      return;
    }
    if (!data) return;
    if (data.status === "preparing") {
      toast("改动审查仍在准备中（checkpoint 尚未写入），请稍后重试");
      return;
    }
    if (data.status === "empty" || (data.ok && !(data.files || []).length)) {
      toast("本轮没有文件改动");
      return;
    }
    if (!data.ok || data.status !== "ready") {
      toast(data.message || data.error || "该轮次无法审查改动");
      return;
    }
    const files = data.files || [];
    reviewSession = {
      files,
      index: 0,
      turnId,
      projectId,
      truncated: Boolean(data.truncated),
    };
    await showReviewFile(0);
  }

  function diffSwitcher() {
    let switcher = document.getElementById("diffFileSwitcher");
    if (!switcher) {
      switcher = document.createElement("select");
      switcher.id = "diffFileSwitcher";
      switcher.className = "ai-select sm";
      switcher.setAttribute("aria-label", "切换审查文件");
      switcher.addEventListener("change", () => showReviewFile(Number(switcher.value)));
      els.diffToolbar?.insertBefore(switcher, els.diffToolbar.querySelector(".diff-actions") || null);
    }
    return switcher;
  }

  function reviewTitleFor(session, file) {
    const label =
      file.change === "renamed" && file.old_path
        ? `${file.old_path} → ${file.path}`
        : file.path;
    return {
      label,
      text: `审查改动: ${label} (${session.index + 1}/${session.files.length})`,
    };
  }

  function updateDiffToolbar(session, file) {
    els.diffToolbar?.classList.add("review-mode");
    if (els.diffTitle) {
      const t = reviewTitleFor(session, file);
      els.diffTitle.textContent = t.text;
      els.diffTitle.title = t.label;
    }
    const switcher = diffSwitcher();
    switcher.textContent = "";
    session.files.forEach((f, i) => {
      const opt = document.createElement("option");
      opt.value = String(i);
      const mark = { added: "A", modified: "M", deleted: "D", renamed: "R" }[f.change] || "?";
      opt.textContent = `${mark} ${f.path}`;
      switcher.appendChild(opt);
    });
    switcher.value = String(session.index);
    // Truncation warning (recreated per session).
    let warn = document.getElementById("diffTruncatedWarn");
    if (session.truncated) {
      if (!warn) {
        warn = document.createElement("span");
        warn.id = "diffTruncatedWarn";
        warn.className = "diff-truncated-warn";
        els.diffToolbar?.insertBefore(warn, switcher.nextSibling);
      }
      warn.textContent = "Diff 过大，已截断";
      warn.title = "改动总量超出显示上限，仅展示部分内容";
    } else if (warn) {
      warn.remove();
    }
  }

  async function showReviewFile(index) {
    if (!reviewSession) return;
    const session = reviewSession;
    session.index = Math.max(0, Math.min(index, session.files.length - 1));
    const file = session.files[session.index];
    let app;
    try {
      app = await ensureEditorReady();
    } catch (err) {
      toast(err.message);
      return;
    }
    let detail;
    try {
      detail = await client.turnDiffFile(session.projectId, session.turnId, file.path);
    } catch (err) {
      toast(`读取改动内容失败: ${err.message}`);
      return;
    }
    if (!reviewSession || reviewSession !== session) return; // switched away
    if (!detail || !detail.ok) {
      if (detail && detail.status === "preparing") {
        toast("改动审查正在准备中，请稍后重试");
      } else {
        toast((detail && detail.message) || "无法读取该文件的改动内容");
      }
      return;
    }
    updateDiffToolbar(session, file);
    const titleInfo = reviewTitleFor(session, file);
    if (detail.binary) {
      // Binary files: metadata only, never fed to the text diff editor.
      app.openDiffNotice({
        title: titleInfo.text,
        message: `二进制文件，不提供文本 Diff。\n路径: ${file.path}\n改动类型: ${detail.change}\n改动前 SHA256: ${file.before_hash || "—"}\n改动后 SHA256: ${file.after_hash || "—"}`,
      });
      return;
    }
    app.openDiff({
      original: typeof detail.before_content === "string" ? detail.before_content : "",
      modified: typeof detail.after_content === "string" ? detail.after_content : "",
      path: file.path,
      language: detail.language || languageFor(file.path),
      title: titleInfo.text,
      review: true,
    });
  }

  function closeReviewSession() {
    if (!reviewSession) return;
    reviewSession = null;
    els.diffToolbar?.classList.remove("review-mode");
    document.getElementById("diffFileSwitcher")?.remove();
    document.getElementById("diffTruncatedWarn")?.remove();
  }

  // —— Server / connection ——

  function applyServerStatus(status) {
    state.serverRunning = Boolean(status.running);
    state.serverManaged = Boolean(status.managed);
    const port = status.port || 8000;
    const phone = status.phoneUrl || (status.lanIp ? `http://${status.lanIp}:${port}` : "");
    state.phoneUrl = phone;

    els.serverPortLabel.textContent = state.serverRunning ? `端口 ${port} · 运行中` : `端口 ${port} · 未运行`;
    els.phoneUrl.textContent = phone || "等待获取局域网地址…";
    els.btnStopServer.hidden = !state.serverManaged;
    els.btnStartServer.disabled = state.serverBusy || (state.serverRunning && state.serverManaged);
    els.btnStopServer.disabled = state.serverBusy || !state.serverManaged;
    els.btnStartServer.textContent = state.serverBusy
      ? "启动中…"
      : state.serverRunning && state.serverManaged
        ? "服务已启动"
        : state.serverRunning
          ? "重新连接"
          : "启动服务";
  }

  async function refreshServerStatus() {
    try {
      const status = await desktop.agentStatus();
      applyServerStatus(status);
      return status;
    } catch (_) {
      els.serverPortLabel.textContent = "端口 —";
      els.phoneUrl.textContent = "状态获取失败";
      return null;
    }
  }

  function isConfiguredLocalServer(baseUrl, status) {
    try {
      const url = new URL(baseUrl);
      const host = url.hostname.toLowerCase();
      const port = Number(url.port || (url.protocol === "https:" ? 443 : 80));
      return (
        ["127.0.0.1", "localhost", "::1"].includes(host) &&
        port === Number(status?.port || 8000)
      );
    } catch (_) {
      return false;
    }
  }

  async function startServer() {
    if (state.serverBusy) return;
    state.serverBusy = true;
    els.btnStartServer.textContent = "启动中…";
    try {
      const result = await desktop.agentStart();
      applyServerStatus(result);
      if (!result.ok) {
        toast(result.error || "启动失败");
        return;
      }
      toast(result.alreadyRunning && !result.managed ? "服务已在运行，正在连接…" : `服务已启动 · 端口 ${result.port}`);
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
        state.pauseRequested = false;
        state.cancelRequested = false;
        state.controlBusy = null;
        setConn("idle", "未连接");
        updateComposer();
        updateStatusDot();
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
      updateComposer();
      updateStatusDot();
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
      const restorePid = restoredSelection.projectId;
      if (!state.selectedProjectId && restorePid && state.projects.some((p) => p.id === restorePid)) {
        // Restart restore: prefer the persisted conversation over the
        // "conversation with most turns" default.
        restoredSelection.projectId = null;
        state.conversationId = restoredSelection.conversationId || null;
        restoredSelection.conversationId = null;
        await selectProject(restorePid, { openWorkspace: false });
      } else {
        await maybeAutoSelectProject();
      }
      updateComposer();
      updateStatusDot();
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
      updateComposer();
      updateStatusDot();
      if (!silent) toast(`连接失败: ${err.message}`);
      throw err;
    }
  }

  async function pair() {
    const baseUrl = (els.serverUrl.value || "http://127.0.0.1:8000").trim().replace(/\/+$/, "");
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

  // —— Projects / conversations ——

  async function loadModels() {
    try {
      const data = await client.models();
      els.modelSelect.textContent = "";
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
    els.projectSelect.textContent = "";
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
    if (prev && state.projects.some((p) => p.id === prev)) els.projectSelect.value = prev;
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
    savePrefs();
    if (projectId) els.projectSelect.value = projectId;
    updateComposer();

    if (!projectId) {
      state.loadToken += 1; // drop any in-flight conversation loads
      state.conversations = [];
      state.conversationId = null;
      state.historyCursor = null;
      renderConversationSelect();
      els.jobHistory.textContent = "";
      els.jobHistory.disabled = true;
      timeline.reset();
      view?.reset();
      updateLoadEarlierButton();
      showEmpty();
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
    select.textContent = "";
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
      if (!preferred) {
        const withTurns = state.conversations.find(
          (c) => (c.turn_count || (c.turns || []).length || 0) > 0,
        );
        preferred = (withTurns || state.conversations[0]).id;
      }
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

  // —— Progressive history loading ——
  //
  // First screen = the newest page of events (last few turns). Older history
  // is paged backward on demand via before_seq; nothing is ever silently
  // truncated at a fixed loop count.

  function updateLoadEarlierButton() {
    if (!loadEarlierBtn) return;
    const hasMore = Boolean(state.historyCursor && state.historyCursor.hasMore);
    loadEarlierBtn.hidden = !hasMore;
    loadEarlierBtn.disabled = loadingEarlier;
    loadEarlierBtn.textContent = loadingEarlier ? "正在加载更早记录…" : "加载更早记录";
  }

  /** Load the newest page of a conversation. Caller checks the load token. */
  async function loadLatestHistory(conversationId) {
    const data = await client.conversationEvents(conversationId, {
      beforeSeq: Number.MAX_SAFE_INTEGER,
      limit: HISTORY_PAGE_LIMIT,
    });
    const events = data.events || [];
    timeline.ingestConversationEvents(events);
    state.historyCursor = {
      minSeq: events.length ? events[0].seq : data.next_before_seq ?? null,
      hasMore: Boolean(data.has_more),
    };
    updateLoadEarlierButton();
  }

  /** Prepend one older page, keeping the visible content anchored. */
  async function loadEarlierHistory({ silent = false } = {}) {
    const conversationId = state.conversationId;
    if (!conversationId || loadingEarlier) return;
    if (!state.historyCursor || !state.historyCursor.hasMore) return;
    const token = state.loadToken;
    const beforeSeq = state.historyCursor.minSeq;
    if (beforeSeq == null) return;
    loadingEarlier = true;
    updateLoadEarlierButton();
    try {
      const data = await client.conversationEvents(conversationId, {
        beforeSeq,
        limit: HISTORY_PAGE_LIMIT,
      });
      if (token !== state.loadToken) return; // conversation switched mid-flight
      const events = data.events || [];
      // Anchor: keep the distance from the bottom constant so prepended
      // history does not make the viewport jump.
      const scroller = els.aiMessages;
      const distanceFromBottom = scroller.scrollHeight - scroller.scrollTop;
      timeline.ingestConversationEvents(events);
      if (events.length) state.historyCursor.minSeq = events[0].seq;
      state.historyCursor.hasMore = Boolean(data.has_more);
      renderTimeline({ immediate: true });
      requestAnimationFrame(() => {
        if (token === state.loadToken) {
          scroller.scrollTop = scroller.scrollHeight - distanceFromBottom;
        }
      });
    } catch (err) {
      if (!silent && token === state.loadToken) toast(err.message);
    } finally {
      loadingEarlier = false;
      if (token === state.loadToken) updateLoadEarlierButton();
    }
  }

  async function selectConversation(conversationId, { loadHistory = true } = {}) {
    if (!conversationId) return;
    const switching = conversationId !== state.conversationId;
    if (switching || loadHistory) {
      stopWatcher();
      setRunning(false);
      state.currentJobId = null;
      state.jobStatus = null;
      state.pauseRequested = false;
      state.cancelRequested = false;
      state.controlBusy = null;
      // Save where the user was reading before leaving this conversation.
      if (state.conversationId) {
        state.scrollTops.set(state.conversationId, els.aiMessages.scrollTop);
      }
    }
    const token = ++state.loadToken; // invalidates every in-flight load
    state.conversationId = conversationId;
    savePrefs();
    state.historyCursor = null;
    loadingEarlier = false;
    view?.setConversationId(conversationId);
    renderConversationSelect();
    if (loadHistory) {
      timeline.reset();
      view?.reset();
      showEmpty();
      updateLoadEarlierButton();
      renderTimeline({ immediate: true });
      try {
        await loadLatestHistory(conversationId);
        if (token !== state.loadToken) return; // stale response: drop it
        renderTimeline({ immediate: true });
      } catch (err) {
        if (token === state.loadToken) toast(err.message);
        return;
      }
      // Restore the saved scroll position, or jump to the newest content.
      const saved = state.scrollTops.get(conversationId);
      requestAnimationFrame(() => {
        if (token !== state.loadToken) return;
        els.aiMessages.scrollTop =
          saved != null && saved > 0 ? saved : els.aiMessages.scrollHeight;
      });
    }
    if (switching || loadHistory) {
      await loadJobHistory(state.selectedProjectId, conversationId);
      if (token !== state.loadToken) return;
      await resumeActiveJobForConversation(conversationId);
      if (token !== state.loadToken) return;
    }
    updateComposer();
  }

  async function resumeActiveJobForConversation(conversationId) {
    if (!conversationId || !state.selectedProjectId || !state.connected) return;
    try {
      const data = await client.jobs(state.selectedProjectId, conversationId);
      const active = (data.jobs || []).find(
        (j) => j.status === "queued" || j.status === "running" || j.status === "awaiting_approval" || j.status === "paused",
      );
      if (!active) return;
      state.currentJobId = active.id;
      state.jobStatus = active.status;
      state.pauseRequested = Boolean(active.pause_requested);
      state.cancelRequested = Boolean(active.cancel_requested);
      if (els.jobHistory) els.jobHistory.value = active.id;
      watchJob(active.id);
    } catch (_) {
      /* ignore */
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
      els.jobHistory.textContent = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "历史任务…";
      els.jobHistory.appendChild(placeholder);
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

  /**
   * Expand + scroll to the Turn that owns a historical job. Pages backward
   * through older history when the turn is not in the initially loaded page.
   */
  async function revealJobTurn(job) {
    const turnId = job.turn_id || null;
    if (!turnId) {
      toast("该任务缺少轮次信息，无法定位");
      return false;
    }
    for (let guard = 0; guard < 25; guard += 1) {
      if (view && view.revealTurn(turnId)) return true;
      if (!state.historyCursor || !state.historyCursor.hasMore) break;
      await loadEarlierHistory({ silent: true });
    }
    toast("未找到该任务对应的轮次（可能已被归档）");
    return false;
  }

  async function loadHistoricalJob(jobId) {
    if (!jobId) return;
    try {
      const data = await client.job(jobId);
      const job = data.job;
      if (!job) {
        toast("任务不存在或已归档");
        return;
      }
      if (job.conversation_id && job.conversation_id !== state.conversationId) {
        await selectConversation(job.conversation_id, { loadHistory: true });
      } else if (!state.conversationId && job.conversation_id) {
        await selectConversation(job.conversation_id, { loadHistory: true });
      }
      if (["queued", "running", "awaiting_approval", "paused"].includes(job.status)) {
        // Still active: re-attach the live watcher instead of just showing history.
        state.currentJobId = job.id;
        state.jobStatus = job.status;
        state.pauseRequested = Boolean(job.pause_requested);
        state.cancelRequested = Boolean(job.cancel_requested);
        watchJob(job.id);
      }
      if (els.jobHistory) els.jobHistory.value = "";
      await revealJobTurn(job);
    } catch (err) {
      toast(err.message);
    }
  }

  // —— Job watching ——

  function setRunning(running) {
    state.running = running;
    updateComposer();
    window.EditorApp?.setStatus?.(running ? "Agent 运行中…" : "就绪");
    updateStatusDot();
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
    if (base) await window.EditorApp?.refreshTree?.({ silent: true });
    for (const file of files) {
      const rel = typeof file === "string" ? file : file.path;
      if (!rel || !base) continue;
      const abs = await desktop.joinPath(base, rel);
      await window.EditorApp?.reloadPathIfOpen?.(abs);
    }
  }

  // Throttles: job status arrives on every poll tick, and awaiting_approval
  // used to trigger a /approvals fetch per tick (5 req/s while waiting).
  const approvalSyncThrottle = { jobId: null, at: 0 };
  let lastErrorToastAt = 0;

  async function syncPendingApprovals(jobId, { force = false } = {}) {
    if (!jobId || !state.connected) return;
    const now = Date.now();
    if (
      !force &&
      approvalSyncThrottle.jobId === jobId &&
      now - approvalSyncThrottle.at < 2000
    ) {
      return;
    }
    approvalSyncThrottle.jobId = jobId;
    approvalSyncThrottle.at = now;
    try {
      const data = await client.listApprovals(jobId);
      const pending = data.approvals || [];
      const liveIds = new Set(pending.map((p) => p.id));
      // Re-sync: approvals still pending server-side must be present locally…
      for (const item of pending) {
        timeline.ingestTaskEvents(
          [
            {
              type: "approval_required",
              job_id: jobId,
              approval_id: item.id,
              kind: item.kind,
              created_at: item.created_at,
              ...(item.payload || {}),
            },
          ],
          { jobId },
        );
      }
      // …and approvals no longer pending server-side must not stay actionable.
      for (const item of timeline.pendingApprovals()) {
        if ((item.jobId || jobId) === jobId && !liveIds.has(item.approvalId)) {
          timeline.setApprovalDecision(item.approvalId, item.metadata.decision || "canceled");
        }
      }
      renderTimeline();
    } catch (_) {
      /* transient */
    }
  }

  function watchJob(jobId) {
    stopWatcher();
    state.currentJobId = jobId;
    setRunning(true);
    syncPendingApprovals(jobId, { force: true });
    state.watcher = client.watchJob(jobId, async (payload) => {
      if (state.currentJobId !== jobId) return;
      if (payload.kind === "event" && payload.event) {
        timeline.ingestTaskEvents([payload.event], { jobId });
        renderTimeline();
        return;
      }
      if (payload.kind === "job" && payload.job) {
        state.jobStatus = payload.job.status;
        state.pauseRequested = Boolean(payload.job.pause_requested);
        state.cancelRequested = Boolean(payload.job.cancel_requested);
        if (payload.job.status === "awaiting_approval") {
          await syncPendingApprovals(jobId);
        }
        updateComposer();
        updateStatusDot();
        return;
      }
      if (payload.kind === "done") {
        stopWatcher();
        setRunning(false);
        state.jobStatus = payload.status;
        state.pauseRequested = false;
        state.cancelRequested = false;
        state.controlBusy = null;
        try {
          const data = await client.job(jobId);
          if (state.currentJobId !== jobId) return;
          // Authoritative reconciliation: persisted conversation events are the
          // source of truth. Re-ingest the newest page (idempotent by seq) so
          // the final assistant message, after_turn checkpoint and lifecycle
          // events settle into the same items the live stream created.
          if (state.conversationId) {
            try {
              const tail = await client.conversationEvents(state.conversationId, {
                beforeSeq: Number.MAX_SAFE_INTEGER,
                limit: HISTORY_PAGE_LIMIT,
              });
              if (state.currentJobId !== jobId && state.currentJobId !== null) return;
              timeline.ingestConversationEvents(tail.events || []);
            } catch (_) {
              /* fall back to task events below */
            }
          }
          timeline.ingestTaskEvents(data.job?.events || [], { jobId });
          renderTimeline();
          await refreshOpenFilesAfterJob(data.job);
          await loadJobHistory(state.selectedProjectId, state.conversationId);
          if (state.selectedProjectId) {
            await loadConversations(state.selectedProjectId, {
              preferId: state.conversationId,
              loadHistory: false,
            });
          }
        } catch (_) {
          renderTimeline();
        }
        return;
      }
      if (payload.kind === "error") {
        // Poll retries every ~1.5s; without a throttle a dead server spams
        // one toast per tick.
        const now = Date.now();
        if (now - lastErrorToastAt > 5000) {
          lastErrorToastAt = now;
          toast(`连接异常: ${payload.error}`);
        }
      }
    });
  }

  // —— Composer ——

  function setRunInputMode(mode) {
    state.runInputMode = mode === "follow_up" ? "follow_up" : "steer";
    if (els.composerModes) {
      els.composerModes.querySelectorAll(".composer-mode").forEach((btn) => {
        const active = btn.dataset.mode === state.runInputMode;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", String(active));
      });
    }
  }

  function updateComposer() {
    const ready = state.connected && state.selectedProjectId && state.conversationId;
    els.promptInput.disabled = !ready;
    const running = state.running;
    const controllable = state.connected && hasActiveJob();
    els.btnSend.hidden = false;
    els.btnStop.hidden = !controllable;
    if (els.composerModes) els.composerModes.hidden = !running;
    els.btnSend.disabled = !ready;
    els.btnStop.disabled = !controllable || Boolean(state.controlBusy) || state.cancelRequested;
    els.btnStop.textContent = state.cancelRequested ? "停止中…" : "停止";
    els.btnAddContext.disabled = !state.selectedProjectId;
    els.btnSend.textContent = running
      ? state.runInputMode === "follow_up"
        ? "发送追问"
        : "发送引导"
      : "发送";
    // Disabled states must explain WHY, not just fade out.
    els.promptInput.placeholder = running
      ? "任务运行中：输入内容将按所选模式插入"
      : !state.connected
        ? "连接中断，输入内容将保留"
        : !state.selectedProjectId
          ? "选择项目后可发送任务"
          : !state.conversationId
            ? "开始或选择一个任务后可发送"
            : "描述要完成的任务";
    setRunInputMode(state.runInputMode);
    updateJobControls();
  }

  function autosizePrompt() {
    const el = els.promptInput;
    el.style.height = "auto";
    el.style.height = `${Math.min(200, Math.max(56, el.scrollHeight))}px`;
  }

  function renderChips() {
    const host = els.aiContext;
    host.textContent = "";
    const chips = state.contextChips;
    host.hidden = !chips.length;
    for (const chip of chips) {
      const node = document.createElement("span");
      node.className = "composer-chip";
      const kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = { file: "文件", folder: "目录", selection: "选区" }[chip.kind] || chip.kind;
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = chip.label || "";
      label.title = chip.path || chip.label || "";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `移除上下文 ${chip.label || ""}`);
      remove.addEventListener("click", () => {
        state.contextChips = state.contextChips.filter((c) => c.key !== chip.key);
        renderChips();
      });
      node.append(kind, label, remove);
      host.appendChild(node);
    }
  }

  async function addContextChip(kind) {
    if (kind === "file") {
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
      pushChip({ key: `file:${tab.path}`, kind: "file", label, path: tab.path });
    } else if (kind === "folder") {
      const root = window.EditorApp?.getRoot?.();
      if (!root) {
        toast("未打开文件夹");
        return;
      }
      pushChip({
        key: `folder:${root}`,
        kind: "folder",
        label: root.split(/[/\\]/).pop() || root,
        path: root,
      });
    } else if (kind === "selection") {
      const sel = window.EditorApp?.getSelection?.();
      if (!sel?.text) {
        toast("当前没有选区");
        return;
      }
      pushChip({
        key: `selection:${Date.now()}`,
        kind: "selection",
        label: `${(sel.path || "").split(/[/\\]/).pop() || "选区"} L${sel.startLine}-L${sel.endLine}`,
        text: sel.text,
        path: sel.path,
      });
    }
    els.promptInput.focus();
  }

  function pushChip(chip) {
    state.contextChips = [...state.contextChips.filter((c) => c.key !== chip.key), chip];
    renderChips();
  }

  function buildPrompt() {
    let prompt = els.promptInput.value.trim();
    if (!prompt) return "";
    const blocks = [];
    for (const chip of state.contextChips) {
      if (chip.kind === "file") blocks.push(`当前聚焦文件: ${chip.path || chip.label}`);
      else if (chip.kind === "folder") blocks.push(`相关目录: ${chip.path || chip.label}`);
      else if (chip.kind === "selection") blocks.push(`选区 (${chip.path || ""}):\n${chip.text || ""}`);
    }
    if (blocks.length) prompt = `${blocks.join("\n\n")}\n\n${prompt}`;
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
      run_mode: state.runMode,
    };
    const provider = els.modelSelect.value;
    if (provider) body.provider = provider;

    timeline.addLocalUserMessage(prompt, {});
    els.promptInput.value = "";
    autosizePrompt();
    setRunInputMode("steer"); // every new task starts in steer mode
    state.pauseRequested = false;
    state.cancelRequested = false;
    state.controlBusy = null;
    setRunning(true);
    renderTimeline();

    try {
      const data = await client.askConversation(conversationId, body);
      const job = data.job;
      state.currentJobId = job.id;
      state.jobStatus = job.status;
      state.pauseRequested = Boolean(job.pause_requested);
      state.cancelRequested = Boolean(job.cancel_requested);
      if (data.conversation_id) state.conversationId = data.conversation_id;
      watchJob(job.id);
      await loadJobHistory(projectId, state.conversationId);
    } catch (err) {
      setRunning(false);
      toast(err.message);
      renderTimeline();
    }
  }

  async function controlJob(action) {
    if (!state.currentJobId || state.controlBusy) return;
    state.controlBusy = action;
    if (action === "cancel") state.cancelRequested = true;
    updateComposer();
    updateStatusDot();
    try {
      const data =
        action === "pause"
          ? await client.pauseJob(state.currentJobId)
          : action === "resume"
            ? await client.resumeJob(state.currentJobId)
            : await client.cancel(state.currentJobId);
      const job = data?.job;
      if (job) {
        state.jobStatus = job.status;
        state.pauseRequested = Boolean(job.pause_requested) || (action === "pause" && job.status === "running");
        state.cancelRequested = Boolean(job.cancel_requested) || action === "cancel";
      } else if (action === "pause") {
        state.pauseRequested = true;
      }
      if (action === "cancel") {
        timeline.cancelPending();
        renderTimeline();
        toast("已请求停止，任务将在安全检查点结束");
      } else if (action === "pause") {
        toast(state.jobStatus === "paused" ? "任务已暂停" : "已请求暂停");
      } else {
        state.pauseRequested = false;
        state.cancelRequested = false;
        toast("已请求继续");
      }
    } catch (err) {
      if (action === "cancel") state.cancelRequested = false;
      if (action === "pause") state.pauseRequested = false;
      toast(`${action === "pause" ? "暂停" : action === "resume" ? "继续" : "停止"}失败: ${err.message}`);
    } finally {
      state.controlBusy = null;
      updateComposer();
      updateStatusDot();
    }
  }

  async function stopJob() {
    await controlJob("cancel");
  }

  async function sendJobMessage(kind) {
    const text = els.promptInput.value.trim();
    if (!text || !state.currentJobId) return;
    try {
      if (kind === "steer") await client.steerJob(state.currentJobId, text);
      else await client.followUpJob(state.currentJobId, text);
      els.promptInput.value = "";
      autosizePrompt();
      toast(kind === "steer" ? "已发送引导" : "已加入追问，本轮结束后继续");
    } catch (err) {
      toast(`${kind === "steer" ? "引导" : "追问"}失败: ${err.message}`);
    }
  }

  // —— Settings / dialogs ——

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

  function onActiveFileChanged() {
    /* context chips are explicit; keep sticky */
  }

  async function onWorkspaceChanged() {
    if (state.connected) await maybeAutoSelectProject();
  }

  function toggleMenu(menu) {
    if (!menu) return;
    const next = menu.hidden;
    document.querySelectorAll(".ai-menu").forEach((m) => {
      m.hidden = true;
    });
    menu.hidden = !next;
  }

  // —— Bindings ——

  function bind() {
    loadPrefs();
    if (els.runModeSelect) {
      els.runModeSelect.value = state.runMode;
      els.runModeSelect.addEventListener("change", () => {
        state.runMode = els.runModeSelect.value;
        savePrefs();
      });
    }

    els.btnOpenSettings?.addEventListener("click", () => {
      toggleMenu(els.aiMoreMenu);
      openSettings();
    });
    els.statusConn.addEventListener("click", () => openSettings());
    els.btnCloseAi.addEventListener("click", () => window.EditorApp?.toggleAi?.());
    els.btnAiMore?.addEventListener("click", () => toggleMenu(els.aiMoreMenu));
    document.addEventListener("mousedown", (ev) => {
      if (
        els.aiMoreMenu &&
        !els.aiMoreMenu.hidden &&
        !els.aiMoreMenu.contains(ev.target) &&
        ev.target !== els.btnAiMore
      ) {
        els.aiMoreMenu.hidden = true;
      }
      if (
        els.contextMenu &&
        !els.contextMenu.hidden &&
        !els.contextMenu.contains(ev.target) &&
        ev.target !== els.btnAddContext
      ) {
        els.contextMenu.hidden = true;
      }
    });

    els.btnStartServer.addEventListener("click", () => startServer());
    els.btnStopServer.addEventListener("click", () => stopServer());
    els.btnCopyPhoneUrl.addEventListener("click", () => copyPhoneUrl());
    els.btnPair?.addEventListener("click", () =>
      pair()
        .then(() => els.settingsDialog.close())
        .catch((err) => toast(`配对失败: ${err.message}`)),
    );
    els.phoneUrl.addEventListener("click", () => copyPhoneUrl());
    desktop.onAgentServerExit?.(async () => {
      state.serverManaged = false;
      state.connected = false;
      setConn("idle", "未连接");
      updateComposer();
      updateStatusDot();
      await refreshServerStatus();
      toast("Agent 服务已退出");
    });

    els.btnNewChat.addEventListener("click", () => createNewConversation());
    els.btnSidebarNewConversation?.addEventListener("click", () => createNewConversation());
    els.btnPauseJob?.addEventListener("click", () => controlJob("pause"));
    els.btnResumeJob?.addEventListener("click", () => controlJob("resume"));
    els.btnHeaderStop?.addEventListener("click", () => stopJob());
    els.conversationSelect?.addEventListener("change", async () => {
      const id = els.conversationSelect.value;
      if (id && id !== state.conversationId) await selectConversation(id);
    });

    els.btnSend.addEventListener("click", () => {
      if (state.running) sendJobMessage(state.runInputMode);
      else sendAsk();
    });
    els.btnStop.addEventListener("click", () => stopJob());
    els.btnSteer?.addEventListener("click", () => setRunInputMode("steer"));
    els.btnFollowUp?.addEventListener("click", () => setRunInputMode("follow_up"));
    els.autoFallback.addEventListener("change", savePrefs);

    els.btnAddContext?.addEventListener("click", () => toggleMenu(els.contextMenu));
    els.contextMenu?.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-context]");
      if (!btn) return;
      els.contextMenu.hidden = true;
      addContextChip(btn.dataset.context);
    });

    els.promptInput.addEventListener("input", autosizePrompt);
    els.promptInput.addEventListener("keydown", (ev) => {
      const submit =
        (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) || (ev.key === "Enter" && !ev.shiftKey && !ev.metaKey && !ev.ctrlKey && !ev.altKey);
      if (submit) {
        ev.preventDefault();
        if (state.running) sendJobMessage(state.runInputMode);
        else if (!els.btnSend.disabled) sendAsk();
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

    // Diff review cleanup when the diff host closes (editor disposal included).
    const cleanupReview = () => {
      closeReviewSession();
      window.EditorApp?.closeDiff?.();
    };
    els.btnCloseDiff?.addEventListener("click", cleanupReview);
    els.btnRejectDiff?.addEventListener("click", cleanupReview);
    els.btnAcceptDiff?.addEventListener("click", cleanupReview);
  }

  async function init() {
    emptyNode = els.aiEmpty;
    if (emptyNode?.parentElement) emptyNode.parentElement.removeChild(emptyNode);
    view = window.AgentTimeline.createTimelineView(els.aiMessages, callbacks);

    // Progressive history: "load earlier" button pinned above the newest turns.
    loadEarlierBtn = document.createElement("button");
    loadEarlierBtn.type = "button";
    loadEarlierBtn.className = "tl-load-earlier";
    loadEarlierBtn.textContent = "加载更早记录";
    loadEarlierBtn.hidden = true;
    loadEarlierBtn.addEventListener("click", () => loadEarlierHistory());
    els.aiMessages.prepend(loadEarlierBtn);
    view.setHeaderNode(loadEarlierBtn);
    // Auto-page when scrolling close to the top of a long history.
    els.aiMessages.addEventListener("scroll", () => {
      if (els.aiMessages.scrollTop < 80 && state.historyCursor?.hasMore) {
        loadEarlierHistory();
      }
    });

    bind();
    showEmpty();
    setRunning(false);
    renderChips();
    updateComposer();
    updateStatusDot();
    const initialServerStatus = await refreshServerStatus();
    if (desktop?.getCredential) {
      const baseUrl = (els.serverUrl.value || "http://127.0.0.1:8000").trim().replace(/\/+$/, "");
      els.apiToken.value = await desktop.getCredential(baseUrl);
    }
    // A remembered local credential means this desktop was already paired.
    // Start its local service automatically so app restarts do not leave a
    // misleading disconnected panel or require a trip through Settings.
    const rememberedServerUrl = (els.serverUrl.value || "http://127.0.0.1:8000")
      .trim()
      .replace(/\/+$/, "");
    if (
      !initialServerStatus?.running &&
      els.apiToken.value.trim() &&
      desktop?.agentStart &&
      isConfiguredLocalServer(rememberedServerUrl, initialServerStatus)
    ) {
      await startServer();
    }
    if (!state.connected) {
      try {
        await connect({ silent: true });
      } catch (_) {
        setConn("err", "未连接");
      }
    }
    setInterval(async () => {
      if (state.serverBusy || state.reconnectBusy) return;
      const status = await refreshServerStatus();
      if (status?.running && !state.connected && els.apiToken.value.trim()) {
        state.reconnectBusy = true;
        try {
          await connect({ silent: true });
        } catch (_) {
          /* retry on the next health interval */
        } finally {
          state.reconnectBusy = false;
        }
      }
    }, 5000);
  }

  window.AiPanel = {
    init,
    openSettings,
    openCreateProject,
    focusComposer,
    onActiveFileChanged,
    onWorkspaceChanged,
    openJob: loadHistoricalJob,
    client,
    getState: () => state,
    dispatch: (action) => {
      if (typeof action === "object" && action) {
        Object.assign(state, action.patch || {});
        if (action.patch && action.patch.conversations) {
          renderConversationSelect();
        }
        if (action.patch && action.patch.conversationId !== undefined) {
          selectConversation(action.patch.conversationId).catch(() => {});
        }
      }
    },
    // Deterministic hooks for tests/screenshots.
    debug: {
      timeline,
      renderTimeline,
      getView: () => view,
      ingestTaskEvents: (events, opts) => {
        timeline.ingestTaskEvents(events, opts);
        renderTimeline();
      },
      ingestConversationEvents: (events) => {
        timeline.ingestConversationEvents(events);
        renderTimeline();
      },
      setRunning,
      openTurnDiffReview,
      closeReviewSession,
      selectConversation,
      setState: (patch) => {
        Object.assign(state, patch || {});
        updateComposer();
        updateStatusDot();
      },
    },
  };
})();
