/*
 * theokit.test.js — unit tests for the TheoKit vanilla JS component library
 * (src/theokit/*). Covers: library surface (108 components), render matrix
 * with per-component fixtures, interactive behaviours, the showcase gallery,
 * and CSS class coverage against components.css + theokit-extra.css.
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

/* ── Minimal DOM stub ──────────────────────────────────────────────── */

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = String(text);
    this.data = this.textContent;
    this.parentNode = null;
  }
}

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag);
    this.attributes = {};
    this.style = {};
    this.dataset = {};
    this.childNodes = [];
    this.parentNode = null;
    this._listeners = {};
    this._innerHTML = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    const self = this;
    this.classList = {
      _set: new Set(),
      add(...cs) { cs.forEach((c) => self.classList._set.add(c)); syncClassAttr(self); },
      remove(...cs) { cs.forEach((c) => self.classList._set.delete(c)); syncClassAttr(self); },
      toggle(c, force) {
        const on = force === undefined ? !self.classList._set.has(c) : !!force;
        if (on) self.classList._set.add(c); else self.classList._set.delete(c);
        syncClassAttr(self);
        return on;
      },
      contains(c) { return self.classList._set.has(c); },
    };
    syncClassAttr(this);
  }
  get className() { return this.attributes.class || ""; }
  set className(v) {
    this.attributes.class = String(v);
    this.classList._set = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const i = this.parentNode.childNodes.indexOf(this);
    return this.parentNode.childNodes[i + 1] || null;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) {
    this._innerHTML = String(v);
    this.childNodes = [];
  }
  get textContent() {
    return this.childNodes.map((n) => (n.nodeType === 3 ? n.textContent : n.textContent)).join("");
  }
  set textContent(v) {
    this.childNodes = [];
    if (v !== "" && v != null) this.childNodes.push(new TextNode(v));
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k === "class") this.className = String(v);
  }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  hasAttribute(k) { return k in this.attributes; }
  addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); }
  removeEventListener(ev, fn) {
    const arr = this._listeners[ev] || [];
    const i = arr.indexOf(fn);
    if (i >= 0) arr.splice(i, 1);
  }
  dispatchEvent(ev) {
    (this._listeners[ev.type] || []).slice().forEach((fn) => fn(ev));
    return true;
  }
  click() {
    return this.dispatchEvent({ type: "click", target: this, currentTarget: this, preventDefault() {}, stopPropagation() {} });
  }
  focus() {}
  blur() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { width: 100, height: 20, top: 0, left: 0 }; }
  appendChild(child) {
    if (!child) return child;
    if (child._isFragment) {
      child.childNodes.slice().forEach((c) => this.appendChild(c));
      return child;
    }
    if (child.parentNode) child.parentNode.removeChild(child);
    this.childNodes.push(child);
    child.parentNode = this;
    return child;
  }
  insertBefore(child, ref) {
    if (!child) return child;
    if (!ref) return this.appendChild(child);
    if (child._isFragment) {
      child.childNodes.slice().forEach((c) => this.insertBefore(c, ref));
      return child;
    }
    if (child.parentNode) child.parentNode.removeChild(child);
    const i = this.childNodes.indexOf(ref);
    if (i < 0) this.childNodes.push(child);
    else this.childNodes.splice(i, 0, child);
    child.parentNode = this;
    return child;
  }
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i >= 0) this.childNodes.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) {
    return this.childNodes.some((c) => c === n || (c.contains && c.contains(n)));
  }
  /* Mini selector engine: tag, .class, [attr], [attr="v"], combos. */
  matches(sel) { return matchesSel(this, sel); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    walk(this, (n) => { if (n !== this && matchesSel(n, sel)) out.push(n); });
    return out;
  }
  closest(sel) {
    let n = this;
    while (n) {
      if (n.nodeType === 1 && matchesSel(n, sel)) return n;
      n = n.parentNode;
    }
    return null;
  }
}

class Fragment extends El {
  constructor() {
    super("#fragment");
    this._isFragment = true;
  }
}

