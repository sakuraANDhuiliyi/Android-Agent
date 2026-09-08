(() => {
  "use strict";

  const STORE_KEY = "android-agent-windows-shell-v3";
  const TODO_KEY = "android-agent-codexia-todos-v1";
  const ACTIVE_STATUSES = new Set(["queued", "running", "awaiting_approval", "paused", "cancel_requested"]);
  const PENDING_STATUSES = new Set(["awaiting_approval", "paused"]);
  const MAX_VISIBLE_CARDS = 16;
  const PREVIEW_MODE = typeof location !== "undefined"
    && ["127.0.0.1", "localhost"].includes(location.hostname)
    && new URLSearchParams(location.search).get("__agent_windows_fixture") === "1";

  // The production bridge is supplied by Electron's preload. This inert shape
  // lets the same page be visually verified through the local browser harness.
  if (PREVIEW_MODE && !window.agentDesktop) {
    window.agentDesktop = {
      getDefaultWorkspace: () => Promise.resolve(null),
      getRepoRoot: () => Promise.resolve("/"),
      readTree: () => Promise.resolve({ entries: [] }),
      listFiles: () => Promise.resolve([]),
      readFile: () => Promise.resolve({ content: "" }),
      writeFile: () => Promise.resolve(),
      exists: () => Promise.resolve(false),
      stat: () => Promise.resolve({}),
      basename: (path) => Promise.resolve(path?.split(/[/\\]/).pop() || path),
      dirname: (path) => Promise.resolve(path?.split(/[/\\]/).slice(0, -1).join("/") || "/"),
      joinPath: (...parts) => Promise.resolve(parts.join("/")),
      relative: (_from, to) => Promise.resolve(to),
      normalize: (path) => Promise.resolve(path),
      agentStatus: () => Promise.resolve({ running: false, managed: false, port: 8000, phoneUrl: null }),
      agentStart: () => Promise.resolve({ ok: true, running: true, port: 8000 }),
      agentStop: () => Promise.resolve({ stopped: true }),
      onAgentServerExit: () => () => {},
      onMenu: () => () => {},
    };
  }

  const state = {
    initialized: false,
    visible: false,
    busy: false,
    layout: "solo",
    selectedId: null,
    selectedProjectId: null,
    selectedConversationId: null,
    runMode: "workspace",
    models: [],
    jobs: [],
    jobDetails: new Map(),
    projects: [],
    pinnedIds: new Set(),
    dismissedIds: new Set(),
    timer: null,
    jobWatcher: null,
    debugData: null,
    activePanel: "launcher",
    categoriesExpanded: true,
    selectedCategoryId: null,
    hideDone: false,
    categories: [],
    todos: [],
    pluxDismissed: false,
    fileTree: null,
    selectedFilePath: null,
    terminal: null,
    terminalProjectId: null,
    reviewData: null,
    reviewJobId: null,
    pinnedOnly: false,
    insightsVisible: false,
    activeOnly: false,
    contextFiles: [],
    panelFocused: false,
    panelCollapsed: false,
    sidebarCollapsed: false,
    searchQuery: "",
    addMenuOpen: false,
    planMode: false,
    goal: "",
  };

  const els = {};

  function displayStatus(job) {
    if (!job) return "queued";
    if (job.display_status) return String(job.display_status);
    if (job.cancel_requested && !["succeeded", "failed", "canceled", "interrupted"].includes(job.status)) {
      return "cancel_requested";
    }
    return String(job.status || "queued");
  }

  function statusClass(job) {
    const status = displayStatus(job);
    if (["running", "queued", "cancel_requested"].includes(status)) return "running";
    if (PENDING_STATUSES.has(status)) return "pending";
    if (["failed", "interrupted"].includes(status)) return "failed";
    return "idle";
  }

  function jobTimestamp(job) {
    return Number(job.updated_at || job.finished_at || job.started_at || job.created_at || 0);
  }

  function normalizeJobs(jobs, projects, dismissedIds = new Set()) {
    const projectMap = new Map((Array.isArray(projects) ? projects : []).map((project) => [project.id, project]));
    return (Array.isArray(jobs) ? jobs : [])
      .filter((job) => job && job.id && !dismissedIds.has(String(job.id)))
      .sort((a, b) => Number(ACTIVE_STATUSES.has(displayStatus(b))) - Number(ACTIVE_STATUSES.has(displayStatus(a))) || jobTimestamp(b) - jobTimestamp(a))
      .slice(0, MAX_VISIBLE_CARDS)
      .map((job) => ({
        ...job,
        id: String(job.id),
        project: projectMap.get(job.project_id) || null,
        displayStatus: displayStatus(job),
        statusClass: statusClass(job),
      }));
  }

  function titleFor(job) {
    const prompt = String(job.prompt || job.title || "").trim().replace(/\s+/g, " ");
    return prompt.slice(0, 60) || String(job.id || "New agent").slice(0, 12);
  }

  function syntheticEvents(job) {
    const events = Array.isArray(job.events) ? job.events.slice() : [];
    const hasUser = events.some((event) => ["user_message", "user"].includes(event?.type));
    const hasAssistant = events.some((event) => ["assistant_message", "assistant", "text", "text_delta"].includes(event?.type));
    if (!hasUser && job.prompt) {
      events.unshift({ id: `cx-user-${job.id}`, type: "user_message", ts: job.created_at, content: [{ type: "text", text: String(job.prompt) }] });
    }
    const result = job.final_message || job.result || job.error_message || job.error;
    if (!hasAssistant && result) {
      events.push({
        id: `cx-assistant-${job.id}`,
        type: job.error_message || job.error ? "error" : "assistant_message",
        ts: job.finished_at || job.updated_at,
        text_blocks: [{ type: "text", text: String(result) }],
        message: String(result),
        is_final: true,
      });
    }
    return events;
  }

  const ASSISTANT_EVENT_TYPES = new Set(["assistant_message", "assistant", "text", "text_delta"]);
  const PRIVATE_EVENT_TYPES = new Set(["reasoning", "reasoning_delta", "reasoning_summary", "thinking", "thought", "chain_of_thought"]);

  function assistantEventText(event) {
    if (typeof event?.delta === "string") return event.delta;
    if (typeof event?.content === "string") return event.content;
    return eventText(event);
  }

  function joinAssistantText(current, next, incremental = false) {
    const left = String(current || "");
    const right = String(next || "");
    if (!right) return left;
    if (!left) return right;
    if (left === right || left.startsWith(right)) return left;
    if (right.startsWith(left)) return right;
    if (incremental || /\s$/.test(left) || /^\s|^[,.;:!?，。；：！？、)}\]]/.test(right)) return `${left}${right}`;
    if (/[.!?。！？:]$/.test(left) && /^[A-Z\u4e00-\u9fff]/.test(right)) return `${left}\n\n${right}`;
    if (/[\u4e00-\u9fff]$/.test(left) || /^[\u4e00-\u9fff]/.test(right)) return `${left}${right}`;
    return `${left} ${right}`;
  }

  function displayEventsFor(job) {
    const result = [];
    const assistantById = new Map();
    let legacyAssistant = null;
    for (const event of syntheticEvents(job)) {
      const type = String(event?.type || "");
      if (PRIVATE_EVENT_TYPES.has(type)) continue;
      if (ASSISTANT_EVENT_TYPES.has(type)) {
        const messageId = event.message_id || event.messageId || event.stream_id || event.streamId || null;
        let display = messageId ? assistantById.get(String(messageId)) : legacyAssistant;
        if (!display) {
          display = { ...event, type: "assistant_message", _displayText: "" };
          result.push(display);
          if (messageId) assistantById.set(String(messageId), display);
          else legacyAssistant = display;
        }
        const text = assistantEventText(event);
        if (type === "assistant_message" && messageId && text) display._displayText = text;
        else display._displayText = joinAssistantText(display._displayText, text, type === "text_delta");
        continue;
      }
      if (["user_message", "user"].includes(type)) {
        result.push(event);
        legacyAssistant = null;
        continue;
      }
      if (["tool_call", "tool", "approval_required", "error"].includes(type)) result.push(event);
      legacyAssistant = null;
    }
    return result.filter((event) => event.type !== "assistant_message" || Boolean(event._displayText || eventText(event)));
  }

  function eventText(event) {
    if (!event) return "";
    if (typeof event.message === "string") return event.message;
    if (typeof event.text === "string") return event.text;
    const blocks = event.content || event.text_blocks;
    if (Array.isArray(blocks)) {
      return blocks.map((block) => typeof block === "string" ? block : block?.text || block?.content || "").filter(Boolean).join("\n");
    }
    return "";
  }

  function svg(path, viewBox = "0 0 24 24") {
    const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    node.setAttribute("viewBox", viewBox);
    node.setAttribute("aria-hidden", "true");
    const child = document.createElementNS("http://www.w3.org/2000/svg", "path");
    child.setAttribute("d", path);
    node.appendChild(child);
    return node;
  }

  function button(className, label) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    if (label) node.textContent = label;
    return node;
  }

  function loadState() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
      if (stored && ["solo", "grid", "list"].includes(stored.layout)) state.layout = stored.layout;
      if (stored?.selectedId) state.selectedId = String(stored.selectedId);
      if (stored?.selectedProjectId) state.selectedProjectId = String(stored.selectedProjectId);
      if (stored?.selectedConversationId) state.selectedConversationId = String(stored.selectedConversationId);
      if (["read_only", "workspace", "ask"].includes(stored?.runMode)) state.runMode = stored.runMode;
      if (Array.isArray(stored?.pinnedIds)) state.pinnedIds = new Set(stored.pinnedIds.map(String));
      if (["launcher", "review", "terminal", "todos", "files"].includes(stored?.activePanel)) state.activePanel = stored.activePanel;
      state.planMode = Boolean(stored?.planMode);
      state.sidebarCollapsed = Boolean(stored?.sidebarCollapsed);
      if (typeof stored?.goal === "string") state.goal = stored.goal;
    } catch (_) {
      /* Local layout state is optional. */
    }
    try {
      const stored = JSON.parse(localStorage.getItem(TODO_KEY) || "null");
      if (stored) {
        state.categories = Array.isArray(stored.categories) ? stored.categories : [];
        state.todos = Array.isArray(stored.todos) ? stored.todos : [];
        state.pluxDismissed = Boolean(stored.pluxDismissed);
      }
    } catch (_) {
      /* Local todo state is optional. */
    }
  }

  function persistState() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        layout: state.layout,
        selectedId: state.selectedId,
        selectedProjectId: state.selectedProjectId,
        selectedConversationId: state.selectedConversationId,
        runMode: state.runMode,
        pinnedIds: Array.from(state.pinnedIds),
        activePanel: state.activePanel,
        planMode: state.planMode,
        goal: state.goal,
        sidebarCollapsed: state.sidebarCollapsed,
      }));
    } catch (_) {
      /* Storage is optional. */
    }
  }

  function persistTodos() {
    try {
      localStorage.setItem(TODO_KEY, JSON.stringify({ categories: state.categories, todos: state.todos, pluxDismissed: state.pluxDismissed }));
    } catch (_) {
      /* Storage is optional. */
    }
  }

  function switchToWorkbench() {
    document.querySelector('.focus-switch-btn[data-mode="code"]')?.click();
  }

  function toggleSidebar() {
    if (window.matchMedia?.("(max-width: 760px)").matches) {
      const open = els.root.classList.toggle("cx-mobile-sidebar-open");
      document.getElementById("cxSidebarRestore")?.setAttribute("aria-expanded", String(open));
      return;
    }
    state.sidebarCollapsed = !state.sidebarCollapsed;
    els.root.classList.toggle("cx-sidebar-collapsed", state.sidebarCollapsed);
    document.getElementById("cxSidebarRestore")?.setAttribute("aria-expanded", String(!state.sidebarCollapsed));
    persistState();
    setTimeout(fitTerminal, 0);
  }

  function createPreviewFixture() {
    const now = Date.now() / 1000;
    return {
      projects: [
        { id: "demo", name: "demo", workspace: "/Users/mac/demo" },
        { id: "codexia", name: "codexia", workspace: "/Users/mac/codexia" },
        { id: "grok-doge", name: "grok-doge", workspace: "/Users/mac/grok-doge" },
      ],
      jobs: [{
        id: "codexia-computer-use",
        project_id: "codexia",
        status: "succeeded",
        conversation_id: "preview-conversation",
        turn_id: "preview-turn",
        prompt: "$computer-use send hi to gemini app prompt input, id is com.google.GeminiMacOS and it's opened",
        changed_files: [{ path: "src/components/agent/AgentView.tsx", change: "modified" }],
        created_at: now - 130,
        finished_at: now - 1,
        events: [
          { id: 1, type: "user_message", content: [{ type: "text", text: "$computer-use send hi to gemini app prompt input, id is com.google.GeminiMacOS and it's opened" }] },
          { id: 2, type: "assistant_message", text_blocks: [{ type: "text", text: "I’m using the computer-use skill to interact with the already-open Gemini app and enter “hi” in its prompt field." }] },
          { id: 3, type: "tool_call", name: "node_repl", message: "js", duration: "1:47" },
          { id: 4, type: "tool_call", name: "node_repl", message: "js", duration: "0:00" },
          { id: 5, type: "assistant_message", text_blocks: [{ type: "text", text: "Entered “hi” into Gemini’s prompt input." }] },
        ],
      }],
      models: [{ id: "gpt-5.6-terra", label: "GPT-5.6-Terra" }],
      diff: {
        ok: true,
        status: "ready",
        files: [{ path: "src/components/agent/AgentView.tsx", change: "modified" }],
        diff: "diff --git a/src/components/agent/AgentView.tsx b/src/components/agent/AgentView.tsx\n--- a/src/components/agent/AgentView.tsx\n+++ b/src/components/agent/AgentView.tsx\n@@ -1,3 +1,4 @@\n export function AgentView() {\n+  // Functional Agent workspace\n   return <main />;\n }",
      },
      tree: {
        children: [{ name: "src", path: "src", type: "dir", children: [{ name: "AgentView.tsx", path: "src/AgentView.tsx", type: "file" }] }],
      },
    };
  }

  function ensurePreviewTodos() {
    if (!PREVIEW_MODE || state.todos.length) return;
    state.categories = [{ id: "research", name: "research" }, { id: "good", name: "good" }];
    state.todos = [{ id: "todo-preview", text: "I’m using the computer-use skill to interact with the already-open Gemini app and enter “hi” in its prompt field.", categoryId: null, isDone: false }];
  }

  function toast(message) {
    window.EditorApp?.toast?.(String(message || ""));
  }

  function selectedProject() {
    return state.projects.find((project) => String(project.id) === state.selectedProjectId) || null;
  }

  function selectedJob() {
    return normalizeJobs(state.jobs, state.projects, state.dismissedIds).find((job) => job.id === state.selectedId) || null;
  }

  async function selectProject(project) {
    const nextId = String(project.id);
    if (nextId === state.selectedProjectId) {
      els.prompt.focus();
      return;
    }
    state.selectedProjectId = nextId;
    state.selectedConversationId = null;
    state.selectedId = null;
    state.fileTree = null;
    state.selectedFilePath = null;
    state.reviewData = null;
    closeJobWatcher();
    closeTerminal();
    persistState();
    renderProjects();
    renderAgents();
    try {
      await window.AiPanel?.selectProject?.(nextId, { openWorkspace: false });
      const aiState = window.AiPanel?.getState?.() || {};
      state.selectedConversationId = aiState.conversationId || null;
      await refresh({ quiet: true });
      if (state.activePanel === "files") await loadFiles();
      if (state.activePanel === "review") await loadReview();
    } catch (error) {
      toast(error.message || error);
    }
    els.prompt.focus();
  }

  function renderProjects() {
    const aiState = window.AiPanel?.getState?.() || {};
    const projects = state.projects.length ? state.projects : (aiState.projects || []);
    if (!state.selectedProjectId || !projects.some((project) => String(project.id) === state.selectedProjectId)) {
      state.selectedProjectId = String(aiState.selectedProjectId || projects[0]?.id || "");
    }
    els.projectList.textContent = "";
    for (const project of projects) {
      const row = button(`cx-project-row${String(project.id) === state.selectedProjectId ? " active" : ""}`);
      row.title = project.workspace || project.name || project.id;
      row.appendChild(svg("M3 7h7l2 2h9v11H3z"));
      const name = document.createElement("span");
      name.textContent = project.name || String(project.workspace || project.id).split(/[/\\]/).filter(Boolean).pop();
      const more = document.createElement("b");
      more.textContent = "···";
      const edit = document.createElement("span");
      edit.className = "cx-project-edit";
      edit.textContent = "↗";
      row.append(name, more, edit);
      row.addEventListener("click", () => selectProject(project));
      els.projectList.appendChild(row);
      if (String(project.id) === state.selectedProjectId) {
        const projectJobs = normalizeJobs(state.jobs, projects, state.dismissedIds)
          .filter((job) => String(job.project_id) === state.selectedProjectId)
          .slice(0, 3);
        for (const job of projectJobs) {
          const task = button(`cx-project-row cx-project-task ${job.statusClass}${job.id === state.selectedId ? " active" : ""}`);
          task.textContent = titleFor(job);
          task.title = titleFor(job);
          task.addEventListener("click", () => selectJobFromSidebar(job));
          els.projectList.appendChild(task);
        }
      }
    }
    renderRecent();
  }

  function selectJobFromSidebar(job) {
    state.selectedProjectId = String(job.project_id || state.selectedProjectId || "");
    state.selectedConversationId = String(job.conversation_id || "") || null;
    state.selectedId = String(job.id);
    state.insightsVisible = false;
    persistState();
    renderProjects();
    renderAgents();
    if (state.activePanel === "review") loadReview();
  }

  function renderRecent() {
    if (!els.recentList) return;
    els.recentList.textContent = "";
    const jobs = normalizeJobs(state.jobs, state.projects, state.dismissedIds).slice(0, 8);
    for (const job of jobs) {
      const row = button("cx-recent-row", titleFor(job));
      row.title = `${job.project?.name || "项目"} · ${titleFor(job)}`;
      row.addEventListener("click", () => selectJobFromSidebar(job));
      els.recentList.appendChild(row);
    }
  }

  function updateHeader() {
    if (!els.headerTitle) return;
    const project = selectedProject();
    const job = selectedJob();
    els.headerTitle.textContent = job ? titleFor(job) : (project?.name || "Agent Windows");
    els.headerTitle.title = project?.workspace || els.headerTitle.textContent;
  }

  function approvalIdFor(event) {
    return event.approval_id || event.approvalId || event.id || "";
  }

  function createApprovalCard(job, event) {
    const card = document.createElement("div");
    card.className = "cx-approval-card";
    const title = document.createElement("strong");
    title.textContent = event.reason || event.intent || "Agent needs approval";
    const detail = document.createElement("pre");
    detail.textContent = event.command || event.path || eventText(event) || JSON.stringify(event.payload || event.input || {}, null, 2);
    const actions = document.createElement("div");
    const reject = button("cx-approval-reject", "Reject");
    const approve = button("cx-approval-approve", "Approve once");
    const resolve = async (approved) => {
      const approvalId = approvalIdFor(event);
      if (!approvalId) return;
      reject.disabled = true;
      approve.disabled = true;
      try {
        await window.AiPanel?.client?.resolveApproval(job.id, approvalId, approved);
        event.decision = approved ? "approved" : "rejected";
        await refresh({ quiet: true });
      } catch (error) {
        toast(error.message || error);
      } finally {
        reject.disabled = false;
        approve.disabled = false;
      }
    };
    reject.addEventListener("click", () => resolve(false));
    approve.addEventListener("click", () => resolve(true));
    actions.append(reject, approve);
    card.append(title, detail, actions);
    return card;
  }

  function createThreadCard(job) {
    const card = document.createElement("article");
    card.className = "cx-thread-card";
    card.dataset.jobId = job.id;
    const body = document.createElement("div");
    body.className = "cx-thread-body";
    const cardToolbar = document.createElement("header");
    cardToolbar.className = "cx-thread-card-toolbar";
    const status = document.createElement("span");
    status.className = `cx-job-status ${job.statusClass}`;
    status.textContent = job.displayStatus.replaceAll("_", " ");
    const title = document.createElement("span");
    title.textContent = titleFor(job);
    title.title = titleFor(job);
    const pin = button(`cx-pin-job${state.pinnedIds.has(job.id) ? " active" : ""}`, state.pinnedIds.has(job.id) ? "★" : "☆");
    pin.title = state.pinnedIds.has(job.id) ? "Unpin Agent" : "Pin Agent";
    pin.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.pinnedIds.has(job.id)) state.pinnedIds.delete(job.id);
      else state.pinnedIds.add(job.id);
      persistState();
      renderAgents();
    });
    cardToolbar.append(status, title, pin);
    card.appendChild(cardToolbar);
    const events = displayEventsFor(job);
    if (!events.length) {
      const empty = document.createElement("div");
      empty.className = "cx-thread-empty";
      empty.textContent = "Start a new chat to open an Agent window.";
      body.appendChild(empty);
    }
    for (const event of events) {
      if (event?.type === "approval_required" && !event.decision) {
        body.appendChild(createApprovalCard(job, event));
        continue;
      }
      if (event?.type === "tool_call" || event?.type === "tool") {
        const row = document.createElement("div");
        row.className = "cx-tool-row";
        const pill = document.createElement("span");
        pill.className = "cx-tool-pill";
        pill.textContent = event.name || "tool";
        const detail = document.createElement("span");
        detail.textContent = eventText(event) || "js";
        const dot = document.createElement("i");
        dot.className = "cx-tool-dot";
        const caret = document.createElement("span");
        caret.textContent = "›";
        const duration = document.createElement("span");
        duration.className = "cx-tool-time";
        duration.textContent = event.duration || "0:00";
        row.append(pill, detail, dot, caret, duration);
        body.appendChild(row);
        continue;
      }
      const text = event._displayText || eventText(event);
      if (!text) continue;
      const message = document.createElement("div");
      const isUser = ["user_message", "user"].includes(event.type);
      const isError = event.type === "error";
      message.className = `cx-thread-message ${isUser ? "user" : isError ? "error" : "assistant"}`;
      message.textContent = text;
      body.appendChild(message);
    }
    card.appendChild(body);
    const project = job.project || selectedProject();
    if (project?.workspace) {
      const workspace = document.createElement("button");
      workspace.type = "button";
      workspace.className = "cx-workspace-card";
      const workspaceIcon = document.createElement("span");
      workspaceIcon.className = "cx-workspace-icon";
      workspaceIcon.textContent = "◎";
      const workspaceInfo = document.createElement("span");
      const workspaceName = document.createElement("strong");
      workspaceName.textContent = project.name || "Android Agent";
      const workspacePath = document.createElement("small");
      workspacePath.textContent = project.workspace;
      workspaceInfo.append(workspaceName, workspacePath);
      const workspaceOpen = document.createElement("b");
      workspaceOpen.textContent = "打开方式⌄";
      workspace.append(workspaceIcon, workspaceInfo, workspaceOpen);
      workspace.addEventListener("click", async (event) => {
        event.stopPropagation();
        switchToWorkbench();
        await window.EditorApp?.openFolder?.(project.workspace);
      });
      card.appendChild(workspace);
    }
    const changedFiles = Array.isArray(job.changed_files) ? job.changed_files : [];
    if (changedFiles.length) {
      const changes = document.createElement("section");
      changes.className = "cx-changes-card";
      const additions = changedFiles.reduce((sum, file) => sum + Number(file.additions || 0), 0);
      const deletions = changedFiles.reduce((sum, file) => sum + Number(file.deletions || 0), 0);
      const head = document.createElement("header");
      const changeIcon = document.createElement("span");
      changeIcon.className = "cx-change-icon";
      changeIcon.textContent = "↕";
      const changeInfo = document.createElement("span");
      const changeTitle = document.createElement("strong");
      changeTitle.textContent = `已编辑 ${changedFiles.length} 个文件`;
      const changeStats = document.createElement("small");
      const addStat = document.createElement("i");
      addStat.textContent = `+${additions}`;
      const deleteStat = document.createElement("em");
      deleteStat.textContent = `-${deletions}`;
      changeStats.append(addStat, " ", deleteStat);
      changeInfo.append(changeTitle, changeStats);
      head.append(changeIcon, changeInfo);
      const review = button("cx-review-change", "审核");
      review.addEventListener("click", (event) => { event.stopPropagation(); setPanel("review"); });
      head.appendChild(review);
      changes.appendChild(head);
      changedFiles.slice(0, 5).forEach((file) => {
        const row = button("cx-change-row");
        const fileName = document.createElement("span");
        fileName.textContent = file.path || file.name || "file";
        const fileAdditions = document.createElement("i");
        fileAdditions.textContent = `+${Number(file.additions || 0)}`;
        const fileDeletions = document.createElement("em");
        fileDeletions.textContent = `-${Number(file.deletions || 0)}`;
        row.append(fileName, fileAdditions, fileDeletions);
        row.addEventListener("click", async (event) => {
          event.stopPropagation();
          if (!file.path) return;
          const absolutePath = /^[/\\]/.test(file.path) || !project?.workspace
            ? file.path
            : await window.agentDesktop?.joinPath?.(project.workspace, file.path) || file.path;
          switchToWorkbench();
          await window.EditorApp?.openPath?.(absolutePath);
        });
        changes.appendChild(row);
      });
      card.appendChild(changes);
    }
    card.addEventListener("click", () => {
      if (state.selectedId !== job.id) {
        state.selectedId = job.id;
        state.selectedConversationId = job.conversation_id || state.selectedConversationId;
        persistState();
        renderAgents();
        if (state.activePanel === "review") loadReview();
      }
    });
    card.addEventListener("dblclick", () => {
      switchToWorkbench();
      window.AiPanel?.openJob?.(job.id).catch?.((error) => window.EditorApp?.toast?.(error.message || String(error)));
    });
    return card;
  }

  function renderAgents() {
    let jobs = normalizeJobs(state.jobs, state.projects, state.dismissedIds)
      .filter((job) => !state.selectedProjectId || String(job.project_id) === state.selectedProjectId);
    if (state.activeOnly) jobs = jobs.filter((job) => ACTIVE_STATUSES.has(job.displayStatus));
    if (state.searchQuery) {
      const query = state.searchQuery.toLowerCase();
      jobs = jobs.filter((job) => `${titleFor(job)} ${syntheticEvents(job).map(eventText).join(" ")}`.toLowerCase().includes(query));
    }
    if (state.pinnedOnly) jobs = jobs.filter((job) => state.pinnedIds.has(job.id));
    if (state.selectedConversationId) {
      const conversationJobs = jobs.filter((job) => String(job.conversation_id || "") === state.selectedConversationId);
      if (conversationJobs.length || !state.selectedId) jobs = conversationJobs;
    }
    if (!state.selectedId || !jobs.some((job) => job.id === state.selectedId)) state.selectedId = jobs[0]?.id || null;
    persistState();
    updateHeader();
    els.scene.dataset.layout = state.layout;
    els.scene.textContent = "";
    els.layoutButtons.forEach((target) => {
      const active = target.dataset.layout === state.layout;
      target.classList.toggle("active", active);
      target.setAttribute("aria-pressed", String(active));
    });

    if (state.insightsVisible) {
      const projectJobs = normalizeJobs(state.jobs, state.projects, state.dismissedIds)
        .filter((job) => !state.selectedProjectId || String(job.project_id) === state.selectedProjectId);
      const succeeded = projectJobs.filter((job) => job.displayStatus === "succeeded").length;
      const failed = projectJobs.filter((job) => ["failed", "interrupted", "canceled"].includes(job.displayStatus)).length;
      const active = projectJobs.filter((job) => ACTIVE_STATUSES.has(job.displayStatus)).length;
      const card = document.createElement("article");
      card.className = "cx-insights-card";
      card.innerHTML = `<h2>Agent Insights</h2><p>${selectedProject()?.name || "All projects"}</p><div><strong>${projectJobs.length}</strong><span>Total tasks</span><strong>${succeeded}</strong><span>Succeeded</span><strong>${active}</strong><span>Active</span><strong>${failed}</strong><span>Failed</span></div>`;
      els.scene.appendChild(card);
      updateTaskControls();
      return;
    }

    if (state.layout === "solo" && jobs.length > 1) {
      const tabs = document.createElement("div");
      tabs.className = "cx-thread-tabs";
      for (const job of jobs) {
        const tab = button(`cx-thread-tab${job.id === state.selectedId ? " active" : ""}`, titleFor(job));
        tab.addEventListener("click", () => {
          state.selectedId = job.id;
          state.selectedConversationId = job.conversation_id || state.selectedConversationId;
          persistState();
          renderAgents();
          if (state.activePanel === "review") loadReview();
        });
        tabs.appendChild(tab);
      }
      els.scene.appendChild(tabs);
    }

    const visible = state.layout === "solo" ? jobs.filter((job) => job.id === state.selectedId) : jobs;
    if (!visible.length) {
      const card = document.createElement("article");
      card.className = "cx-thread-card";
      card.innerHTML = '<div class="cx-thread-empty">No agent windows yet.<br>Describe a task below to start one.</div>';
      els.scene.appendChild(card);
    } else {
      visible.forEach((job) => els.scene.appendChild(createThreadCard(job)));
    }
    updateTaskControls();
    syncJobWatcher();
  }

  function closeJobWatcher() {
    try { state.jobWatcher?.close?.(); } catch (_) {}
    state.jobWatcher = null;
    state.watchedJobId = null;
  }

  function mergeJob(nextJob) {
    if (!nextJob?.id) return;
    const id = String(nextJob.id);
    const current = state.jobs.find((job) => String(job.id) === id) || {};
    const merged = { ...current, ...nextJob, id };
    state.jobDetails.set(id, merged);
    state.jobs = [merged, ...state.jobs.filter((job) => String(job.id) !== id)];
  }

  function syncJobWatcher() {
    const job = selectedJob();
    if (!job || !ACTIVE_STATUSES.has(job.displayStatus) || !window.AiPanel?.client?.watchJob) {
      closeJobWatcher();
      return;
    }
    if (state.watchedJobId === job.id) return;
    closeJobWatcher();
    state.watchedJobId = job.id;
    state.jobWatcher = window.AiPanel.client.watchJob(job.id, (payload) => {
      const current = state.jobs.find((item) => String(item.id) === job.id) || job;
      if (payload.kind === "event" && payload.event) {
        const events = Array.isArray(current.events) ? current.events.slice() : [];
        const eventId = payload.event.id;
        if (!eventId || !events.some((event) => event.id === eventId)) events.push(payload.event);
        mergeJob({ ...current, events });
      } else if (payload.kind === "job" && payload.job) {
        mergeJob(payload.job);
      } else if (payload.kind === "done") {
        mergeJob(payload.job || { ...current, status: payload.status, result: payload.result, error: payload.error });
        closeJobWatcher();
      }
      renderAgents();
      if (state.activePanel === "review" && payload.kind === "done") loadReview();
    });
  }

  function updateTaskControls() {
    if (!els.taskControls) return;
    const job = selectedJob();
    const active = Boolean(job && ACTIVE_STATUSES.has(job.displayStatus));
    els.taskControls.hidden = !active;
    if (!active) return;
    els.taskStatus.textContent = job.displayStatus.replaceAll("_", " ");
    els.pauseTask.hidden = job.displayStatus === "paused" || job.displayStatus === "cancel_requested";
    els.resumeTask.hidden = job.displayStatus !== "paused";
    els.stopTask.disabled = job.displayStatus === "cancel_requested";
  }

  async function controlSelectedJob(action) {
    const job = selectedJob();
    const client = window.AiPanel?.client;
    if (!job || !client) return;
    const target = action === "pause" ? els.pauseTask : action === "resume" ? els.resumeTask : els.stopTask;
    target.disabled = true;
    try {
      const result = action === "pause"
        ? await client.pauseJob(job.id)
        : action === "resume"
          ? await client.resumeJob(job.id)
          : await client.cancel(job.id);
      mergeJob(result?.job || {
        ...job,
        status: action === "resume" ? "running" : action === "pause" ? "paused" : job.status,
        cancel_requested: action === "cancel",
      });
      renderAgents();
    } catch (error) {
      toast(error.message || error);
    } finally {
      target.disabled = false;
    }
  }

  async function newConversation() {
    if (!state.selectedProjectId || state.busy) {
      toast("Please select a project first");
      return;
    }
    state.busy = true;
    updateSendButton();
    try {
      const conversation = await window.AiPanel?.createConversation?.(state.selectedProjectId);
      if (!conversation) throw new Error("Unable to create a new chat");
      state.selectedConversationId = String(conversation.id);
      state.selectedId = null;
      persistState();
      renderAgents();
      els.prompt.value = "";
      els.prompt.focus();
    } catch (error) {
      toast(error.message || error);
    } finally {
      state.busy = false;
      updateSendButton();
    }
  }

  function renderRunMode() {
    const labels = { workspace: "▣  Approval for me", ask: "◉  Ask every time", read_only: "◇  Read only" };
    els.approvalMode.textContent = labels[state.runMode];
  }

  function cycleRunMode() {
    const modes = ["workspace", "ask", "read_only"];
    state.runMode = modes[(modes.indexOf(state.runMode) + 1) % modes.length];
    renderRunMode();
    persistState();
  }

  function renderModels() {
    if (!els.modelSelect) return;
    const previous = els.modelSelect.value;
    els.modelSelect.textContent = "";
    const fallback = document.createElement("option");
    fallback.value = "";
    fallback.textContent = "Default model";
    els.modelSelect.appendChild(fallback);
    for (const model of state.models) {
      const option = document.createElement("option");
      option.value = model.id || model.provider || "";
      option.textContent = model.label || model.name || `${model.provider || ""}/${model.model || ""}`.replace(/^\//, "");
      els.modelSelect.appendChild(option);
    }
    if (Array.from(els.modelSelect.options).some((option) => option.value === previous)) els.modelSelect.value = previous;
  }

  function categoryCount(id) {
    return state.todos.filter((todo) => todo.categoryId === id && !todo.isDone).length;
  }

  function renderCategories() {
    els.categories.hidden = !state.categoriesExpanded;
    els.categoryToggle.firstChild.nodeValue = state.categoriesExpanded ? "⌄ " : "› ";
    els.categories.textContent = "";
    const addRow = (id, name, inbox = false) => {
      const row = button(`cx-category-row${state.selectedCategoryId === id ? " active" : ""}`);
      row.appendChild(svg(inbox ? "M4 5h16v14H4zM4 14h5l1.5 2h3L15 14h5" : "M3 7h7l2 2h9v11H3z"));
      const label = document.createElement("span");
      label.textContent = name;
      row.appendChild(label);
      const count = id ? categoryCount(id) : state.todos.filter((todo) => !todo.isDone).length;
      if (count) {
        const badge = document.createElement("em");
        badge.textContent = String(count);
        row.appendChild(badge);
      }
      row.addEventListener("click", () => { state.selectedCategoryId = id; renderTodos(); });
      if (id) {
        row.title = "Double-click to rename · right-click to delete";
        row.addEventListener("dblclick", (event) => {
          event.preventDefault();
          const category = state.categories.find((item) => item.id === id);
          const name = window.prompt?.("Rename category", category?.name || "")?.trim();
          if (category && name) {
            category.name = name;
            persistTodos();
            renderTodos();
          }
        });
        row.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          const category = state.categories.find((item) => item.id === id);
          if (!category || !window.confirm?.(`Delete category “${category.name}”? Its todos will be kept.`)) return;
          state.categories = state.categories.filter((item) => item.id !== id);
          state.todos.forEach((todo) => { if (todo.categoryId === id) todo.categoryId = null; });
          if (state.selectedCategoryId === id) state.selectedCategoryId = null;
          persistTodos();
          renderTodos();
        });
      }
      els.categories.appendChild(row);
    };
    addRow(null, "All", true);
    state.categories.forEach((category) => addRow(category.id, category.name));
  }

  function renderTodos() {
    renderCategories();
    els.hideDone.classList.toggle("active", state.hideDone);
    els.clearDone.hidden = !state.todos.some((todo) => todo.isDone);
    els.todoList.textContent = "";
    const todos = state.todos.filter((todo) => {
      if (state.hideDone && todo.isDone) return false;
      return state.selectedCategoryId == null || todo.categoryId === state.selectedCategoryId;
    }).sort((a, b) => Number(Boolean(b.pinnedAt)) - Number(Boolean(a.pinnedAt)) || Number(a.isDone) - Number(b.isDone));
    for (const todo of todos) {
      const row = document.createElement("div");
      row.className = `cx-todo-row${todo.isDone ? " done" : ""}`;
      const check = button("cx-todo-check");
      check.title = todo.isDone ? "Mark as not done" : "Mark as done";
      check.addEventListener("click", () => {
        todo.isDone = !todo.isDone;
        persistTodos();
        renderTodos();
      });
      const text = document.createElement("span");
      text.textContent = todo.text;
      text.addEventListener("dblclick", () => {
        const next = window.prompt?.("Edit todo", todo.text)?.trim();
        if (next) {
          todo.text = next;
          persistTodos();
          renderTodos();
        }
      });
      const pin = button(`cx-todo-action${todo.pinnedAt ? " active" : ""}`, todo.pinnedAt ? "★" : "☆");
      pin.title = todo.pinnedAt ? "Unpin" : "Pin";
      pin.addEventListener("click", () => {
        todo.pinnedAt = todo.pinnedAt ? null : Date.now();
        persistTodos();
        renderTodos();
      });
      const remove = button("cx-todo-action", "×");
      remove.title = "Delete";
      remove.addEventListener("click", () => {
        state.todos = state.todos.filter((item) => item.id !== todo.id);
        persistTodos();
        renderTodos();
      });
      row.append(check, text, pin, remove);
      els.todoList.appendChild(row);
    }
  }

  function addCategory() {
    const name = window.prompt?.("New category")?.trim();
    if (!name) return;
    const existing = state.categories.find((category) => category.name.toLowerCase() === name.toLowerCase());
    if (existing) state.selectedCategoryId = existing.id;
    else {
      const category = { id: `category-${Date.now()}`, name };
      state.categories.push(category);
      state.selectedCategoryId = category.id;
    }
    persistTodos();
    renderTodos();
  }

  function submitTodo() {
    const draft = els.todoInput.value.trim();
    if (!draft) return;
    const tagMatch = draft.match(/(?:^|\s)#([^#]+?)\s*$/);
    let categoryId = state.selectedCategoryId;
    let text = draft;
    if (tagMatch) {
      const name = tagMatch[1].trim();
      text = draft.slice(0, tagMatch.index).trim();
      let category = state.categories.find((item) => item.name.toLowerCase() === name.toLowerCase());
      if (!category) {
        category = { id: `category-${Date.now()}`, name };
        state.categories.push(category);
      }
      categoryId = category.id;
    }
    if (text) state.todos.push({ id: `todo-${Date.now()}`, text, categoryId, isDone: false, pinnedAt: null });
    els.todoInput.value = "";
    els.todoSubmit.classList.remove("ready");
    persistTodos();
    renderTodos();
  }

  function renderFileTree(nodes, container, depth = 0) {
    for (const node of nodes || []) {
      const row = button("cx-file-row");
      row.style.paddingLeft = `${8 + depth * 15}px`;
      row.appendChild(svg(node.type === "dir" ? "M3 7h7l2 2h9v11H3z" : "M6 2h9l5 5v15H6zM14 2v6h6"));
      const label = document.createElement("span");
      label.textContent = node.name;
      row.appendChild(label);
      if (node.type === "dir") {
        const group = document.createElement("div");
        group.className = "cx-file-group";
        group.hidden = depth > 0;
        row.addEventListener("click", () => { group.hidden = !group.hidden; });
        container.append(row, group);
        renderFileTree(node.children || [], group, depth + 1);
      } else {
        row.addEventListener("click", () => selectFile(node.path, row));
        container.appendChild(row);
      }
    }
  }

  async function loadFiles() {
    const project = selectedProject();
    els.filesTree.textContent = "";
    els.filePreview.textContent = "Loading files…";
    els.filesTitle.textContent = project?.name || "Files";
    if (!project?.workspace) {
      els.filePreview.textContent = "The selected project has no local workspace.";
      return;
    }
    try {
      const tree = PREVIEW_MODE && state.debugData?.tree
        ? state.debugData.tree
        : await window.agentDesktop.readTree(project.workspace);
      state.fileTree = tree;
      renderFileTree(tree.children || tree.entries || [], els.filesTree);
      els.filePreview.textContent = "Select a file to preview it.";
    } catch (error) {
      els.filePreview.textContent = `Unable to load files: ${error.message || error}`;
    }
  }

  async function selectFile(path, row) {
    document.querySelectorAll(".cx-file-row.active").forEach((item) => item.classList.remove("active"));
    row?.classList.add("active");
    state.selectedFilePath = path;
    els.openSelectedFile.disabled = false;
    els.filePreview.textContent = "Loading…";
    const project = selectedProject();
    try {
      let content;
      if (PREVIEW_MODE) content = "export function AgentView() {\n  return <main>Functional Agent workspace</main>;\n}\n";
      else {
        try {
          const absolute = await window.agentDesktop.joinPath(project.workspace, path);
          content = (await window.agentDesktop.readFile(absolute)).content;
        } catch (_) {
          content = (await window.AiPanel?.client?.readProjectFile(project.id, path))?.content || "";
        }
      }
      els.filePreview.textContent = String(content || "");
    } catch (error) {
      els.filePreview.textContent = `Unable to read file: ${error.message || error}`;
    }
  }

  async function openSelectedFile() {
    const project = selectedProject();
    if (!project?.workspace || !state.selectedFilePath) return;
    try {
      const absolute = await window.agentDesktop.joinPath(project.workspace, state.selectedFilePath);
      switchToWorkbench();
      await window.EditorApp?.openPath?.(absolute);
    } catch (error) {
      toast(error.message || error);
    }
  }

  function paintDiff(diff) {
    els.diffView.textContent = "";
    const lines = String(diff || "No textual diff available.").split("\n");
    for (const line of lines) {
      const span = document.createElement("span");
      span.className = line.startsWith("+") && !line.startsWith("+++")
        ? "add"
        : line.startsWith("-") && !line.startsWith("---")
          ? "del"
          : line.startsWith("@@")
            ? "hunk"
            : "";
      span.textContent = `${line}\n`;
      els.diffView.appendChild(span);
    }
  }

  async function loadReview() {
    const job = selectedJob();
    state.reviewJobId = job?.id || null;
    els.reviewFiles.textContent = "";
    state.reviewData = null;
    if (!job) {
      els.reviewSummary.textContent = "";
      paintDiff("Select an Agent task with file changes.");
      return;
    }
    if (!job.turn_id) {
      els.reviewSummary.textContent = "No turn diff";
      paintDiff((job.changed_files || []).length ? "This task predates Turn Diff checkpoints." : "This task did not change files.");
      return;
    }
    els.reviewSummary.textContent = "Loading…";
    try {
      const data = PREVIEW_MODE && state.debugData?.diff
        ? state.debugData.diff
        : await window.AiPanel?.client?.turnDiff(job.project_id || state.selectedProjectId, job.turn_id);
      state.reviewData = data;
      const files = data?.files || job.changed_files || [];
      els.reviewSummary.textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;
      for (const file of files) {
        const path = typeof file === "string" ? file : file.path;
        const row = button("cx-review-file");
        const change = document.createElement("b");
        change.textContent = String(file.change || file.status || "M").slice(0, 1).toUpperCase();
        const label = document.createElement("span");
        label.textContent = path;
        row.append(change, label);
        row.addEventListener("click", async () => {
          if (PREVIEW_MODE || !window.AiPanel?.client?.turnDiffFile) return paintDiff(data.diff);
          try {
            const detail = await window.AiPanel.client.turnDiffFile(job.project_id || state.selectedProjectId, job.turn_id, path);
            paintDiff(detail.diff || data.diff);
          } catch (_) {
            paintDiff(data.diff);
          }
        });
        els.reviewFiles.appendChild(row);
      }
      paintDiff(data?.diff || data?.message || "No textual diff available.");
    } catch (error) {
      els.reviewSummary.textContent = "Unavailable";
      paintDiff(error.message || error);
    }
  }

  async function openReviewInEditor() {
    const job = selectedJob();
    if (!job?.turn_id) return toast("This task has no review checkpoint");
    switchToWorkbench();
    await window.AiPanel?.openReview?.(job.project_id || state.selectedProjectId, job.turn_id);
  }

  function closeTerminal() {
    const terminal = state.terminal;
    if (!terminal) return;
    try { terminal.watcher?.close?.(); } catch (_) {}
    if (terminal.backendId) window.AiPanel?.client?.deleteTerminal?.(terminal.backendId).catch(() => {});
    try { terminal.term?.dispose?.(); } catch (_) {}
    state.terminal = null;
    state.terminalProjectId = null;
    if (els.terminalHost) els.terminalHost.textContent = "";
    if (els.terminalStatus) els.terminalStatus.textContent = "Closed";
  }

  async function createTerminal() {
    closeTerminal();
    const projectId = state.selectedProjectId;
    if (!projectId) return toast("请先选择项目");
    els.terminalHost.textContent = "";
    const TerminalCtor = window.Terminal?.Terminal || window.Terminal;
    const FitAddonCtor = window.FitAddon?.FitAddon || window.FitAddon;
    if (PREVIEW_MODE) {
      const preview = document.createElement("pre");
      preview.className = "cx-terminal-preview";
      preview.textContent = "$ pwd\n/Users/mac/codexia\n$ Ready";
      els.terminalHost.appendChild(preview);
      els.terminalStatus.textContent = "Preview";
      return;
    }
    if (typeof TerminalCtor !== "function" || typeof FitAddonCtor !== "function") {
      els.terminalStatus.textContent = "运行时不可用";
      els.terminalHost.textContent = "终端组件加载失败，请重启桌面端。";
      return;
    }
    els.terminalStatus.textContent = "Starting…";
    let term = null;
    let fitAddon = null;
    try {
      term = new TerminalCtor({
        fontFamily: "'SF Mono', Menlo, Monaco, Consolas, monospace",
        fontSize: 12,
        cursorBlink: true,
        scrollback: 5000,
        theme: { background: "#111111", foreground: "#eeeeee", cursor: "#8fc99a", selectionBackground: "#3c6045" },
      });
      fitAddon = new FitAddonCtor();
      term.loadAddon(fitAddon);
      term.open(els.terminalHost);
      try { fitAddon.fit(); } catch (_) {}
      if (!window.AiPanel?.client?.createTerminal) throw new Error("Agent 服务尚未连接");
      const info = await window.AiPanel.client.createTerminal(projectId, { shell: "/bin/zsh", cols: term.cols || 80, rows: term.rows || 24 });
      const terminal = { backendId: info.id, term, fitAddon, watcher: null };
      terminal.watcher = window.AiPanel.client.watchTerminal(info.id, (message) => {
        if (message.kind === "output") term.write(message.data || "");
        else if (message.kind === "done") {
          term.write(`\r\n[session ${message.status}]\r\n`);
          els.terminalStatus.textContent = message.status;
        } else if (message.kind === "error") els.terminalStatus.textContent = message.error || "Disconnected";
      });
      term.onData((data) => window.AiPanel.client.terminalInput(info.id, data).catch(() => {}));
      term.onResize(({ cols, rows }) => window.AiPanel.client.terminalResize(info.id, cols, rows).catch(() => {}));
      state.terminal = terminal;
      state.terminalProjectId = projectId;
      els.terminalStatus.textContent = "Connected";
      term.focus();
    } catch (error) {
      if (term) term.write(`\r\n\x1b[1;31m启动失败：${error.message || error}\x1b[0m\r\n`);
      else els.terminalHost.textContent = `终端启动失败：${error.message || error}`;
      els.terminalStatus.textContent = "Failed";
      state.terminal = { backendId: null, term, fitAddon, watcher: null };
    }
  }

  function fitTerminal() {
    try { state.terminal?.fitAddon?.fit?.(); } catch (_) {}
  }

  function setPanel(panel) {
    if (!["launcher", "review", "terminal", "todos", "files"].includes(panel)) return;
    state.activePanel = panel;
    els.panelTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.panel === panel));
    els.panelContents.forEach((content) => { content.hidden = content.dataset.panelContent !== panel; });
    if (els.panelTitle) els.panelTitle.textContent = ({ launcher: "工作区", review: "审查", terminal: "终端", todos: "任务清单", files: "文件" })[panel];
    if (els.panelHome) els.panelHome.hidden = panel === "launcher";
    closeAddMenu();
    persistState();
    if (!state.visible) return;
    if (panel === "files" && !state.fileTree) loadFiles();
    if (panel === "review") loadReview();
    if (panel === "terminal") {
      if (!state.terminal || state.terminalProjectId !== state.selectedProjectId) createTerminal();
      else setTimeout(fitTerminal, 0);
    }
  }

  async function loadRemoteData() {
    const client = window.AiPanel?.client;
    const aiState = window.AiPanel?.getState?.() || {};
    if (!client || !aiState.connected) return { projects: aiState.projects || [], jobs: state.jobs, models: state.models };
    const [jobsResult, projectsResult, modelsResult] = await Promise.all([
      client.jobs(),
      client.projects().catch(() => ({ projects: aiState.projects || [] })),
      state.models.length ? Promise.resolve({ models: state.models }) : client.models().catch(() => ({ models: [] })),
    ]);
    const summaries = normalizeJobs(jobsResult.jobs || [], projectsResult.projects || [], state.dismissedIds);
    const details = await Promise.all(summaries.map(async (summary, index) => {
      const cached = state.jobDetails.get(summary.id);
      if (cached && !ACTIVE_STATUSES.has(summary.displayStatus)) return { ...summary, ...cached };
      if (index >= 8 && !ACTIVE_STATUSES.has(summary.displayStatus)) return { ...summary, ...(cached || {}) };
      try {
        const result = await client.job(summary.id);
        const detail = { ...summary, ...(result.job || summary) };
        state.jobDetails.set(summary.id, detail);
        return detail;
      } catch (_) {
        return { ...summary, ...(cached || {}) };
      }
    }));
    return { projects: projectsResult.projects || [], jobs: details, models: modelsResult.models || [] };
  }

  async function refresh({ quiet = false } = {}) {
    if (!state.initialized || state.busy) return;
    state.busy = true;
    try {
      const data = state.debugData || await loadRemoteData();
      state.projects = data.projects || [];
      state.jobs = data.jobs || [];
      state.models = data.models || state.models;
      const aiState = window.AiPanel?.getState?.() || {};
      if (!state.selectedProjectId && aiState.selectedProjectId) state.selectedProjectId = String(aiState.selectedProjectId);
      if (!state.selectedConversationId && aiState.conversationId) state.selectedConversationId = String(aiState.conversationId);
    } catch (error) {
      if (!quiet) window.EditorApp?.toast?.(error.message || "Unable to refresh agents");
    } finally {
      state.busy = false;
      renderProjects();
      renderModels();
      renderAgents();
      if (state.visible && state.activePanel === "review" && state.reviewJobId !== state.selectedId) loadReview();
      updateSendButton();
    }
  }

  function updateSendButton() {
    if (!els.send) return;
    els.send.disabled = !els.prompt.value.trim() || !state.selectedProjectId || state.busy;
  }

  async function startAgent() {
    const rawPrompt = els.prompt.value.trim();
    const context = [];
    if (state.goal) context.push(`持续目标：${state.goal}`);
    if (state.planMode) context.push("请先给出清晰、可验证的实施计划，再按计划执行。");
    if (state.contextFiles.length) context.push(state.contextFiles.map((path) => `Relevant file: ${path}`).join("\n"));
    const prompt = context.length ? `${context.join("\n")}\n\n${rawPrompt}` : rawPrompt;
    if (!prompt || !state.selectedProjectId || state.busy) return;
    state.busy = true;
    updateSendButton();
    try {
      const activeJob = selectedJob();
      if (activeJob && ACTIVE_STATUSES.has(activeJob.displayStatus)) {
        await window.AiPanel?.client?.steerJob(activeJob.id, prompt);
        const events = Array.isArray(activeJob.events) ? activeJob.events.slice() : [];
        events.push({ id: `local-steer-${Date.now()}`, type: "user_message", content: [{ type: "text", text: prompt }] });
        mergeJob({ ...activeJob, events });
        els.prompt.value = "";
        renderAgents();
        toast("Guidance sent to the running Agent");
        return;
      }
      const job = await window.AiPanel?.startAgent?.(prompt, state.selectedProjectId, {
        conversationId: state.selectedConversationId,
        newConversation: !state.selectedConversationId,
        runMode: state.runMode,
        provider: els.modelSelect?.value || "",
      });
      els.prompt.value = "";
      state.contextFiles = [];
      els.attach.textContent = "＋";
      if (job?.id) {
        state.selectedId = String(job.id);
        state.selectedConversationId = String(job.conversation_id || window.AiPanel?.getState?.().conversationId || state.selectedConversationId || "") || null;
        state.jobs = [job, ...state.jobs.filter((item) => String(item.id) !== String(job.id))];
      }
      renderAgents();
    } catch (error) {
      window.EditorApp?.toast?.(error.message || String(error));
    } finally {
      state.busy = false;
      renderAgents();
      updateSendButton();
      els.prompt.focus();
    }
  }

  function setLayout(layout) {
    if (!["solo", "grid", "list"].includes(layout)) return;
    state.layout = layout;
    persistState();
    renderAgents();
  }

  async function attachFile() {
    try {
      const path = await window.agentDesktop?.openFileDialog?.();
      if (!path) return;
      if (!state.contextFiles.includes(path)) state.contextFiles.push(path);
      els.attach.textContent = `＋ ${state.contextFiles.length}`;
      els.attach.title = state.contextFiles.join("\n");
      els.prompt.focus();
    } catch (error) {
      toast(error.message || error);
    }
  }

  function setAddMenu(open) {
    state.addMenuOpen = Boolean(open);
    if (els.addMenu) els.addMenu.hidden = !state.addMenuOpen;
    els.attach?.setAttribute("aria-expanded", String(state.addMenuOpen));
  }

  function closeAddMenu() {
    setAddMenu(false);
  }

  function attachCurrentFile() {
    const path = window.EditorApp?.getActiveTab?.()?.path;
    if (!path) return toast("当前编辑器没有打开文件");
    if (!state.contextFiles.includes(path)) state.contextFiles.push(path);
    els.attach.textContent = `＋ ${state.contextFiles.length}`;
    els.attach.title = state.contextFiles.join("\n");
    closeAddMenu();
    els.prompt.focus();
  }

  function editGoal() {
    const next = window.prompt("设置 Agent 持续追求的目标", state.goal || "");
    if (next === null) return;
    state.goal = next.trim();
    document.getElementById("cxAddGoal")?.classList.toggle("active", Boolean(state.goal));
    persistState();
    closeAddMenu();
    toast(state.goal ? "目标已设置" : "目标已清除");
  }

  function togglePlanMode() {
    state.planMode = !state.planMode;
    document.getElementById("cxPlanMode")?.classList.toggle("active", state.planMode);
    persistState();
    closeAddMenu();
    toast(state.planMode ? "已开启计划模式" : "已关闭计划模式");
  }

  function startVoiceInput() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return toast("Voice dictation is not available in this Electron runtime");
    const recognition = new SpeechRecognition();
    recognition.lang = navigator.language || "zh-CN";
    recognition.interimResults = false;
    els.mic.classList.add("active");
    recognition.onresult = (event) => {
      const text = event.results?.[0]?.[0]?.transcript || "";
      els.prompt.value = `${els.prompt.value}${els.prompt.value ? " " : ""}${text}`;
      updateSendButton();
    };
    recognition.onerror = (event) => toast(event.error || "Voice input failed");
    recognition.onend = () => els.mic.classList.remove("active");
    recognition.start();
  }

  function togglePanelFocus() {
    state.panelFocused = !state.panelFocused;
    state.panelCollapsed = false;
    els.root.classList.toggle("cx-panel-focused", state.panelFocused);
    els.root.classList.remove("cx-panel-collapsed");
    document.getElementById("cxFocusPanel").textContent = state.panelFocused ? "↙" : "↗";
    setTimeout(fitTerminal, 0);
  }

  function togglePanelCollapsed() {
    state.panelCollapsed = !state.panelCollapsed;
    state.panelFocused = false;
    els.root.classList.toggle("cx-panel-collapsed", state.panelCollapsed);
    els.root.classList.remove("cx-panel-focused");
    document.getElementById("cxCollapsePanel").textContent = state.panelCollapsed ? "◨" : "◧";
  }

  function bind() {
    document.getElementById("cxAgentBack")?.addEventListener("click", toggleSidebar);
    document.getElementById("cxSidebarRestore")?.addEventListener("click", toggleSidebar);
    document.getElementById("cxSidebarBackdrop")?.addEventListener("click", toggleSidebar);
    els.root.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && els.root.classList.contains("cx-mobile-sidebar-open")) {
        toggleSidebar();
        document.getElementById("cxSidebarRestore")?.focus();
      }
    });
    document.getElementById("cxNewAgent")?.addEventListener("click", newConversation);
    document.getElementById("cxSidebarSearch")?.addEventListener("click", () => {
      els.searchBox.hidden = false;
      els.taskSearch.focus();
      els.taskSearch.select();
    });
    document.getElementById("cxCloseSearch")?.addEventListener("click", () => {
      els.searchBox.hidden = true;
      els.taskSearch.value = "";
      state.searchQuery = "";
      renderAgents();
    });
    els.taskSearch.addEventListener("input", () => {
      state.searchQuery = els.taskSearch.value.trim();
      state.insightsVisible = false;
      renderAgents();
    });
    document.getElementById("cxNewProject")?.addEventListener("click", () => window.AiPanel?.openCreateProject?.());
    document.getElementById("cxAccount")?.addEventListener("click", () => window.AiPanel?.openSettings?.());
    document.getElementById("cxSettings")?.addEventListener("click", () => window.AiPanel?.openSettings?.());
    document.getElementById("cxSidebarDownload")?.addEventListener("click", switchToWorkbench);
    document.getElementById("cxFilterTasks")?.addEventListener("click", (event) => {
      state.activeOnly = !state.activeOnly;
      event.currentTarget.classList.toggle("active", state.activeOnly);
      renderAgents();
    });
    document.getElementById("cxOpenIn")?.addEventListener("click", async () => {
      const project = selectedProject();
      if (!project?.workspace) return toast("The selected project has no local workspace");
      switchToWorkbench();
      await window.EditorApp?.openFolder?.(project.workspace);
    });
    document.getElementById("cxHeaderShare")?.addEventListener("click", async () => {
      const workspace = selectedProject()?.workspace;
      if (!workspace) return toast("当前项目没有本地工作区");
      try {
        await navigator.clipboard.writeText(workspace);
        toast("工作区路径已复制");
      } catch (_) {
        toast(workspace);
      }
    });
    document.querySelectorAll("[data-cx-unavailable]").forEach((target) => target.addEventListener("click", () => {
      toast(`${target.dataset.cxUnavailable} requires a backend capability that Android Agent does not provide yet.`);
    }));
    document.getElementById("cxInsights")?.addEventListener("click", () => {
      state.insightsVisible = !state.insightsVisible;
      state.pinnedOnly = false;
      renderAgents();
    });
    document.getElementById("cxPinned")?.addEventListener("click", (event) => {
      state.pinnedOnly = !state.pinnedOnly;
      state.insightsVisible = false;
      event.currentTarget.classList.toggle("active", state.pinnedOnly);
      renderAgents();
    });
    els.layoutButtons.forEach((target) => target.addEventListener("click", () => setLayout(target.dataset.layout)));
    els.approvalMode.addEventListener("click", cycleRunMode);
    els.attach.addEventListener("click", (event) => {
      event.stopPropagation();
      setAddMenu(!state.addMenuOpen);
    });
    document.getElementById("cxAddFile")?.addEventListener("click", async () => { closeAddMenu(); await attachFile(); });
    document.getElementById("cxAddCurrentFile")?.addEventListener("click", attachCurrentFile);
    document.getElementById("cxAddGoal")?.addEventListener("click", editGoal);
    document.getElementById("cxPlanMode")?.addEventListener("click", togglePlanMode);
    els.mic.addEventListener("click", startVoiceInput);
    els.pauseTask.addEventListener("click", () => controlSelectedJob("pause"));
    els.resumeTask.addEventListener("click", () => controlSelectedJob("resume"));
    els.stopTask.addEventListener("click", () => controlSelectedJob("cancel"));
    els.send.addEventListener("click", startAgent);
    els.prompt.addEventListener("input", updateSendButton);
    els.prompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); startAgent(); }
    });
    els.panelTabs.forEach((tab) => tab.dataset.panel && tab.addEventListener("click", () => setPanel(tab.dataset.panel)));
    document.getElementById("cxPanelHome")?.addEventListener("click", () => setPanel("launcher"));
    document.getElementById("cxOpenBrowser")?.addEventListener("click", () => {
      switchToWorkbench();
      window.EditorApp?.togglePreview?.();
    });
    document.getElementById("cxRefreshReview")?.addEventListener("click", loadReview);
    document.getElementById("cxOpenReview")?.addEventListener("click", openReviewInEditor);
    document.getElementById("cxRefreshFiles")?.addEventListener("click", loadFiles);
    els.openSelectedFile.addEventListener("click", openSelectedFile);
    document.getElementById("cxNewTerminal")?.addEventListener("click", createTerminal);
    document.getElementById("cxCloseTerminal")?.addEventListener("click", closeTerminal);
    document.getElementById("cxFocusPanel")?.addEventListener("click", togglePanelFocus);
    document.getElementById("cxCollapsePanel")?.addEventListener("click", togglePanelCollapsed);
    document.addEventListener("click", (event) => {
      if (state.addMenuOpen && !els.addMenu?.contains(event.target) && event.target !== els.attach) closeAddMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.addMenuOpen) closeAddMenu();
    });
    els.hideDone.addEventListener("click", () => { state.hideDone = !state.hideDone; renderTodos(); });
    els.clearDone.addEventListener("click", () => {
      state.todos = state.todos.filter((todo) => !todo.isDone);
      persistTodos();
      renderTodos();
    });
    els.categoryToggle.addEventListener("click", () => { state.categoriesExpanded = !state.categoriesExpanded; renderCategories(); });
    document.getElementById("cxAddCategory")?.addEventListener("click", addCategory);
    els.todoInput.addEventListener("input", () => els.todoSubmit.classList.toggle("ready", Boolean(els.todoInput.value.trim())));
    els.todoInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); submitTodo(); }
    });
    els.todoSubmit.addEventListener("click", submitTodo);
    document.getElementById("cxDismissPlux")?.addEventListener("click", () => {
      state.pluxDismissed = true;
      persistTodos();
      document.querySelector(".cx-plux")?.setAttribute("hidden", "");
    });
  }

  function init() {
    if (state.initialized) return;
    els.root = document.getElementById("codexiaAgentView");
    if (!els.root) return;
    els.projectList = document.getElementById("cxProjectList");
    els.recentList = document.getElementById("cxRecentList");
    els.searchBox = document.getElementById("cxSidebarSearchBox");
    els.taskSearch = document.getElementById("cxTaskSearch");
    els.scene = document.getElementById("cxAgentCards");
    els.prompt = document.getElementById("cxAgentPrompt");
    els.send = document.getElementById("cxSendAgent");
    els.attach = document.getElementById("cxAttach");
    els.addMenu = document.getElementById("cxAddMenu");
    els.mic = document.getElementById("cxMic");
    els.approvalMode = document.getElementById("cxApprovalMode");
    els.modelSelect = document.getElementById("cxModelSelect");
    els.taskControls = document.getElementById("cxTaskControls");
    els.taskStatus = document.getElementById("cxTaskStatus");
    els.pauseTask = document.getElementById("cxPauseTask");
    els.resumeTask = document.getElementById("cxResumeTask");
    els.stopTask = document.getElementById("cxStopTask");
    els.layoutButtons = Array.from(document.querySelectorAll(".cx-layout-button"));
    els.panelTabs = Array.from(document.querySelectorAll("[data-panel]"));
    els.panelContents = Array.from(document.querySelectorAll("[data-panel-content]"));
    els.panelTitle = document.getElementById("cxPanelTitle");
    els.panelHome = document.getElementById("cxPanelHome");
    els.headerTitle = document.getElementById("cxHeaderTitle");
    els.hideDone = document.getElementById("cxHideDone");
    els.clearDone = document.getElementById("cxClearDone");
    els.categoryToggle = document.getElementById("cxCategoryToggle");
    els.categories = document.getElementById("cxCategories");
    els.todoList = document.getElementById("cxTodoList");
    els.todoInput = document.getElementById("cxTodoInput");
    els.todoSubmit = document.getElementById("cxTodoSubmit");
    els.reviewSummary = document.getElementById("cxReviewSummary");
    els.reviewFiles = document.getElementById("cxReviewFiles");
    els.diffView = document.getElementById("cxDiffView");
    els.terminalHost = document.getElementById("cxTerminalHost");
    els.terminalStatus = document.getElementById("cxTerminalStatus");
    els.filesTree = document.getElementById("cxFilesTree");
    els.filesTitle = document.getElementById("cxFilesTitle");
    els.filePreview = document.getElementById("cxFilePreview");
    els.openSelectedFile = document.getElementById("cxOpenSelectedFile");

    loadState();
    els.root.classList.toggle("cx-sidebar-collapsed", state.sidebarCollapsed);
    // Electron draws native macOS traffic lights for hiddenInset windows. Keep
    // the CSS copy only in browser previews, where no native chrome exists.
    if (!PREVIEW_MODE) document.querySelector(".cx-traffic-lights")?.setAttribute("aria-hidden", "true");
    if (!PREVIEW_MODE) document.querySelector(".cx-traffic-lights")?.style.setProperty("visibility", "hidden");
    if (PREVIEW_MODE) {
      state.debugData = createPreviewFixture();
      state.projects = state.debugData.projects;
      state.jobs = state.debugData.jobs;
      state.models = state.debugData.models;
      state.selectedProjectId = "codexia";
      state.selectedConversationId = "preview-conversation";
      ensurePreviewTodos();
    }
    bind();
    state.initialized = true;
    if (state.pluxDismissed) document.querySelector(".cx-plux")?.setAttribute("hidden", "");
    renderProjects();
    renderModels();
    renderAgents();
    renderTodos();
    renderRunMode();
    document.getElementById("cxPlanMode")?.classList.toggle("active", state.planMode);
    document.getElementById("cxAddGoal")?.classList.toggle("active", Boolean(state.goal));
    setPanel(state.activePanel || "launcher");
    updateSendButton();
  }

  function setVisible(visible) {
    if (!state.initialized) init();
    state.visible = Boolean(visible);
    if (!els.root) return;
    els.root.hidden = !state.visible;
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
    if (state.visible) {
      refresh();
      setPanel(state.activePanel || "launcher");
      state.timer = setInterval(() => refresh({ quiet: true }), 4000);
    } else closeJobWatcher();
  }

  window.CodexiaAgentView = {
    init,
    setVisible,
    refresh,
    setLayout,
    isVisible: () => state.visible,
    _internal: {
      displayStatus,
      statusClass,
      normalizeJobs,
      syntheticEvents,
      joinAssistantText,
      displayEventsFor,
      titleFor,
      setDebugData(data) {
        state.debugData = data;
        state.projects = data?.projects || [];
        state.jobs = data?.jobs || [];
        if (state.initialized) { renderProjects(); renderAgents(); }
      },
      getState: () => state,
    },
  };

  if (typeof module !== "undefined" && module.exports) module.exports = window.CodexiaAgentView;
})();
