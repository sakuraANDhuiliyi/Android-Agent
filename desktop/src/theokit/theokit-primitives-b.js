/*
 * theokit-primitives-b.js — TheoKit primitives part 2: selectors, cards,
 * charts, terminal, whiteboard and work-log families.
 */
(function (root) {
  "use strict";

  var Tk = root.Theokit = root.Theokit || {};
  var h = Tk.h;
  var cn = Tk.cn;
  var Button = Tk.Button;
  var icons = Tk.icons || {};

  /* ── intent-selector ──────────────────────────────────────────────── */

  function IntentSelector(props) {
    props = props || {};
    var intents = props.intents || [];
    return h(
      "div",
      {
        "data-slot": "intent-selector",
        class: cn("grid gap-1.5 rounded-xl border border-border/40 bg-card p-2", props.class),
      },
      h(
        "p",
        { class: "font-medium text-foreground text-xs leading-snug" },
        props.label || "What do you want to do?"
      ),
      intents.map(function (intent) {
        var active = props.value === intent.id;
        return h(
          "button",
          {
            type: "button",
            "aria-pressed": active ? "true" : "false",
            class: cn(
              "flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors",
              active ? "bg-primary/10" : "hover:bg-muted/40"
            ),
            onClick: function () {
              if (props.onSelect) props.onSelect(intent);
            },
          },
          icons[intent.icon || "target"](
            cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground")
          ),
          h(
            "div",
            { class: "flex flex-1 flex-col" },
            h("span", { class: cn("font-medium", active ? "text-foreground" : "text-foreground/90") }, intent.label),
            intent.description
              ? h("span", { class: "text-label text-muted-foreground" }, intent.description)
              : null
          ),
          active ? icons.check("size-3.5 shrink-0 text-primary") : null
        );
      })
    );
  }

  /* ── lane-board ───────────────────────────────────────────────────── */

  function LaneBoard(props) {
    props = props || {};
    var lanes = props.lanes || [];
    return h(
      "div",
      { "data-slot": "lane-board", class: cn("grid gap-3", props.class) },
      h(
        "div",
        { class: "grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4" },
        lanes.map(function (lane) {
          var items = lane.items || [];
          return h(
            "section",
            { class: "grid auto-rows-max gap-2 rounded-xl border border-border/40 bg-card p-3" },
            h(
              "header",
              { class: "flex items-center justify-between px-1" },
              h("h3", { class: "font-display text-title-sm tracking-tight" }, lane.title || ""),
              h("span", { class: "font-mono text-label text-muted-foreground tabular-nums" }, String(items.length))
            ),
            items.length === 0
              ? h(
                  "div",
                  {
                    class:
                      "rounded-md border border-border/40 border-dashed px-3 py-4 text-center font-sans text-body-sm text-muted-foreground",
                  },
                  lane.emptyLabel || "Empty"
                )
              : h(
                  "ul",
                  { class: "grid gap-2" },
                  items.map(function (item) {
                    return h(
                      "li",
                      null,
                      h(
                        "div",
                        {
                          class:
                            "rounded-lg border border-border/40 bg-background/40 p-3 transition-colors hover:border-border/60",
                        },
                        h(
                          "p",
                          { class: "font-medium text-body-sm text-foreground" },
                          item.title || ""
                        ),
                        item.description
                          ? h("p", { class: "text-body-sm text-muted-foreground" }, item.description)
                          : null,
                        item.meta
                          ? h(
                              "p",
                              { class: "mt-1 font-mono text-label text-muted-foreground" },
                              item.meta
                            )
                          : null
                      )
                    );
                  })
                )
          );
        })
      )
    );
  }

  /* ── mcp-server-card ──────────────────────────────────────────────── */

  function McpServerCard(props) {
    props = props || {};
    var s = props.server || props;
    var connected = s.status === "connected";
    return h(
      "div",
      {
        "data-slot": "mcp-server-card",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "flex items-start justify-between gap-3" },
        h(
          "div",
          { class: "flex min-w-0 items-center gap-2" },
          icons.server("size-4 shrink-0 text-primary"),
          h(
            "div",
            { class: "min-w-0" },
            h("p", { class: "font-medium font-mono text-body-sm text-foreground" }, s.name || ""),
            h("p", { class: "truncate font-mono text-label text-muted-foreground" }, s.url || "")
          )
        ),
        h(
          "span",
          {
            class: cn(
              "shrink-0 rounded-full px-2 py-0.5 font-mono text-label uppercase",
              connected ? "bg-success/10 text-success" : s.status === "error" ? "bg-destructive/10 text-destructive" : "bg-muted text-muted-foreground"
            ),
          },
          s.status || "offline"
        )
      ),
      s.error
        ? h(
            "p",
            { class: "mt-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 font-mono text-code-sm text-warning" },
            s.error
          )
        : null,
      h(
        "div",
        { class: "mt-3 grid gap-1.5" },
        h(
          "span",
          { class: "font-mono text-label-caps text-muted-foreground uppercase tracking-wider" },
          "Tools (" + String((s.tools || []).length) + ")"
        ),
        h(
          "div",
          { class: "flex flex-wrap gap-1.5" },
          (s.tools || []).slice(0, 8).map(function (t) {
            return h(
              "span",
              { class: "inline-flex items-center rounded-md bg-muted px-2 py-0.5 font-mono text-foreground text-label" },
              typeof t === "string" ? t : t.name
            );
          })
        )
      ),
      h(
        "div",
        { class: "mt-3 flex items-center justify-end gap-1.5" },
        props.onToggle
          ? Button({
              size: "sm",
              variant: connected ? "ghost" : "primary",
              children: connected ? "Disconnect" : "Connect",
              onClick: function () {
                props.onToggle(s);
              },
            })
          : null
      )
    );
  }

  /* ── memory-editor ────────────────────────────────────────────────── */

  function MemoryEditor(props) {
    props = props || {};
    var memories = props.memories || [];
    var mode = props.mode || "view";
    return h(
      "div",
      {
        "data-slot": "memory-editor",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "header",
        { class: "flex items-center justify-between gap-3 border-border/40 border-b px-4 py-3" },
        h(
          "div",
          { class: "flex items-center gap-2" },
          icons.brain("size-4 text-primary"),
          h("h3", { class: "font-display text-title-md tracking-tight" }, "Memory")
        ),
        h(
          "div",
          { class: "inline-flex items-center rounded-lg border border-border/60 bg-muted p-0.5" },
          ["view", "edit"].map(function (m) {
            return h(
              "button",
              {
                type: "button",
                class: cn(
                  "rounded-md px-2.5 py-1 font-mono text-label transition-colors",
                  mode === m ? "bg-card text-foreground shadow-sm" : "text-muted-foreground"
                ),
                onClick: function () {
                  if (props.onModeChange) props.onModeChange(m);
                },
              },
              m
            );
          })
        )
      ),
      memories.length === 0
        ? h(
            "div",
            { class: "px-4 py-8 text-center font-sans text-label text-muted-foreground italic" },
            "No memories stored yet."
          )
        : h(
            "div",
            { class: "divide-y divide-border/30" },
            memories.map(function (m) {
              return h(
                "div",
                {
                  class:
                    "flex items-center justify-between gap-3 border-border/40 border-b bg-muted/30 px-4 py-2",
                },
                h(
                  "div",
                  { class: "flex min-w-0 items-center gap-2" },
                  icons.hash("size-3 shrink-0 text-muted-foreground"),
                  h(
                    "span",
                    { class: "truncate font-mono text-code-sm text-foreground" },
                    m.key || m.id
                  )
                ),
                h(
                  "span",
                  { class: "shrink-0 font-mono text-label text-muted-foreground" },
                  m.value || ""
                ),
                props.onDelete
                  ? h(
                      "button",
                      {
                        type: "button",
                        "aria-label": "Delete memory",
                        class:
                          "grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive",
                        onClick: function () {
                          props.onDelete(m);
                        },
                      },
                      icons.trash("size-3")
                    )
                  : null
              );
            })
          )
    );
  }

  /* ── mention-menu ─────────────────────────────────────────────────── */

  var MENTION_DEFAULT_TITLE = { "/": "Commands", "@": "Files", "#": "Memories" };
  var MENTION_DEFAULT_ICON = { "/": "slash", "@": "at-sign", "#": "hash" };

  function MentionMenu(props) {
    props = props || {};
    var items = props.items || [];
    var activeIndex = 0;
    var open = !!props.open;
    var trigger = props.trigger || "/";

    var menu = h(
      "div",
      {
        "data-slot": "mention-menu",
        role: "menu",
        "aria-orientation": "vertical",
        "aria-label": MENTION_DEFAULT_TITLE[trigger] || "Mention menu",
        tabindex: "-1",
        class: cn(
          "absolute bottom-full left-0 z-40 mb-2 w-[22rem] max-w-full",
          "overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-md",
          props.class
        ),
        "data-state": "open",
        style: { display: open ? "" : "none" },
      },
      h(
        "div",
        {
          role: "presentation",
          class:
            "flex items-center justify-between gap-2 border-border/40 border-b bg-muted/30 px-3 py-2",
        },
        h(
          "span",
          {
            class:
              "inline-flex items-center gap-1.5 font-mono text-label text-muted-foreground uppercase tracking-wider",
          },
          icons[MENTION_DEFAULT_ICON[trigger] || "slash"]("size-3"),
          props.title || MENTION_DEFAULT_TITLE[trigger] || "Mentions"
        ),
        h("span", { class: "font-mono text-label text-muted-foreground tabular-nums" }, String(items.length))
      )
    );

    var listWrap = h("div", { role: "presentation" });
    menu.appendChild(listWrap);

    function renderList() {
      Tk.append(listWrap, []);
      while (listWrap.firstChild) listWrap.removeChild(listWrap.firstChild);
      if (items.length === 0) {
        listWrap.appendChild(
          h(
            "div",
            { role: "presentation", class: "px-3 py-4 text-body-sm text-muted-foreground" },
            props.emptyLabel || "No matches"
          )
        );
        return;
      }
      var ul = h("ul", { role: "presentation", class: "max-h-[18rem] overflow-y-auto py-1" });
      items.forEach(function (item, idx) {
        var btn = h(
          "button",
          {
            type: "button",
            role: "menuitem",
            class: cn(
              "flex w-full items-start gap-3 px-3 py-2 text-left",
              "transition-colors duration-base ease-out-soft",
              idx === activeIndex ? "bg-muted" : "hover:bg-muted/60"
            ),
            "data-active": idx === activeIndex ? "true" : null,
          },
          item.icon
            ? h(
                "span",
                { class: "mt-0.5 size-4 shrink-0 text-muted-foreground" },
                icons[item.icon]("size-4")
              )
            : null,
          h(
            "span",
            { class: "grid min-w-0 flex-1 gap-0.5" },
            h("span", { class: "truncate font-medium font-mono text-code-md" }, item.label),
            item.description
              ? h(
                  "span",
                  { class: "truncate font-sans text-label text-muted-foreground" },
                  item.description
                )
              : null
          )
        );
        btn.addEventListener("mouseenter", function () {
          activeIndex = idx;
          renderList();
        });
        btn.addEventListener("mousedown", function (e) {
          e.preventDefault();
        });
        btn.addEventListener("click", function () {
          if (props.onSelect) props.onSelect(item);
        });
        ul.appendChild(h("li", { role: "presentation" }, btn));
      });
      listWrap.appendChild(ul);
    }
    renderList();

    var onKey = function (e) {
      if (!open) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = Math.min(items.length - 1, activeIndex + 1);
        renderList();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(0, activeIndex - 1);
        renderList();
      } else if (e.key === "Enter") {
        if (items.length === 0) return;
        e.preventDefault();
        var item = items[activeIndex];
        if (item && props.onSelect) props.onSelect(item);
      } else if (e.key === "Escape") {
        e.preventDefault();
        if (props.onClose) props.onClose();
      }
    };
    root.document.addEventListener("keydown", onKey, true);

    return menu;
  }

  /* ── model-card ───────────────────────────────────────────────────── */

  function ModelCard(props) {
    props = props || {};
    var m = props.model || props;
    return h(
      "div",
      {
        "data-slot": "model-card",
        class: cn(
          "rounded-xl border border-border/40 bg-card p-4 transition-colors",
          props.onClick && "cursor-pointer hover:border-border/60",
          props.class
        ),
        onClick: function () {
          if (props.onClick) props.onClick(m);
        },
      },
      h(
        "div",
        { class: "flex items-start justify-between gap-3" },
        h(
          "div",
          { class: "min-w-0" },
          h("h4", { class: "font-display text-title-md tracking-tight" }, m.name || m.id),
          h(
            "span",
            {
              class:
                "font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
            },
            m.vendor || ""
          )
        ),
        m.badge || m.recommended
          ? h(
              "span",
              {
                class:
                  "inline-flex items-center gap-1 rounded-full bg-accent/15 px-2 py-0.5 font-mono text-accent text-label uppercase",
              },
              m.badge || "Recommended"
            )
          : null
      ),
      m.description
        ? h("p", { class: "mt-1 text-body-sm text-muted-foreground" }, m.description)
        : null,
      h(
        "dl",
        { class: "mt-3 grid grid-cols-2 gap-3 border-border/30 border-t pt-3 font-mono" },
        h(
          "div",
          { class: "grid gap-0.5" },
          h("dt", { class: "text-label-caps text-muted-foreground uppercase tracking-wider" }, "Context"),
          h("dd", { class: "font-medium text-body-sm tabular-nums text-foreground" }, (m.contextWindow || 0).toLocaleString())
        ),
        h(
          "div",
          { class: "grid gap-0.5" },
          h("dt", { class: "text-label-caps text-muted-foreground uppercase tracking-wider" }, "Input cost"),
          h("dd", { class: "font-medium text-body-sm tabular-nums text-foreground" }, "$" + (m.inputCost ?? "—") + "/M")
        )
      ),
      (m.capabilities || []).length
        ? h(
            "div",
            { class: "mt-3 flex flex-wrap gap-1.5" },
            m.capabilities.map(function (c) {
              return h(
                "span",
                {
                  class:
                    "inline-flex items-center gap-1 font-mono text-label text-muted-foreground",
                },
                icons.check("size-3"),
                c
              );
            })
          )
        : null
    );
  }

  /* ── model-effort-picker ──────────────────────────────────────────── */

  function ModelEffortPicker(props) {
    props = props || {};
    var levels = props.options || [
      { value: "low", label: "Low", hint: "Fastest, shallow" },
      { value: "medium", label: "Medium", hint: "Balanced" },
      { value: "high", label: "High", hint: "Deep reasoning" },
    ];
    var value = props.value || levels[1].value;
    return h(
      "div",
      {
        "data-slot": "model-effort-picker",
        class: cn("grid gap-1", props.class),
        role: "radiogroup",
        "aria-label": "Model effort",
      },
      levels.map(function (lv) {
        var active = lv.value === value;
        return h(
          "button",
          {
            type: "button",
            role: "radio",
            "aria-checked": active ? "true" : "false",
            class: cn(
              "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors",
              active ? "bg-muted" : "hover:bg-muted/40"
            ),
            onClick: function () {
              if (props.onValueChange) props.onValueChange(lv.value);
            },
          },
          h(
            "span",
            {
              class: cn(
                "flex size-4 items-center justify-center rounded",
                active ? "bg-primary/15 text-primary" : "text-muted-foreground/60"
              ),
            },
            active ? icons.check("size-3") : icons.circle("size-3")
          ),
          h(
            "span",
            { class: "flex min-w-0 flex-col gap-0.5" },
            h("span", { class: cn("font-medium", active ? "text-foreground" : "text-foreground/80") }, lv.label),
            h("span", { class: "text-muted-foreground text-xs" }, lv.hint || "")
          )
        );
      })
    );
  }

  /* ── model-selector ───────────────────────────────────────────────── */

  function ModelSelector(props) {
    props = props || {};
    var models = props.models || [];
    var current =
      models.filter(function (m) {
        return m.id === props.value;
      })[0] || models[0] || { id: "", name: "No model" };
    var open = false;

    var trigger = h(
      "button",
      {
        type: "button",
        "aria-haspopup": "listbox",
        "aria-expanded": "false",
        "data-slot": "model-selector",
        class: cn(
          "flex items-center gap-2 rounded-lg border border-border/60 bg-card px-2.5 py-1.5 transition-colors hover:border-border",
          props.class
        ),
        onClick: function () {
          open = !open;
          popover.style.display = open ? "" : "none";
          trigger.setAttribute("aria-expanded", String(open));
        },
      },
      h("span", { class: "size-1.5 rounded-full bg-primary" }),
      h(
        "span",
        { class: "flex flex-col" },
        h("span", { class: "font-medium text-body-sm text-foreground" }, current.name || current.id),
        current.vendor
          ? h(
              "span",
              { class: "font-mono text-label text-muted-foreground" },
              current.vendor
            )
          : null
      ),
      icons.chevronDown("size-3 text-muted-foreground")
    );

    var popover = h(
      "div",
      {
        class:
          "absolute z-40 mt-1 w-64 overflow-hidden rounded-lg border border-border bg-popover shadow-md",
        style: { display: "none" },
        role: "listbox",
      },
      h(
        "ul",
        { class: "max-h-72 overflow-y-auto py-1" },
        models.map(function (m) {
          return h(
            "li",
            null,
            h(
              "button",
              {
                type: "button",
                role: "option",
                "aria-selected": m.id === current.id ? "true" : "false",
                class: cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors",
                  m.id === current.id ? "bg-muted" : "hover:bg-muted/60"
                ),
                onClick: function () {
                  open = false;
                  popover.style.display = "none";
                  trigger.setAttribute("aria-expanded", "false");
                  if (props.onSelect) props.onSelect(m.id, m);
                },
              },
              h(
                "span",
                { class: "flex flex-col" },
                h("span", { class: "font-medium text-body-sm text-foreground" }, m.name || m.id),
                h("span", { class: "font-mono text-label text-muted-foreground" }, m.vendor || "")
              ),
              m.id === current.id
                ? icons.check("size-3.5 ml-auto text-primary")
                : null
            )
          );
        })
      )
    );

    var wrap = h("div", { class: "relative" }, trigger, popover);
    return wrap;
  }

  /* ── permission-matrix ────────────────────────────────────────────── */

  function PermissionMatrix(props) {
    props = props || {};
    var rules = props.rules || [];
    return h(
      "form",
      {
        "data-slot": "permission-matrix",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
        onSubmit: function (e) {
          e.preventDefault();
          if (props.onSave) props.onSave(rules);
        },
      },
      h(
        "header",
        { class: "flex items-baseline justify-between border-border/40 border-b px-4 py-3" },
        h("h3", { class: "font-display text-title-md tracking-tight" }, "Permissions"),
        h("span", { class: "font-mono text-label text-muted-foreground" }, String(rules.length) + " rules")
      ),
      h(
        "div",
        { class: "grid grid-cols-[1fr_2fr_auto_auto] gap-2 border-border/40 border-b p-3" },
        h("input", {
          type: "text",
          placeholder: "tool pattern",
          "aria-label": "Tool pattern",
          class: "h-9 rounded-md border border-input bg-card px-2 font-mono text-code-sm",
        }),
        h("input", {
          type: "text",
          placeholder: "path / argument matcher",
          "aria-label": "Matcher",
          class: "h-9 rounded-md border border-input bg-card px-2 font-mono text-code-sm",
        }),
        h("select", {
          "aria-label": "Permission",
          class: "h-9 rounded-md border border-input bg-card px-2 font-mono text-code-sm uppercase",
        }),
        props.onAdd
          ? Button({
              size: "sm",
              variant: "secondary",
              children: [icons.plus("size-3.5"), "Add"],
              onClick: function () {
                if (props.onAdd) props.onAdd();
              },
            })
          : null
      ),
      rules.length === 0
        ? h(
            "div",
            { class: "px-4 py-8 text-center font-sans text-body-sm text-muted-foreground" },
            "No permission rules."
          )
        : h(
            "div",
            { class: "divide-y divide-border/30" },
            rules.map(function (r) {
              var tone =
                r.permission === "allow"
                  ? "text-success"
                  : r.permission === "deny"
                    ? "text-destructive"
                    : "text-warning";
              return h(
                "div",
                { class: "grid grid-cols-[1fr_2fr_auto_auto] items-center gap-3 px-4 py-2.5" },
                h("span", { class: "truncate font-mono text-code-sm text-foreground" }, r.tool || "*"),
                h("span", { class: "truncate font-mono text-code-sm text-muted-foreground" }, r.matcher || "*"),
                h(
                  "span",
                  { class: cn("font-mono text-label uppercase", tone) },
                  r.permission || "ask"
                ),
                props.onRemove
                  ? h(
                      "button",
                      {
                        type: "button",
                        "aria-label": "Remove rule",
                        class:
                          "grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive",
                        onClick: function () {
                          props.onRemove(r);
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

  /* ── progress-checklist ───────────────────────────────────────────── */

  var STEP_STATUS_ICON = {
    pending: "circle",
    running: "loader",
    done: "check-circle",
    failed: "alert-circle",
    skipped: "circle-dot",
  };
  var STEP_STATUS_COLOR = {
    pending: "text-muted-foreground",
    running: "text-primary",
    done: "text-success",
    failed: "text-destructive",
    skipped: "text-muted-foreground",
  };

  function ProgressChecklist(props) {
    props = props || {};
    var steps = props.steps || [];
    var done = steps.filter(function (s) {
      return s.status === "done";
    }).length;
    return h(
      "div",
      {
        "data-slot": "progress-checklist",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "mb-3 flex items-center justify-between" },
        h("h4", { class: "font-display text-title-md tracking-tight" }, props.title || "Progress"),
        h(
          "span",
          { class: "font-mono text-label text-muted-foreground tabular-nums" },
          done + " / " + steps.length
        )
      ),
      h(
        "ol",
        { class: "grid gap-3" },
        steps.map(function (s) {
          return h(
            "li",
            { class: "grid grid-cols-[auto_1fr] items-start gap-3" },
            h(
              "span",
              { class: cn("mt-0.5", STEP_STATUS_COLOR[s.status] || "text-muted-foreground") },
              icons[STEP_STATUS_ICON[s.status] || "circle"](
                cn("size-4", s.status === "running" && "animate-spin")
              )
            ),
            h(
              "div",
              { class: "min-w-0" },
              h(
                "span",
                {
                  class: cn(
                    "text-body-sm",
                    s.status === "done" ? "text-muted-foreground line-through" : "text-foreground"
                  ),
                },
                s.label || ""
              ),
              s.detail
                ? h("p", { class: "text-label text-muted-foreground" }, s.detail)
                : null,
              s.progress != null
                ? h(
                    "div",
                    { class: "mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted" },
                    h("div", {
                      class: "h-full bg-primary transition-[width] duration-base ease-out-soft",
                      style: { width: s.progress + "%" },
                    })
                  )
                : null
            )
          );
        })
      )
    );
  }

  /* ── project-switcher ─────────────────────────────────────────────── */

  function ProjectSwitcher(props) {
    props = props || {};
    var p = props.project || {};
    return h(
      "button",
      {
        type: "button",
        "data-slot": "project-switcher",
        class: cn(
          "flex w-full items-center gap-3 rounded-lg border border-transparent p-2 text-left transition-colors hover:bg-muted/40",
          props.class
        ),
        onClick: props.onSwitch,
        "aria-label": "Switch project",
      },
      h(
        "span",
        {
          class:
            "grid size-8 shrink-0 place-items-center rounded-lg bg-primary font-black font-display text-primary-foreground",
        },
        (p.name || "?").slice(0, 1).toUpperCase()
      ),
      h(
        "span",
        { class: "grid min-w-0 flex-1 text-left" },
        h(
          "span",
          { class: "flex min-w-0 items-center gap-1.5" },
          h("span", { class: "truncate font-display text-title-md leading-none" }, p.name || "No project"),
          icons.chevronDown("size-3 shrink-0 text-muted-foreground")
        ),
        h(
          "span",
          {
            class:
              "mt-1 inline-flex items-center gap-1 truncate font-mono text-label text-muted-foreground",
          },
          icons.folder("size-3 shrink-0"),
          p.path || ""
        )
      )
    );
  }

  /* ── quick-action-chips ───────────────────────────────────────────── */

  function QuickActionChips(props) {
    props = props || {};
    var actions = props.actions || [];
    return h(
      "div",
      {
        "data-slot": "quick-action-chips",
        class: cn("flex flex-wrap gap-1.5", props.class),
      },
      actions.map(function (a) {
        return h(
          "button",
          {
            type: "button",
            class: cn(
              "inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-card px-3 py-1.5 text-body-sm text-muted-foreground transition-colors hover:border-border hover:text-foreground",
              a.primary && "border-primary/40 bg-primary/10 text-primary hover:bg-primary/15"
            ),
            onClick: function () {
              if (props.onAction) props.onAction(a);
              else if (a.onClick) a.onClick();
            },
          },
          a.icon ? icons[a.icon]("size-4") : null,
          a.label || ""
        );
      })
    );
  }

  /* ── recent-folders-list ──────────────────────────────────────────── */

  function RecentFoldersList(props) {
    props = props || {};
    var folders = props.folders || [];
    return h(
      "div",
      {
        "data-slot": "recent-folders-list",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "div",
        {
          class:
            "border-border/40 border-b px-3 py-2 font-sans text-label-caps text-muted-foreground uppercase tracking-wider",
        },
        props.title || "Recent"
      ),
      h(
        "ul",
        { class: "divide-y divide-border/30" },
        folders.map(function (f) {
          return h(
            "li",
            null,
            h(
              "button",
              {
                type: "button",
                class:
                  "flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-muted/40",
                onClick: function () {
                  if (props.onOpen) props.onOpen(f);
                },
              },
              icons.folder("size-3.5 shrink-0 text-muted-foreground"),
              h(
                "span",
                { class: "min-w-0 flex-1" },
                h("span", { class: "truncate font-medium text-body-sm" }, f.name || f.path),
                h(
                  "span",
                  { class: "block truncate font-mono text-label text-muted-foreground" },
                  f.path || ""
                )
              ),
              f.timestamp
                ? h(
                    "span",
                    { class: "shrink-0 font-mono text-label text-muted-foreground" },
                    f.timestamp
                  )
                : null
            )
          );
        })
      )
    );
  }

  /* ── rule-card ────────────────────────────────────────────────────── */

  function RuleCard(props) {
    props = props || {};
    var r = props.rule || props;
    return h(
      "div",
      {
        "data-slot": "rule-card",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "flex items-start gap-2" },
        h(
          "h4",
          { class: "flex-1 truncate font-display text-foreground text-title-md tracking-tight" },
          r.name || ""
        ),
        props.onDelete
          ? h(
              "button",
              {
                type: "button",
                "aria-label": "Delete rule",
                class:
                  "grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive",
                onClick: function () {
                  props.onDelete(r);
                },
              },
              icons.trash("size-3.5")
            )
          : null
      ),
      r.description
        ? h("p", { class: "mt-1 line-clamp-2 text-body-sm text-muted-foreground" }, r.description)
        : null,
      h(
        "div",
        { class: "mt-3 flex items-center justify-between gap-2" },
        h(
          "div",
          { class: "flex flex-wrap items-center gap-1" },
          (r.tags || []).map(function (t) {
            return h(
              "span",
              { class: "rounded bg-muted px-1.5 py-0.5 font-mono text-label text-muted-foreground" },
              t
            );
          })
        ),
        h(
          "span",
          { class: "font-mono text-label text-muted-foreground tabular-nums" },
          r.scope || "always"
        )
      )
    );
  }

  /* ── run-stats ────────────────────────────────────────────────────── */

  function RunStats(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "run-stats",
        class: cn(
          "flex flex-wrap items-center gap-3 font-mono text-code-sm text-muted-foreground",
          props.class
        ),
      },
      props.duration
        ? h(
            "span",
            { class: "inline-flex items-center gap-1.5" },
            icons.clock("size-3"),
            " " + props.duration
          )
        : null,
      props.tokens
        ? h(
            "span",
            { class: "inline-flex items-center gap-1.5" },
            icons.coins ? icons.coins("size-3") : icons.hash("size-3"),
            " " + props.tokens + " tokens"
          )
        : null,
      props.filesChanged !== undefined
        ? h(
            "span",
            { class: "inline-flex items-center gap-1.5" },
            icons.fileEdit ? icons.fileEdit("size-3") : icons.fileText("size-3"),
            " " + props.filesChanged + " files"
          )
        : null
    );
  }

  /* ── run-status-pill ──────────────────────────────────────────────── */

  var RUN_STATUS_META = {
    queued: { label: "Queued", cls: "bg-muted text-muted-foreground", dot: "bg-muted-foreground" },
    running: { label: "Running", cls: "bg-primary/15 text-primary", dot: "bg-primary" },
    paused: { label: "Paused", cls: "bg-warning/15 text-warning", dot: "bg-warning" },
    completed: { label: "Completed", cls: "bg-success/10 text-success", dot: "bg-success" },
    failed: { label: "Failed", cls: "bg-destructive/10 text-destructive", dot: "bg-destructive" },
    canceled: { label: "Canceled", cls: "bg-muted text-muted-foreground", dot: "bg-muted-foreground" },
  };

  function RunStatusPill(props) {
    props = props || {};
    var meta = RUN_STATUS_META[props.status] || RUN_STATUS_META.queued;
    var el = h(
      "span",
      {
        "data-slot": "run-status-pill",
        class: cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-label uppercase tracking-wider",
          meta.cls,
          props.class
        ),
        role: "status",
      },
      h("span", { class: cn("size-2 rounded-full", meta.dot) }),
      props.detail || meta.label
    );
    if (props["data-testid"]) el.setAttribute("data-testid", props["data-testid"]);
    return el;
  }

  /* ── running-tasks-panel ──────────────────────────────────────────── */

  function RunningTasksPanel(props) {
    props = props || {};
    var tasks = props.tasks || [];
    if (tasks.length === 0) return h("div", { "data-slot": "running-tasks-panel" });
    return h(
      "div",
      { "data-slot": "running-tasks-panel", class: cn("mt-4", props.class) },
      h(
        "p",
        {
          class:
            "mb-2 font-sans text-label-caps text-muted-foreground uppercase tracking-wider",
        },
        props.title || "Running tasks"
      ),
      h(
        "ul",
        { class: "grid gap-1" },
        tasks.map(function (t) {
          return h(
            "li",
            null,
            h(
              "button",
              {
                type: "button",
                class: "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-body-sm transition-colors hover:bg-muted/40",
                onClick: function () {
                  if (props.onOpen) props.onOpen(t);
                },
              },
              t.status === "failed"
                ? h("span", { class: "size-2 rounded-full bg-destructive" })
                : t.status === "running"
                  ? icons.loader("size-3.5 animate-spin text-primary")
                  : icons.check("size-3.5 text-success"),
              h("span", { class: "flex-1 truncate text-foreground" }, t.title || t.id),
              h(
                "span",
                { class: "font-mono text-label text-muted-foreground uppercase" },
                t.status || "running"
              )
            )
          );
        })
      )
    );
  }

  /* ── session-list-item ────────────────────────────────────────────── */

  function SessionListItem(props) {
    props = props || {};
    var s = props.session || props;
    return h(
      "button",
      {
        type: "button",
        "data-slot": "session-list-item",
        "aria-current": props.active ? "true" : null,
        class: cn(
          "flex w-full items-center gap-2.5 rounded-lg border border-transparent px-3 py-2 text-left transition-colors",
          props.active ? "border-border/40 bg-muted/40" : "hover:bg-muted/30",
          props.class
        ),
        onClick: function () {
          if (props.onClick) props.onClick(s);
        },
      },
      h(
        "span",
        { class: cn("shrink-0", props.active ? "text-primary" : "text-muted-foreground") },
        icons[s.icon || "messageSquare"]("size-3.5 shrink-0")
      ),
      h(
        "span",
        { class: "grid min-w-0" },
        h("span", { class: "truncate font-medium text-body-sm text-foreground" }, s.title || "Session"),
        h(
          "span",
          {
            class:
              "mt-0.5 flex items-center gap-1.5 font-mono text-label text-muted-foreground",
          },
          h("span", { class: "truncate" }, s.subtitle || s.updatedAt || "")
        )
      )
    );
  }

  /* ── session-timeline ─────────────────────────────────────────────── */

  function SessionTimeline(props) {
    props = props || {};
    var events = props.events || [];
    return h(
      "div",
      {
        "data-slot": "session-timeline",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "header",
        { class: "flex items-baseline justify-between border-border/40 border-b px-4 py-3" },
        h("h3", { class: "font-display text-title-md tracking-tight" }, props.title || "Timeline"),
        h("span", { class: "font-mono text-label text-muted-foreground" }, String(events.length) + " events")
      ),
      events.length === 0
        ? h(
            "div",
            { class: "px-4 py-8 text-center font-sans text-body-sm text-muted-foreground" },
            "No events yet."
          )
        : h(
            "ol",
            { class: "divide-y divide-border/30" },
            events.map(function (ev) {
              return h(
                "li",
                null,
                h(
                  "button",
                  {
                    type: "button",
                    class:
                      "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/30",
                    onClick: function () {
                      if (props.onSelect) props.onSelect(ev);
                    },
                  },
                  h(
                    "span",
                    { class: "mt-0.5 shrink-0 text-muted-foreground" },
                    icons[ev.icon || "activity"]("size-4 shrink-0")
                  ),
                  h(
                    "div",
                    { class: "min-w-0" },
                    h(
                      "p",
                      { class: "flex flex-wrap items-center gap-2" },
                      h(
                        "span",
                        { class: "truncate font-medium text-body-sm text-foreground" },
                        ev.label || ""
                      )
                    ),
                    h(
                      "p",
                      {
                        class:
                          "mt-1 flex flex-wrap items-center gap-3 font-mono text-label text-muted-foreground",
                      },
                      h("span", { class: "inline-flex items-center gap-1" }, icons.clock("size-3"), ev.timestamp || ""),
                      ev.meta
                        ? h("span", { class: "inline-flex items-center gap-1 tabular-nums" }, ev.meta)
                        : null
                    )
                  )
                )
              );
            })
          )
    );
  }

  /* ── skill-card ───────────────────────────────────────────────────── */

  var SKILL_SOURCE = {
    builtin: { label: "Built-in", icon: "sparkles", tone: "text-primary" },
    project: { label: "Project", icon: "bookOpen", tone: "text-accent" },
    user: { label: "User", icon: "user", tone: "text-info" },
    plugin: { label: "Plugin", icon: "users", tone: "text-muted-foreground" },
  };

  function SkillCard(props) {
    props = props || {};
    var s = props.skill || props;
    var cfg = SKILL_SOURCE[s.source] || SKILL_SOURCE.user;
    var enabled = (s.state || "enabled") === "enabled";
    return h(
      "article",
      {
        "data-slot": "skill-card",
        class: cn(
          "grid gap-3 rounded-xl border border-border bg-card p-4",
          !enabled && "opacity-60",
          props.class
        ),
        onClick: props.onClick,
      },
      h(
        "header",
        { class: "flex items-start justify-between gap-3" },
        h(
          "div",
          { class: "flex items-center gap-2" },
          h(
            "span",
            { class: cn("grid size-8 place-items-center rounded-md bg-muted", cfg.tone) },
            icons[cfg.icon]("size-4")
          ),
          h(
            "div",
            { class: "grid" },
            h(
              "h4",
              { class: "font-medium font-mono text-body-sm text-foreground" },
              s.name || ""
            ),
            h(
              "span",
              {
                class:
                  "font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
              },
              cfg.label
            )
          )
        ),
        props.onToggle
          ? h(
              "button",
              {
                type: "button",
                "aria-pressed": String(enabled),
                class: cn(
                  "inline-flex items-center rounded-full border px-2.5 py-0.5",
                  "font-mono text-label uppercase tracking-wider transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  enabled
                    ? "border-success/40 bg-success/15 text-success"
                    : "border-border/40 bg-muted text-muted-foreground"
                ),
                onClick: function (ev) {
                  ev.stopPropagation();
                  props.onToggle(s.id, enabled ? "disabled" : "enabled");
                },
              },
              enabled ? "Enabled" : "Disabled"
            )
          : null
      ),
      s.description
        ? h("p", { class: "text-body-sm text-muted-foreground" }, s.description)
        : null,
      s.allowedTools && s.allowedTools.length
        ? h(
            "div",
            { class: "flex flex-wrap gap-1.5" },
            h(
              "span",
              { class: "font-mono text-label text-muted-foreground uppercase tracking-wider" },
              "tools:"
            ),
            s.allowedTools.map(function (tool) {
              return h(
                "span",
                {
                  class:
                    "inline-flex items-center rounded-md bg-muted px-2 py-0.5 font-mono text-foreground text-label",
                },
                tool
              );
            })
          )
        : null,
      s.triggers && s.triggers.length
        ? h(
            "div",
            { class: "flex flex-wrap gap-1.5" },
            h(
              "span",
              { class: "font-mono text-label text-muted-foreground uppercase tracking-wider" },
              "triggers:"
            ),
            s.triggers.map(function (trigger) {
              return h(
                "span",
                {
                  class:
                    "inline-flex items-center rounded-md bg-primary/10 px-2 py-0.5 font-mono text-label text-primary",
                },
                trigger
              );
            })
          )
        : null
    );
  }

  /* ── slide ────────────────────────────────────────────────────────── */

  function renderSlideMarkdown(md) {
    var lines = String(md || "").split("\n");
    var out = [];
    var bullets = null;
    function flush() {
      if (bullets && bullets.length) {
        out.push(h("ul", { class: "theo-slide-list" }, bullets));
        bullets = null;
      }
    }
    lines.forEach(function (raw) {
      var line = raw.trimEnd();
      if (!line.trim()) {
        flush();
        return;
      }
      if (/^---+$/.test(line.trim())) {
        flush();
        return;
      }
      var h1 = line.match(/^#\s+(.*)$/);
      if (h1) {
        flush();
        out.push(h("h1", { class: "theo-slide-title" }, h1[1]));
        return;
      }
      var h2 = line.match(/^##\s+(.*)$/);
      if (h2) {
        flush();
        out.push(h("h2", { class: "theo-slide-subtitle" }, h2[1]));
        return;
      }
      var bullet = line.match(/^[-*+]\s+(.*)$/);
      if (bullet) {
        if (!bullets) bullets = [];
        bullets.push(h("li", null, bullet[1]));
        return;
      }
      flush();
      out.push(h("p", { class: "theo-slide-text" }, line));
    });
    flush();
    return out;
  }

  function Slide(props) {
    props = props || {};
    var ratio = props.aspectRatio || "16:9";
    var dims = ratio === "4:3" ? [1024, 768] : [1280, 720];
    return h(
      "div",
      {
        "data-slot": "slide",
        class: cn("theo-slide", "overflow-hidden rounded-xl border border-border bg-card", props.class),
        "aria-label": props["aria-label"] || "Slide",
        style: { aspectRatio: dims[0] + " / " + dims[1] },
      },
      h(
        "div",
        { class: "theo-slide-body grid h-full grid-rows-[auto_1fr_auto] p-8" },
        h("header", { class: "theo-slide-header" }, props.header),
        h("div", { class: "theo-slide-content grid content-center gap-3" }, renderSlideMarkdown(props.markdown || "")),
        h("footer", { class: "theo-slide-footer" }, props.footer)
      )
    );
  }

  /* ── steps-rail ───────────────────────────────────────────────────── */

  function StepsRail(props) {
    props = props || {};
    var steps = props.steps || [];
    var current = props.currentStep != null ? props.currentStep : 0;
    return h(
      "div",
      {
        "data-slot": "steps-rail",
        class: cn("relative grid gap-3 pl-1", props.class),
      },
      steps.map(function (s, i) {
        var state = i < current ? "done" : i === current ? "current" : "todo";
        return h(
          "div",
          { class: "grid grid-cols-[auto_1fr] items-start gap-3" },
          h(
            "div",
            { class: "relative z-10 grid place-items-center" },
            h(
              "span",
              {
                class: cn(
                  "grid size-6 place-items-center rounded-full border font-mono text-label",
                  state === "done" && "border-primary bg-primary text-primary-foreground",
                  state === "current" && "border-primary bg-primary/15 text-primary",
                  state === "todo" && "border-border bg-card text-muted-foreground"
                ),
              },
              state === "done" ? "" : String(i + 1)
            )
          ),
          h(
            "div",
            { class: "min-w-0" },
            h(
              "p",
              {
                class: cn(
                  "font-mono text-label-caps uppercase tracking-wider",
                  state === "current" ? "text-foreground" : "text-muted-foreground"
                ),
              },
              s.label || ""
            ),
            s.description
              ? h("p", { class: "text-body-sm text-muted-foreground" }, s.description)
              : null
          )
        );
      })
    );
  }

  /* ── sub-agent-dispatch ───────────────────────────────────────────── */

  function SubAgentDispatch(props) {
    props = props || {};
    var status = props.status || "running";
    return h(
      "div",
      {
        "data-slot": "sub-agent-dispatch",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "flex items-start justify-between gap-3" },
        h(
          "div",
          { class: "flex min-w-0 items-center gap-2" },
          icons.bot("size-4 shrink-0 text-primary"),
          h(
            "span",
            { class: "font-medium font-mono text-code-sm text-foreground" },
            props.agent || "sub-agent"
          )
        ),
        props.timestamp
          ? h(
              "span",
              { class: "font-mono text-label text-muted-foreground tabular-nums" },
              props.timestamp
            )
          : null
      ),
      h(
        "p",
        { class: "mt-2 text-body-sm text-foreground" },
        props.task || ""
      ),
      props.output
        ? h(
            "pre",
            { class: "mt-2 rounded-md bg-muted/40 px-3 py-2 text-body-sm text-foreground" },
            props.output
          )
        : null,
      h(
        "div",
        { class: "mt-3 flex justify-end" },
        h(
          "span",
          {
            class:
              "mr-2 inline-flex items-center gap-1.5 font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
          },
          status === "running" ? icons.loader("size-3 animate-spin") : null,
          status
        )
      )
    );
  }

  /* ── system-prompt-editor ─────────────────────────────────────────── */

  function SystemPromptEditor(props) {
    props = props || {};
    var entries = props.entries || [];
    return h(
      "div",
      {
        "data-slot": "system-prompt-editor",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "header",
        { class: "flex items-center justify-between gap-3 border-border/40 border-b px-4 py-3" },
        h(
          "div",
          { class: "flex items-center gap-2" },
          icons.terminal("size-4 text-primary"),
          h("h3", { class: "font-display text-title-md tracking-tight" }, "System prompt")
        ),
        h(
          "span",
          { class: "font-mono text-label text-muted-foreground tabular-nums" },
          String(entries.length) + " entries"
        )
      ),
      entries.length === 0
        ? h(
            "div",
            { class: "px-4 py-8 text-center font-sans text-body-sm text-muted-foreground" },
            "No system prompt entries."
          )
        : h(
            "div",
            { class: "max-h-48 overflow-auto border-border/40 border-b bg-muted/40 px-4 py-3" },
            entries.map(function (e) {
              return h(
                "p",
                { class: "font-mono text-code-sm text-muted-foreground" },
                h("span", { class: "text-foreground" }, e.source || "base"),
                ": " + (e.content || "")
              );
            })
          ),
      props.onEdit
        ? h(
            "div",
            { class: "flex justify-end p-2" },
            Button({
              size: "sm",
              variant: "secondary",
              children: [icons.pencil("size-3.5"), "Edit"],
              onClick: props.onEdit,
            })
          )
        : null
    );
  }

  /* ── task-plan ────────────────────────────────────────────────────── */

  var TASK_STATUS_ICON = {
    pending: "circleDashed",
    running: "loader",
    done: "checkCircle",
    skipped: "circle",
    failed: "circleX",
  };

  var TASK_STATUS_COLOR = {
    pending: "text-muted-foreground",
    running: "text-primary",
    done: "text-success",
    skipped: "text-muted-foreground/60",
    failed: "text-destructive",
  };

  var TASK_LABEL_STYLE = {
    pending: "text-foreground",
    running: "text-foreground",
    done: "text-foreground line-through decoration-muted-foreground/40",
    skipped: "text-muted-foreground line-through",
    failed: "text-destructive",
  };

  function TaskNode(props) {
    props = props || {};
    var node = props.node || {};
    var depth = props.depth || 0;
    var icon = TASK_STATUS_ICON[node.status] || "circle";
    return h(
      "li",
      {
        "data-slot": "task-node",
        class: cn("grid gap-1", props.class),
        style: depth ? { marginLeft: depth * 1.25 + "rem" } : undefined,
      },
      h(
        "div",
        { class: "grid grid-cols-[auto_1fr] items-baseline gap-2" },
        h("span", {
          class: cn(
            "grid place-items-center",
            TASK_STATUS_COLOR[node.status] || "text-muted-foreground",
            node.status === "running" && "animate-spin"
          ),
        }, icons[icon]("size-3.5")),
        h(
          "div",
          { class: "min-w-0" },
          h("p", { class: cn("text-body-sm", TASK_LABEL_STYLE[node.status] || "") }, node.label || ""),
          node.detail
            ? h("p", { class: "text-body-sm text-muted-foreground" }, node.detail)
            : null
        )
      ),
      node.children && node.children.length
        ? h(
            "ul",
            { class: "grid gap-1 border-border/30 border-l pl-2" },
            node.children.map(function (child) {
              return TaskNode({ node: child, depth: depth + 1 });
            })
          )
        : null
    );
  }

  function TaskPlan(props) {
    props = props || {};
    var nodes = props.nodes || [];
    var done = nodes.filter(function (n) {
      return (n || {}).status === "done";
    }).length;
    var summary =
      props.summary !== undefined
        ? props.summary
        : nodes.length > 0
          ? done + " of " + nodes.length + " done"
          : "no steps";
    return h(
      "section",
      {
        "data-slot": "task-plan",
        class: cn("rounded-xl border border-border bg-card p-4", props.class),
      },
      h(
        "header",
        { class: "mb-3 flex items-baseline justify-between gap-3" },
        props.title !== undefined && props.title !== null
          ? h("h3", { class: "font-display text-title-md tracking-tight" }, props.title || "Plan")
          : h("span"),
        h(
          "span",
          { class: "font-mono text-label text-muted-foreground tabular-nums" },
          summary
        )
      ),
      h(
        "ul",
        { class: "grid gap-1.5" },
        nodes.map(function (node) {
          return TaskNode({ node: node });
        })
      )
    );
  }

  /* ── terminal-panel ───────────────────────────────────────────────── */

  var TERM_KIND_COLOR = {
    stdout: "text-foreground",
    stderr: "text-destructive",
    command: "text-primary",
    meta: "text-muted-foreground",
  };

  function TerminalPanel(props) {
    props = props || {};
    var lines = props.lines || [];
    return h(
      "div",
      {
        "data-slot": "terminal-panel",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card font-mono", props.class),
      },
      h(
        "div",
        { class: "flex items-center gap-2 border-border/40 border-b px-3 py-2" },
        icons.terminal("size-3.5 text-muted-foreground"),
        h(
          "span",
          {
            class:
              "font-sans text-label-caps text-muted-foreground uppercase tracking-wider",
          },
          props.title || "Terminal"
        )
      ),
      h(
        "div",
        { class: "grid gap-0.5 px-3 py-2 font-mono text-code-sm" },
        lines.length === 0
          ? h("span", { class: "text-muted-foreground" }, props.emptyLabel || "$")
          : lines.map(function (line) {
              return h(
                "span",
                { class: TERM_KIND_COLOR[line.kind] || "text-foreground" },
                line.kind === "command"
                  ? h("span", { class: "select-none text-primary" }, "$ ")
                  : null,
                line.content || ""
              );
            }),
        h("span", { class: "select-none text-primary motion-safe:animate-pulse" }, "▌")
      )
    );
  }

  /* ── thinking-level-selector ──────────────────────────────────────── */

  function ThinkingLevelSelector(props) {
    props = props || {};
    var levels = [
      { value: "off", label: "Off" },
      { value: "low", label: "Low" },
      { value: "medium", label: "Medium" },
      { value: "high", label: "High" },
    ];
    var value = props.value != null ? props.value : props.inheritedValue || "medium";
    var sel = h(
      "select",
      {
        "data-slot": "thinking-level-selector",
        "aria-label": "Thinking level",
        disabled: props.disabled ? "" : null,
        class: cn(
          "h-8 rounded-md border border-input bg-card px-2 font-mono text-label",
          props.class
        ),
      },
      props.inheritedValue
        ? h("option", { value: "inherit" }, "Inherited (" + props.inheritedValue + ")")
        : null,
      levels.map(function (lv) {
        return h("option", { value: lv.value, selected: lv.value === value ? "" : null }, lv.label);
      })
    );
    sel.addEventListener("change", function () {
      if (props.onChange) props.onChange(sel.value);
    });
    if (props["data-testid"]) sel.setAttribute("data-testid", props["data-testid"]);
    return sel;
  }

  /* ── token-usage-chart ────────────────────────────────────────────── */

  function TokenUsageChart(props) {
    props = props || {};
    var data = props.data || [];
    var max = 1;
    data.forEach(function (d) {
      max = Math.max(max, d.input || 0, d.output || 0);
    });
    var totalIn = data.reduce(function (a, d) {
      return a + (d.input || 0);
    }, 0);
    var totalOut = data.reduce(function (a, d) {
      return a + (d.output || 0);
    }, 0);
    return h(
      "div",
      {
        "data-slot": "token-usage-chart",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      h(
        "div",
        { class: "flex items-baseline justify-between gap-3" },
        h("h4", { class: "font-display text-title-md tracking-tight" }, props.title || "Token usage"),
        h(
          "span",
          { class: "font-mono text-label text-muted-foreground tabular-nums" },
          (totalIn + totalOut).toLocaleString() + " total"
        )
      ),
      h(
        "div",
        { class: "mt-3 grid grid-cols-[auto_1fr] gap-2" },
        h(
          "div",
          { class: "grid font-mono text-label text-muted-foreground" },
          data.map(function (d) {
            return h("span", { class: "truncate text-right" }, d.label || "");
          })
        ),
        h(
          "div",
          { class: "grid items-end gap-1.5" },
          data.map(function (d) {
            var inH = Math.round(((d.input || 0) / max) * 100);
            var outH = Math.round(((d.output || 0) / max) * 100);
            return h(
              "div",
              {
                class: "flex h-24 items-end gap-1",
                role: "img",
                "aria-label": (d.label || "") + ": " + (d.input || 0) + " in / " + (d.output || 0) + " out",
              },
              h("div", {
                class: "w-full rounded-sm bg-accent",
                style: { height: Math.max(2, inH) + "%" },
              }),
              h("div", {
                class: "w-full rounded-sm bg-primary",
                style: { height: Math.max(2, outH) + "%" },
              })
            );
          })
        )
      ),
      h(
        "div",
        {
          class:
            "mt-3 flex items-center gap-4 font-mono text-label text-muted-foreground",
        },
        h("span", { class: "inline-flex items-center gap-1.5" }, h("span", { class: "size-2 rounded-sm bg-accent" }), "Input " + totalIn.toLocaleString()),
        h("span", { class: "inline-flex items-center gap-1.5" }, h("span", { class: "size-2 rounded-sm bg-primary" }), "Output " + totalOut.toLocaleString())
      )
    );
  }

  /* ── tool-call ────────────────────────────────────────────────────── */

  function ToolCall(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "tool-call",
        class: cn("rounded-lg border border-border/40 bg-card/40", props.class),
      },
      h(
        "button",
        {
          type: "button",
          class: "flex w-full items-center gap-2 px-3 py-2 text-left",
          onClick: props.onClick,
        },
        icons.wrench("size-3.5 shrink-0 text-primary"),
        h("span", { class: "font-mono text-code-sm text-foreground" }, props.tool || ""),
        props.target
          ? h(
              "span",
              { class: "flex-1 truncate font-mono text-code-sm text-muted-foreground" },
              props.target
            )
          : null
      ),
      props.output
        ? h(
            "div",
            {
              class:
                "border-border/40 border-t bg-card px-3 py-2 font-mono text-code-sm text-muted-foreground",
            },
            props.output
          )
        : null
    );
  }

  /* ── tool-call-card ───────────────────────────────────────────────── */

  var TCC_STATUS_ICON = {
    running: "loader",
    success: "check",
    failed: "x",
    queued: "dot",
    skipped: "dot",
  };
  var TCC_STATUS_LABEL = {
    running: "Running",
    success: "Completed",
    failed: "Failed",
    queued: "Queued",
    skipped: "Skipped",
  };
  var TCC_STATUS_CLS = {
    running: "size-3.5 animate-spin text-primary",
    success: "size-3.5 text-success",
    failed: "size-3.5 text-destructive",
    queued: "size-2 rounded-full bg-warning",
    skipped: "size-2 rounded-full bg-muted-foreground",
  };

  function ToolCallCard(props) {
    props = props || {};
    var open = !!props.defaultExpanded;
    var expandable = !!props.output;
    var status = props.status || "running";
    var chev = null;

    var card = h(
      "article",
      {
        "data-slot": "tool-call-card",
        class: cn(
          "overflow-hidden rounded-lg border border-border/40 bg-card/40 text-card-foreground",
          props.class
        ),
      }
    );

    var header = h(
      "div",
      { class: "flex items-center gap-2 px-3 py-2" },
      expandable
        ? (function () {
            var btn = h(
              "button",
              {
                type: "button",
                "aria-expanded": String(open),
                "aria-label": (open ? "Collapse " : "Expand ") + props.tool + " details",
                class:
                  "-m-1 inline-flex size-6 shrink-0 items-center justify-center rounded-md p-1 text-muted-foreground hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                onClick: function () {
                  open = !open;
                  btn.setAttribute("aria-expanded", String(open));
                  chev.classList.toggle("rotate-90", open);
                  if (outputBox) outputBox.style.display = open ? "" : "none";
                  else if (open) renderOutput();
                },
              },
              (chev = icons.chevronRight("size-3.5 transition-transform duration-base"))
            );
            return btn;
          })()
        : null,
      props.icon ? icons[props.icon]("size-4 shrink-0 text-muted-foreground") : null,
      h(
        "span",
        { class: "shrink-0 font-medium font-mono text-code-sm text-foreground" },
        props.tool || ""
      ),
      props.target
        ? h(
            "span",
            { class: "truncate font-mono text-code-sm text-muted-foreground" },
            props.target
          )
        : null,
      h(
        "span",
        {
          role: "img",
          "aria-label": TCC_STATUS_LABEL[status] || status,
          class: "ml-auto inline-flex shrink-0 items-center gap-1.5",
        },
        icons[TCC_STATUS_ICON[status] || "dot"](TCC_STATUS_CLS[status] || "")
      ),
      props.timestamp
        ? h(
            "span",
            { class: "shrink-0 font-mono text-label text-muted-foreground tabular-nums" },
            props.timestamp
          )
        : null
    );
    card.appendChild(header);

    var outputBox = null;
    function renderOutput() {
      outputBox = h(
        "div",
        {
          class: "border-border/40 border-t bg-muted/20 px-3 py-2 font-mono text-code-sm",
        },
        props.output
      );
      card.appendChild(outputBox);
    }
    if (expandable && open) renderOutput();
    else if (expandable) {
      /* collapsed: rendered lazily on expand */
    }

    /* expose toggle for tests via dataset */
    card.__toggle = function () {
      var btn = card.firstChild && card.firstChild.firstChild;
      if (btn && btn.click) btn.click();
    };
    return card;
  }

  /* ── tool-result ──────────────────────────────────────────────────── */

  function ToolResult(props) {
    props = props || {};
    var r = props.result || props;
    var ok = r.ok !== false && r.error == null;
    return h(
      "div",
      {
        "data-slot": "tool-result",
        class: cn(
          "rounded-lg border px-3 py-2 font-mono text-code-sm",
          ok ? "border-border/40 bg-muted/20" : "border-destructive/30 bg-destructive/5",
          props.class
        ),
      },
      h(
        "div",
        { class: "flex items-center gap-2" },
        ok
          ? icons.check("size-3.5 text-success")
          : icons.alertCircle("size-3.5 text-destructive"),
        h(
          "span",
          { class: ok ? "text-foreground" : "text-destructive" },
          ok ? "Result" : "Error"
        )
      ),
      h(
        "pre",
        { class: "mt-1 overflow-x-auto whitespace-pre-wrap text-muted-foreground" },
        r.output || r.error || ""
      )
    );
  }

  /* ── tools-list ───────────────────────────────────────────────────── */

  function ToolsList(props) {
    props = props || {};
    var tools = props.tools || [];
    return h(
      "div",
      {
        "data-slot": "tools-list",
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      h(
        "header",
        { class: "flex items-center justify-between border-border/40 border-b px-4 py-3" },
        h("h3", { class: "font-display text-title-md tracking-tight" }, "Tools"),
        h("span", { class: "font-mono text-label text-muted-foreground" }, String(tools.length))
      ),
      tools.length === 0
        ? h(
            "div",
            { class: "px-4 py-8 text-center font-sans text-body-sm text-muted-foreground" },
            "No tools registered."
          )
        : h(
            "div",
            { class: "divide-y divide-border/30" },
            tools.map(function (t) {
              return h(
                "div",
                { class: "grid grid-cols-[auto_1fr_auto] items-start gap-3 px-4 py-3" },
                h(
                  "span",
                  {
                    class:
                      "mt-0.5 grid size-8 place-items-center rounded-md bg-muted text-muted-foreground",
                  },
                  icons[t.icon || "wrench"]("size-4")
                ),
                h(
                  "div",
                  { class: "min-w-0" },
                  h(
                    "p",
                    { class: "flex flex-wrap items-center gap-2" },
                    h(
                      "span",
                      { class: "font-medium font-mono text-body-sm text-foreground" },
                      t.name || ""
                    ),
                    t.kind
                      ? h(
                          "span",
                          {
                            class:
                              "inline-flex items-center gap-1 rounded-full bg-accent/15 px-2 py-0.5 font-mono text-accent text-label uppercase",
                          },
                          t.kind
                        )
                      : null
                  ),
                  t.description
                    ? h("p", { class: "mt-0.5 text-body-sm text-muted-foreground" }, t.description)
                    : null
                ),
                props.onToggle
                  ? h(
                      "button",
                      {
                        type: "button",
                        role: "switch",
                        "aria-checked": t.enabled !== false ? "true" : "false",
                        class: cn(
                          "relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors",
                          t.enabled !== false ? "bg-primary" : "bg-muted"
                        ),
                        onClick: function () {
                          props.onToggle(t);
                        },
                      },
                      h("span", {
                        class: cn(
                          "absolute top-0.5 size-4 rounded-full bg-background transition-all",
                          t.enabled !== false ? "left-[1.15rem]" : "left-0.5"
                        ),
                      })
                    )
                  : null
              );
            })
          )
    );
  }

  /* ── whiteboard ───────────────────────────────────────────────────── */

  var WB_NS = "http://www.w3.org/2000/svg";

  function Whiteboard(props) {
    props = props || {};
    var data = props.data || {};
    var elements = data.elements || [];
    var w = data.width || 800;
    var hgt = data.height || 480;

    var svg = root.document.createElementNS(WB_NS, "svg");
    svg.setAttribute("xmlns", WB_NS);
    svg.setAttribute("viewBox", "0 0 " + w + " " + hgt);
    svg.setAttribute("data-slot", "whiteboard");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", props["aria-label"] || "Whiteboard diagram");
    if (props.class) svg.setAttribute("class", props.class);

    elements.forEach(function (el) {
      var node;
      if (el.type === "ellipse") {
        node = root.document.createElementNS(WB_NS, "ellipse");
        node.setAttribute("cx", String(el.x + (el.width || 0) / 2));
        node.setAttribute("cy", String(el.y + (el.height || 0) / 2));
        node.setAttribute("rx", String((el.width || 0) / 2));
        node.setAttribute("ry", String((el.height || 0) / 2));
      } else if (el.type === "line" || el.type === "arrow") {
        node = root.document.createElementNS(WB_NS, "line");
        node.setAttribute("x1", String(el.x));
        node.setAttribute("y1", String(el.y));
        node.setAttribute("x2", String(el.x2 != null ? el.x2 : (el.x || 0) + (el.width || 0)));
        node.setAttribute("y2", String(el.y2 != null ? el.y2 : (el.y || 0) + (el.height || 0)));
      } else if (el.type === "text") {
        node = root.document.createElementNS(WB_NS, "text");
        node.setAttribute("x", String(el.x));
        node.setAttribute("y", String(el.y));
        node.setAttribute("fill", el.fill || "currentColor");
        node.setAttribute("font-size", String(el.fontSize || 14));
        node.textContent = el.text || el.label || "";
      } else {
        node = root.document.createElementNS(WB_NS, "rect");
        node.setAttribute("x", String(el.x || 0));
        node.setAttribute("y", String(el.y || 0));
        node.setAttribute("width", String(el.width || 0));
        node.setAttribute("height", String(el.height || 0));
        if (el.type === "diamond") {
          node.setAttribute("transform", "rotate(45 " + ((el.x || 0) + (el.width || 0) / 2) + " " + ((el.y || 0) + (el.height || 0) / 2) + ")");
        }
      }
      if (el.type !== "text") {
        node.setAttribute("fill", el.fill || "none");
        node.setAttribute("stroke", el.stroke || "currentColor");
        node.setAttribute("stroke-width", String(el.strokeWidth || 1.5));
      }
      if (el.id) node.setAttribute("data-id", el.id);
      svg.appendChild(node);
      if (el.label && el.type !== "text") {
        var label = root.document.createElementNS(WB_NS, "text");
        label.setAttribute("x", String((el.x || 0) + (el.width || 0) / 2));
        label.setAttribute("y", String((el.y || 0) + (el.height || 0) / 2 + 4));
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("font-size", "12");
        label.setAttribute("fill", "currentColor");
        label.textContent = el.label;
        svg.appendChild(label);
      }
    });

    var wrap = h(
      "div",
      {
        class: cn("overflow-hidden rounded-xl border border-border/40 bg-card", props.className),
        "data-slot": "whiteboard",
      },
      svg
    );
    return wrap;
  }

  /* ── work-log ─────────────────────────────────────────────────────── */

  function WorkLog(props) {
    props = props || {};
    var entries = props.entries || [];
    return h(
      "div",
      { "data-slot": "work-log", class: cn("flex flex-col gap-1", props.class) },
      entries.length === 0
        ? h("span", { class: "text-muted-foreground text-xs" }, "No work recorded.")
        : entries.slice(0, props.limit || 5).map(function (e) {
            return h(
              "button",
              {
                type: "button",
                class:
                  "flex items-center gap-1 text-muted-foreground text-xs transition-colors hover:text-foreground",
                onClick: function () {
                  if (props.onOpen) props.onOpen(e);
                },
              },
              icons.clock("size-3.5"),
              h("span", { class: "text-muted-foreground text-xs" }, e.label || "")
            );
          })
    );
  }

  Object.assign(Tk, {
    IntentSelector: IntentSelector,
    LaneBoard: LaneBoard,
    McpServerCard: McpServerCard,
    MemoryEditor: MemoryEditor,
    MentionMenu: MentionMenu,
    ModelCard: ModelCard,
    ModelEffortPicker: ModelEffortPicker,
    ModelSelector: ModelSelector,
    PermissionMatrix: PermissionMatrix,
    ProgressChecklist: ProgressChecklist,
    ProjectSwitcher: ProjectSwitcher,
    QuickActionChips: QuickActionChips,
    RecentFoldersList: RecentFoldersList,
    RuleCard: RuleCard,
    RunStats: RunStats,
    RunStatusPill: RunStatusPill,
    RunningTasksPanel: RunningTasksPanel,
    SessionListItem: SessionListItem,
    SessionTimeline: SessionTimeline,
    SkillCard: SkillCard,
    Slide: Slide,
    StepsRail: StepsRail,
    SubAgentDispatch: SubAgentDispatch,
    SystemPromptEditor: SystemPromptEditor,
    TaskPlan: TaskPlan,
    TaskNode: TaskNode,
    TerminalPanel: TerminalPanel,
    ThinkingLevelSelector: ThinkingLevelSelector,
    TokenUsageChart: TokenUsageChart,
    ToolCall: ToolCall,
    ToolCallCard: ToolCallCard,
    ToolResult: ToolResult,
    ToolsList: ToolsList,
    Whiteboard: Whiteboard,
    WorkLog: WorkLog,
    RUN_STATUS_META: RUN_STATUS_META,
  });
  if (typeof module !== "undefined" && module.exports) module.exports = Tk;
})(typeof window !== "undefined" ? window : globalThis);