function syncClassAttr(el) {
  el.attributes.class = [...el.classList._set].join(" ");
}
function walk(node, fn) {
  (node.childNodes || []).forEach((c) => {
    fn(c);
    if (c.nodeType === 1) walk(c, fn);
  });
}
function matchesSel(el, sel) {
  if (!el || el.nodeType !== 1) return false;
  return String(sel)
    .trim()
    .split(/\s*,\s*/)
    .some((one) => {
      // single compound: tag.class[attr=...][...]
      const parts = [];
      const re = /([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)|(\[[^\]]+\])/g;
      let m;
      let tag = null;
      const classes = [];
      const attrs = [];
      const s = one;
      let consumed = "";
      const tagClassRe = /^([a-zA-Z][\w-]*)((?:\.[\w-]+)*)/;
      const tm = s.match(tagClassRe);
      if (tm) { tag = tm[1] || null; (tm[2] || "").split(".").filter(Boolean).forEach((c) => classes.push(c)); consumed = tm[0]; }
      let rest = s.slice(consumed.length);
      const attrRe = /\[([^\]=~^$*]+)(?:([~^$*]?=)("([^\"]*)"|'([^']*)'|([^\]]*)))?\]/g;
      let guard = 0;
      while ((m = attrRe.exec(rest)) !== null && guard++ < 20) {
        attrs.push({ name: m[1], op: m[2] || (m[3] ? "=" : null), value: m[4] ?? m[5] ?? m[6] });
      }
      if (rest.replace(attrRe, "").trim()) return false; // unsupported selector
      if (tag && el.tagName.toUpperCase() !== tag.toUpperCase()) return false;
      if (classes.some((c) => !el.classList.contains(c))) return false;
      return attrs.every((a) => {
        if (a.op === null) return el.hasAttribute(a.name);
        const v = el.getAttribute(a.name);
        if (v === null) return false;
        if (a.op === "=") return v === a.value;
        if (a.op === "~=") return v.split(/\s+/).includes(a.value);
        if (a.op === "^=") return v.startsWith(a.value);
        if (a.op === "$=") return v.endsWith(a.value);
        if (a.op === "*=") return v.includes(a.value);
        return false;
      });
    });
}

const docListeners = {};
const document = {
  createElement: (t) => new El(t),
  createElementNS: (_ns, t) => new El(t),
  createDocumentFragment: () => new Fragment(),
  createTextNode: (t) => new TextNode(t),
  addEventListener(ev, fn) { (docListeners[ev] = docListeners[ev] || []).push(fn); },
  removeEventListener(ev, fn) {
    const arr = docListeners[ev] || [];
    const i = arr.indexOf(fn);
    if (i >= 0) arr.splice(i, 1);
  },
  body: new El("body"),
  documentElement: new El("html"),
};

global.window = {
  document,
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  localStorage: { getItem: () => null, setItem() {} },
  addEventListener() {},
  removeEventListener() {},
  getSelection: () => null,
  requestAnimationFrame: (fn) => fn(),
};
global.document = document;
global.localStorage = global.window.localStorage;

/* ── Load the library ──────────────────────────────────────────────── */

const SRC = path.join(__dirname, "..", "src", "theokit");
["theokit-icons.js", "theokit-core.js", "theokit-primitives.js", "theokit-primitives-b.js", "theokit-composites.js"].forEach(
  (f) => require(path.join(SRC, f))
);
require(path.join(SRC, "theokit-showcase.js"));

const Tk = global.window.Theokit;
assert.ok(Tk, "Theokit namespace must load");

/* ── 1. Library surface ────────────────────────────────────────────── */

const EXPECTED_COMPONENTS = [
  "AgentComposer", "AgentEditor", "AgentErrorCard", "AgentEvent", "AgentHandoff", "AgentProfile",
  "AgentStartingState", "AgentStream", "AgentStreaming", "AgentTimeline", "AgentToolRenderer",
  "ApprovalCard", "ApprovalModeSelector", "ArtifactPreview", "AttachmentChip", "AuditLogEntry",
  "AutoCompactNotice", "BranchIndicator", "BrowserControls", "BuildLogStream", "Button",
  "CapabilityIndicator", "ChannelCard", "ChatComposer", "ChatMessage", "ChatMessageAction",
  "ChatMessageActions", "ChatMessageBranch", "ChatMessageBranchContent", "ChatMessageBranchNext",
  "ChatMessageBranchPage", "ChatMessageBranchPrevious", "ChatMessageBranchSelector",
  "ChatMessageContent", "ChatMessageResponse", "ChatMessageRoot", "ChatMessageToolbar",
  "ChatThread", "ChoicePrompt", "CodeBlock", "CodeReviewPanel", "ConfirmPrompt", "ContextCard",
  "ContextWindowBar", "CostMeter", "CreatedFilesCard", "CronJobCard", "CronJobsList", "DataPart",
  "DiffViewer", "ExportChatDialog", "FilePart", "FolderContextCard", "FolderSelector",
  "GatewayStatusIndicator", "HookConfig", "HookEventLog", "IntentSelector", "LaneBoard",
  "McpServerCard", "McpServerList", "MemoryEditor", "MentionMenu", "ModelCard",
  "ModelEffortPicker", "ModelSelector", "MultiSelectPrompt", "PermissionMatrix", "PermissionModal",
  "PreviewPanel", "ProgressChecklist", "ProjectSwitcher", "QuickActionChips", "ReasoningPart",
  "RecentFoldersList", "RuleCard", "RuleEditor", "RunStats", "RunStatusPill", "RunningTasksPanel",
  "SessionListItem", "SessionTimeline", "SkillCard", "SkillEditor", "SkillsList", "Slide",
  "SlideDeck", "SourceDocumentPart", "SourceUrlPart", "StabilityBundleViewer", "StepsRail",
  "SubAgentDispatch", "SystemPromptEditor", "TaskNode", "TaskPlan", "TerminalPanel", "TextPart",
  "TextPrompt", "ThinkingLevelSelector", "TokenUsageChart", "ToolCall", "ToolCallCard",
  "ToolCallPart", "ToolResult", "ToolsList", "UsageMeter", "Whiteboard", "WorkLog",
];
for (const name of EXPECTED_COMPONENTS) {
  assert.strictEqual(typeof Tk[name], "function", `component ${name} must be exported as a function`);
}
assert.strictEqual(EXPECTED_COMPONENTS.length, 108, "108 migrated components");
assert.ok(Tk.RUN_STATUS_META && typeof Tk.RUN_STATUS_META === "object", "RUN_STATUS_META constant exported");
for (const util of ["h", "cn", "toNode", "append", "renderIcon", "text", "kindFromEnvelopeCode",
  "parseUnifiedDiffToHunks", "renderPart", "defaultClassifyTool", "defaultToolRegistry", "detectTrigger"]) {
  assert.strictEqual(typeof Tk[util], "function", `util ${util} exported`);
}
console.log(`ok surface: ${EXPECTED_COMPONENTS.length} components + RUN_STATUS_META + 7 utils`);

