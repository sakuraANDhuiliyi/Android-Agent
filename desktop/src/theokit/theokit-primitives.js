/*
 * theokit-primitives.js — TheoKit primitive components (vanilla JS migration
 * of @theokit/ui primitives/*). Every factory mirrors the React component's
 * DOM structure, data-slot attributes and Tailwind classes so the compiled
 * components.css from @theokit/ui styles them unmodified.
 */
(function (root) {
  "use strict";

  var Tk = root.Theokit = root.Theokit || {};
  var h = Tk.h;
  var cn = Tk.cn;
  var renderIcon = Tk.renderIcon;
  var Button = Tk.Button;
  var icons = Tk.icons || {};

  /* ── agent-error-card ─────────────────────────────────────────────── */

  var ENVELOPE_CODE_KIND = {
    E_TOOL_TIMEOUT: "timeout",
    E_TOOL_DENIED: "denied",
    E_TOOL_FAILED: "tool",
    E_MODEL_RATE_LIMIT: "rate-limit",
    E_MODEL_OVERLOAD: "overload",
    E_NETWORK: "network",
    E_QUOTA: "quota",
    E_CANCELLED: "cancelled",
  };

  function kindFromEnvelopeCode(code) {
    return ENVELOPE_CODE_KIND[code] || "unknown";
  }

  var ERROR_KIND_LABEL = {
    timeout: "Timeout",
    denied: "Denied",
    tool: "Tool error",
    "rate-limit": "Rate limited",
    overload: "Overloaded",
    network: "Network error",
    quota: "Quota exceeded",
    cancelled: "Cancelled",
    unknown: "Error",
  };

  function AgentErrorCard(props) {
    props = props || {};
    var kind = props.kind || kindFromEnvelopeCode(props.code) || "unknown";
    var card = h(
      "div",
      { "data-slot": "agent-error-card", class: cn("flex items-start gap-3", props.class) },
      h(
        "span",
        { class: "mt-0.5 inline-flex shrink-0 text-destructive" },
        icons.alertTriangle("size-4")
      ),
      h(
        "div",
        { class: "grid min-w-0 flex-1 gap-1" },
        h(
          "div",
          { class: "flex items-baseline justify-between gap-2" },
          h(
            "h4",
            { class: "font-display text-foreground text-title-md tracking-tight" },
            props.title || ERROR_KIND_LABEL[kind]
          ),
          props.timestamp
            ? h(
                "span",
                { class: "shrink-0 font-mono text-label text-muted-foreground tabular-nums" },
                props.timestamp
              )
            : null
        ),
        props.detail
          ? h(
              "pre",
              { class: "break-words font-mono text-code-sm text-muted-foreground" },
              props.detail
            )
          : null,
        props.actions
          ? h("div", { class: "flex flex-wrap items-center justify-end gap-2" }, props.actions)
          : null
      )
    );
    return card;
  }

  /* ── agent-event ──────────────────────────────────────────────────── */

  var EVENT_TYPE_ICON = {
    command: "terminal",
    file_read: "fileSearch",
    file_write: "filePlus",
    edit: "edit-3",
    lint: "shield-check",
    typecheck: "shield-check",
    build: "hammer",
    tool: "wrench",
  };
  var EVENT_STATUS_ICON = {
    pending: "circle-dot",
    running: "loader",
    success: "check-circle",
    failed: "alert-triangle",
  };
  var EVENT_STATUS_COLOR = {
    pending: "text-muted-foreground",
    running: "text-primary",
    success: "text-success",
    failed: "text-destructive",
  };

  function AgentEvent(props) {
    props = props || {};
    var event = props.event || {};
    var open = !!props.defaultOpen;
    var isExpandable = !!(props.collapsible && event.detail !== undefined);

    var wrap = h("div", {
      "data-slot": "agent-event",
      class: cn(
        "rounded-md border border-transparent",
        isExpandable && "hover:border-border/40 hover:bg-muted/40",
        props.class
      ),
    });

    function headerContent() {
      var typeIcon = icons[EVENT_TYPE_ICON[event.type] || "wrench"];
      var statusIcon = icons[EVENT_STATUS_ICON[event.status] || "circle-dot"];
      return [
        h(
          "span",
          { class: "grid size-7 place-items-center rounded-md bg-muted text-muted-foreground" },
          typeIcon("size-3.5")
        ),
        h(
          "div",
          { class: "min-w-0" },
          h(
            "p",
            { class: "flex flex-wrap items-baseline gap-x-2 gap-y-0.5" },
            h(
              "span",
              { class: "truncate font-medium text-body-sm text-foreground" },
              event.label || ""
            ),
            event.path
              ? h(
                  "span",
                  { class: "truncate font-mono text-code-sm text-muted-foreground" },
                  event.path
                )
              : null,
            event.diff
              ? h(
                  "span",
                  { class: "font-mono text-code-sm" },
                  h("span", { class: "text-success" }, "+" + event.diff.added),
                  " ",
                  h("span", { class: "text-destructive" }, "-" + event.diff.removed)
                )
              : null
          ),
          event.timestamp
            ? h("p", { class: "font-mono text-label text-muted-foreground" }, event.timestamp)
            : null
        ),
        h(
          "div",
          { class: "flex items-center gap-1.5" },
          (function () {
            var el = statusIcon(cn("size-4", EVENT_STATUS_COLOR[event.status] || "text-muted-foreground"));
            el.setAttribute("aria-label", event.status || "");
            if (event.status === "running") el.classList.add("motion-safe:animate-spin");
            return el;
          })(),
          isExpandable
            ? (function () {
                var chev = icons.chevronRight("size-4 text-muted-foreground transition-transform");
                wrap.__chevron = chev;
                return chev;
              })()
            : null
        ),
      ];
    }

    var detailBox = null;
    function toggle() {
      if (!isExpandable) return;
      open = !open;
      if (detailBox) {
        detailBox.style.display = open ? "" : "none";
      } else if (open) {
        detailBox = h(
          "div",
          {
            class:
              "border-border/40 border-t bg-muted/20 px-3 py-2 font-mono text-code-sm text-muted-foreground",
          },
          event.detail
        );
        wrap.appendChild(detailBox);
      }
      if (wrap.__chevron && open) wrap.__chevron.classList.add("rotate-90");
      else if (wrap.__chevron) wrap.__chevron.classList.remove("rotate-90");
      var btn = wrap.querySelector ? wrap.querySelector("button") : null;
      if (btn && btn.setAttribute) btn.setAttribute("aria-expanded", String(open));
    }

    var row = isExpandable
      ? h(
          "button",
          {
            type: "button",
            "aria-expanded": "false",
            class: cn(
              "grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 px-3 py-2 text-left",
              "cursor-pointer rounded-md",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            ),
            onClick: function () {
              toggle();
              if (props.onClick) props.onClick(event);
            },
          },
          headerContent()
        )
      : h(
          "div",
          { class: "grid grid-cols-[auto_1fr_auto] items-center gap-3 px-3 py-2" },
          headerContent()
        );
    wrap.appendChild(row);
    return wrap;
  }

  /* ── agent-handoff ────────────────────────────────────────────────── */

  function AgentHandoff(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "agent-handoff",
        class: cn("flex items-center gap-2", props.class),
        "aria-label": "Agent handoff",
      },
      h(
        "span",
        { class: "font-medium font-mono text-code-sm text-foreground" },
        props.from || ""
      ),
      h("span", { class: "flex items-center gap-2" }, icons.arrowRight("size-4 text-primary")),
      h(
        "span",
        { class: "font-medium font-mono text-code-sm text-foreground" },
        props.to || ""
      ),
      props.reason
        ? h("span", { class: "text-body-sm text-foreground" }, props.reason)
        : null,
      props.timestamp
        ? h(
            "span",
            {
              class:
                "ml-auto font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
            },
            props.timestamp
          )
        : null
    );
  }

  /* ── agent-profile ────────────────────────────────────────────────── */

  var TONE_BG = {
    primary: "bg-primary/15 text-primary",
    accent: "bg-accent/15 text-accent",
    success: "bg-success/15 text-success",
    warning: "bg-warning/20 text-warning",
    info: "bg-info/15 text-info",
    muted: "bg-muted text-muted-foreground",
  };

  function AgentProfile(props) {
    props = props || {};
    var p = props.profile || {};
    var tone = p.tone || "primary";
    var selected = !!props.selected;
    var card = h(
      "button",
      {
        type: "button",
        "data-slot": "agent-profile",
        "data-selected": selected ? "true" : null,
        "aria-pressed": selected ? "true" : "false",
        class: cn(
          "flex w-full items-center gap-3 rounded-lg border border-border/40 bg-card px-3 py-2.5 text-left transition-colors",
          selected ? "border-primary/60 bg-primary/5" : "hover:border-border/60 hover:bg-muted/30",
          props.class
        ),
        onClick: function () {
          if (props.onSelect) props.onSelect(p);
        },
      },
      p.initials
        ? h(
            "span",
            { class: cn("grid size-8 shrink-0 place-items-center rounded-lg font-display text-title-sm", TONE_BG[tone] || TONE_BG.primary) },
            p.initials
          )
        : null,
      h(
        "div",
        { class: "grid min-w-0 flex-1 gap-0.5" },
        h(
          "div",
          { class: "flex items-center gap-2" },
          h(
            "span",
            { class: "font-medium font-sans text-body-sm text-foreground" },
            p.name || ""
          ),
          p.model
            ? h(
                "span",
                {
                  class:
                    "inline-flex items-center gap-1 rounded-full bg-accent/15 px-1.5 py-0 font-mono text-accent text-label uppercase",
                },
                h("span", { class: "size-2.5" }),
                p.model
              )
            : null
        ),
        p.description
          ? h("span", { class: "text-body-sm text-muted-foreground" }, p.description)
          : null
      ),
      selected ? icons.check("mt-1 size-4 shrink-0 text-primary") : null
    );
    return card;
  }

  /* ── agent-starting-state ─────────────────────────────────────────── */

  function AgentStartingState(props) {
    props = props || {};
    return h(
      "div",
      { "data-slot": "agent-starting-state", class: cn("flex items-start gap-3", props.class) },
      icons.loader("size-4 animate-spin text-primary"),
      h(
        "div",
        { class: "grid" },
        h(
          "span",
          { class: "font-medium text-body-sm text-foreground" },
          props.title || "Starting agent…"
        ),
        props.subtitle
          ? h("span", { class: "text-body-sm text-muted-foreground" }, props.subtitle)
          : null
      )
    );
  }

  /* ── agent-streaming ──────────────────────────────────────────────── */

  function AgentStreaming(props) {
    props = props || {};
    var body;
    if (props.partial != null && props.partial !== "") {
      body = h(
        "span",
        { class: "break-words text-body-md text-foreground" },
        props.partial,
        h("span", {
          class:
            "ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-primary align-middle",
        })
      );
    } else {
      body = h(
        "span",
        { class: "flex items-center gap-1.5" },
        h("span", { class: "size-1.5 animate-pulse rounded-full bg-primary" }),
        h("span", { class: "size-1.5 animate-pulse rounded-full bg-primary" }),
        h("span", { class: "size-1.5 animate-pulse rounded-full bg-primary" }),
        h(
          "span",
          { class: "ml-1 text-body-sm text-muted-foreground" },
          props.placeholder || "thinking…"
        )
      );
    }
    return h(
      "div",
      {
        "data-slot": "agent-streaming",
        class: cn(
          "flex items-start gap-3 rounded-xl border border-border/40 bg-card/40 px-4 py-3",
          props.class
        ),
        "aria-live": "polite",
      },
      h(
        "span",
        {
          class:
            "grid size-7 shrink-0 place-items-center rounded-full bg-primary/15 text-primary",
        },
        icons.sparkles("size-3.5")
      ),
      h(
        "div",
        { class: "grid min-w-0 flex-1 gap-1" },
        props.model
          ? h(
              "span",
              {
                class:
                  "font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
              },
              props.model
            )
          : null,
        body
      )
    );
  }

  /* ── approval-mode-selector ───────────────────────────────────────── */

  function ApprovalModeSelector(props) {
    props = props || {};
    var value = props.value;
    var options = props.options || [
      { value: "ask", label: "Ask every time", icon: "shield" },
      { value: "allow-safe", label: "Allow safe", icon: "shield-check" },
      { value: "yolo", label: "Allow all", icon: "zap" },
    ];
    return h(
      "div",
      {
        "data-slot": "approval-mode-selector",
        class: cn("inline-flex rounded-lg border border-border/60 bg-muted p-0.5", props.class),
        role: "radiogroup",
      },
      options.map(function (opt) {
        var active = opt.value === value;
        return h(
          "button",
          {
            type: "button",
            role: "radio",
            "aria-checked": active ? "true" : "false",
            class: cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-mono text-label transition-colors",
              active
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            ),
            onClick: function () {
              if (props.onSelect) props.onSelect(opt.value);
            },
          },
          opt.icon ? icons[opt.icon]("size-3.5") : null,
          opt.label
        );
      })
    );
  }

  /* ── artifact-preview ─────────────────────────────────────────────── */

  function ArtifactPreview(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "artifact-preview",
        class: cn(
          "grid overflow-hidden rounded-xl border border-border/40 bg-card",
          props.class
        ),
      },
      h(
        "div",
        { class: "flex items-center gap-3 border-border/40 border-b px-3 py-2" },
        h(
          "div",
          { class: "min-w-0 flex-1" },
          h(
            "span",
            { class: "truncate font-medium text-body-sm text-foreground" },
            props.title || "Artifact"
          ),
          h(
            "span",
            { class: "block truncate font-mono text-label text-muted-foreground" },
            props.subtitle || ""
          )
        ),
        h("div", { class: "flex items-center gap-1" }, props.actions || null)
      ),
      h("div", { class: "flex-1 overflow-auto" }, props.children),
      h(
        "div",
        { class: "flex items-center gap-1 border-border/40 border-t px-2 py-1" },
        props.footer || null
      )
    );
  }

  /* ── attachment-chip ──────────────────────────────────────────────── */

  function AttachmentChip(props) {
    props = props || {};
    return h(
      "span",
      {
        "data-slot": "attachment-chip",
        class: cn(
          "inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border border-border/60 bg-card px-2.5 py-1 font-mono text-code-sm",
          props.onRemove && "pr-1",
          props.class
        ),
      },
      icons.paperclip("size-3.5 shrink-0 text-primary"),
      h("span", { class: "truncate text-foreground" }, props.name || "file"),
      props.onRemove
        ? h(
            "button",
            {
              type: "button",
              "aria-label": "Remove attachment",
              class:
                "grid size-5 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground",
              onClick: function (e) {
                e.stopPropagation();
                props.onRemove(props.name);
              },
            },
            icons.x("size-3")
          )
        : null
    );
  }

  /* ── audit-log-entry ──────────────────────────────────────────────── */

  function AuditLogEntry(props) {
    props = props || {};
    return h(
      "div",
      { "data-slot": "audit-log-entry", class: cn("flex items-start gap-2.5", props.class) },
      icons.activity("size-3.5 shrink-0 mt-1 text-muted-foreground"),
      h(
        "div",
        { class: "min-w-0" },
        h(
          "p",
          { class: "flex flex-wrap items-baseline gap-2 text-body-sm" },
          h("span", { class: "font-medium font-mono text-code-sm text-foreground" }, props.actor || "system"),
          h("span", { class: "truncate font-mono text-code-sm text-foreground/80" }, props.action || ""),
          props.target
            ? h("span", { class: "truncate font-mono text-code-sm text-foreground/80" }, props.target)
            : null
        ),
        props.detail
          ? h(
              "pre",
              {
                class:
                  "mt-1 rounded-md bg-muted/40 px-2.5 py-1.5 font-mono text-code-sm text-muted-foreground",
              },
              props.detail
            )
          : null
      ),
      props.timestamp
        ? h(
            "span",
            { class: "shrink-0 font-mono text-label text-muted-foreground tabular-nums" },
            props.timestamp
          )
        : null
    );
  }

  /* ── auto-compact-notice ──────────────────────────────────────────── */

  function AutoCompactNotice(props) {
    props = props || {};
    var used = props.tokensUsed || 0;
    var limit = props.tokenLimit || 0;
    var pct = limit > 0 ? Math.round((used / limit) * 100) : 0;
    return h(
      "div",
      {
        "data-slot": "auto-compact-notice",
        class: cn("flex items-start gap-3 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3", props.class),
      },
      h("span", { class: "mt-0.5 size-4 shrink-0 text-warning" }, icons.lightbulb("size-4")),
      h(
        "div",
        { class: "grid gap-1" },
        h(
          "p",
          { class: "flex items-baseline gap-2 font-medium text-body-sm text-foreground" },
          props.title || "Context nearly full",
          h(
            "span",
            { class: "inline-flex items-center rounded-full bg-warning/20 px-2 py-0.5 font-mono text-label text-warning tabular-nums" },
            pct + "%"
          )
        ),
        h(
          "p",
          { class: "text-body-sm text-muted-foreground" },
          props.description ||
            Tk.text(used) + " / " + Tk.text(limit) + " tokens — older turns will be compacted."
        ),
        props.onDismiss
          ? h(
              "button",
              {
                type: "button",
                class:
                  "justify-self-start rounded-md p-1 text-warning hover:bg-warning/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                "aria-label": "Dismiss notice",
                onClick: props.onDismiss,
              },
              icons.x("size-3.5")
            )
          : null
      )
    );
  }

  /* ── branch-indicator ─────────────────────────────────────────────── */

  function BranchIndicator(props) {
    props = props || {};
    var count = props.branchCount || 0;
    var el = h(
      "span",
      {
        "data-slot": "branch-indicator",
        class: cn(
          "inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 font-mono text-label text-muted-foreground",
          props.class
        ),
        title: props.tooltipText || count + " branches",
      },
      icons.gitBranch("size-3"),
      h("span", { class: "tabular-nums" }, String(count))
    );
    if (props["data-testid"]) el.setAttribute("data-testid", props["data-testid"]);
    return el;
  }

  /* ── browser-controls ─────────────────────────────────────────────── */

  function BrowserControls(props) {
    props = props || {};
    var urlInput = h("input", {
      type: "text",
      value: props.url || "",
      placeholder: "https://…",
      "aria-label": "Browser URL",
      class:
        "h-8 min-w-0 flex-1 rounded-md border border-input bg-card px-2 font-mono text-code-sm focus:outline-none focus:ring-2 focus:ring-ring",
    });
    urlInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && props.onNavigate) props.onNavigate(urlInput.value);
    });
    return h(
      "div",
      {
        "data-slot": "browser-controls",
        class: cn("flex items-center gap-2 border-border/40 border-b px-3 py-2", props.class),
      },
      h(
        "button",
        {
          type: "button",
          "aria-label": "Back",
          class: "grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
          onClick: props.onBack,
        },
        icons.chevronLeft("size-3.5")
      ),
      h(
        "button",
        {
          type: "button",
          "aria-label": "Forward",
          class: "grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
          onClick: props.onForward,
        },
        icons.chevronRight("size-3.5")
      ),
      h(
        "button",
        {
          type: "button",
          "aria-label": "Refresh",
          class: "grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
          onClick: props.onRefresh,
        },
        icons.rotateCw("size-3.5")
      ),
      urlInput,
      props.running ? icons.loader("size-3.5 animate-spin text-primary") : null,
      props.onNavigate
        ? h(
            "button",
            {
              type: "button",
              "aria-label": "Go",
              class: "grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
              onClick: function () {
                props.onNavigate(urlInput.value);
              },
            },
            icons.arrowRight("size-3.5")
          )
        : null
    );
  }

  /* ── build-log-stream ─────────────────────────────────────────────── */

  var LOG_KIND_CLASS = {
    stdout: "",
    stderr: "text-destructive",
    command: "text-primary",
    meta: "text-muted-foreground",
  };

  function BuildLogStream(props) {
    props = props || {};
    var lines = props.lines || [];
    var body;
    if (lines.length === 0) {
      body = h(
        "div",
        { class: "px-4 py-3 text-muted-foreground" },
        props.emptyLabel || "No build output yet."
      );
    } else {
      body = h(
        "div",
        { class: "divide-y divide-border/30" },
        lines.map(function (line) {
          return h(
            "div",
            {
              class:
                "grid grid-cols-[auto_auto_1fr] gap-3 px-4 py-1.5 leading-relaxed hover:bg-muted/30 font-mono text-code-sm",
            },
            h("span", { class: "select-none text-muted-foreground tabular-nums" }, line.number != null ? String(line.number) : ""),
            h("span", { class: cn("select-none", LOG_KIND_CLASS[line.kind] || "") }, (line.kind === "stderr" ? "!" : line.kind === "command" ? "$" : line.kind === "meta" ? "#" : " ")),
            h("span", { class: cn(LOG_KIND_CLASS[line.kind] || "", "whitespace-pre-wrap") }, line.content || "")
          );
        })
      );
    }
    return h(
      "div",
      {
        "data-slot": "build-log-stream",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "div",
        { class: "flex flex-wrap gap-1.5 border-border/30 border-b bg-muted/40 px-4 py-1.5" },
        (props.filters || []).map(function (f) {
          return h(
            "button",
            {
              type: "button",
              class: cn(
                "rounded-full px-2 py-0.5 font-mono text-label transition-colors",
                f.active
                  ? "bg-primary/15 text-primary"
                  : "bg-muted text-muted-foreground hover:text-foreground"
              ),
              onClick: function () {
                if (props.onFilterChange) props.onFilterChange(f.value);
              },
            },
            f.label
          );
        })
      ),
      body
    );
  }

  /* ── capability-indicator ─────────────────────────────────────────── */

  function CapabilityIndicator(props) {
    props = props || {};
    var caps = props.capabilities || [];
    return h(
      "div",
      {
        "data-slot": "capability-indicator",
        class: cn("flex flex-wrap items-center gap-1.5", props.class),
      },
      caps.map(function (c) {
        var on = c.enabled !== false;
        return h(
          "span",
          {
            class: cn(
              "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-label",
              on ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"
            ),
            title: c.description || c.name,
          },
          on ? icons.check("size-3") : icons.x("size-3"),
          c.name
        );
      })
    );
  }

  /* ── channel-card ─────────────────────────────────────────────────── */

  function ChannelCard(props) {
    props = props || {};
    var c = props.channel || props;
    var connected = c.status === "connected";
    return h(
      "div",
      {
        "data-slot": "channel-card",
        class: cn(
          "rounded-xl border border-border/40 bg-card p-4 transition-colors",
          props.onClick && "cursor-pointer hover:border-border/60",
          props.class
        ),
        onClick: function () {
          if (props.onClick) props.onClick(c);
        },
      },
      h(
        "div",
        { class: "flex items-start justify-between gap-3" },
        h(
          "div",
          { class: "flex min-w-0 items-center gap-2" },
          icons[c.icon || "zap"]("size-4 shrink-0 text-primary"),
          h(
            "div",
            { class: "min-w-0" },
            h(
              "p",
              { class: "font-medium font-mono text-body-sm text-foreground" },
              c.name || ""
            ),
            h(
              "p",
              { class: "truncate font-mono text-label text-muted-foreground" },
              c.protocol || ""
            )
          )
        ),
        h(
          "span",
          {
            class: cn(
              "shrink-0 rounded-full px-2 py-0.5 font-mono text-label uppercase",
              connected ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"
            ),
          },
          c.status || "offline"
        )
      ),
      h(
        "dl",
        { class: "mt-3 grid grid-cols-2 gap-2 font-mono text-label" },
        h("div", null, h("dt", { class: "text-muted-foreground uppercase tracking-wider" }, "Latency"), h("dd", { class: "text-foreground tabular-nums" }, (c.latencyMs != null ? c.latencyMs + "ms" : "—"))),
        h("div", null, h("dt", { class: "text-muted-foreground uppercase tracking-wider" }, "Messages"), h("dd", { class: "text-foreground tabular-nums" }, String(c.messages != null ? c.messages : 0)))
      ),
      h(
        "div",
        { class: "mt-3 flex items-center justify-end gap-1.5" },
        props.actions ||
          (props.onToggle
            ? Button({
                size: "sm",
                variant: connected ? "ghost" : "primary",
                label: connected ? "Disconnect" : "Connect",
                children: connected ? "Disconnect" : "Connect",
                onClick: function () {
                  props.onToggle(c);
                },
              })
            : null)
      )
    );
  }

  /* ── chat-thread ──────────────────────────────────────────────────── */

  function ChatThread(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "chat-thread",
        class: cn("flex flex-col gap-4", props.class),
        role: "log",
        "aria-live": "polite",
      },
      props.children
    );
  }

  /* ── code-review-panel ────────────────────────────────────────────── */

  function CodeReviewPanel(props) {
    props = props || {};
    var files = props.files || [];
    var comments = props.comments || [];
    var active = files[0];
    return h(
      "div",
      {
        "data-slot": "code-review-panel",
        class: cn(
          "grid min-h-0 flex-1 grid-cols-[1fr_9rem] overflow-hidden rounded-xl border border-border/40 bg-card",
          props.class
        ),
      },
      h(
        "div",
        { class: "flex min-w-0 flex-col" },
        h(
          "div",
          {
            class:
              "flex items-center gap-2 border-border/40 border-b bg-card/80 px-3 py-1.5 font-mono text-xs",
          },
          h("span", { class: "truncate text-foreground" }, active ? active.path : "No file"),
          h("span", { class: "ml-auto shrink-0 text-success" }, "+" + (active ? active.added || 0 : 0)),
          h("span", { class: "shrink-0 text-destructive" }, "-" + (active ? active.removed || 0 : 0))
        ),
        h(
          "div",
          { class: "overflow-auto font-mono text-xs leading-relaxed" },
          (active && active.lines ? active.lines : []).map(function (line) {
            return h(
              "div",
              {
                class: cn(
                  "flex",
                  line.kind === "added" && "bg-success/10",
                  line.kind === "removed" && "bg-destructive/10",
                  line.comment && "bg-warning/10"
                ),
              },
              h(
                "span",
                {
                  class:
                    "w-10 shrink-0 select-none border-border/30 border-r px-1 text-right text-muted-foreground/60",
                },
                String(line.number || "")
              ),
              h("span", { class: "min-w-0 flex-1 whitespace-pre px-2" }, line.content || "")
            );
          })
        ),
        h(
          "div",
          { class: "flex items-center gap-1 border-border/40 border-b bg-card/80 px-3 py-2" },
          h(
            "span",
            {
              class:
                "flex items-center gap-1 rounded-full bg-muted/50 px-3 py-0.5 font-medium text-foreground text-xs",
            },
            icons.messageSquare("size-3"),
            String(comments.length) + " comments"
          ),
          h(
            "span",
            { class: "ml-auto flex items-center gap-2 text-xs" },
            h("span", { class: "text-muted-foreground" }, "Review"),
            props.onApprove
              ? h(
                  "button",
                  {
                    type: "button",
                    class: "text-success transition-colors hover:opacity-80",
                    onClick: props.onApprove,
                  },
                  "Approve"
                )
              : null,
            props.onRequestChanges
              ? h(
                  "button",
                  {
                    type: "button",
                    class: "text-destructive transition-colors hover:opacity-80",
                    onClick: props.onRequestChanges,
                  },
                  "Request changes"
                )
              : null
          )
        ),
        h(
          "div",
          { class: "flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3" },
          comments.length === 0
            ? h("p", { class: "text-body-sm text-muted-foreground" }, "No review comments yet.")
            : comments.map(function (cm) {
                return h(
                  "div",
                  {
                    class:
                      "flex items-start gap-2 rounded-lg border border-border/40 bg-background/40 p-2.5",
                  },
                  icons.messageSquare("size-3.5 mt-0.5 shrink-0 text-muted-foreground"),
                  h(
                    "div",
                    { class: "grid min-w-0 flex-1 gap-0.5" },
                    h(
                      "span",
                      { class: "font-medium font-mono text-code-sm text-foreground" },
                      cm.author || "reviewer"
                    ),
                    h(
                      "span",
                      { class: "text-body-sm text-muted-foreground" },
                      cm.body || ""
                    )
                  )
                );
              })
        )
      ),
      h(
        "aside",
        { class: "w-36 shrink-0 overflow-auto border-border/40 border-l px-2 py-2.5" },
        h(
          "p",
          { class: "font-mono text-label text-muted-foreground uppercase tracking-wider" },
          "Files"
        ),
        h(
          "ul",
          { class: "mt-1 space-y-0.5" },
          files.map(function (f) {
            return h(
              "li",
              null,
              h(
                "button",
                {
                  type: "button",
                  class: cn(
                    "grid w-full grid-cols-[1fr_auto] items-baseline gap-1 rounded px-1.5 py-1 text-left font-mono text-code-sm",
                    f === active ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
                  ),
                  onClick: function () {
                    if (props.onSelectFile) props.onSelectFile(f);
                  },
                },
                h("span", { class: "truncate" }, f.path),
                h("span", { class: "shrink-0 tabular-nums" }, "+" + (f.added || 0) + " -" + (f.removed || 0))
              )
            );
          })
        )
      )
    );
  }

  /* ── context-card ─────────────────────────────────────────────────── */

  function ContextCard(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "context-card",
        class: cn("grid gap-2 rounded-xl border border-border/40 bg-card p-8 text-center", props.class),
      },
      h("div", { class: "flex justify-center" }, icons[props.icon || "sparkles"]("size-5 text-primary")),
      h(
        "h3",
        { class: "font-display text-title-md tracking-tight" },
        props.title || "No context yet"
      ),
      h(
        "p",
        { class: "text-body-sm text-muted-foreground" },
        props.description || "Start a conversation to build context."
      ),
      props.actions
        ? h("div", { class: "mt-2 flex justify-center gap-2" }, props.actions)
        : null
    );
  }

  /* ── context-window-bar ───────────────────────────────────────────── */

  function ContextWindowBar(props) {
    props = props || {};
    var used = props.used || 0;
    var total = props.total || 1;
    var pct = Math.max(0, Math.min(100, Math.round((used / total) * 100)));
    return h(
      "div",
      { "data-slot": "context-window-bar", class: cn("grid gap-1.5", props.class) },
      h(
        "div",
        { class: "flex items-baseline justify-between gap-2" },
        h(
          "span",
          {
            class: "font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
          },
          props.label || "Context window"
        ),
        h(
          "span",
          { class: "font-mono text-label text-muted-foreground tabular-nums" },
          used.toLocaleString() + " / " + total.toLocaleString()
        )
      ),
      h(
        "div",
        { class: "h-1.5 w-full overflow-hidden rounded-full bg-muted" },
        h("div", {
          class:
            "h-full rounded-full transition-[width] duration-base ease-out-soft " +
            (pct > 90 ? "bg-destructive" : pct > 70 ? "bg-warning" : "bg-primary"),
          style: { width: pct + "%" },
        })
      )
    );
  }

  /* ── cost-meter ───────────────────────────────────────────────────── */

  function CostMeter(props) {
    props = props || {};
    var cost = props.cost || 0;
    var budget = props.budget || 0;
    var pct = budget > 0 ? Math.max(0, Math.min(100, Math.round((cost / budget) * 100))) : 0;
    return h(
      "div",
      {
        "data-slot": "cost-meter",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "flex items-baseline justify-between" },
        h(
          "span",
          {
            class: "font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
          },
          props.label || "Spend"
        ),
        props.icon ? icons[props.icon]("size-3 text-primary") : icons.dollarSign("size-3 text-primary")
      ),
      h(
        "div",
        { class: "mt-2 flex items-baseline gap-1.5" },
        h(
          "span",
          { class: "font-bold font-display text-display-md text-foreground tabular-nums leading-none" },
          (props.currency || "$") + cost.toFixed(2)
        ),
        budget > 0
          ? h(
              "span",
              { class: "font-mono text-body-sm text-muted-foreground" },
              "/ " + (props.currency || "$") + budget.toFixed(2)
            )
          : null
      ),
      h(
        "div",
        { class: "mt-2 grid gap-1" },
        h(
          "div",
          { class: "h-1.5 w-full overflow-hidden rounded-full bg-muted" },
          h("div", {
            class:
              "h-full rounded-full transition-[width] duration-base ease-out-soft " +
              (pct > 90 ? "bg-destructive" : "bg-primary"),
            style: { width: pct + "%" },
          })
        ),
        h(
          "span",
          { class: "font-mono text-label text-muted-foreground tabular-nums" },
          pct + "% of budget" + (props.period ? " · " + props.period : "")
        )
      )
    );
  }

  /* ── created-files-card ───────────────────────────────────────────── */

  function CreatedFilesCard(props) {
    props = props || {};
    var files = props.files || [];
    var added = files.length;
    return h(
      "div",
      {
        "data-slot": "created-files-card",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "mb-3 flex items-center gap-2" },
        icons.filePlus("size-4 text-primary"),
        h(
          "h4",
          { class: "font-display text-title-md tracking-tight" },
          props.title || "Created files"
        ),
        h(
          "span",
          { class: "ml-auto font-mono text-code-sm" },
          h("span", { class: "text-success" }, "+" + added)
        )
      ),
      h(
        "ul",
        { class: "grid gap-2" },
        files.map(function (f) {
          return h(
            "li",
            null,
            h(
              "button",
              {
                type: "button",
                class:
                  "flex w-full items-center gap-3 rounded-lg border border-border/40 bg-background/40 px-3 py-2 text-left transition-colors hover:border-border/60",
                onClick: function () {
                  if (props.onOpen) props.onOpen(f);
                },
              },
              icons.file("size-5 shrink-0 text-primary"),
              h(
                "div",
                { class: "min-w-0 flex-1" },
                h(
                  "span",
                  { class: "truncate font-mono text-code-md text-foreground" },
                  f.name || f.id
                ),
                f.meta
                  ? h(
                      "span",
                      {
                        class:
                          "flex items-center gap-1 truncate text-body-sm text-muted-foreground",
                      },
                      f.meta
                    )
                  : null
              ),
              f.lines != null
                ? h("span", { class: "shrink-0 font-mono text-code-sm" }, f.lines + " lines")
                : null
            )
          );
        })
      ),
      props.footer
        ? h("div", { class: "mt-3 flex justify-end" }, props.footer)
        : null
    );
  }

  /* ── cron-job-card ────────────────────────────────────────────────── */

  function CronJobCard(props) {
    props = props || {};
    var job = props.job || props;
    return h(
      "div",
      {
        "data-slot": "cron-job-card",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "flex items-start justify-between gap-3" },
        h(
          "div",
          { class: "min-w-0" },
          h("h4", { class: "font-display text-title-md tracking-tight" }, job.name || "cron-job"),
          h(
            "p",
            {
              class:
                "mt-0.5 inline-flex items-center gap-2 font-mono text-code-sm text-muted-foreground",
            },
            icons.calendar("size-3"),
            job.schedule || ""
          )
        ),
        h(
          "button",
          {
            type: "button",
            role: "switch",
            "aria-checked": job.enabled !== false ? "true" : "false",
            class: cn(
              "relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors",
              job.enabled !== false ? "bg-primary" : "bg-muted"
            ),
            onClick: function () {
              if (props.onToggle) props.onToggle(job);
            },
          },
          h("span", {
            class: cn(
              "absolute top-0.5 size-4 rounded-full bg-background transition-all",
              job.enabled !== false ? "left-[1.15rem]" : "left-0.5"
            ),
          })
        )
      ),
      job.prompt
        ? h(
            "p",
            { class: "mt-2 line-clamp-2 rounded-md bg-muted/60 px-3 py-2 font-mono text-code-sm text-foreground" },
            job.prompt
          )
        : null,
      h(
        "div",
        { class: "mt-3 grid grid-cols-2 gap-3 font-mono text-label text-muted-foreground" },
        h("span", null, "Next run", h("span", { class: "block text-foreground" }, job.nextRun || "—")),
        h("span", null, "Last run", h("span", { class: "block text-foreground" }, job.lastRun || "—"))
      ),
      h(
        "div",
        { class: "mt-3 flex items-center justify-end gap-1.5" },
        props.onRun
          ? Button({
              size: "sm",
              variant: "secondary",
              children: [icons.play("size-3.5"), "Run now"],
              onClick: function () {
                props.onRun(job);
              },
            })
          : null
      )
    );
  }

  /* ── diff-viewer ──────────────────────────────────────────────────── */

  function parseUnifiedDiffToHunks(diff) {
    var hunks = [];
    var current = null;
    var oldNumber = 1;
    var newNumber = 1;
    function ensureHunk() {
      if (!current) {
        current = { id: String(hunks.length), lines: [] };
        hunks.push(current);
      }
      return current;
    }
    String(diff)
      .split("\n")
      .forEach(function (line) {
        if (line.indexOf("@@") === 0) {
          current = { id: String(hunks.length), header: line, lines: [] };
          hunks.push(current);
          return;
        }
        if (line.indexOf("+++") === 0 || line.indexOf("---") === 0) {
          ensureHunk().lines.push({ kind: "meta", content: line });
          return;
        }
        if (line.indexOf("+") === 0) {
          ensureHunk().lines.push({ kind: "added", newNumber: newNumber++, content: line.slice(1) });
          return;
        }
        if (line.indexOf("-") === 0) {
          ensureHunk().lines.push({ kind: "removed", oldNumber: oldNumber++, content: line.slice(1) });
          return;
        }
        ensureHunk().lines.push({
          kind: "unchanged",
          oldNumber: oldNumber++,
          newNumber: newNumber++,
          content: line.indexOf(" ") === 0 ? line.slice(1) : line,
        });
      });
    return hunks;
  }

  var DIFF_LINE_BG = {
    added: "bg-success/10",
    removed: "bg-destructive/10",
    unchanged: "",
    meta: "bg-muted/60 text-primary",
  };
  var DIFF_SIGN = { added: "+", removed: "-", unchanged: " ", meta: "@" };

  function DiffViewer(props) {
    props = props || {};
    var hunks =
      props.hunks || (props.diff != null ? parseUnifiedDiffToHunks(props.diff) : []);
    return h(
      "div",
      {
        "data-slot": "diff-viewer",
        class: cn(
          "overflow-hidden rounded-xl border border-border bg-card font-mono",
          props.class
        ),
      },
      h(
        "header",
        {
          class:
            "flex items-center justify-between gap-3 border-border/40 border-b bg-muted/30 px-3 py-2",
        },
        h("span", { class: "truncate text-code-sm text-foreground" }, props.path || ""),
        props.stats
          ? h(
              "span",
              { class: "font-mono text-code-sm" },
              h("span", { class: "text-success" }, "+" + props.stats.added),
              " ",
              h("span", { class: "text-destructive" }, "-" + props.stats.removed)
            )
          : null
      ),
      h(
        "ol",
        { class: "text-code-sm" },
        hunks.map(function (hunk) {
          if (hunk.collapsed) {
            return h(
              "li",
              null,
              h(
                "div",
                { class: "px-3 py-1 text-muted-foreground italic" },
                hunk.lines.length + " unmodified lines"
              )
            );
          }
          return h(
            "li",
            null,
            hunk.header
              ? h("div", { class: "bg-muted/60 px-3 py-1 text-primary" }, hunk.header)
              : null,
            h(
              "table",
              { class: "w-full border-collapse", "aria-label": "Diff hunk for " + (props.path || "") },
              h(
                "tbody",
                null,
                hunk.lines.map(function (line) {
                  var tone =
                    line.kind === "added"
                      ? "text-success"
                      : line.kind === "removed"
                        ? "text-destructive"
                        : line.kind === "meta"
                          ? "text-primary"
                          : "";
                  return h(
                    "tr",
                    { class: DIFF_LINE_BG[line.kind] || "" },
                    h(
                      "td",
                      {
                        class:
                          "select-none px-2 text-right text-muted-foreground/60 tabular-nums",
                      },
                      line.oldNumber != null ? String(line.oldNumber) : ""
                    ),
                    h(
                      "td",
                      {
                        class:
                          "select-none px-2 text-right text-muted-foreground/60 tabular-nums",
                      },
                      line.newNumber != null ? String(line.newNumber) : ""
                    ),
                    h("td", { class: cn("select-none pr-1 pl-2", tone) }, DIFF_SIGN[line.kind] || " "),
                    h("td", { class: cn("w-full whitespace-pre", tone) }, line.content || "")
                  );
                })
              )
            )
          );
        })
      )
    );
  }

  /* ── export-chat-dialog ───────────────────────────────────────────── */

  function ExportChatDialog(props) {
    props = props || {};
    var formats = props.availableFormats || ["markdown", "json", "txt"];
    var selected = formats[0];
    var dlg = h(
      "div",
      {
        "data-slot": "export-chat-dialog",
        class: cn(
          "fixed inset-0 z-50 grid place-items-center bg-background/80 p-4",
          props.class
        ),
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "Export chat",
        style: { display: props.open ? "grid" : "none" },
      },
      h(
        "div",
        { class: "w-full max-w-sm rounded-lg border border-border bg-card p-4 shadow-lg" },
        h(
          "div",
          { class: "flex items-center justify-between" },
          h("h2", { class: "font-semibold text-base" }, "Export chat"),
          h(
            "button",
            {
              type: "button",
              "aria-label": "Close",
              class: "text-muted-foreground hover:text-foreground",
              onClick: function () {
                if (props.onOpenChange) props.onOpenChange(false);
              },
            },
            icons.x("size-4")
          )
        ),
        props.sessionLabel
          ? h("p", { class: "ml-2 text-muted-foreground text-xs" }, props.sessionLabel)
          : null,
        h(
          "div",
          { class: "mt-3 space-y-1.5", role: "radiogroup", "aria-label": "Format" },
          formats.map(function (fmt, idx) {
            var input = h("input", {
              type: "radio",
              name: "theokit-export-format",
              class: "sr-only",
              value: fmt,
              checked: idx === 0 ? "" : null,
            });
            input.addEventListener("change", function () {
              selected = fmt;
            });
            return h(
              "label",
              {
                class: "flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-muted/30",
              },
              input,
              h("span", { class: "font-medium text-sm capitalize" }, fmt),
              h("span", { class: "ml-auto font-mono text-label text-muted-foreground" }, "." + fmt)
            );
          })
        ),
        h(
          "div",
          { class: "mt-4 flex justify-end gap-2" },
          Button({
            variant: "secondary",
            children: "Cancel",
            onClick: function () {
              if (props.onOpenChange) props.onOpenChange(false);
            },
          }),
          Button({
            children: "Export",
            onClick: function () {
              if (props.onExport) props.onExport(selected);
            },
          })
        )
      )
    );
    return dlg;
  }

  /* ── folder-context-card ──────────────────────────────────────────── */

  function FolderContextCard(props) {
    props = props || {};
    var files = props.files || [];
    return h(
      "div",
      {
        "data-slot": "folder-context-card",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "mb-3 flex items-center justify-between" },
        h("h4", { class: "font-display text-title-md tracking-tight" }, props.title || "Workspace"),
        props.onRefresh
          ? h(
              "button",
              {
                type: "button",
                "aria-label": "Refresh",
                class: "text-muted-foreground hover:text-foreground",
                onClick: props.onRefresh,
              },
              icons.rotateCw("w-3.5 h-3.5")
            )
          : null
      ),
      h(
        "ul",
        { class: "grid gap-0.5" },
        files.slice(0, props.maxFiles || 6).map(function (f) {
          return h(
            "li",
            null,
            h(
              "button",
              {
                type: "button",
                class:
                  "flex w-full items-center gap-2 rounded px-2 py-1 text-left font-mono text-code-sm text-muted-foreground hover:bg-muted/40 hover:text-foreground",
                onClick: function () {
                  if (props.onOpenFile) props.onOpenFile(f);
                },
              },
              f.directory ? icons.folder("w-3.5 h-3.5") : icons.file("w-3.5 h-3.5"),
              h("span", { class: "flex-1 truncate text-left" }, f.name),
              f.directory ? null : h("span", { class: "shrink-0" }, f.size || "")
            )
          );
        })
      )
    );
  }

  /* ── folder-selector ──────────────────────────────────────────────── */

  function FolderSelector(props) {
    props = props || {};
    return h(
      "button",
      {
        type: "button",
        "data-slot": "folder-selector",
        class: cn(
          "flex w-full items-center gap-2 rounded-lg border border-border/60 bg-card px-3 py-2 text-left transition-colors hover:border-border",
          props.class
        ),
        onClick: props.onPick,
      },
      icons.folder("size-4 shrink-0 text-muted-foreground"),
      h(
        "span",
        { class: "min-w-0 flex-1 truncate text-left font-mono text-code-sm text-foreground" },
        props.value || props.placeholder || "Choose folder…"
      ),
      icons.chevronRight("size-3 shrink-0 text-muted-foreground")
    );
  }

  /* ── gateway-status-indicator ─────────────────────────────────────── */

  var GATEWAY_META = {
    connected: { label: "Connected", cls: "text-success", icon: "wifi" },
    connecting: { label: "Connecting", cls: "text-warning", icon: "loader" },
    degraded: { label: "Degraded", cls: "text-warning", icon: "wifi" },
    disconnected: { label: "Disconnected", cls: "text-destructive", icon: "wifi-off" },
  };

  function GatewayStatusIndicator(props) {
    props = props || {};
    var meta = GATEWAY_META[props.status] || GATEWAY_META.disconnected;
    var el = h(
      "span",
      {
        "data-slot": "gateway-status-indicator",
        class: cn(
          "inline-flex items-center gap-1.5 font-mono text-label",
          meta.cls,
          props.class
        ),
        role: "status",
      },
      icons[meta.icon](cn("size-3.5", props.status === "connecting" && "animate-spin")),
      h("span", { class: "text-foreground" }, meta.label),
      props.latencyMs != null
        ? h("span", { class: "ml-1 text-muted-foreground" }, props.latencyMs + "ms")
        : null
    );
    if (props["data-testid"]) el.setAttribute("data-testid", props["data-testid"]);
    return el;
  }

  /* ── hook-config ──────────────────────────────────────────────────── */

  function HookConfig(props) {
    props = props || {};
    var hooks = props.hooks || [];
    return h(
      "div",
      {
        "data-slot": "hook-config",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "header",
        {
          class:
            "flex items-baseline justify-between border-border/40 border-b px-4 py-3",
        },
        h(
          "div",
          { class: "flex items-center gap-2" },
          icons.zap("size-4 text-primary"),
          h("h3", { class: "font-display text-title-md tracking-tight" }, "Hooks")
        ),
        props.onAdd
          ? Button({
              size: "sm",
              variant: "secondary",
              children: [icons.plus("size-3.5"), "Add hook"],
              onClick: props.onAdd,
            })
          : null
      ),
      h(
        "div",
        {
          class:
            "grid grid-cols-[140px_140px_1fr_auto] items-center gap-2 border-border/40 border-b p-3",
        },
        h("span", { class: "font-mono text-label text-muted-foreground uppercase" }, "Event"),
        h("span", { class: "font-mono text-label text-muted-foreground uppercase" }, "Matcher"),
        h("span", { class: "font-mono text-label text-muted-foreground uppercase" }, "Command"),
        h("span")
      ),
      hooks.length === 0
        ? h(
            "div",
            { class: "px-4 py-8 text-center font-sans text-body-sm text-muted-foreground" },
            "No hooks configured."
          )
        : h(
            "div",
            { class: "divide-y divide-border/30" },
            hooks.map(function (hk) {
              return h(
                "div",
                {
                  class:
                    "grid grid-cols-[140px_140px_1fr_auto] items-center gap-2 px-4 py-2.5",
                },
                h("span", { class: "font-mono text-code-sm text-primary" }, hk.event || ""),
                h("span", { class: "font-mono text-code-sm text-muted-foreground" }, hk.matcher || "*"),
                h("span", { class: "truncate font-mono text-code-sm text-foreground" }, hk.command || ""),
                props.onRemove
                  ? h(
                      "button",
                      {
                        type: "button",
                        "aria-label": "Remove hook",
                        class:
                          "grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive",
                        onClick: function () {
                          props.onRemove(hk);
                        },
                      },
                      icons.trash("size-3.5")
                    )
                  : null
              );
            })
          )
    );
  }

  /* ── hook-event-log ───────────────────────────────────────────────── */

  function HookEventLog(props) {
    props = props || {};
    var events = props.events || [];
    return h(
      "div",
      {
        "data-slot": "hook-event-log",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "header",
        {
          class: "flex items-baseline justify-between border-border/40 border-b px-4 py-3",
        },
        h("h3", { class: "font-display text-title-md tracking-tight" }, props.title || "Hook events"),
        h("span", { class: "font-mono text-label text-muted-foreground" }, String(events.length))
      ),
      events.length === 0
        ? h(
            "div",
            { class: "px-4 py-8 text-center font-sans text-body-sm text-muted-foreground" },
            "No hook events yet."
          )
        : h(
            "div",
            { class: "divide-y divide-border/30" },
            events.map(function (ev) {
              return h(
                "div",
                { class: "grid grid-cols-[auto_1fr_auto] items-start gap-3 px-4 py-2.5" },
                icons.zap("size-3.5 mt-0.5 text-primary"),
                h(
                  "div",
                  { class: "min-w-0" },
                  h(
                    "p",
                    { class: "flex flex-wrap items-baseline gap-2" },
                    h("span", { class: "font-mono text-code-sm text-primary" }, ev.event || ""),
                    h(
                      "span",
                      { class: "font-mono text-code-sm text-muted-foreground" },
                      ev.matcher || ""
                    )
                  ),
                  ev.output
                    ? h(
                        "pre",
                        {
                          class:
                            "mt-1 max-h-24 overflow-auto rounded-md bg-muted/60 px-2 py-1 font-mono text-code-sm text-muted-foreground",
                        },
                        ev.output
                      )
                    : null
                ),
                h(
                  "span",
                  { class: "font-mono text-label text-muted-foreground tabular-nums" },
                  ev.timestamp || ""
                )
              );
            })
          )
    );
  }

  Object.assign(Tk, {
    kindFromEnvelopeCode: kindFromEnvelopeCode,
    AgentErrorCard: AgentErrorCard,
    AgentEvent: AgentEvent,
    AgentHandoff: AgentHandoff,
    AgentProfile: AgentProfile,
    AgentStartingState: AgentStartingState,
    AgentStreaming: AgentStreaming,
    ApprovalModeSelector: ApprovalModeSelector,
    ArtifactPreview: ArtifactPreview,
    AttachmentChip: AttachmentChip,
    AuditLogEntry: AuditLogEntry,
    AutoCompactNotice: AutoCompactNotice,
    BranchIndicator: BranchIndicator,
    BrowserControls: BrowserControls,
    BuildLogStream: BuildLogStream,
    CapabilityIndicator: CapabilityIndicator,
    ChannelCard: ChannelCard,
    ChatThread: ChatThread,
    CodeReviewPanel: CodeReviewPanel,
    ContextCard: ContextCard,
    ContextWindowBar: ContextWindowBar,
    CostMeter: CostMeter,
    CreatedFilesCard: CreatedFilesCard,
    CronJobCard: CronJobCard,
    DiffViewer: DiffViewer,
    parseUnifiedDiffToHunks: parseUnifiedDiffToHunks,
    ExportChatDialog: ExportChatDialog,
    FolderContextCard: FolderContextCard,
    FolderSelector: FolderSelector,
    GatewayStatusIndicator: GatewayStatusIndicator,
    HookConfig: HookConfig,
    HookEventLog: HookEventLog,
  });
  if (typeof module !== "undefined" && module.exports) module.exports = Tk;
})(typeof window !== "undefined" ? window : globalThis);
