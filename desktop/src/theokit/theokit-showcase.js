/*
 * theokit-showcase.js — renders the TheoKit component gallery: every
 * migrated component with representative sample data, grouped by family.
 * Open src/theokit-showcase.html to view.
 */
(function (root) {
  "use strict";
  var Tk = root.Theokit;
  var h = Tk.h;
  var cn = Tk.cn;

  function section(id, title, subtitle) {
    var body = h("div", { id: id + "-body", class: "grid gap-5" });
    var el = h(
      "section",
      { id: id, class: "scroll-mt-20" },
      h(
        "header",
        { class: "sticky top-0 z-10 -mx-2 mb-1 border-b border-border bg-background/90 px-2 py-3 backdrop-blur" },
        h("h2", { class: "font-display text-title-md tracking-tight" }, title),
        subtitle ? h("p", { class: "text-body-sm text-muted-foreground" }, subtitle) : null
      ),
      body
    );
    el.__body = body;
    return el;
  }

  function demo(name, description, propsFactory) {
    var slot = h("div", { class: "grid gap-2" });
    var el = h(
      "article",
      { class: "rounded-xl border border-border/60 bg-card/40 p-4" },
      h(
        "header",
        { class: "mb-3 flex items-baseline justify-between gap-3" },
        h("h3", { class: "font-mono text-body-sm font-medium text-foreground" }, name),
        description
          ? h("span", { class: "text-label text-muted-foreground" }, description)
          : null
      ),
      slot
    );
    try {
      var node = propsFactory();
      if (node) slot.appendChild(node);
    } catch (e) {
      slot.appendChild(
        h(
          "p",
          { class: "font-mono text-code-sm text-destructive" },
          "render error: " + e.message
        )
      );
    }
    return el;
  }

  function build() {
    var main = h("div", { class: "grid gap-10" });

    /* ── 1. Buttons & primitives shell ─────────────────────────────── */
    var s1 = section("s-buttons", "Buttons", "Core button variants and sizes");
    s1.__body.appendChild(
      demo("Button", "variants × sizes", function () {
        return h(
          "div",
          { class: "flex flex-wrap items-center gap-3" },
          ["primary", "secondary", "ghost", "destructive", "outline"].map(function (v) {
            return Tk.Button({ variant: v, children: v });
          }),
          Tk.Button({ size: "sm", children: "sm" }),
          Tk.Button({ size: "lg", children: "lg" }),
          Tk.Button({ size: "icon", children: Tk.icons.terminal("size-4") }),
          Tk.Button({ variant: "primary", disabled: true, children: "disabled" })
        );
      })
    );
    main.appendChild(s1);

    /* ── 2. Agent status & events ──────────────────────────────────── */
    var s2 = section("s-agent", "Agent status & events", "Streaming, handoff, profile, events, errors");
    s2.__body.appendChild(
      demo("AgentStartingState", null, function () {
        return Tk.AgentStartingState({ label: "Booting agent runtime" });
      })
    );
    s2.__body.appendChild(
      demo("AgentStreaming", "live partial output", function () {
        return Tk.AgentStreaming({ model: "gpt-5", partial: "Reading project layout and inferring module boundaries…", tokens: 1420 });
      })
    );
    s2.__body.appendChild(
      demo("AgentHandoff", null, function () {
        return Tk.AgentHandoff({ from: "planner", to: "coder" });
      })
    );
    s2.__body.appendChild(
      demo("AgentProfile", null, function () {
        return Tk.AgentProfile({ profile: { name: "Atlas", model: "gpt-5", status: "running", tools: 4 }, onSelect: function () {} });
      })
    );
    s2.__body.appendChild(
      demo("AgentEvent", "collapsible with output", function () {
        return Tk.AgentEvent({ event: { icon: "check", title: "npm test", detail: "152 passed · 0 failed", status: "success", meta: "9.4s", output: "PASS tests/theme.test.js\nPASS tests/state.test.js\n…" } });
      })
    );
    s2.__body.appendChild(
      demo("AgentErrorCard", null, function () {
        return Tk.AgentErrorCard({ envelope: { code: "E_TOOL_TIMEOUT", title: "Tool timed out", detail: "grep exceeded the 30s budget in workspaces/usr_x", remediation: "Narrow the search path or raise the timeout." }, onRetry: function () {} });
      })
    );
    s2.__body.appendChild(
      demo("AutoCompactNotice", null, function () {
        return Tk.AutoCompactNotice({ kept: 12, dropped: 30 });
      })
    );
    main.appendChild(s2);

    /* ── 3. Chat & message pipeline ────────────────────────────────── */
    var s3 = section("s-chat", "Chat & message pipeline", "Thread, message shell, markdown response, parts, branches");
    s3.__body.appendChild(
      demo("ChatMessage (assistant, contained)", null, function () {
        return Tk.ChatMessage({
          message: {
            role: "assistant",
            parts: [
              { type: "reasoning", text: "The user wants a table plus a code sample." },
              { type: "text", text: "Here is the summary:" },
            ],
          },
          variant: "contained",
        });
      })
    );
    s3.__body.appendChild(
      demo("ChatMessageResponse", "markdown: headings, lists, table, code, quote", function () {
        return Tk.ChatMessageResponse({
          text:
            "# Release 1.4\n\nShips **three** fixes and a `retry` helper.\n\n" +
            "- pause no longer marks tasks failed\n- canceled tasks release quota\n- turn_canceled events written\n\n" +
            "1. first\n2. second\n\n| Check | Result |\n|---|---|\n| unit | 152 ✅ |\n| e2e | 17 ✅ |\n\n" +
            "> Verified via /tmp/t08-fix-verify/verify_fixes.py\n\n" +
            "```ts\nconst fix = { pause: \"paused\", cancel: \"canceled\" };\n```",
        });
      })
    );
    s3.__body.appendChild(
      demo("ChatThread", "user + assistant turns", function () {
        return Tk.ChatThread({
          children: [
            Tk.ChatMessage({ message: { role: "user", parts: [{ type: "text", text: "Run the smoke test." }] } }),
            Tk.ChatMessage({ message: { role: "assistant", parts: [{ type: "text", text: "Done — 15 links passed, electron-smoke: OK." }] } }),
          ],
        });
      })
    );
    s3.__body.appendChild(
      demo("ChatMessageToolbar + Actions + Branch", "copy / retry / branch navigation", function () {
        return Tk.ChatMessage({
          message: { role: "assistant", parts: [{ type: "text", text: "Answer v2 — refined after review." }] },
          variant: "flat",
          actions: Tk.ChatMessageToolbar({
            children: [
              Tk.ChatMessageActions({
                children: [
                  Tk.ChatMessageAction({ tooltip: "Copy", label: "Copy", children: Tk.icons.copy("size-3.5") }),
                  Tk.ChatMessageAction({ tooltip: "Retry", label: "Retry", children: Tk.icons.refreshCw("size-3.5") }),
                ],
              }),
              Tk.ChatMessageBranch({
                children: [
                  Tk.ChatMessageBranchContent({
                    children: [
                      Tk.h("p", { class: "text-body-sm" }, "Answer v1 — initial draft."),
                      Tk.h("p", { class: "text-body-sm" }, "Answer v2 — refined after review."),
                    ],
                  }),
                  Tk.ChatMessageBranchSelector({
                    children: [
                      Tk.ChatMessageBranchPrevious({}),
                      Tk.ChatMessageBranchPage({}),
                      Tk.ChatMessageBranchNext({}),
                    ],
                  }),
                ],
              }),
            ],
          }),
        });
      })
    );
    s3.__body.appendChild(
      demo("Message parts", "file / source-url / source-document / data", function () {
        return h(
          "div",
          { class: "grid gap-2" },
          Tk.FilePart({
            url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='180'%3E%3Crect width='320' height='180' fill='%236d5bb8' fill-opacity='0.15'/%3E%3Cpath d='M40 130 L120 60 L180 110 L230 70 L280 130' stroke='%236d5bb8' stroke-width='4' fill='none'/%3E%3C/svg%3E",
            mediaType: "image/svg+xml",
            filename: "chart-preview.svg",
          }),
          Tk.SourceUrlPart({ url: "https://example.com/docs", title: "example.com/docs" }),
          Tk.SourceDocumentPart({ title: "spec.md", mediaType: "text/markdown" }),
          Tk.DataPart({ part: { type: "data-weather", data: { city: "Shanghai", temp: 31 } } })
        );
      })
    );
    main.appendChild(s3);

    /* ── 4. Composer ───────────────────────────────────────────────── */
    var s4 = section("s-composer", "Composer", "Chat and agent composers, attachments, mentions");
    s4.__body.appendChild(
      demo("ChatComposer", null, function () {
        return Tk.ChatComposer({ placeholder: "Ask the agent…", onSubmit: function (v) { root.console.log("submit", v); } });
      })
    );
    s4.__body.appendChild(
      demo("AgentComposer", "intent + effort + model", function () {
        return Tk.AgentComposer({
          intents: [{ id: "code", label: "Code" }, { id: "review", label: "Review" }],
          intent: "code",
          models: [{ id: "gpt-5", name: "GPT-5" }],
          model: "gpt-5",
          onSubmit: function (v) { root.console.log("agent submit", v); },
        });
      })
    );
    s4.__body.appendChild(
      demo("AttachmentChip", null, function () {
        return Tk.AttachmentChip({ name: "audit-report.pdf", onRemove: function () {} });
      })
    );
    s4.__body.appendChild(
      demo("MentionMenu", null, function () {
        return Tk.MentionMenu({
          items: [
            { label: "@agent/planner", kind: "agent" },
            { label: "@file spec.md", kind: "file" },
            { label: "@project android", kind: "project" },
          ],
          query: "@",
          onPick: function () {},
        });
      })
    );
    main.appendChild(s4);

    /* ── 5. Prompts ────────────────────────────────────────────────── */
    var s5 = section("s-prompts", "Prompts", "Text / choice / multi-select / confirm / permission");
    s5.__body.appendChild(
      demo("TextPrompt", null, function () {
        return Tk.TextPrompt({ question: "What should the new module be called?", placeholder: "module name…" });
      })
    );
    s5.__body.appendChild(
      demo("ChoicePrompt", null, function () {
        return Tk.ChoicePrompt({
          question: "Which database?",
          options: [{ value: "sqlite", label: "SQLite", description: "Local, zero-config" }, { value: "pg", label: "Postgres", description: "Full SQL server" }],
          defaultValue: "sqlite",
        });
      })
    );
    s5.__body.appendChild(
      demo("MultiSelectPrompt", null, function () {
        return Tk.MultiSelectPrompt({
          question: "Select test suites to run",
          options: [{ value: "unit", label: "Unit" }, { value: "e2e", label: "E2E" }, { value: "screenshot", label: "Screenshot" }],
          defaultValue: ["unit"],
        });
      })
    );
    s5.__body.appendChild(
      demo("ConfirmPrompt", null, function () {
        return Tk.ConfirmPrompt({ question: "Delete 75 test workspace directories?", variant: "destructive" });
      })
    );
    main.appendChild(s5);

    /* ── 6. Approvals & permissions ────────────────────────────────── */
    var s6 = section("s-approval", "Approvals & permissions", "Risk-graded approval cards and permission matrix");
    s6.__body.appendChild(
      demo("ApprovalCard × risk levels", "info / warning / destructive", function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.ApprovalCard({ title: "Read workspace file", request: "Read(src/theme.js)", severity: "info", onApprove: function () {}, onDeny: function () {} }),
          Tk.ApprovalCard({ title: "Install dependency", request: "npm i left-pad", severity: "warning", onApprove: function () {}, onDeny: function () {} }),
          Tk.ApprovalCard({ title: "Delete directory", request: "rm -rf builds/usr_legacy", severity: "destructive", onApprove: function () {}, onDeny: function () {} })
        );
      })
    );
    s6.__body.appendChild(
      demo("PermissionModal", null, function () {
        return Tk.PermissionModal({
          class: "dialog-static",
          permission: { tool: "bash", target: "git push origin main", reason: "Publishes commits to a shared remote branch." },
          onAllow: function () {},
          onDeny: function () {},
        });
      })
    );
    s6.__body.appendChild(
      demo("PermissionMatrix", null, function () {
        return Tk.PermissionMatrix({ permissions: [{ tool: "bash", modes: ["ask"] }, { tool: "read", modes: ["allow"] }, { tool: "write", modes: ["ask"] }] });
      })
    );
    s6.__body.appendChild(
      demo("ApprovalModeSelector", null, function () {
        return Tk.ApprovalModeSelector({ modes: [{ id: "ask", label: "Ask every time" }, { id: "session", label: "Allow for session" }, { id: "never", label: "Never allow" }], value: "ask", onChange: function () {} });
      })
    );
    main.appendChild(s6);

    /* ── 7. Tools & diff ───────────────────────────────────────────── */
    var s7 = section("s-tools", "Tools & diff", "Tool calls, results, diff viewer, code review");
    s7.__body.appendChild(
      demo("ToolCallCard", "running → done", function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.ToolCallCard({ tool: "bash", target: "npm run test:unit", status: "running" }),
          Tk.ToolCallCard({ tool: "bash", target: "npm run test:unit", output: "PASS 61 checks\nPASS 19 checks\nexit 0", status: "done", timestamp: "12.8s" })
        );
      })
    );
    s7.__body.appendChild(
      demo("DiffViewer", "unified diff with stats", function () {
        return Tk.DiffViewer({
          path: "src/loop.py",
          stats: { added: 6, removed: 2 },
          diff: "@@ -10,7 +10,11 @@ def run_task():\n     ctx = acquire()\n-    result = loop(ctx)\n-    return result\n+    try:\n+        result = loop(ctx)\n+    except PauseRequested:\n+        return paused(ctx)\n+    return result",
        });
      })
    );
    s7.__body.appendChild(
      demo("CreatedFilesCard", null, function () {
        return Tk.CreatedFilesCard({ files: ["theokit-core.js", "theokit-icons.js", "theokit-primitives.js"] });
      })
    );
    s7.__body.appendChild(
      demo("ToolsList", null, function () {
        return Tk.ToolsList({ tools: [{ name: "bash", risk: "high" }, { name: "read", risk: "low" }, { name: "write", risk: "medium" }] });
      })
    );
    s7.__body.appendChild(
      demo("CodeReviewPanel", null, function () {
        return Tk.CodeReviewPanel({
          files: [
            { path: "agent/loop.py", added: 9, removed: 2 },
            { path: "agent/worker.py", added: 4, removed: 1 },
            { path: "agent/jobs.py", added: 12, removed: 3 },
          ],
          comments: [
            { file: "agent/loop.py", line: 42, severity: "warning", text: "Rethrow CancellationRequested before the generic handler." },
            { file: "agent/jobs.py", line: 118, severity: "info", text: "turn_canceled event keeps the timeline consistent." },
          ],
        });
      })
    );
    main.appendChild(s7);

    /* ── 8. Plans, progress & logs ─────────────────────────────────── */
    var s8 = section("s-plan", "Plans, progress & logs", "Task plan with nested nodes, steps rail, checklists, logs");
    s8.__body.appendChild(
      demo("TaskPlan", "hierarchical nodes × status", function () {
        return Tk.TaskPlan({
          nodes: [
            { id: "1", label: "Fix pause misjudged as failed", status: "done" },
            { id: "2", label: "Fix paused-cancel deadlock", status: "done", children: [
              { id: "2.1", label: "finalize paused on cancel", status: "done" },
              { id: "2.2", label: "release_task race guard", status: "done" },
              { id: "2.3", label: "write turn_canceled events", status: "running" },
            ] },
            { id: "3", label: "Regression suite 152 green", status: "done" },
            { id: "4", label: "Optional: quota dashboard", status: "skipped" },
            { id: "5", label: "Unknown perf regression", status: "failed", detail: "flaky sandbox baseline" },
          ],
        });
      })
    );
    s8.__body.appendChild(
      demo("StepsRail", null, function () {
        return Tk.StepsRail({ steps: [{ label: "connect", status: "done" }, { label: "pair", status: "done" }, { label: "chat", status: "running" }, { label: "verify", status: "pending" }] });
      })
    );
    s8.__body.appendChild(
      demo("ProgressChecklist", null, function () {
        return Tk.ProgressChecklist({ steps: [{ label: "stub model", status: "done" }, { label: "agent service", status: "done" }, { label: "electron pairing", status: "done" }, { label: "approval flow", status: "running" }] });
      })
    );
    s8.__body.appendChild(
      demo("WorkLog", null, function () {
        return Tk.WorkLog({ entries: [{ at: "10:02", text: "started agent service on :8123" }, { at: "10:05", text: "registered temp user" }, { at: "10:11", text: "electron-smoke: OK" }] });
      })
    );
    s8.__body.appendChild(
      demo("AuditLogEntry", null, function () {
        return Tk.AuditLogEntry({ entry: { at: "2026-08-23 10:05", actor: "t09user", action: "account.create", meta: "ok" } });
      })
    );
    s8.__body.appendChild(
      demo("HookEventLog", null, function () {
        return Tk.HookEventLog({ events: [{ at: "10:02", name: "pre-commit", ok: true }, { at: "10:14", name: "post-tool", ok: false }] });
      })
    );
    main.appendChild(s8);

    /* ── 9. Session & context ──────────────────────────────────────── */
    var s9 = section("s-session", "Session & context", "Sessions, context window, branches, folders, projects");
    s9.__body.appendChild(
      demo("SessionListItem + SessionTimeline", null, function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.SessionListItem({ session: { id: "s1", title: "T07 desktop smoke", at: "today 10:02", turns: 15 }, active: true }),
          Tk.SessionTimeline({ sessions: [{ id: "s1", title: "T07 desktop smoke" }, { id: "s2", title: "T08 android e2e" }, { id: "s3", title: "T09 admin e2e" }] })
        );
      })
    );
    s9.__body.appendChild(
      demo("ContextWindowBar", null, function () {
        return Tk.ContextWindowBar({ used: 118, total: 200 });
      })
    );
    s9.__body.appendChild(
      demo("BranchIndicator", null, function () {
        return Tk.BranchIndicator({ current: "feat/theokit-migration", ahead: 5, behind: 1 });
      })
    );
    s9.__body.appendChild(
      demo("ProjectSwitcher", null, function () {
        return Tk.ProjectSwitcher({ projects: [{ id: "p1", name: "Android Agent" }, { id: "p2", name: "theokit-ui" }], value: "p1", onSelect: function () {} });
      })
    );
    s9.__body.appendChild(
      demo("FolderSelector + RecentFoldersList", null, function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.FolderSelector({ path: "/Users/sakura/Android Agent", onPick: function () {} }),
          Tk.RecentFoldersList({ folders: [{ path: "/Users/sakura/Android Agent", at: "today" }, { path: "/Users/sakura/theokit-ui", at: "yesterday" }], onOpen: function () {} })
        );
      })
    );
    s9.__body.appendChild(
      demo("ContextCard + FolderContextCard", null, function () {
        return h(
          "div",
          { class: "grid gap-3 md:grid-cols-2" },
          Tk.ContextCard({ title: "Attached context", items: ["docs/ui-controls-inventory.md", "AI_Agent_UI_Design_Reference_Summary.md"] }),
          Tk.FolderContextCard({ folder: { path: "desktop/src/theokit", files: 5 } })
        );
      })
    );
    s9.__body.appendChild(
      demo("QuickActionChips", null, function () {
        return Tk.QuickActionChips({ actions: [{ id: "run", label: "Run tests" }, { id: "build", label: "Build APK" }, { id: "clean", label: "Clean" }], onPick: function () {} });
      })
    );
    main.appendChild(s9);

    /* ── 10. Models & usage ────────────────────────────────────────── */
    var s10 = section("s-model", "Models & usage", "Model cards, pickers, token charts, cost meters");
    s10.__body.appendChild(
      demo("ModelCard", null, function () {
        return Tk.ModelCard({ model: { id: "gpt-5", name: "GPT-5", context: 200, strengths: ["code", "reasoning"] }, selected: true });
      })
    );
    s10.__body.appendChild(
      demo("ModelSelector + ModelEffortPicker + ThinkingLevelSelector", null, function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.ModelSelector({ models: [{ id: "gpt-5", name: "GPT-5" }, { id: "claude-4", name: "Claude 4" }], value: "gpt-5", onChange: function () {} }),
          Tk.ModelEffortPicker({ efforts: [{ id: "low", label: "Low" }, { id: "medium", label: "Medium" }, { id: "high", label: "High" }], value: "medium" }),
          Tk.ThinkingLevelSelector({ levels: [{ id: "off", label: "Off" }, { id: "on", label: "On" }], value: "on" })
        );
      })
    );
    s10.__body.appendChild(
      demo("TokenUsageChart", null, function () {
        return Tk.TokenUsageChart({ points: [{ input: 8, output: 12 }, { input: 14, output: 22 }, { input: 10, output: 16 }, { input: 20, output: 30 }, { input: 12, output: 18 }] });
      })
    );
    s10.__body.appendChild(
      demo("UsageMeter", null, function () {
        return Tk.UsageMeter({ metrics: [{ label: "tokens", value: 118 }, { label: "cost", value: 62, tone: "warning" }, { label: "quota", value: 95, tone: "danger" }] });
      })
    );
    s10.__body.appendChild(
      demo("CostMeter", null, function () {
        return Tk.CostMeter({ spent: 1.24, budget: 10 });
      })
    );
    s10.__body.appendChild(
      demo("RunStats + RunStatusPill", null, function () {
        return h(
          "div",
          { class: "flex flex-wrap items-center gap-4" },
          Tk.RunStats({ duration: "2m 41s", tokens: "35.7k", filesChanged: 9 }),
          Tk.RunStatusPill({ status: "running" }),
          Tk.RunStatusPill({ status: "succeeded" }),
          Tk.RunStatusPill({ status: "failed" })
        );
      })
    );
    main.appendChild(s10);

    /* ── 11. Infrastructure ────────────────────────────────────────── */
    var s11 = section("s-infra", "Infrastructure", "MCP, gateways, cron, hooks, capabilities, channels");
    s11.__body.appendChild(
      demo("McpServerCard + McpServerList", null, function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.McpServerCard({ server: { name: "filesystem", status: "connected", tools: ["read", "write", "list"] }, onToggle: function () {} }),
          Tk.McpServerList({ servers: [
            { id: "m1", name: "filesystem", status: "connected", tools: ["read"] },
            { id: "m2", name: "browser", status: "disconnected", tools: [] },
          ] })
        );
      })
    );
    s11.__body.appendChild(
      demo("GatewayStatusIndicator", null, function () {
        return h("div", { class: "flex items-center gap-4" }, Tk.GatewayStatusIndicator({ status: "healthy" }), Tk.GatewayStatusIndicator({ status: "degraded" }));
      })
    );
    s11.__body.appendChild(
      demo("CronJobCard + CronJobsList", null, function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.CronJobCard({ job: { name: "daily-report", cron: "0 9 * * 1-5", enabled: true, lastRun: "today 09:00" }, onToggle: function () {}, onRun: function () {} }),
          Tk.CronJobsList({ jobs: [{ id: "j1", name: "daily-report", cron: "0 9 * * 1-5", enabled: true }] })
        );
      })
    );
    s11.__body.appendChild(
      demo("HookConfig", null, function () {
        return Tk.HookConfig({ hook: { name: "pre-commit", command: "npm run check", enabled: true } });
      })
    );
    s11.__body.appendChild(
      demo("CapabilityIndicator", null, function () {
        return Tk.CapabilityIndicator({ capabilities: [{ name: "network", level: "on" }, { name: "process", level: "ask" }, { name: "sandbox", level: "off" }] });
      })
    );
    s11.__body.appendChild(
      demo("ChannelCard", null, function () {
        return Tk.ChannelCard({ channel: { name: "lark", status: "connected", messages: 12 }, onToggle: function () {} });
      })
    );
    main.appendChild(s11);

    /* ── 12. Rules, skills & memory ────────────────────────────────── */
    var s12 = section("s-rules", "Rules, skills & memory", "Rules, skills, memory editors and lists");
    s12.__body.appendChild(
      demo("SkillCard", "source badges + toggle + tools/triggers", function () {
        return h(
          "div",
          { class: "grid gap-3 md:grid-cols-2" },
          Tk.SkillCard({ skill: { id: "sk1", name: "diff-explainer", description: "Explain a diff in plain English with intent + risk.", source: "user", state: "enabled", allowedTools: ["Read", "Grep"], triggers: ["explain diff"] }, onToggle: function () {} }),
          Tk.SkillCard({ skill: { id: "sk3", name: "security-audit", description: "OWASP-style sweep over the changed files.", source: "plugin", state: "disabled" }, onToggle: function () {} })
        );
      })
    );
    s12.__body.appendChild(
      demo("SkillsList", null, function () {
        return Tk.SkillsList({ skills: [{ id: "sk1", name: "diff-explainer", source: "user", state: "enabled" }, { id: "sk2", name: "test-runner", source: "project", state: "enabled" }] });
      })
    );
    s12.__body.appendChild(
      demo("SkillEditor", null, function () {
        return Tk.SkillEditor({ skill: { id: "sk1", name: "diff-explainer", content: "Explain diffs with intent + risk." }, onSave: function () {} });
      })
    );
    s12.__body.appendChild(
      demo("RuleCard + RuleEditor", null, function () {
        return h(
          "div",
          { class: "grid gap-3" },
          Tk.RuleCard({ rule: { id: "r1", name: "prefer-tabs", enabled: true }, onToggle: function () {} }),
          Tk.RuleEditor({ rule: { name: "prefer-tabs", content: "Use tab indentation in this repo." }, onSave: function () {} })
        );
      })
    );
    s12.__body.appendChild(
      demo("MemoryEditor", null, function () {
        return Tk.MemoryEditor({ memories: [{ id: "m1", content: "AVD needs hw.cpu.arch=arm64", scope: "project" }] });
      })
    );
    s12.__body.appendChild(
      demo("SystemPromptEditor", null, function () {
        return Tk.SystemPromptEditor({ entries: [{ source: "base", content: "You are a coding agent." }, { source: "project", content: "Prefer minimal diffs." }] });
      })
    );
    main.appendChild(s12);

    /* ── 13. Panels & viewers ──────────────────────────────────────── */
    var s13 = section("s-panels", "Panels & viewers", "Terminal, artifact preview, whiteboard, stability bundles, lane board");
    s13.__body.appendChild(
      demo("TerminalPanel", null, function () {
        return Tk.TerminalPanel({ lines: [
          { kind: "command", text: "npm run test:unit" },
          { kind: "stdout", text: "PASS tests/theme.test.js" },
          { kind: "stdout", text: "PASS tests/state.test.js" },
          { kind: "stderr", text: "(none)" },
          { kind: "meta", text: "exit 0 · 12.8s" },
        ] });
      })
    );
    s13.__body.appendChild(
      demo("ArtifactPreview", null, function () {
        return Tk.ArtifactPreview({ name: "app-debug.apk", size: "8.0 MB", lines: ["META-INF/", "classes.dex", "resources.arsc"] });
      })
    );
    s13.__body.appendChild(
      demo("Whiteboard", null, function () {
        return Tk.Whiteboard({
          data: {
            width: 480,
            height: 240,
            elements: [
              { type: "rect", x: 24, y: 60, width: 130, height: 56, rx: 10, stroke: "#8b7cf6", fill: "none" },
              { type: "rect", x: 300, y: 60, width: 150, height: 56, rx: 10, stroke: "#8b7cf6", fill: "none" },
              { type: "text", x: 44, y: 94, text: "Electron", fill: "#a99bf3", fontSize: 15 },
              { type: "text", x: 320, y: 94, text: "Android", fill: "#a99bf3", fontSize: 15 },
              { type: "arrow", x: 160, y: 88, x2: 294, y2: 88, stroke: "#7c6fd0" },
              { type: "text", x: 196, y: 76, text: "theokit", fill: "#8d84b8", fontSize: 12 },
              { type: "ellipse", x: 150, y: 160, width: 200, height: 60, stroke: "#5d548a", fill: "none" },
              { type: "text", x: 196, y: 196, text: "108 components", fill: "#8d84b8", fontSize: 13 },
            ],
          },
        });
      })
    );
    s13.__body.appendChild(
      demo("StabilityBundleViewer", null, function () {
        return Tk.StabilityBundleViewer({ bundles: [{ id: "b1", checks: [{ name: "unit", ok: true }, { name: "e2e", ok: true }, { name: "screenshot", ok: false }] }] });
      })
    );
    s13.__body.appendChild(
      demo("LaneBoard", null, function () {
        return Tk.LaneBoard({ lanes: [
          { id: "todo", title: "Todo", cards: [{ id: "c1", title: "Android views" }] },
          { id: "doing", title: "Doing", cards: [{ id: "c2", title: "Desktop showcase" }] },
          { id: "done", title: "Done", cards: [{ id: "c3", title: "Component library" }] },
        ] });
      })
    );
    s13.__body.appendChild(
      demo("RunningTasksPanel", null, function () {
        return Tk.RunningTasksPanel({ tasks: [{ id: "t1", title: "build APK", progress: 62 }, { id: "t2", title: "screenshot matrix", progress: 20 }] });
      })
    );
    s13.__body.appendChild(
      demo("SubAgentDispatch", null, function () {
        return Tk.SubAgentDispatch({ agents: [{ name: "explorer", task: "survey theokit-ui exports" }, { name: "planner", task: "map migration order" }] });
      })
    );
    s13.__body.appendChild(
      demo("ExportChatDialog", null, function () {
        return Tk.ExportChatDialog({ open: true, class: "dialog-static", sessionLabel: "T07 desktop smoke · 15 turns", onExport: function () {}, onOpenChange: function () {} });
      })
    );
    main.appendChild(s13);

    /* ── 14. Stream & timeline ─────────────────────────────────────── */
    var s14 = section("s-stream", "Stream & timeline", "Agent stream pipeline and timeline views");
    s14.__body.appendChild(
      demo("AgentStream", "message + tool + event + streaming + error", function () {
        return Tk.AgentStream({
          items: [
            { kind: "message", message: { role: "user", parts: [{ type: "text", text: "迁移全部控件" }] } },
            { kind: "streaming", model: "gpt-5", partial: "Analyzing 103 component exports…" },
            { kind: "tool", part: { type: "tool", toolName: "bash", state: "output-available", input: "ls src/components", output: "primitives composites" } },
            { kind: "event", event: { icon: "check", title: "unit tests", detail: "61 + 19 checks", status: "success" } },
            { kind: "message", message: { role: "assistant", parts: [{ type: "text", text: "Migration complete: 108/108 components render." }] } },
          ],
        });
      })
    );
    s14.__body.appendChild(
      demo("AgentTimeline", null, function () {
        return Tk.AgentTimeline({
          events: [
            { icon: "terminal", title: "npm run test:unit", detail: "152 passed · 0 failed", status: "success", meta: "41s" },
            { icon: "wrench", title: "edit agent/loop.py", detail: "+9 −2 lines", status: "success" },
            { icon: "hammer", title: "assembleDebug", status: "running" },
          ],
        });
      })
    );
    s14.__body.appendChild(
      demo("AgentToolRenderer", null, function () {
        return Tk.AgentToolRenderer({ part: { type: "tool", toolName: "bash", state: "output-available", input: "node /tmp/theokit-smoke.js", output: "SMOKE: 108/108 components render OK" } });
      })
    );
    main.appendChild(s14);

    /* ── 15. Editors & slides ──────────────────────────────────────── */
    var s15 = section("s-editor", "Editors & slides", "Agent editor, slide deck, code block");
    s15.__body.appendChild(
      demo("AgentEditor", null, function () {
        return Tk.AgentEditor({ file: { path: "src/theokit/theokit-core.js", content: "function h(tag, attrs) { /* … */ }" }, onSave: function () {} });
      })
    );
    s15.__body.appendChild(
      demo("CodeBlock", null, function () {
        return Tk.CodeBlock({ code: "const fix = { pause: \"paused\", cancel: \"canceled\" };\nconsole.log(fix.pause);", language: "ts" });
      })
    );
    s15.__body.appendChild(
      demo("SlideDeck", "markdown slides", function () {
        return Tk.SlideDeck({
          slides: [
            "# TheoKit Migration\n## 108 components across desktop + Android\n- vanilla JS component library\n- Kotlin view family\n- showcase galleries",
            "# Verification\n- desktop: check / unit / screenshot\n- android: compile + unit\n- pixel-level color checks",
          ],
        });
      })
    );
    main.appendChild(s15);

    return main;
  }

  root.TheokitShowcase = { build: build };
})(typeof window !== "undefined" ? window : globalThis);