/* ── 2. Core utils ─────────────────────────────────────────────────── */

assert.strictEqual(Tk.cn("a", false && "b", null, ["c", { d: true, e: false }]), "a c d");
assert.strictEqual(Tk.text(undefined), "");
assert.strictEqual(Tk.text(42), "42");

{
  const el = Tk.h("div", { class: ["x", { y: true }], "data-k": "v" }, "hello", null, [" ", "world"]);
  assert.strictEqual(el.tagName.toUpperCase(), "DIV");
  assert.strictEqual(el.getAttribute("data-k"), "v");
  assert.strictEqual(el.textContent, "hello world");
}
{
  let clicked = 0;
  const btn = Tk.h("button", { onClick: () => clicked++ }, "go");
  btn.click();
  assert.strictEqual(clicked, 1, "onClick handler binds via addEventListener");
}
{
  const svg = Tk.h("svg", { viewBox: "0 0 24 24" }, Tk.h("path", { d: "M1 1" }));
  assert.strictEqual(svg.tagName, "svg", "svg tag preserved (not uppercased by stub mismatch)");
  assert.strictEqual(svg.children.length, 1);
}
console.log("ok core utils: cn/h/text/event binding");

/* ── 3. Icons ──────────────────────────────────────────────────────── */

{
  const ic = Tk.icons.icon("terminal", "size-3.5");
  assert.strictEqual(ic.tagName, "svg", "icon() returns an svg element");
  assert.ok(ic.getAttribute("class").includes("size-3.5"), "icon size class applied");
  const camel = Tk.icons.icon("alertTriangle");
  assert.strictEqual(camel.tagName, "svg", "camelCase icon name resolves");
  const unknown = Tk.icons.icon("does-not-exist");
  assert.strictEqual(unknown.tagName, "svg", "unknown icon degrades to empty svg");
  assert.strictEqual(unknown.childNodes.length, 0, "unknown icon has no path children");
}
console.log("ok icons: factory + kebab/camel resolution + unknown fallback");

/* ── 4. Render matrix: every component renders with fixture ────────── */

