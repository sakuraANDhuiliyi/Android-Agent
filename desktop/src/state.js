(() => {
  "use strict";

  const STORAGE_KEY = "android-agent-desktop-v2";

  const initialState = {
    // Connection & identity
    baseUrl: "http://127.0.0.1:8000",
    token: "",
    userId: "",
    connected: false,
    serverManaged: false,
    serverRunning: false,
    phoneUrl: "",

    // Layout
    sidebarView: "explorer",
    sidebarCollapsed: false,
    aiCollapsed: false,
    bottomView: "terminal",
    bottomPanelOpen: true,
    bottomPanelHeight: 240,

    // Projects
    projects: [],
    selectedProjectId: null,

    // Conversations
    conversations: [],
    conversationId: null,
    archivedConversations: [],

    // Jobs & messages
    jobs: [],
    currentJobId: null,
    jobStatus: null,
    jobEvents: [],
    lastEventId: 0,
    plan: [],
    approvals: [],
    toolCalls: [],
    toolResults: [],
    running: false,
    awaitingApproval: false,

    // Context chips
    contextChips: [],

    // Editor
    tabs: [],
    activeTabId: null,
    root: null,
    fileIndex: [],

    // Terminal
    terminals: [],
    activeTerminalId: null,

    // Search / problems
    searchResults: [],
    problems: [],
    outputLog: "",
    buildLog: "",

    // Meta
    tokenEstimate: null,
    timingMs: null,
    providerModel: null,
    fallbackUsed: false,
    recovery: false,
  };

  function loadPersisted() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      return JSON.parse(raw);
    } catch (_) {
      return {};
    }
  }

  let state = { ...initialState, ...loadPersisted() };

  const listeners = [];

  function subscribe(fn) {
    listeners.push(fn);
    return () => {
      const idx = listeners.indexOf(fn);
      if (idx >= 0) listeners.splice(idx, 1);
    };
  }

  function dispatch(action) {
    const next = reducer(state, action);
    state = next;
    persistState();
    listeners.forEach((fn) => fn(state, action));
  }

  function getState() {
    return state;
  }

  function persistState() {
    try {
      const persistable = {
        baseUrl: state.baseUrl,
        token: state.token,
        sidebarView: state.sidebarView,
        sidebarCollapsed: state.sidebarCollapsed,
        aiCollapsed: state.aiCollapsed,
        bottomView: state.bottomView,
        bottomPanelOpen: state.bottomPanelOpen,
        bottomPanelHeight: state.bottomPanelHeight,
        selectedProjectId: state.selectedProjectId,
        conversationId: state.conversationId,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(persistable));
    } catch (_) {
      /* ignore */
    }
  }

  function reducer(s, action) {
    const next = { ...s };
    switch (action.type) {
      case "LAYOUT_SIDEBAR_VIEW":
        next.sidebarView = action.view;
        next.sidebarCollapsed = false;
        break;
      case "LAYOUT_TOGGLE_SIDEBAR":
        next.sidebarCollapsed = !s.sidebarCollapsed;
        break;
      case "LAYOUT_TOGGLE_AI":
        next.aiCollapsed = !s.aiCollapsed;
        break;
      case "LAYOUT_BOTTOM_VIEW":
        next.bottomView = action.view;
        next.bottomPanelOpen = true;
        break;
      case "LAYOUT_TOGGLE_BOTTOM":
        next.bottomPanelOpen = !s.bottomPanelOpen;
        break;
      case "LAYOUT_BOTTOM_HEIGHT":
        next.bottomPanelHeight = Math.max(120, Math.min(600, action.height));
        break;
      case "SET_CONNECTION":
        next.connected = action.connected;
        if (action.baseUrl !== undefined) next.baseUrl = action.baseUrl;
        if (action.token !== undefined) next.token = action.token;
        if (action.userId !== undefined) next.userId = action.userId;
        break;
      case "SET_SERVER_STATUS":
        next.serverRunning = action.running;
        next.serverManaged = action.managed;
        next.phoneUrl = action.phoneUrl || "";
        break;
      case "SET_PROJECTS":
        next.projects = action.projects || [];
        break;
      case "SELECT_PROJECT":
        next.selectedProjectId = action.projectId;
        // Reset conversation-related state when project changes.
        next.conversations = [];
        next.conversationId = null;
        next.jobs = [];
        next.currentJobId = null;
        next.plan = [];
        next.approvals = [];
        next.toolCalls = [];
        next.toolResults = [];
        next.contextChips = [];
        break;
      case "SET_CONVERSATIONS":
        next.conversations = action.conversations || [];
        next.archivedConversations = action.archived || next.archivedConversations;
        break;
      case "SELECT_CONVERSATION":
        next.conversationId = action.conversationId;
        next.jobs = [];
        next.currentJobId = null;
        next.plan = [];
        next.approvals = [];
        next.toolCalls = [];
        next.toolResults = [];
        next.jobEvents = [];
        next.lastEventId = 0;
        next.running = false;
        break;
      case "RENAME_CONVERSATION": {
        const list = next.conversations.map((c) =>
          c.id === action.id ? { ...c, title: action.title } : c
        );
        next.conversations = list;
        break;
      }
      case "ARCHIVE_CONVERSATION": {
        const conv = next.conversations.find((c) => c.id === action.id);
        if (conv) {
          next.archivedConversations = [...next.archivedConversations, { ...conv, archivedAt: Date.now() }];
          next.conversations = next.conversations.filter((c) => c.id !== action.id);
          if (next.conversationId === action.id) next.conversationId = null;
        }
        break;
      }
      case "RESTORE_CONVERSATION": {
        const conv = next.archivedConversations.find((c) => c.id === action.id);
        if (conv) {
          next.conversations = [conv, ...next.conversations];
          next.archivedConversations = next.archivedConversations.filter((c) => c.id !== action.id);
        }
        break;
      }
      case "SET_JOBS":
        next.jobs = action.jobs || [];
        break;
      case "SET_CURRENT_JOB":
        next.currentJobId = action.jobId;
        next.jobStatus = action.status || "queued";
        next.running = ["queued", "running", "awaiting_approval", "paused"].includes(next.jobStatus);
        next.awaitingApproval = next.jobStatus === "awaiting_approval";
        break;
      case "JOB_EVENTS": {
        const events = action.events || [];
        const merged = mergeEvents(s.jobEvents || [], events);
        next.jobEvents = merged;
        next.lastEventId = merged.reduce((max, e) => Math.max(max, e.id || 0), 0);
        if (action.status) {
          next.jobStatus = action.status;
          next.running = ["queued", "running", "awaiting_approval", "paused"].includes(action.status);
          next.awaitingApproval = action.status === "awaiting_approval";
        }
        if (action.plan !== undefined) next.plan = action.plan;
        if (action.approvals !== undefined) next.approvals = action.approvals;
        if (action.toolCalls !== undefined) next.toolCalls = action.toolCalls;
        if (action.toolResults !== undefined) next.toolResults = action.toolResults;
        if (action.tokenEstimate !== undefined) next.tokenEstimate = action.tokenEstimate;
        if (action.timingMs !== undefined) next.timingMs = action.timingMs;
        if (action.providerModel !== undefined) next.providerModel = action.providerModel;
        if (action.fallbackUsed !== undefined) next.fallbackUsed = action.fallbackUsed;
        if (action.recovery !== undefined) next.recovery = action.recovery;
        break;
      }
      case "SET_PLAN":
        next.plan = action.plan || [];
        break;
      case "SET_APPROVALS":
        next.approvals = action.approvals || [];
        next.awaitingApproval = (action.approvals || []).some((a) => a.status === "pending");
        break;
      case "SET_TOOL_CALLS":
        next.toolCalls = action.toolCalls || [];
        next.toolResults = action.toolResults || [];
        break;
      case "ADD_CONTEXT_CHIP": {
        const chips = next.contextChips.filter((c) => c.key !== action.chip.key);
        next.contextChips = [...chips, action.chip];
        break;
      }
      case "REMOVE_CONTEXT_CHIP":
        next.contextChips = next.contextChips.filter((c) => c.key !== action.key);
        break;
      case "CLEAR_CONTEXT_CHIPS":
        next.contextChips = [];
        break;
      case "SET_TABS":
        next.tabs = action.tabs || [];
        if (action.activeTabId !== undefined) next.activeTabId = action.activeTabId;
        break;
      case "SELECT_TAB":
        next.activeTabId = action.tabId;
        break;
      case "SET_ROOT":
        next.root = action.root;
        break;
      case "SET_FILE_INDEX":
        next.fileIndex = action.files || [];
        break;
      case "SET_SEARCH_RESULTS":
        next.searchResults = action.results || [];
        break;
      case "SET_PROBLEMS":
        next.problems = action.problems || [];
        break;
      case "APPEND_OUTPUT_LOG":
        next.outputLog = (next.outputLog + action.text).slice(-20000);
        break;
      case "APPEND_BUILD_LOG":
        next.buildLog = (next.buildLog + action.text).slice(-20000);
        break;
      case "CLEAR_OUTPUT_LOG":
        next.outputLog = "";
        break;
      case "CLEAR_BUILD_LOG":
        next.buildLog = "";
        break;
      case "SET_TERMINALS":
        next.terminals = action.terminals || [];
        if (action.activeTerminalId !== undefined) next.activeTerminalId = action.activeTerminalId;
        break;
      case "SELECT_TERMINAL":
        next.activeTerminalId = action.terminalId;
        break;
      case "RESET_JOB":
        next.currentJobId = null;
        next.jobStatus = null;
        next.jobEvents = [];
        next.lastEventId = 0;
        next.plan = [];
        next.approvals = [];
        next.toolCalls = [];
        next.toolResults = [];
        next.running = false;
        next.awaitingApproval = false;
        next.tokenEstimate = null;
        next.timingMs = null;
        break;
      case "PATCH":
        Object.assign(next, action.patch);
        break;
      default:
        break;
    }
    return next;
  }

  function mergeEvents(existing, incoming) {
    const seen = new Set(existing.map((e) => e.id || `${e.type}:${e.created_at || e.seq}`));
    const merged = [...existing];
    for (const e of incoming) {
      const key = e.id || `${e.type}:${e.created_at || e.seq}`;
      if (!seen.has(key)) {
        seen.add(key);
        merged.push(e);
      }
    }
    merged.sort((a, b) => (a.id || 0) - (b.id || 0));
    return merged;
  }

  window.DesktopState = {
    getState,
    dispatch,
    subscribe,
    reducer,
    mergeEvents,
  };
})();
