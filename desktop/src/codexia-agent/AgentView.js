(() => {
  "use strict";

  const STORE_KEY = "android-agent-codexia-shell-v2";
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
    jobs: [],
    projects: [],
    dismissedIds: new Set(),
    timer: null,
    debugData: null,
    activePanel: "todos",
    categoriesExpanded: true,
    selectedCategoryId: null,
    hideDone: false,
    categories: [],
    todos: [],
    pluxDismissed: false,
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
      localStorage.setItem(STORE_KEY, JSON.stringify({ layout: state.layout, selectedId: state.selectedId, selectedProjectId: state.selectedProjectId }));
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
        prompt: "$computer-use send hi to gemini app prompt input, id is com.google.GeminiMacOS and it's opened",
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
    };
  }

  function ensurePreviewTodos() {
    if (!PREVIEW_MODE || state.todos.length) return;
    state.categories = [{ id: "research", name: "research" }, { id: "good", name: "good" }];
    state.todos = [{ id: "todo-preview", text: "I’m using the computer-use skill to interact with the already-open Gemini app and enter “hi” in its prompt field.", categoryId: null, isDone: false }];
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
      row.addEventListener("click", () => {
        state.selectedProjectId = String(project.id);
        persistState();
        renderProjects();
        els.prompt.focus();
      });
      els.projectList.appendChild(row);
    }
  }

  function createThreadCard(job) {
    const card = document.createElement("article");
    card.className = "cx-thread-card";
    card.dataset.jobId = job.id;
    const body = document.createElement("div");
    body.className = "cx-thread-body";
    const events = syntheticEvents(job);
    if (!events.length) {
      const empty = document.createElement("div");
      empty.className = "cx-thread-empty";
      empty.textContent = "Start a new chat to open an Agent window.";
      body.appendChild(empty);
    }
    for (const event of events) {
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
      const text = eventText(event);
      if (!text) continue;
      const message = document.createElement("div");
      const isUser = ["user_message", "user"].includes(event.type);
      message.className = `cx-thread-message ${isUser ? "user" : "assistant"}`;
      message.textContent = text;
      body.appendChild(message);
    }
    card.appendChild(body);
    card.addEventListener("dblclick", () => {
      switchToWorkbench();
      window.AiPanel?.openJob?.(job.id).catch?.((error) => window.EditorApp?.toast?.(error.message || String(error)));
    });
    return card;
  }

  function renderAgents() {
    const jobs = normalizeJobs(state.jobs, state.projects, state.dismissedIds);
    if (!state.selectedId || !jobs.some((job) => job.id === state.selectedId)) state.selectedId = jobs[0]?.id || null;
    persistState();
    els.scene.dataset.layout = state.layout;
    els.scene.textContent = "";
    els.layoutButtons.forEach((target) => {
      const active = target.dataset.layout === state.layout;
      target.classList.toggle("active", active);
      target.setAttribute("aria-pressed", String(active));
    });

    if (state.layout === "solo" && jobs.length > 1) {
      const tabs = document.createElement("div");
      tabs.className = "cx-thread-tabs";
      for (const job of jobs) {
        const tab = button(`cx-thread-tab${job.id === state.selectedId ? " active" : ""}`, titleFor(job));
        tab.addEventListener("click", () => { state.selectedId = job.id; renderAgents(); });
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
      els.categories.appendChild(row);
    };
    addRow(null, "All", true);
    state.categories.forEach((category) => addRow(category.id, category.name));
  }

  function renderTodos() {
    renderCategories();
    els.hideDone.classList.toggle("active", state.hideDone);
    els.todoList.textContent = "";
    const todos = state.todos.filter((todo) => {
      if (state.hideDone && todo.isDone) return false;
      return state.selectedCategoryId == null || todo.categoryId === state.selectedCategoryId;
    });
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
      row.append(check, text);
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
    if (text) state.todos.push({ id: `todo-${Date.now()}`, text, categoryId, isDone: false });
    els.todoInput.value = "";
    els.todoSubmit.classList.remove("ready");
    persistTodos();
    renderTodos();
  }

  function setPanel(panel) {
    state.activePanel = panel;
    els.panelTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.panel === panel));
    els.panelContents.forEach((content) => { content.hidden = content.dataset.panelContent !== panel; });
  }

  async function loadRemoteData() {
    const client = window.AiPanel?.client;
    const aiState = window.AiPanel?.getState?.() || {};
    if (!client || !aiState.connected) return { projects: aiState.projects || [], jobs: state.jobs };
    const [jobsResult, projectsResult] = await Promise.all([
      client.jobs(),
      client.projects().catch(() => ({ projects: aiState.projects || [] })),
    ]);
    return { projects: projectsResult.projects || [], jobs: jobsResult.jobs || [] };
  }

  async function refresh({ quiet = false } = {}) {
    if (!state.initialized || state.busy) return;
    state.busy = true;
    try {
      const data = state.debugData || await loadRemoteData();
      state.projects = data.projects || [];
      state.jobs = data.jobs || [];
    } catch (error) {
      if (!quiet) window.EditorApp?.toast?.(error.message || "Unable to refresh agents");
    } finally {
      state.busy = false;
      renderProjects();
      renderAgents();
      updateSendButton();
    }
  }

  function updateSendButton() {
    if (!els.send) return;
    els.send.disabled = !els.prompt.value.trim() || !state.selectedProjectId || state.busy;
  }

  async function startAgent() {
    const prompt = els.prompt.value.trim();
    if (!prompt || !state.selectedProjectId || state.busy) return;
    state.busy = true;
    updateSendButton();
    try {
      const job = await window.AiPanel?.startAgent?.(prompt, state.selectedProjectId);
      els.prompt.value = "";
      if (job?.id) {
        state.selectedId = String(job.id);
        state.jobs = [job, ...state.jobs.filter((item) => String(item.id) !== String(job.id))];
      }
      await refresh({ quiet: true });
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

  function bind() {
    document.getElementById("cxAgentBack")?.addEventListener("click", switchToWorkbench);
    document.getElementById("cxNewAgent")?.addEventListener("click", () => { els.prompt.focus(); els.prompt.select(); });
    document.getElementById("cxSidebarSearch")?.addEventListener("click", () => els.prompt.focus());
    els.layoutButtons.forEach((target) => target.addEventListener("click", () => setLayout(target.dataset.layout)));
    els.send.addEventListener("click", startAgent);
    els.prompt.addEventListener("input", updateSendButton);
    els.prompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); startAgent(); }
    });
    els.panelTabs.forEach((tab) => tab.dataset.panel && tab.addEventListener("click", () => setPanel(tab.dataset.panel)));
    els.hideDone.addEventListener("click", () => { state.hideDone = !state.hideDone; renderTodos(); });
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
    els.scene = document.getElementById("cxAgentCards");
    els.prompt = document.getElementById("cxAgentPrompt");
    els.send = document.getElementById("cxSendAgent");
    els.layoutButtons = Array.from(document.querySelectorAll(".cx-layout-button"));
    els.panelTabs = Array.from(document.querySelectorAll(".cx-right-tabs [data-panel]"));
    els.panelContents = Array.from(document.querySelectorAll("[data-panel-content]"));
    els.hideDone = document.getElementById("cxHideDone");
    els.categoryToggle = document.getElementById("cxCategoryToggle");
    els.categories = document.getElementById("cxCategories");
    els.todoList = document.getElementById("cxTodoList");
    els.todoInput = document.getElementById("cxTodoInput");
    els.todoSubmit = document.getElementById("cxTodoSubmit");

    loadState();
    // Electron draws native macOS traffic lights for hiddenInset windows. Keep
    // the CSS copy only in browser previews, where no native chrome exists.
    if (!PREVIEW_MODE) document.querySelector(".cx-traffic-lights")?.setAttribute("aria-hidden", "true");
    if (!PREVIEW_MODE) document.querySelector(".cx-traffic-lights")?.style.setProperty("visibility", "hidden");
    if (PREVIEW_MODE) {
      state.debugData = createPreviewFixture();
      state.projects = state.debugData.projects;
      state.jobs = state.debugData.jobs;
      state.selectedProjectId = "codexia";
      ensurePreviewTodos();
    }
    bind();
    state.initialized = true;
    if (state.pluxDismissed) document.querySelector(".cx-plux")?.setAttribute("hidden", "");
    renderProjects();
    renderAgents();
    renderTodos();
    setPanel("todos");
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
      state.timer = setInterval(() => refresh({ quiet: true }), 4000);
    }
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