const FIXTURES = {
  AgentComposer: { intents: [{ id: "code", label: "Code" }], intent: "code", models: [{ id: "gpt-5", name: "GPT-5" }], model: "gpt-5", onSubmit() {} },
  AgentEditor: { file: { path: "src/app.js", content: "console.log(1)" }, onSave() {} },
  AgentErrorCard: { code: "E_TOOL_TIMEOUT", title: "Tool timed out", detail: "grep exceeded 30s" },
  AgentEvent: { event: { icon: "check", title: "npm test", detail: "152 passed", status: "success", meta: "9.4s" }, collapsible: true },
  AgentHandoff: { from: "planner", to: "coder" },
  AgentProfile: { profile: { name: "Atlas", model: "gpt-5", status: "running", tools: 4 } },
  AgentStartingState: { label: "Booting agent runtime" },
  AgentStream: { items: [
    { kind: "message", message: { role: "user", parts: [{ type: "text", text: "Run tests" }] } },
    { kind: "tool-call", tool: "bash", status: "done", output: "PASS" },
  ] },
  AgentStreaming: { model: "gpt-5", partial: "Reading project layout…", tokens: 1420 },
  AgentTimeline: { events: [
    { icon: "terminal", title: "npm test", detail: "152 passed", status: "success" },
    { icon: "wrench", title: "edit agent/loop.py", status: "running" },
  ] },
  AgentToolRenderer: { part: { type: "tool", toolName: "bash", state: "output-available", input: "node smoke.js", output: "OK" } },
  ApprovalCard: { title: "Read workspace file", request: "Read(src/theme.js)", severity: "info" },
  ApprovalModeSelector: { modes: [{ id: "ask", label: "Ask" }], value: "ask" },
  ArtifactPreview: { name: "app-debug.apk", size: "8.0 MB", kind: "apk" },
  AttachmentChip: { name: "audit.pdf" },
  AuditLogEntry: { entry: { at: "2026-08-23 10:05", actor: "t09user", action: "account.create" } },
  AutoCompactNotice: { kept: 12, dropped: 30 },
  BranchIndicator: { current: "feat/x", ahead: 5, behind: 1 },
  BrowserControls: { url: "http://localhost:3000" },
  BuildLogStream: { lines: ["> Task :app:assembleDebug", "BUILD SUCCESSFUL"] },
  Button: { children: "Click" },
  CapabilityIndicator: { capabilities: [{ name: "network", ok: true }, { name: "shell", ok: false }] },
  ChannelCard: { channel: { name: "release", kind: "email", enabled: true } },
  ChatComposer: { placeholder: "Ask…" },
  ChatMessage: { message: { role: "assistant", parts: [{ type: "text", text: "Done." }] } },
  ChatMessageAction: { tooltip: "Copy", label: "Copy", children: "⧉" },
  ChatMessageActions: { children: Tk.h("span", null, "acts") },
  ChatMessageBranch: { children: [
    Tk.ChatMessageBranchContent({ children: [Tk.h("p", null, "v1"), Tk.h("p", null, "v2")] }),
    Tk.ChatMessageBranchSelector({ children: [Tk.ChatMessageBranchPrevious({}), Tk.ChatMessageBranchPage({}), Tk.ChatMessageBranchNext({})] }),
  ] },
  ChatMessageBranchContent: { children: Tk.h("p", null, "v1") },
  ChatMessageBranchNext: {},
  ChatMessageBranchPage: {},
  ChatMessageBranchPrevious: {},
  ChatMessageBranchSelector: { children: Tk.h("span", null, "sel") },
  ChatMessageContent: { variant: "contained", children: Tk.h("p", null, "body") },
  ChatMessageResponse: { text: "# Title\n\n- one\n- two\n\n```js\nconst a = 1;\n```" },
  ChatMessageRoot: { from: "assistant", children: Tk.h("p", null, "msg") },
  ChatMessageToolbar: { children: Tk.h("span", null, "toolbar") },
  ChatThread: { children: [
    Tk.ChatMessage({ message: { role: "user", parts: [{ type: "text", text: "Hi" }] } }),
    Tk.ChatMessage({ message: { role: "assistant", parts: [{ type: "text", text: "Done." }] } }),
  ] },
  ChoicePrompt: { question: "Which database?", options: [{ value: "sqlite", label: "SQLite" }, { value: "pg", label: "Postgres" }], defaultValue: "sqlite" },
  CodeBlock: { code: "const a = 1;", language: "js" },
  CodeReviewPanel: { files: [{ path: "agent/loop.py", added: 9, removed: 2 }], comments: [{ file: "agent/loop.py", line: 42, text: "rethrow before generic handler", severity: "warning" }] },
  ConfirmPrompt: { question: "Delete 75 test workspace directories?", variant: "destructive" },
  ContextCard: { title: "Context", items: ["a.md", "b.md"] },
  ContextWindowBar: { used: 118, total: 200 },
  CostMeter: { spent: 1.24, budget: 10 },
  CreatedFilesCard: { files: ["a.js", "b.js"] },
  CronJobCard: { job: { name: "daily", cron: "0 9 * * 1-5", enabled: true } },
  CronJobsList: { jobs: [{ id: "j", name: "daily", cron: "0 9 * * 1-5", enabled: true }] },
  DataPart: { part: { type: "data-weather", data: { city: "Shanghai" } } },
  DiffViewer: { path: "src/loop.py", stats: { added: 6, removed: 2 }, diff: "@@ -1,3 +1,4 @@\n a\n-b\n+c\n+d" },
  ExportChatDialog: { open: true, onExport() {} },
  FilePart: { url: "shot.png", mediaType: "image/png", filename: "shot.png" },
  FolderContextCard: { folder: { path: "src/theokit", files: 5 } },
  FolderSelector: { path: "/tmp/x" },
  GatewayStatusIndicator: { status: "healthy" },
  HookConfig: { hook: { name: "pre-commit", command: "npm test", enabled: true } },
  HookEventLog: { events: [{ at: "10:02", name: "pre-commit", ok: true }] },
  IntentSelector: { intents: [{ id: "code", label: "Code" }], value: "code" },
  LaneBoard: { lanes: [{ id: "l1", title: "Todo", cards: [{ id: "c1", title: "Fix pause" }] }] },
  McpServerCard: { server: { name: "filesystem", status: "connected", tools: ["read"] } },
  McpServerList: { servers: [{ id: "m", name: "filesystem", status: "connected", tools: [] }] },
  MemoryEditor: { memories: [{ id: "m1", text: "Use temp dirs for tests" }] },
  MentionMenu: { items: [{ label: "@file a.md", kind: "file" }], query: "@" },
  ModelCard: { model: { id: "gpt-5", name: "GPT-5", context: 200, strengths: ["code"] }, selected: true },
  ModelEffortPicker: { efforts: [{ id: "low", label: "Low" }], value: "low" },
  ModelSelector: { models: [{ id: "gpt-5", name: "GPT-5" }], value: "gpt-5" },
  MultiSelectPrompt: { question: "Select test suites to run", options: [{ value: "unit", label: "Unit" }, { value: "e2e", label: "E2E" }], defaultValue: ["unit"] },
  PermissionMatrix: { permissions: [{ tool: "bash", modes: ["ask"] }] },
  PermissionModal: { permission: { tool: "bash", target: "git push origin main", reason: "Publishes commits to a shared remote" }, onAllow() {}, onDeny() {} },
  PreviewPanel: { header: Tk.h("span", null, "preview"), children: Tk.h("div", null, "content") },
  ProgressChecklist: { steps: [{ label: "stub model", status: "done" }, { label: "verify", status: "running" }] },
  ProjectSwitcher: { projects: [{ id: "p1", name: "Android Agent" }], value: "p1" },
  QuickActionChips: { actions: [{ id: "run", label: "Run tests" }] },
  ReasoningPart: { text: "Considering options…" },
  RecentFoldersList: { folders: [{ path: "/tmp/x", at: "today" }] },
  RuleCard: { rule: { name: "no-force-push", enabled: true } },
  RuleEditor: { rules: [{ id: "r1", pattern: "*.lock", action: "deny" }] },
  RunStats: { duration: "2m 41s", tokens: "35.7k", filesChanged: 9 },
  RunStatusPill: { status: "running" },
  RunningTasksPanel: { tasks: [{ id: "t1", name: "build apk", status: "running", progress: 60 }] },
  SessionListItem: { session: { id: "s1", title: "smoke", at: "today", turns: 3 }, active: true },
  SessionTimeline: { sessions: [{ id: "s1", title: "smoke" }] },
  SkillCard: { skill: { name: "pdf", description: "PDF toolkit", enabled: true } },
  SkillEditor: { skills: [{ id: "sk1", name: "pdf", body: "# pdf" }] },
  SkillsList: { skills: [{ id: "sk1", name: "pdf", description: "toolkit", enabled: true }] },
  Slide: { title: "Title", subtitle: "Sub", bullets: ["one", "two"] },
  SlideDeck: { slides: ["# A\n- one\n- two", "# B"] },
  SourceDocumentPart: { title: "spec.md", mediaType: "text/markdown" },
  SourceUrlPart: { url: "https://example.com", title: "example.com" },
  StabilityBundleViewer: { files: [{ name: "screenshot.png", kind: "png" }] },
  StepsRail: { steps: [{ label: "connect", status: "done" }, { label: "chat", status: "running" }] },
  SubAgentDispatch: { agents: [{ name: "planner", status: "done" }, { name: "coder", status: "running" }] },
  SystemPromptEditor: { value: "You are a helpful agent." },
  TaskNode: { node: { id: "1", label: "Fix bug", status: "done" } },
  TaskPlan: { nodes: [{ id: "1", label: "Root", status: "done", children: [{ id: "1.1", label: "Child", status: "running" }] }] },
  TerminalPanel: { lines: ["$ npm test", "PASS"] },
  TextPart: { text: "Plain text answer." },
  TextPrompt: { question: "What should the new module be called?", placeholder: "module name…" },
  ThinkingLevelSelector: { levels: [{ id: "on", label: "On" }], value: "on" },
  TokenUsageChart: { points: [{ input: 8, output: 12 }, { input: 14, output: 22 }] },
  ToolCall: { tool: "bash", target: "ls -la", status: "done" },
  ToolCallCard: { tool: "bash", target: "npm test", status: "done", output: "PASS", timestamp: "12.8s" },
  ToolCallPart: { part: { type: "tool", toolName: "bash", state: "output-available", input: "ls", output: "ok" } },
  ToolResult: { result: { ok: true, output: "exit 0" } },
  ToolsList: { tools: [{ name: "bash", risk: "high" }, { name: "read", risk: "low" }] },
  UsageMeter: { metrics: [{ label: "tokens", value: 40 }, { label: "quota", value: 95, tone: "danger" }] },
  Whiteboard: { strokes: [] },
  WorkLog: { entries: [{ at: "10:02", text: "started" }] },
};

const renderClasses = new Set();
function collectClasses(node) {
  walk(node, (n) => {
    if (n.nodeType === 1 && n.getAttribute) {
      const cls = n.getAttribute("class");
      if (cls) cls.split(/\s+/).filter(Boolean).forEach((c) => renderClasses.add(c));
    }
  });
}

let rendered = 0;
for (const name of EXPECTED_COMPONENTS) {
  const fixture = FIXTURES[name];
  assert.ok(fixture !== undefined, `fixture defined for ${name}`);
  let node;
  try {
    node = Tk[name](fixture);
  } catch (e) {
    e.message = `${name} render threw: ${e.message}`;
    throw e;
  }
  assert.ok(node && node.nodeType === 1, `${name} returns an element`);
  assert.ok(node.childNodes.length > 0, `${name} renders non-empty content`);
  let prev = null;
  if (process.env.THEOKIT_TRACE_NEW) prev = new Set(renderClasses);
  collectClasses(node);
  if (prev) {
    const added = [...renderClasses].filter((c) => !prev.has(c));
    if (added.length) console.log(`  [${name}] + ${added.join(", ")}`);
  }
  rendered++;
}
assert.strictEqual(rendered, 108, "all 108 components render");
console.log(`ok render matrix: ${rendered}/108 components render with fixtures`);

/* state variants that must not crash */
Tk.AgentErrorCard({}); // no props at all
Tk.RunStatusPill({ status: "failed" });
Tk.Button({ variant: "destructive", size: "icon", children: "!" });
Tk.ChatMessage({ message: { role: "user", parts: [{ type: "text", text: "hi" }] }, variant: "flat" });
console.log("ok render matrix: empty-prop / variant edge cases");

/* ── 5. Behaviour: AgentErrorCard mapping ──────────────────────────── */

assert.strictEqual(Tk.kindFromEnvelopeCode("E_TOOL_TIMEOUT"), "timeout");
assert.strictEqual(Tk.kindFromEnvelopeCode("E_MODEL_RATE_LIMIT"), "rate-limit");
assert.strictEqual(Tk.kindFromEnvelopeCode("E_NOPE"), "unknown");
{
  const card = Tk.AgentErrorCard({ code: "E_TOOL_DENIED" });
  const text = card.textContent;
  assert.ok(text.includes("Denied"), `denied label rendered (got: ${text.slice(0, 60)})`);
}
console.log("ok AgentErrorCard: envelope code → kind → label");

/* ── 6. Behaviour: parseUnifiedDiffToHunks ─────────────────────────── */

{
  const hunks = Tk.parseUnifiedDiffToHunks(
    "@@ -10,7 +10,11 @@ def run_task():\n     ctx = acquire()\n-    result = loop(ctx)\n-    return result\n+    try:\n+        result = loop(ctx)\n+    except PauseRequested:\n+        return paused(ctx)"
  );
  assert.strictEqual(hunks.length, 1, "one hunk parsed");
  assert.ok(hunks[0].header.startsWith("@@"), "hunk keeps its header line");
  const removed = hunks[0].lines.filter((l) => l.kind === "removed");
  const added = hunks[0].lines.filter((l) => l.kind === "added");
  const unchanged = hunks[0].lines.filter((l) => l.kind === "unchanged");
  assert.strictEqual(removed.length, 2, "2 removed lines");
  assert.strictEqual(added.length, 4, "4 added lines");
  assert.strictEqual(unchanged.length, 1, "1 unchanged context line");
  assert.strictEqual(removed[0].content, "    result = loop(ctx)", "removed line content stripped of marker");
}
console.log("ok parseUnifiedDiffToHunks: hunk header + line classification");

/* ── 7. Behaviour: renderPart dispatch ─────────────────────────────── */

{
  const text = Tk.renderPart({ type: "text", text: "answer" });
  assert.ok(text, "text part renders");
  const reasoning = Tk.renderPart({ type: "reasoning", text: "hmm" });
  assert.ok(reasoning, "reasoning part renders");
  const tool = Tk.renderPart({ type: "tool", toolName: "bash", state: "output-available", input: "ls", output: "ok" });
  assert.ok(tool, "tool part renders");
  const file = Tk.renderPart({ type: "file", url: "a.png", mediaType: "image/png", filename: "a.png" });
  assert.ok(file, "file part renders");
  assert.ok(Tk.renderPart({ type: "unknown-kind" }) !== undefined, "unknown part type does not throw");
}
console.log("ok renderPart: text/reasoning/tool/file/unknown dispatch");

/* ── 8. Behaviour: ChatMessageBranch navigation ────────────────────── */

{
  const branch = Tk.ChatMessageBranch({
    children: [
      Tk.ChatMessageBranchContent({ children: [Tk.h("p", null, "v1"), Tk.h("p", null, "v2")] }),
      Tk.ChatMessageBranchSelector({
        children: [Tk.ChatMessageBranchPrevious({}), Tk.ChatMessageBranchPage({}), Tk.ChatMessageBranchNext({})],
      }),
    ],
  });
  const page = branch.querySelector('[data-slot="chat-message-branch-page"]');
  assert.ok(page, "branch page element exists");
  assert.strictEqual(page.textContent, "1 of 2", "initial page label");

  const next = branch.querySelector('[data-slot="chat-message-branch-next"]');
  const prev = branch.querySelector('[data-slot="chat-message-branch-previous"]');
  assert.ok(next && prev, "prev/next buttons exist");

  next.click();
  assert.strictEqual(page.textContent, "2 of 2", "next advances to branch 2");
  next.click();
  assert.strictEqual(page.textContent, "1 of 2", "next wraps around to branch 1");
  prev.click();
  assert.strictEqual(page.textContent, "2 of 2", "prev wraps around to branch 2");

  let changed = -1;
  const branch2 = Tk.ChatMessageBranch({
    onBranchChange: (i) => { changed = i; },
    children: [
      Tk.ChatMessageBranchContent({ children: [Tk.h("p", null, "a"), Tk.h("p", null, "b")] }),
      Tk.ChatMessageBranchSelector({ children: [Tk.ChatMessageBranchNext({})] }),
    ],
  });
  branch2.querySelector('[data-slot="chat-message-branch-next"]').click();
  assert.strictEqual(changed, 1, "onBranchChange fired with new index");
}
console.log("ok ChatMessageBranch: page label, wrap-around nav, onBranchChange");

/* ── 9. Behaviour: ChoicePrompt / MultiSelectPrompt interaction ────── */

{
  let picked = null;
  const prompt = Tk.ChoicePrompt({
    question: "Which DB?",
    options: [{ value: "sqlite", label: "SQLite" }, { value: "pg", label: "Postgres" }],
    onValueChange: (v) => { picked = v; },
  });
  const options = prompt.querySelectorAll('[role="radio"]');
  assert.strictEqual(options.length, 2, "choice options render as radios");
  options[1].click();
  assert.strictEqual(picked, "pg", "onValueChange fired with option value");
}
{
  const multi = Tk.MultiSelectPrompt({
    question: "Pick suites",
    options: [{ value: "unit", label: "Unit" }, { value: "e2e", label: "E2E" }, { value: "shot", label: "Screenshot" }],
    defaultValue: ["unit"],
  });
  const boxes = multi.querySelectorAll('[role="checkbox"], label[data-value], button');
  assert.ok(boxes.length >= 3, `multi options rendered (${boxes.length})`);
  assert.ok(multi.textContent.includes("Unit"), "option label rendered");
}
{
  let confirmed = null;
  const text = Tk.TextPrompt({ question: "Module name?", onConfirm: (v) => { confirmed = v; } });
  const input = text.querySelector("input, textarea");
  assert.ok(input, "TextPrompt renders an input");
  input.value = "theokit";
  input.dispatchEvent({ type: "input", target: input, currentTarget: input });
  const btn = text.querySelectorAll("button");
  const submit = btn[btn.length - 1];
  submit.click();
  assert.deepStrictEqual(confirmed, { value: "theokit" }, "onConfirm receives input value");
}
console.log("ok prompts: choice/multi-select option rendering + pick callback");

/* ── 10. Behaviour: ApprovalCard risk classes ──────────────────────── */

{
  const destructive = Tk.ApprovalCard({ title: "rm -rf", request: "rm -rf builds/usr_legacy", severity: "destructive" });
  const cls = destructive.getAttribute("class") || "";
  assert.ok(/border-destructive\/40/.test(cls), `destructive severity styles card root (got: ${cls})`);
  const info = Tk.ApprovalCard({ title: "Read", request: "Read(x)", severity: "info" });
  assert.ok(/border-info\/40/.test(info.getAttribute("class") || ""), "info severity styles card root");
  const warn = Tk.ApprovalCard({ title: "Install", request: "npm i left-pad", severity: "warning" });
  assert.ok(/border-warning\/40/.test(warn.getAttribute("class") || ""), "warning severity styles card root");
}
console.log("ok ApprovalCard: risk-level class on card root");

/* ── 11. Behaviour: TaskPlan hierarchy ─────────────────────────────── */

{
  const plan = Tk.TaskPlan({
    nodes: [
      { id: "1", label: "Root", status: "done", children: [
        { id: "1.1", label: "Child A", status: "running" },
        { id: "1.2", label: "Child B", status: "pending" },
      ] },
    ],
  });
  const nodes = plan.querySelectorAll('[data-slot="task-node"]');
  assert.strictEqual(nodes.length, 3, "root + 2 children render as task nodes");
  const text = plan.textContent;
  assert.ok(text.includes("Root") && text.includes("Child A") && text.includes("Child B"), "labels rendered");
}
console.log("ok TaskPlan: hierarchical node rendering");

/* ── 12. Behaviour: SlideDeck markdown parsing ─────────────────────── */

{
  let idx = -1;
  const deck = Tk.SlideDeck({ slides: ["# Title One\n- a\n- b", "## Title Two\nplain"], onIndexChange: (i) => { idx = i; } });
  const stage = deck.querySelectorAll('[data-slot="slide"]');
  assert.strictEqual(stage.length, 1, "one slide visible at a time");
  assert.ok(deck.textContent.includes("1 / 2"), "counter shows 1 / 2");
  assert.ok(deck.textContent.includes("Title One"), "first slide markdown rendered");
  const buttons = deck.querySelectorAll("button");
  const next = buttons[buttons.length - 1];
  next.click();
  assert.strictEqual(idx, 1, "onIndexChange fired after next");
  assert.ok(deck.textContent.includes("2 / 2"), "counter advances to 2 / 2");
  assert.ok(deck.textContent.includes("Title Two"), "second slide markdown rendered");
}
{
  const deck = Tk.SlideDeck({ slides: "# Solo\n---\n## Second" });
  assert.ok(deck.textContent.includes("2"), "string input split on --- into 2 slides");
}
console.log("ok SlideDeck: markdown → slides");

/* ── 13. Behaviour: detectTrigger ──────────────────────────────────── */

{
  const t = Tk.detectTrigger("hey @sak");
  assert.deepStrictEqual(t, { trigger: "@", query: "sak", start: 4 }, "mention trigger parsed with start offset");
  assert.deepStrictEqual(Tk.detectTrigger("run /build"), { trigger: "/", query: "build", start: 4 }, "slash trigger parsed");
  assert.strictEqual(Tk.detectTrigger("email me at a@b.com x"), null, "mid-word @ is not a trigger");
  assert.strictEqual(Tk.detectTrigger("hello world"), null, "no trigger on plain text");
}
console.log("ok detectTrigger: trigger words + negative case");

/* ── 14. RUN_STATUS_META completeness ──────────────────────────────── */

{
  const statuses = ["queued", "running", "paused", "completed", "failed", "canceled"];
  for (const s of statuses) {
    assert.ok(Tk.RUN_STATUS_META[s], `RUN_STATUS_META has entry for "${s}"`);
    assert.strictEqual(typeof Tk.RUN_STATUS_META[s].label, "string", `label for "${s}"`);
  }
  assert.strictEqual(Tk.RunStatusPill({ status: "failed" }).textContent, "Failed", "RunStatusPill renders label");
  assert.strictEqual(Tk.RunStatusPill({ status: "nonsense-status" }).textContent, "Queued", "unknown status falls back to queued");
}
console.log("ok RUN_STATUS_META: all run statuses mapped");

/* ── 15. Showcase builds clean ─────────────────────────────────────── */

{
  const node = global.window.TheokitShowcase.build();
  assert.ok(node && node.nodeType === 1, "showcase root element");
  const errors = [];
  walk(node, (n) => {
    if (n.nodeType === 3 && String(n.textContent).startsWith("render error:")) errors.push(String(n.textContent));
  });
  assert.deepStrictEqual(errors, [], `showcase must render with zero errors: ${errors.join("; ")}`);
  let count = 0;
  walk(node, () => count++);
  assert.ok(count > 1500, `showcase tree is substantial (${count} nodes)`);
  console.log(`ok showcase: builds clean, ${count} nodes, 0 render errors`);
}

/* ── 16. CSS coverage: rendered classes exist in shipped CSS ───────── */

{
  const css =
    fs.readFileSync(path.join(__dirname, "..", "node_modules", "@theokit", "ui", "dist", "components.css"), "utf8") +
    fs.readFileSync(path.join(SRC, "theokit-extra.css"), "utf8");
  const cssHasClass = (cls) => {
    // Tailwind emits escaped selectors: .border-primary\/60, .max-w-\[95\%\]
    const selector = "." + cls.replace(/[/[\].:%]/g, (c) => "\\" + c);
    const rx = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "([ ,{:.)]|$)");
    return rx.test(css);
  };
  // Marker/runtime classes with no stylesheet of their own
  const ignore = new Set(["block", "hidden", "group", "language-js", "language-ts", "language-py"]);
  const missing = [...renderClasses].filter((c) => !ignore.has(c) && !cssHasClass(c)).sort();
  assert.deepStrictEqual(missing, [], `all rendered classes covered by CSS (missing: ${missing.join(", ")})`);
  console.log(`ok css coverage: ${renderClasses.size} distinct classes all present in components.css/extra.css`);
}

console.log("theokit.test.js: ALL CHECKS PASSED");
