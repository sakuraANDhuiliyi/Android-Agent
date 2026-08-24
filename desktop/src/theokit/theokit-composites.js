/*
 * theokit-composites.js — TheoKit composite components (vanilla JS migration
 * of @theokit/ui composites/*): composer, chat message pipeline, prompts,
 * approval card, agent stream/timeline, editors, lists and slide deck.
 */
(function (root) {
  "use strict";

  var Tk = root.Theokit = root.Theokit || {};
  var h = Tk.h;
  var cn = Tk.cn;
  var Button = Tk.Button;
  var icons = Tk.icons || {};

  /* ── approval-card ────────────────────────────────────────────────── */

  var APPROVAL_SEVERITY = {
    info: { icon: "shield-check", tone: "text-info", card: "border-info/40 bg-info/5" },
    warning: { icon: "alert-triangle", tone: "text-warning", card: "border-warning/40 bg-warning/5" },
    destructive: { icon: "lock", tone: "text-destructive", card: "border-destructive/40 bg-destructive/5" },
  };

  function ApprovalCard(props) {
    props = props || {};
    var severity = props.severity || "warning";
    var meta = APPROVAL_SEVERITY[severity] || APPROVAL_SEVERITY.warning;
    return h(
      "section",
      {
        "data-slot": "approval-card",
        role: "alertdialog",
        "aria-label": typeof props.title === "string" ? props.title : "Approval required",
        class: cn(
          "grid w-full gap-3 rounded-xl border border-border p-4 transition-colors duration-base ease-out-soft",
          meta.card,
          props.class
        ),
      },
      h(
        "header",
        { class: "flex items-start gap-3" },
        h(
          "span",
          { class: cn("mt-0.5 inline-flex shrink-0", meta.tone) },
          icons[props.icon || meta.icon]("size-4")
        ),
        h(
          "div",
          { class: "grid min-w-0 flex-1 gap-1" },
          h(
            "h4",
            { class: "font-display text-foreground text-title-md tracking-tight" },
            props.title || "Approval required"
          ),
          h(
            "code",
            {
              class:
                "overflow-hidden break-words font-mono text-code-md text-muted-foreground",
            },
            props.request || ""
          ),
          props.description
            ? h("p", { class: "text-body-sm text-muted-foreground" }, props.description)
            : null
        )
      ),
      props.details
        ? h(
            "details",
            {
              class:
                "rounded-md border border-border/40 bg-background/40 px-3 py-2 text-body-sm",
            },
            h(
              "summary",
              {
                class:
                  "cursor-pointer select-none font-mono text-label text-muted-foreground",
              },
              "Show details"
            ),
            h("div", { class: "mt-2 break-words" }, props.details)
          )
        : null,
      h(
        "footer",
        { class: "flex flex-wrap items-center justify-end gap-2" },
        props.onAlways
          ? Button({
              size: "sm",
              variant: "ghost",
              children: "Always allow",
              onClick: props.onAlways,
            })
          : null,
        props.onDeny
          ? Button({
              size: "sm",
              variant: "secondary",
              children: "Deny",
              onClick: props.onDeny,
            })
          : null,
        props.onApprove
          ? Button({
              size: "sm",
              children: "Approve",
              onClick: props.onApprove,
            })
          : null
      )
    );
  }

  /* ── chat-composer ────────────────────────────────────────────────── */

  var COMPOSER_PLACEHOLDER = {
    chat: "How can I help you today?",
    code: "Type / for commands",
    infra: "Ask about deploys, metrics, env, or rollback…",
  };
  var COMPOSER_LABEL = {
    chat: "Chat message",
    code: "Code prompt",
    infra: "Infra command",
  };

  function ChatComposer(props) {
    props = props || {};
    var mode = props.mode || "chat";
    var isCode = mode === "code";
    var value = props.value || "";
    var running = !!props.running;

    var form = h(
      "form",
      {
        "data-slot": "chat-composer",
        class: cn(
          "rounded-2xl border border-border bg-card text-card-foreground transition-shadow",
          "focus-within:border-primary/60 focus-within:shadow-glow",
          isCode && "rounded-xl shadow-sm",
          props.class
        ),
      },
      props.contextSlot
        ? h("div", { class: "border-border/40 border-b px-3 pt-3" }, props.contextSlot)
        : null,
      props.attachmentsSlot
        ? h("div", { class: "flex flex-wrap gap-2 px-4 pt-3" }, props.attachmentsSlot)
        : null
    );

    var textarea = h("textarea", {
      placeholder: props.placeholder || COMPOSER_PLACEHOLDER[mode],
      "aria-label": props.textareaLabel || COMPOSER_LABEL[mode],
      rows: isCode ? "1" : "2",
      class: cn(
        "w-full resize-none bg-transparent px-4 py-3",
        "placeholder:text-muted-foreground",
        "focus:outline-none",
        isCode ? "font-mono text-code-md" : "min-h-[3.5rem] font-sans text-body-md"
      ),
    });
    textarea.value = value;
    textarea.addEventListener("input", function () {
      if (props.onValueChange) props.onValueChange(textarea.value);
      syncSubmit();
    });
    if (props.textareaProps && props.textareaProps.onKeyDown) {
      textarea.addEventListener("keydown", props.textareaProps.onKeyDown);
    }
    form.appendChild(textarea);

    function submit() {
      if (running) return;
      if (!textarea.value.trim()) return;
      if (props.onSubmit) props.onSubmit(textarea.value);
    }
    textarea.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submit();
      }
    });

    var submitBtn;
    function syncSubmit() {
      if (submitBtn) submitBtn.disabled = !textarea.value.trim();
    }

    var actionRow = h(
      "div",
      { class: "flex items-center justify-between gap-2 border-border/40 border-t px-3 py-2" },
      h(
        "div",
        { class: "flex items-center gap-1" },
        props.leadingActions !== undefined
          ? props.leadingActions
          : props.onAttach
            ? Button({
                size: "icon",
                variant: "ghost",
                label: "Attach file",
                children: icons.paperclip(),
                onClick: props.onAttach,
              })
            : null
      ),
      h(
        "div",
        { class: "flex items-center gap-2" },
        props.trailingActions || null,
        props.onVoiceInput
          ? Button({
              size: "icon",
              variant: "ghost",
              label: "Voice input",
              children: icons.mic(),
              onClick: props.onVoiceInput,
            })
          : null,
        running
          ? Button({
              size: "icon",
              variant: "destructive",
              label: "Stop generation",
              children: icons.square(),
              onClick: props.onStop,
            })
          : (submitBtn = Button({
              size: "icon",
              type: "submit",
              label: props.submitLabel || "Send message",
              disabled: !value.trim(),
              children: icons[props.submitIcon || "send"] ? icons[props.submitIcon || "send"]() : icons.send(),
            }))
      )
    );
    form.appendChild(actionRow);
    if (submitBtn) {
      submitBtn.addEventListener("click", function (e) {
        e.preventDefault();
        submit();
      });
    }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submit();
    });

    form.__textarea = textarea;
    return form;
  }

  /* ── mention-menu wiring (agent-composer) ─────────────────────────── */

  var TRIGGER_RE = /(^|\s)([/@#])([^\s]*)$/;

  function detectTrigger(value) {
    var m = String(value || "").match(TRIGGER_RE);
    if (!m) return null;
    var leading = m[1] || "";
    var triggerChar = m[2];
    if (["/", "@", "#"].indexOf(triggerChar) === -1) return null;
    return {
      trigger: triggerChar,
      query: m[3] || "",
      start: (m.index || 0) + leading.length,
    };
  }

  function resolveItems(source, query) {
    if (!source) return [];
    if (typeof source === "function") return source(query);
    if (!query) return source;
    var q = query.toLowerCase();
    return source.filter(function (item) {
      var haystack = (
        (typeof item.label === "string" ? item.label : "") +
        " " +
        (typeof item.description === "string" ? item.description : "")
      ).toLowerCase();
      return haystack.indexOf(q) !== -1;
    });
  }

  function AgentComposer(props) {
    props = props || {};
    var value = props.value || "";
    var dismissedAt = null;

    var menuHost = h("div", { class: "relative" });

    var composer = ChatComposer(
      Object.assign({}, props, {
        value: value,
        onValueChange: function (next) {
          value = next;
          render();
          if (props.onValueChange) props.onValueChange(next);
        },
        onSubmit: props.onSubmit,
        textareaProps: props.textareaProps,
      })
    );

    function detected() {
      return detectTrigger(value);
    }
    function activeTrigger() {
      var d = detected();
      if (!d) return null;
      if (dismissedAt === d.start) return null;
      return d;
    }

    function pick(item) {
      var d = detected();
      if (!d) return;
      var before = value.slice(0, d.start);
      var label = typeof item.label === "string" ? item.label : "";
      var insert =
        props.resolveInsertText && props.resolveInsertText(item, d.trigger) !== undefined
          ? props.resolveInsertText(item, d.trigger)
          : label.indexOf(d.trigger) === 0
            ? label
            : d.trigger + label;
      value = before + insert + " ";
      dismissedAt = null;
      if (props.onValueChange) props.onValueChange(value);
      render();
      composer.__textarea.value = value;
      if (composer.__textarea.focus) composer.__textarea.focus();
    }

    var currentMenu = null;
    function render() {
      if (currentMenu) {
        menuHost.removeChild(currentMenu);
        currentMenu = null;
      }
      var d = activeTrigger();
      if (!d) return;
      var items =
        d.trigger === "/"
          ? resolveItems(props.commands, d.query)
          : d.trigger === "@"
            ? resolveItems(props.files, d.query)
            : resolveItems(props.memories, d.query);
      currentMenu = Tk.MentionMenu({
        open: true,
        trigger: d.trigger,
        items: items,
        onSelect: pick,
        onClose: function () {
          var dd = detected();
          if (dd) dismissedAt = dd.start;
          render();
        },
        emptyLabel: (props.emptyLabels && props.emptyLabels[d.trigger]) || "No matches",
      });
      menuHost.appendChild(currentMenu);
    }

    menuHost.appendChild(composer);
    var d0 = activeTrigger();
    if (d0) render();
    return menuHost;
  }

  /* ── chat-message parts ───────────────────────────────────────────── */

  function TextPart(part) {
    return h(
      "p",
      { "data-slot": "text-part", class: "whitespace-pre-wrap break-words text-body-md" },
      part.text || ""
    );
  }

  function ReasoningPart(part) {
    return h(
      "details",
      {
        "data-slot": "reasoning-part",
        class: "rounded-lg border border-border/40 bg-muted/20 px-3 py-2",
      },
      h(
        "summary",
        {
          class:
            "cursor-pointer select-none font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
        },
        "Thinking"
      ),
      h(
        "p",
        { class: "mt-1.5 whitespace-pre-wrap text-body-sm text-muted-foreground" },
        part.text || part.reasoning || ""
      )
    );
  }

  function FilePart(part) {
    var url = part.url || "";
    var media = part.mediaType || "";
    if (media.indexOf("image/") === 0) {
      return h(
        "figure",
        { "data-slot": "file-part", class: "overflow-hidden rounded-lg border border-border/40" },
        h("img", { src: url, alt: part.filename || "image", class: "max-h-72 w-auto" })
      );
    }
    return Tk.AttachmentChip
      ? Tk.AttachmentChip({ name: part.filename || url, class: "max-w-full" })
      : h("span", { "data-slot": "file-part" }, part.filename || url);
  }

  function SourceUrlPart(part) {
    return h(
      "a",
      {
        "data-slot": "source-part",
        href: part.url || "#",
        target: "_blank",
        rel: "noreferrer",
        class:
          "inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 font-mono text-code-sm text-primary hover:bg-muted/60",
      },
      icons.externalLink("size-3"),
      part.title || part.url || "source"
    );
  }

  function SourceDocumentPart(part) {
    return h(
      "span",
      {
        "data-slot": "source-document-part",
        class:
          "my-1 inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1 align-middle font-mono text-label",
        "data-theo-source": "document",
      },
      icons.fileText("size-3 text-muted-foreground"),
      h("span", { class: "truncate text-foreground" }, part.title || ""),
      h("span", { class: "text-muted-foreground" }, "·"),
      h("span", { class: "text-muted-foreground" }, part.mediaType || "")
    );
  }

  function DataPart(props) {
    props = props || {};
    var part = props.part || {};
    var name = String(part.type || "").slice("data-".length);
    var renderers = props.renderers || {};
    var renderer = renderers[part.type] || renderers[name];
    if (renderer) return renderer(part.data, part);
    return h(
      "details",
      {
        class: "my-2 rounded-md border border-border bg-muted/20 px-3 py-1.5 text-body-sm",
        "data-theo-data": name,
      },
      h(
        "summary",
        {
          class:
            "flex cursor-pointer items-center gap-1.5 font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
        },
        icons.code("size-3"),
        h("span", null, "data-" + name)
      ),
      h(
        "pre",
        { class: "mt-2 overflow-x-auto border-border border-t pt-2 text-code-sm" },
        h("code", null, safeStringify(part.data))
      )
    );
  }

  /* ── code-block ───────────────────────────────────────────────────── */

  function CodeBlock(props) {
    props = props || {};
    var code = props.code != null ? String(props.code) : "";
    var language = props.language || "";
    var copyBtn;
    var block = h(
      "div",
      {
        class: cn(
          "group relative my-4 overflow-hidden rounded-lg border border-border bg-muted/30",
          props.class
        ),
        "data-theo-code-block": "",
      },
      h(
        "div",
        { class: "flex items-center justify-between border-border border-b bg-muted/50 px-3 py-1.5" },
        h(
          "span",
          {
            class:
              "font-mono text-label-caps text-muted-foreground uppercase tracking-wider",
          },
          language || "text"
        ),
        (copyBtn = h(
          "button",
          {
            type: "button",
            class: cn(
              "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-label",
              "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
            ),
            "aria-label": "Copy code",
            onClick: function () {
              if (root.navigator && root.navigator.clipboard) {
                root.navigator.clipboard.writeText(code);
              }
              copyBtn.textContent = "";
              copyBtn.appendChild(icons.check("size-3.5"));
              copyBtn.appendChild(h("span", null, "Copied"));
              copyBtn.setAttribute("aria-label", "Copied");
              setTimeout(function () {
                copyBtn.textContent = "";
                copyBtn.appendChild(icons.copy("size-3.5"));
                copyBtn.appendChild(h("span", null, "Copy"));
                copyBtn.setAttribute("aria-label", "Copy code");
              }, 2000);
            },
          },
          icons.copy("size-3.5"),
          h("span", null, "Copy")
        ))
      ),
      h(
        "pre",
        { class: "overflow-x-auto p-3 text-code-sm" },
        h("code", language ? { class: "language-" + language } : null, code)
      )
    );
    return block;
  }

  function InlineCode(props) {
    return h(
      "code",
      {
        class:
          "rounded-md border border-border bg-muted/60 px-1.5 py-0.5 font-mono text-code-sm text-foreground",
      },
      props.children
    );
  }

  /* ── chat-message markdown response ───────────────────────────────── */

  function renderInlineMarkdown(text) {
    var nodes = [];
    var rest = String(text == null ? "" : text);
    var pattern =
      /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/;
    var m;
    while ((m = pattern.exec(rest))) {
      if (m.index > 0) nodes.push(rest.slice(0, m.index));
      var tok = m[0];
      if (tok.charAt(0) === "`") {
        nodes.push(InlineCode({ children: tok.slice(1, -1) }));
      } else if (tok.indexOf("**") === 0) {
        nodes.push(h("strong", { class: "font-semibold" }, tok.slice(2, -2)));
      } else if (tok.charAt(0) === "*") {
        nodes.push(h("em", null, tok.slice(1, -1)));
      } else {
        var link = tok.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
        if (link) {
          nodes.push(
            h(
              "a",
              { href: link[2], target: "_blank", rel: "noopener noreferrer" },
              link[1]
            )
          );
        } else {
          nodes.push(tok);
        }
      }
      rest = rest.slice(m.index + tok.length);
    }
    if (rest) nodes.push(rest);
    return nodes;
  }

  function renderMarkdownBlocks(md) {
    var lines = String(md == null ? "" : md).split("\n");
    var out = [];
    var i = 0;
    function flushList(ordered) {
      var items = [];
      while (i < lines.length) {
        var line = lines[i];
        var li = ordered ? line.match(/^\s*\d+\.\s+(.*)$/) : line.match(/^\s*[-*]\s+(.*)$/);
        if (!li) break;
        items.push(h("li", null, renderInlineMarkdown(li[1])));
        i++;
      }
      out.push(
        h(
          ordered ? "ol" : "ul",
          { class: "my-2 pl-5 " + (ordered ? "list-decimal" : "list-disc") },
          items
        )
      );
    }
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) {
        i++;
        continue;
      }
      var fence = line.match(/^```(\w*)/);
      if (fence) {
        i++;
        var codeLines = [];
        while (i < lines.length && !/^```/.test(lines[i])) {
          codeLines.push(lines[i]);
          i++;
        }
        i++;
        out.push(CodeBlock({ code: codeLines.join("\n"), language: fence[1] || undefined }));
        continue;
      }
      if (/^---+$/.test(line.trim())) {
        out.push(h("hr", { class: "my-4 border-border" }));
        i++;
        continue;
      }
      var heading = line.match(/^(#{1,3})\s+(.*)$/);
      if (heading) {
        var tag = "h" + heading[1].length;
        out.push(h(tag, null, renderInlineMarkdown(heading[2])));
        i++;
        continue;
      }
      if (/^\s*>\s?/.test(line)) {
        var quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        out.push(h("blockquote", null, renderInlineMarkdown(quote.join(" "))));
        continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        flushList(false);
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        flushList(true);
        continue;
      }
      if (line.trim().charAt(0) === "|") {
        var rows = [];
        while (i < lines.length && lines[i].trim().charAt(0) === "|") {
          rows.push(
            lines[i]
              .trim()
              .replace(/^\|/, "")
              .replace(/\|$/, "")
              .split("|")
              .map(function (c) {
                return c.trim();
              })
          );
          i++;
        }
        if (rows.length >= 2 && /^[\s:|-]+$/.test(rows[1].join(""))) {
          var head = rows[0];
          var body = rows.slice(2);
          out.push(
            h(
              "table",
              { class: "my-3 w-full border-collapse" },
              h(
                "thead",
                null,
                h(
                  "tr",
                  null,
                  head.map(function (c) {
                    return h(
                      "th",
                      { class: "border border-border bg-muted/40 px-3 py-1.5 text-left" },
                      renderInlineMarkdown(c)
                    );
                  })
                )
              ),
              h(
                "tbody",
                null,
                body.map(function (r) {
                  return h(
                    "tr",
                    null,
                    r.map(function (c) {
                      return h(
                        "td",
                        { class: "border border-border px-3 py-1.5" },
                        renderInlineMarkdown(c)
                      );
                    })
                  );
                })
              )
            )
          );
          continue;
        }
      }
      var para = [line];
      i++;
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(#{1,3}\s|```|\s*[-*]\s|\s*\d+\.\s|\s*>|\|)/.test(lines[i])
      ) {
        para.push(lines[i]);
        i++;
      }
      out.push(h("p", { class: "my-2" }, renderInlineMarkdown(para.join("\n"))));
    }
    return out;
  }

  function ChatMessageResponse(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "chat-message-response-impl",
        class: cn(
          "prose-theo max-w-none text-body-md text-foreground leading-relaxed",
          props.class
        ),
        "data-theo-chat-response": "",
      },
      renderMarkdownBlocks(props.text || ""),
      props.isStreaming
        ? h("span", {
            class:
              "ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-primary align-middle",
          })
        : null
    );
  }

  /* ── chat-message actions / toolbar ────────────────────────────────── */

  function ChatMessageActions(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "chat-message-actions",
        class: cn("flex items-center gap-1", props.class),
        "data-theo-chat-actions": "",
      },
      props.children
    );
  }

  function ChatMessageAction(props) {
    props = props || {};
    return Button({
      "data-slot": "chat-message-action",
      type: "button",
      variant: props.variant || "ghost",
      size: props.size || "icon",
      label: props.label,
      title: props.tooltip,
      class: props.class,
      disabled: props.disabled,
      onClick: props.onClick,
      children: props.children,
    });
  }

  function ChatMessageToolbar(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "chat-message-toolbar",
        class: cn("mt-3 flex w-full items-center justify-between gap-3", props.class),
        "data-theo-chat-toolbar": "",
      },
      props.children
    );
  }

  /* ── chat-message branch ───────────────────────────────────────────── */

  function findBranchCtx(el) {
    var n = el;
    while (n) {
      if (n.__theoBranch) return n.__theoBranch;
      n = n.parentNode;
    }
    return null;
  }

  function ChatMessageBranch(props) {
    props = props || {};
    var current = props.defaultBranch || 0;
    var wrappers = [];
    var pageEls = [];
    var prevBtns = [];
    var nextBtns = [];
    var selectorEls = [];

    function setBranchVisibility(el, visible) {
      var toks = String(el.className || "").split(/\s+/).filter(function (t) {
        return t && t !== "block" && t !== "hidden";
      });
      toks.push(visible ? "block" : "hidden");
      el.className = toks.join(" ");
    }

    function apply() {
      wrappers.forEach(function (wrap, idx) {
        setBranchVisibility(wrap, idx === current);
      });
      pageEls.forEach(function (el) {
        el.textContent = current + 1 + " of " + wrappers.length;
      });
      var disabled = wrappers.length <= 1;
      prevBtns.concat(nextBtns).forEach(function (btn) {
        if (disabled) btn.setAttribute("disabled", "");
        else btn.removeAttribute("disabled");
      });
      selectorEls.forEach(function (el) {
        var toks = String(el.className || "").split(/\s+/).filter(function (t) {
          return t && t !== "hidden";
        });
        if (wrappers.length <= 1) toks.push("hidden");
        el.className = toks.join(" ");
      });
    }

    function goTo(next) {
      if (wrappers.length <= 1) return;
      current = ((next % wrappers.length) + wrappers.length) % wrappers.length;
      apply();
      if (props.onBranchChange) props.onBranchChange(current);
    }

    var ctx = {
      register: function (wrap) {
        if (wrap.__theoBranchRegistered) return;
        wrap.__theoBranchRegistered = true;
        wrappers.push(wrap);
        apply();
      },
      registerPage: function (el) {
        if (el.__theoBranchRegistered) return;
        el.__theoBranchRegistered = true;
        pageEls.push(el);
        apply();
      },
      registerPrev: function (btn) {
        if (btn.__theoBranchRegistered) return;
        btn.__theoBranchRegistered = true;
        prevBtns.push(btn);
        apply();
      },
      registerNext: function (btn) {
        if (btn.__theoBranchRegistered) return;
        btn.__theoBranchRegistered = true;
        nextBtns.push(btn);
        apply();
      },
      registerSelector: function (el) {
        if (el.__theoBranchRegistered) return;
        el.__theoBranchRegistered = true;
        selectorEls.push(el);
        apply();
      },
      goToPrevious: function () {
        goTo(current > 0 ? current - 1 : wrappers.length - 1);
      },
      goToNext: function () {
        goTo(current < wrappers.length - 1 ? current + 1 : 0);
      },
      totalBranches: function () {
        return wrappers.length;
      },
    };

    var rootEl = h(
      "div",
      { "data-slot": "chat-message-branch", class: cn("grid w-full gap-2", props.class) },
      props.children
    );
    rootEl.__theoBranch = ctx;

    function walk(node) {
      var kids = node.children || node.childNodes;
      if (!kids) return;
      for (var i = 0; i < kids.length; i++) {
        var n = kids[i];
        if (!n || n.nodeType === 3) continue;
        var slot = typeof n.getAttribute === "function" ? n.getAttribute("data-slot") : null;
        if (slot === "chat-message-branch-content") {
          var wraps = n.children || n.childNodes;
          for (var j = 0; j < wraps.length; j++) ctx.register(wraps[j]);
        } else if (slot === "chat-message-branch-page") {
          ctx.registerPage(n);
        } else if (slot === "chat-message-branch-selector") {
          ctx.registerSelector(n);
        } else if (slot === "chat-message-branch-previous") {
          ctx.registerPrev(n);
        } else if (slot === "chat-message-branch-next") {
          ctx.registerNext(n);
        }
        walk(n);
      }
    }
    walk(rootEl);
    apply();
    return rootEl;
  }

  function ChatMessageBranchContent(props) {
    props = props || {};
    var kids = [];
    var c = props.children;
    if (Array.isArray(c)) kids = c.slice();
    else if (c) kids = [c];
    return h(
      "div",
      { "data-slot": "chat-message-branch-content", class: "grid gap-2" },
      kids.map(function (kid, idx) {
        return h(
          "div",
          { class: "grid gap-2 overflow-hidden" + (idx === 0 ? " block" : " hidden") },
          kid
        );
      })
    );
  }

  function ChatMessageBranchSelector(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "chat-message-branch-selector",
        class: cn(
          "inline-flex items-center gap-0.5 rounded-md border border-border",
          props.class
        ),
        role: "group",
        "aria-label": "Branch selector",
      },
      props.children
    );
  }

  function ChatMessageBranchPrevious(props) {
    props = props || {};
    var btn;
    btn = Button({
      "data-slot": "chat-message-branch-previous",
      type: "button",
      variant: "ghost",
      size: "icon",
      label: "Previous branch",
      class: props.class,
      onClick: function () {
        var ctx = findBranchCtx(btn);
        if (ctx) ctx.goToPrevious();
      },
      children: props.children || icons.chevronLeft("size-3.5"),
    });
    return btn;
  }

  function ChatMessageBranchNext(props) {
    props = props || {};
    var btn;
    btn = Button({
      "data-slot": "chat-message-branch-next",
      type: "button",
      variant: "ghost",
      size: "icon",
      label: "Next branch",
      class: props.class,
      onClick: function () {
        var ctx = findBranchCtx(btn);
        if (ctx) ctx.goToNext();
      },
      children: props.children || icons.chevronRight("size-3.5"),
    });
    return btn;
  }

  function ChatMessageBranchPage(props) {
    props = props || {};
    return h(
      "span",
      {
        "data-slot": "chat-message-branch-page",
        class: cn(
          "inline-flex items-center px-2 font-mono text-label-caps text-muted-foreground",
          props.class
        ),
      },
      "1 of 1"
    );
  }

  /* ── tool-call-part (agent-tool-renderer fallback) ────────────────── */

  var PART_STATE_BADGE = {
    "input-streaming": { icon: "loader", label: "Streaming input", tone: "text-muted-foreground" },
    "input-available": { icon: "wrench", label: "Ready to call", tone: "text-primary" },
    "approval-requested": { icon: "shield", label: "Awaiting approval", tone: "text-warning" },
    "approval-responded": { icon: "shield", label: "Approval responded", tone: "text-primary" },
    "output-available": { icon: "check-circle", label: "Completed", tone: "text-success" },
    "output-error": { icon: "alert-circle", label: "Error", tone: "text-destructive" },
    "output-denied": { icon: "shield", label: "Denied", tone: "text-destructive" },
  };

  function safeStringify(value) {
    if (value === undefined || value === null) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch (_) {
      return String(value);
    }
  }

  function deriveToolName(part) {
    if (part.toolName) return part.toolName;
    if (part.type === "dynamic-tool") return "dynamic-tool";
    return String(part.type || "").replace(/^tool-/, "");
  }

  function ToolCallPart(props) {
    props = props || {};
    var part = props.part || {};
    var toolName = deriveToolName(part);
    var state = part.state || "input-available";
    var badge = PART_STATE_BADGE[state] || PART_STATE_BADGE["input-available"];
    var inputStr = safeStringify(part.input);
    var outputStr = part.state === "output-available" ? safeStringify(part.output) : part.errorText || "";

    return h(
      "div",
      {
        "data-slot": "tool-call-part",
        class: cn(
          "my-3 overflow-hidden rounded-lg border border-border bg-card shadow-sm",
          props.class
        ),
        "data-theo-tool-call": state,
      },
      h(
        "header",
        { class: "flex items-center justify-between gap-3 border-border border-b bg-muted/30 px-3 py-1.5" },
        h(
          "div",
          { class: "flex min-w-0 items-center gap-2" },
          icons.wrench("size-3.5 shrink-0 text-muted-foreground"),
          h("span", { class: "truncate font-mono text-foreground text-label" }, toolName)
        ),
        h(
          "span",
          {
            class: cn(
              "inline-flex items-center gap-1 text-label-caps uppercase tracking-wider",
              badge.tone
            ),
          },
          icons[badge.icon](cn("size-3.5", state === "input-streaming" && "animate-spin")),
          h("span", null, badge.label)
        )
      ),
      inputStr
        ? h(
            "details",
            { class: "border-border border-b", open: state === "input-streaming" ? "" : null },
            h(
              "summary",
              {
                class:
                  "cursor-pointer px-3 py-1.5 font-mono text-label-caps text-muted-foreground uppercase tracking-wider hover:text-foreground",
              },
              "Input"
            ),
            h(
              "pre",
              { class: "overflow-x-auto bg-muted/20 px-3 py-2 text-code-sm" },
              h("code", null, inputStr)
            )
          )
        : null,
      outputStr
        ? h(
            "details",
            { open: state === "output-error" || state === "output-available" ? "" : null },
            h(
              "summary",
              {
                class: cn(
                  "cursor-pointer px-3 py-1.5 font-mono text-label-caps uppercase tracking-wider hover:text-foreground",
                  state === "output-error" ? "text-destructive" : "text-muted-foreground"
                ),
              },
              state === "output-error" ? "Error" : "Output"
            ),
            h(
              "pre",
              {
                class: cn(
                  "overflow-x-auto px-3 py-2 text-code-sm",
                  state === "output-error" ? "bg-destructive/5" : "bg-muted/20"
                ),
              },
              h("code", null, outputStr)
            )
          )
        : null
    );
  }

  /* ── agent-tool-renderer ──────────────────────────────────────────── */

  var KIND_HINTS = [
    ["diff", ["diff", "patch"]],
    ["created-files", ["edit_file", "write_file", "create_file", "created_file"]],
    ["terminal", ["shell", "bash", "exec", "run_", "command", "vitest", "pytest"]],
    ["data-table", ["list_dir", "glob", "list_", "search", "grep", "find"]],
    ["code", ["read_file", "cat_file", "code", "read"]],
  ];

  function defaultClassifyTool(part) {
    var name = deriveToolName(part).toLowerCase();
    for (var i = 0; i < KIND_HINTS.length; i++) {
      var hints = KIND_HINTS[i][1];
      for (var j = 0; j < hints.length; j++) {
        if (name.indexOf(hints[j]) !== -1) return KIND_HINTS[i][0];
      }
    }
    return undefined;
  }

  function isRecord(v) {
    return typeof v === "object" && v !== null && !Array.isArray(v);
  }

  function defaultToolRegistry(part, kind) {
    var out = part.output;
    if (kind === "code") {
      return h(
        "pre",
        { class: "overflow-x-auto rounded-lg border border-border/40 bg-muted/20 px-3 py-2 font-mono text-code-sm" },
        h("code", null, safeStringify(out))
      );
    }
    if (kind === "terminal") {
      return Tk.TerminalPanel({
        lines: safeStringify(out)
          .split(/\r?\n/)
          .map(function (content, i) {
            return { id: "line-" + i, kind: "stdout", content: content };
          }),
      });
    }
    if (kind === "diff") {
      if (isRecord(out) && typeof out.path === "string" && Array.isArray(out.hunks)) {
        return Tk.DiffViewer({ path: out.path, stats: out.stats, hunks: out.hunks });
      }
      return ToolCallPart({ part: part });
    }
    if (kind === "created-files") {
      if (isRecord(out) && Array.isArray(out.files)) {
        return Tk.CreatedFilesCard({ files: out.files, title: out.title });
      }
      return ToolCallPart({ part: part });
    }
    if (kind === "data-table") {
      if (Array.isArray(out) && out.length > 0 && out.every(isRecord)) {
        var columns = Object.keys(out[0]);
        return h(
          "div",
          { class: "overflow-x-auto rounded-lg border border-border/40" },
          h(
            "table",
            { class: "w-full border-collapse font-mono text-code-sm" },
            h(
              "thead",
              null,
              h(
                "tr",
                { class: "border-border/40 border-b bg-muted/30" },
                columns.map(function (c) {
                  return h("th", { class: "px-3 py-1.5 text-left font-medium text-muted-foreground" }, c);
                })
              )
            ),
            h(
              "tbody",
              null,
              out.map(function (row) {
                return h(
                  "tr",
                  { class: "border-border/30 border-b" },
                  columns.map(function (c) {
                    return h("td", { class: "px-3 py-1.5" }, safeStringify(row[c]));
                  })
                );
              })
            )
          )
        );
      }
      return ToolCallPart({ part: part });
    }
    return ToolCallPart({ part: part });
  }

  function AgentToolRenderer(props) {
    props = props || {};
    var part = props.part || {};
    var renderer = null;
    if (part.state === "output-available") {
      var classify = props.classifyTool || defaultClassifyTool;
      var kind = classify(part);
      if (kind) {
        var registry = props.toolRenderers || defaultToolRegistry;
        renderer =
          typeof registry === "function"
            ? registry(part, kind)
            : registry[kind]
              ? registry[kind](part)
              : defaultToolRegistry(part, kind);
      }
    }
    return h(
      "div",
      { "data-slot": "agent-tool-renderer", class: "contents" },
      renderer || ToolCallPart({ part: part })
    );
  }

  /* ── chat-message ─────────────────────────────────────────────────── */

  function renderPart(part, opts) {
    opts = opts || {};
    var type = part.type || "";
    if (type === "text") return opts.text ? opts.text(part) : TextPart(part);
    if (type === "reasoning") return opts.reasoning ? opts.reasoning(part) : ReasoningPart(part);
    if (type === "reasoning-file") return null;
    if (type === "file") return opts.file ? opts.file(part) : FilePart(part);
    if (type === "source-url") return opts["source-url"] ? opts["source-url"](part) : SourceUrlPart(part);
    if (type === "source-document") {
      return opts["source-document"]
        ? opts["source-document"](part)
        : SourceDocumentPart(part);
    }
    if (type === "tool" || type.indexOf("tool-") === 0 || type === "dynamic-tool") {
      if (opts.tool) return opts.tool(part);
      return AgentToolRenderer({
        part: part,
        toolRenderers: opts.toolRenderers,
        classifyTool: opts.classifyTool,
      });
    }
    if (type.indexOf("data-") === 0) {
      if (opts.data) return opts.data(part);
      return DataPart({ part: part, renderers: opts.dataRenderers });
    }
    if (type === "step-start") {
      return h("hr", { class: "my-3 border-border", "aria-label": "Step boundary" });
    }
    return null;
  }

  function ChatMessageRoot(props) {
    props = props || {};
    var from = props.from || "assistant";
    return h(
      "div",
      {
        "data-slot": "chat-message-root",
        class: cn(
          "group flex w-full max-w-[95%] flex-col gap-2",
          from === "user"
            ? "is-user ml-auto justify-end"
            : from === "assistant"
              ? "is-assistant"
              : "is-system",
          props.class
        ),
        "data-theo-chat-message": from,
      },
      props.children
    );
  }

  function ChatMessageContent(props) {
    props = props || {};
    var variant = props.variant;
    return h(
      "div",
      {
        "data-slot": "chat-message-content",
        class: cn(
          "flex w-fit min-w-0 max-w-full flex-col gap-2 overflow-hidden text-body-md",
          "group-[.is-user]:ml-auto",
          variant !== "flat" &&
            "group-[.is-user]:rounded-2xl group-[.is-user]:rounded-tr-md group-[.is-user]:border group-[.is-user]:border-border/40 group-[.is-user]:bg-secondary group-[.is-user]:px-4 group-[.is-user]:py-3",
          variant === "contained" &&
            "group-[.is-assistant]:rounded-2xl group-[.is-assistant]:rounded-tl-md group-[.is-assistant]:border group-[.is-assistant]:border-border/40 group-[.is-assistant]:border-l-2 group-[.is-assistant]:border-l-primary group-[.is-assistant]:bg-card group-[.is-assistant]:px-5 group-[.is-assistant]:py-4 group-[.is-assistant]:shadow-sm",
          "group-[.is-system]:rounded-lg group-[.is-system]:border group-[.is-system]:border-accent-deep/40 group-[.is-system]:border-l-4 group-[.is-system]:bg-accent/10 group-[.is-system]:px-4 group-[.is-system]:py-2 group-[.is-system]:text-body-sm",
          "group-[.is-assistant]:text-foreground group-[.is-user]:text-secondary-foreground",
          props.class
        ),
        "data-theo-chat-content": "",
      },
      props.children
    );
  }

  function ChatMessage(props) {
    props = props || {};
    var message = props.message || { role: "assistant", parts: [] };
    var parts = message.parts || [];
    var variant =
      props.variant !== undefined ? props.variant : message.role === "assistant" ? "contained" : undefined;

    var content = ChatMessageContent({
      variant: variant,
      children: parts
        .map(function (part, idx) {
          return h(
            "div",
            { key: part.type + "-" + idx },
            renderPart(part, {
              dataRenderers: props.dataRenderers,
              partRenderers: props.partRenderers,
              toolRenderers: props.toolRenderers,
              classifyTool: props.classifyTool,
            })
          );
        })
        .concat([props.actions || null]),
    });

    if (message.role === "user") {
      return ChatMessageRoot({
        "data-slot": "chat-message",
        from: "user",
        children: [content, props.avatar ? h("div", { class: "shrink-0" }, props.avatar) : null],
      });
    }
    return ChatMessageRoot({
      "data-slot": "chat-message",
      from: message.role,
      children: [props.avatar ? h("div", { class: "shrink-0" }, props.avatar) : null, content],
    });
  }

  /* ── agent-stream ─────────────────────────────────────────────────── */

  function AgentStream(props) {
    props = props || {};
    var items = props.items || [];
    return h(
      "div",
      {
        "data-slot": "agent-stream",
        role: "log",
        "aria-live": "polite",
        "aria-relevant": "additions",
        "aria-atomic": "false",
        class: cn("flex flex-col gap-3", props.class),
      },
      items.map(function (item) {
        if (item.kind === "message") return ChatMessage({ message: item.message });
        if (item.kind === "tool-call")
          return Tk.ToolCallCard({
            tool: item.tool,
            icon: item.icon,
            target: item.target,
            status: item.status,
            output: item.output,
            defaultExpanded: item.defaultExpanded,
            timestamp: item.timestamp,
          });
        if (item.kind === "approval")
          return ApprovalCard({
            severity: item.severity,
            title: item.title,
            request: item.request,
            description: item.description,
            details: item.details,
            onApprove: item.onApprove,
            onDeny: item.onDeny,
            onAlways: item.onAlways,
          });
        if (item.kind === "error")
          return Tk.AgentErrorCard({
            kind: item.errorKind,
            title: item.title,
            detail: item.detail,
            actions: item.actions,
            timestamp: item.timestamp,
          });
        if (item.kind === "streaming")
          return Tk.AgentStreaming({ model: item.model, partial: item.partial });
        if (item.kind === "custom") return h("div", null, item.node);
        return null;
      })
    );
  }

  /* ── agent-timeline ───────────────────────────────────────────────── */

  function AgentTimeline(props) {
    props = props || {};
    var events = props.events || [];
    var showLine = props.showLine !== false;
    return h(
      "ol",
      {
        "data-slot": "agent-timeline",
        class: cn(
          "grid gap-1",
          showLine &&
            "relative pl-4 before:absolute before:top-1 before:bottom-1 before:left-[11px] before:w-px before:bg-border/60",
          props.class
        ),
      },
      events.map(function (event) {
        return h(
          "li",
          { class: "animate-fade-in-up" },
          Tk.AgentEvent({ event: event, collapsible: props.collapsible !== false })
        );
      })
    );
  }

  /* ── prompts (text / choice / multi-select / confirm) ─────────────── */

  function promptHeader(props) {
    return h(
      "header",
      { class: "flex items-start justify-between gap-3" },
      h(
        "div",
        { class: "grid gap-1" },
        h(
          "h4",
          { class: "font-display text-foreground text-title-md tracking-tight" },
          props.question || ""
        ),
        props.description
          ? h("p", { class: "text-body-sm text-muted-foreground" }, props.description)
          : null
      ),
      props.badge ? h("span", { class: "shrink-0" }, props.badge) : null
    );
  }

  function TextPrompt(props) {
    props = props || {};
    var value = props.value != null ? props.value : props.defaultValue || "";
    var input;
    function confirm() {
      if (props.required && !value.trim()) return;
      if (props.onConfirm) props.onConfirm({ value: value });
    }
    var card = h(
      "div",
      {
        "data-slot": "text-prompt",
        class: cn("grid gap-3 rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      promptHeader(props),
      props.multiline
        ? (input = h("textarea", {
            rows: String(props.rows || 3),
            placeholder: props.placeholder || "",
            "aria-label": "Answer",
            class:
              "min-h-[3rem] w-full resize-none rounded-lg border border-input bg-card px-3 py-2 text-body-md focus:outline-none focus:ring-2 focus:ring-ring",
          }))
        : (input = h("input", {
            type: "text",
            placeholder: props.placeholder || "",
            "aria-label": "Answer",
            class:
              "h-9 w-full rounded-lg border border-input bg-card px-3 text-body-md focus:outline-none focus:ring-2 focus:ring-ring",
          })),
      h(
        "div",
        { class: "flex items-center justify-end gap-2" },
        props.onCancel
          ? Button({ variant: "secondary", children: props.cancelLabel || "Cancel", onClick: props.onCancel })
          : null,
        props.onConfirm
          ? Button({ children: props.confirmLabel || "Confirm", onClick: confirm })
          : null
      )
    );
    input.value = value;
    input.addEventListener("input", function () {
      value = input.value;
      if (props.onValueChange) props.onValueChange(value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !props.multiline) {
        e.preventDefault();
        confirm();
      }
    });
    return card;
  }

  function ChoicePrompt(props) {
    props = props || {};
    var options = props.options || [];
    var value = props.value != null ? props.value : props.defaultValue || null;
    var otherText = props.otherText || "";
    var card = h(
      "div",
      {
        "data-slot": "choice-prompt",
        class: cn("grid gap-3 rounded-xl border border-border/40 bg-card p-4", props.class),
        role: "radiogroup",
      },
      promptHeader(props),
      h(
        "div",
        { class: "grid gap-2" },
        options.map(function (opt, idx) {
          var selected = value === opt.value;
          var row = h(
            "button",
            {
              type: "button",
              role: "radio",
              "aria-checked": selected ? "true" : "false",
              class: cn(
                "flex items-start gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
                selected
                  ? "border-primary/60 bg-primary/10"
                  : "border-border/40 hover:border-border/60"
              ),
              onClick: function () {
                value = opt.value;
                if (props.onValueChange) props.onValueChange(value);
                rerender();
              },
            },
            h(
              "span",
              {
                class: cn(
                  "mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border",
                  selected ? "border-primary" : "border-border"
                ),
              },
              selected ? h("span", { class: "size-2 rounded-full bg-primary" }) : null
            ),
            h(
              "div",
              { class: "flex flex-1 cursor-pointer flex-col items-start gap-0.5" },
              h("span", { class: "text-body-md text-foreground" }, opt.label),
              opt.description
                ? h("span", { class: "text-body-sm text-muted-foreground" }, opt.description)
                : null
            ),
            props.showNumbers !== false && idx < 9
              ? h(
                  "kbd",
                  {
                    class:
                      "mt-0.5 shrink-0 rounded border border-border bg-muted px-1.5 font-mono text-label text-muted-foreground",
                  },
                  String(idx + 1)
                )
              : null
          );
          return row;
        })
      ),
      props.allowOther
        ? h(
            "div",
            { class: "rounded-lg border border-border/60 bg-muted/40 p-3" },
            h(
              "p",
              {
                class:
                  "mb-2 font-sans text-label-caps text-muted-foreground uppercase tracking-wider",
              },
              props.otherLabel || "Other"
            ),
            h("input", {
              type: "text",
              placeholder: props.otherPlaceholder || "Type your answer…",
              "aria-label": props.otherLabel || "Other",
              class:
                "h-9 w-full rounded-lg border border-input bg-card px-3 font-mono text-code-sm",
            })
          )
        : null,
      props.onConfirm || props.onCancel
        ? h(
            "div",
            { class: "flex items-center justify-end gap-2" },
            props.onCancel
              ? Button({ variant: "secondary", children: props.cancelLabel || "Cancel", onClick: props.onCancel })
              : null,
            props.onConfirm
              ? Button({
                  children: props.confirmLabel || "Confirm",
                  onClick: function () {
                    props.onConfirm({ value: value, otherText: otherText || undefined });
                  },
                })
              : null
          )
        : null
    );
    function rerender() {
      var rows = card.querySelectorAll ? card.querySelectorAll('[role="radio"]') : [];
      for (var i = 0; i < rows.length; i++) {
        var btn = rows[i];
        var opt = options[i];
        var selected = value === opt.value;
        btn.setAttribute("aria-checked", selected ? "true" : "false");
        btn.className = cn(
          "flex items-start gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
          selected ? "border-primary/60 bg-primary/10" : "border-border/40 hover:border-border/60"
        );
      }
    }
    return card;
  }

  function MultiSelectPrompt(props) {
    props = props || {};
    var options = props.options || [];
    var selected = (props.value || props.defaultValue || []).slice();
    var card = h(
      "div",
      {
        "data-slot": "multi-select-prompt",
        class: cn("grid gap-3 rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      promptHeader(props),
      h(
        "fieldset",
        { class: "m-0 grid min-w-0 gap-2 border-0 p-0" },
        options.map(function (opt, idx) {
          var checked = selected.indexOf(opt.value) !== -1;
          return h(
            "label",
            {
              class: cn(
                "flex items-start gap-3 rounded-lg border px-3 py-2 transition-colors cursor-pointer",
                checked
                  ? "border-primary/60 bg-primary/10"
                  : "border-border/40 hover:border-border/60"
              ),
              "data-value": opt.value,
            },
            h("input", {
              type: "checkbox",
              checked: checked ? "" : null,
              "aria-label": opt.label,
              class:
                "mt-0.5 size-4 shrink-0 rounded border-border accent-primary",
              onChange: function (e) {
                var v = opt.value;
                var pos = selected.indexOf(v);
                if (e.target.checked && pos === -1) selected.push(v);
                if (!e.target.checked && pos !== -1) selected.splice(pos, 1);
                if (props.onValueChange) props.onValueChange(selected.slice());
                syncLabels();
              },
            }),
            h(
              "div",
              { class: "flex flex-1 cursor-pointer flex-col items-start gap-0.5" },
              h("span", { class: "text-body-md text-foreground" }, opt.label),
              opt.description
                ? h("span", { class: "text-body-sm text-muted-foreground" }, opt.description)
                : null
            ),
            props.showNumbers !== false && idx < 9
              ? h(
                  "kbd",
                  {
                    class:
                      "mt-0.5 shrink-0 rounded border border-border bg-muted px-1.5 font-mono text-label text-muted-foreground",
                  },
                  String(idx + 1)
                )
              : null
          );
        })
      ),
      props.onConfirm || props.onCancel
        ? h(
            "div",
            { class: "flex items-center justify-end gap-2" },
            props.onCancel
              ? Button({ variant: "secondary", children: props.cancelLabel || "Cancel", onClick: props.onCancel })
              : null,
            props.onConfirm
              ? Button({
                  children: props.confirmLabel || "Confirm",
                  onClick: function () {
                    props.onConfirm({ values: selected.slice() });
                  },
                })
              : null
          )
        : null
    );
    function syncLabels() {
      var labels = card.querySelectorAll ? card.querySelectorAll("label[data-value]") : [];
      for (var i = 0; i < labels.length; i++) {
        var lb = labels[i];
        var v = lb.getAttribute("data-value");
        var checked = selected.indexOf(v) !== -1;
        lb.className = cn(
          "flex items-start gap-3 rounded-lg border px-3 py-2 transition-colors cursor-pointer",
          checked ? "border-primary/60 bg-primary/10" : "border-border/40 hover:border-border/60"
        );
        var cb = lb.firstChild;
        if (cb) cb.setAttribute("checked", checked ? "" : null);
      }
    }
    return card;
  }

  function ConfirmPrompt(props) {
    props = props || {};
    var variant = props.variant || "primary";
    return h(
      "div",
      {
        "data-slot": "confirm-prompt",
        class: cn("grid gap-3 rounded-xl border border-border/40 bg-card p-4", props.class),
        role: variant === "destructive" ? "alertdialog" : null,
      },
      promptHeader(props),
      h(
        "div",
        { class: "flex items-center justify-end gap-2" },
        props.onCancel
          ? Button({ variant: "secondary", children: props.cancelLabel || "Cancel", onClick: props.onCancel })
          : null,
        props.onConfirm
          ? Button({
              variant: variant === "destructive" ? "destructive" : "primary",
              children: props.confirmLabel || "Confirm",
              onClick: props.onConfirm,
            })
          : null
      )
    );
  }

  /* ── permission-modal ─────────────────────────────────────────────── */

  function PermissionModal(props) {
    props = props || {};
    var p = props.permission || props;
    return h(
      "div",
      {
        "data-slot": "permission-modal",
        class: cn(
          "fixed inset-0 z-50 grid place-items-center bg-background/80 p-4",
          props.class
        ),
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "Permission request",
        style: { display: props.open === false ? "none" : "grid" },
      },
      h(
        "div",
        { class: cn("w-full max-w-xl rounded-xl border border-border bg-card p-4 shadow-lg", props.contentClass) },
        h(
          "header",
          { class: "flex items-center gap-2" },
          icons.alertTriangle("size-5 text-warning"),
          h(
            "span",
            { class: "font-display text-title-md tracking-tight" },
            props.title || "Permission request"
          ),
          p.tool
            ? h(
                "code",
                { class: "rounded-md bg-muted px-1.5 py-0.5 font-mono text-code-md text-primary" },
                p.tool
              )
            : null
        ),
        h(
          "div",
          { class: "mt-3 flex items-start gap-3 rounded-md border border-border/40 bg-muted/40 p-3" },
          h("span", { class: "mt-0.5 size-4 shrink-0 text-muted-foreground" }, icons.file("size-4")),
          h(
            "div",
            { class: "grid gap-1" },
            h("code", { class: "font-mono text-code-sm text-foreground" }, p.target || ""),
            p.reason
              ? h(
                  "p",
                  { class: "flex items-center gap-1.5 font-sans text-label text-warning" },
                  icons.info("size-3"),
                  p.reason
                )
              : null
          )
        ),
        h(
          "footer",
          { class: "mt-4 flex items-center justify-end gap-2" },
          props.onDeny
            ? Button({ variant: "secondary", children: "Deny", onClick: function () { props.onDeny(false); } })
            : null,
          props.onAllow
            ? Button({ children: "Allow", onClick: function () { props.onAllow(true); } })
            : null
        )
      )
    );
  }

  /* ── agent-editor ─────────────────────────────────────────────────── */

  function formField(labelText, control, hint) {
    return h(
      "div",
      { class: "grid gap-1.5" },
      h("label", { class: "font-medium text-body-sm text-foreground" }, labelText),
      control,
      hint ? h("p", { class: "text-label text-muted-foreground" }, hint) : null
    );
  }

  var TONES = [
    { id: "primary", label: "Primary (violet)" },
    { id: "accent", label: "Accent (sienna)" },
    { id: "success", label: "Success (green)" },
    { id: "warning", label: "Warning (amber)" },
    { id: "info", label: "Info (blue)" },
    { id: "muted", label: "Muted (neutral)" },
  ];

  function AgentEditor(props) {
    props = props || {};
    var initial = props.initial || {};
    var state = {
      name: initial.name || "",
      initials: initial.initials || "",
      description: typeof initial.description === "string" ? initial.description : "",
      tone: initial.tone || "primary",
      model: initial.model || (props.models && props.models[0] ? props.models[0].id : ""),
      systemPrompt: initial.systemPrompt || "",
      allowedToolsRaw: (initial.allowedTools || []).join(", "),
      skillsSelected: (initial.skillIds || []).slice(),
      modes: (initial.modes || []).slice(),
    };

    function chip(label, on) {
      return h(
        "button",
        {
          type: "button",
          "aria-pressed": on ? "true" : "false",
          class: cn(
            "inline-flex h-7 items-center rounded-full border px-3 font-mono text-body-sm transition-colors",
            on
              ? "border-primary bg-primary/15 text-primary"
              : "border-border/60 bg-card text-muted-foreground hover:text-foreground"
          ),
        },
        label
      );
    }

    var nameInput = h("input", {
      type: "text",
      placeholder: "Coder",
      class: "h-9 rounded-md border border-input bg-card px-3 text-body-sm",
    });
    nameInput.value = state.name;
    var initialsInput = h("input", {
      type: "text",
      placeholder: "CO",
      maxlength: "2",
      class: "h-9 rounded-md border border-input bg-card px-3 text-center font-mono text-body-sm uppercase",
    });
    initialsInput.value = state.initials;
    var descInput = h("input", {
      type: "text",
      placeholder: "Writes code, edits files, runs verification.",
      class: "h-9 rounded-md border border-input bg-card px-3 text-body-sm",
    });
    descInput.value = state.description;
    var promptArea = h("textarea", {
      rows: "6",
      placeholder: "You are the Coder. You write code, edit files, and run verification…",
      class: "min-h-[10rem] flex-1 rounded-md border border-input bg-card px-3 py-2 font-mono text-code-sm",
    });
    promptArea.value = state.systemPrompt;
    var toolsInput = h("input", {
      type: "text",
      placeholder: "Read, Edit, Bash",
      class: "h-9 rounded-md border border-input bg-card px-3 text-body-sm",
    });
    toolsInput.value = state.allowedToolsRaw;
    var toneSelect = h(
      "select",
      { class: "h-9 rounded-md border border-input bg-card px-2 text-body-sm" },
      TONES.map(function (t) {
        return h("option", { value: t.id, selected: t.id === state.tone ? "" : null }, t.label);
      })
    );
    toneSelect.addEventListener("change", function () {
      state.tone = toneSelect.value;
    });

    var skillsWrap = h("div", { class: "flex flex-wrap gap-1.5" });
    (props.skills || []).forEach(function (s) {
      var on = state.skillsSelected.indexOf(s.id) !== -1;
      var btn = chip(s.label, on);
      btn.addEventListener("click", function () {
        var pos = state.skillsSelected.indexOf(s.id);
        if (pos === -1) state.skillsSelected.push(s.id);
        else state.skillsSelected.splice(pos, 1);
        btn.setAttribute("aria-pressed", state.skillsSelected.indexOf(s.id) !== -1 ? "true" : "false");
        btn.className = cn(
          "inline-flex h-7 items-center rounded-full border px-3 font-mono text-body-sm transition-colors",
          state.skillsSelected.indexOf(s.id) !== -1
            ? "border-primary bg-primary/15 text-primary"
            : "border-border/60 bg-card text-muted-foreground hover:text-foreground"
        );
      });
      skillsWrap.appendChild(btn);
    });

    var modesWrap = h("div", { class: "flex flex-wrap gap-1.5" });
    var ALL_MODES = ["chat", "code", "infra"];
    ALL_MODES.forEach(function (m) {
      var on = state.modes.indexOf(m) !== -1;
      var btn = chip(m, on);
      btn.addEventListener("click", function () {
        var pos = state.modes.indexOf(m);
        if (pos === -1) state.modes.push(m);
        else state.modes.splice(pos, 1);
        btn.setAttribute("aria-pressed", state.modes.indexOf(m) !== -1 ? "true" : "false");
        btn.className = cn(
          "inline-flex h-7 items-center rounded-full border px-3 font-mono text-body-sm transition-colors",
          state.modes.indexOf(m) !== -1
            ? "border-primary bg-primary/15 text-primary"
            : "border-border/60 bg-card text-muted-foreground hover:text-foreground"
        );
      });
      modesWrap.appendChild(btn);
    });

    return h(
      "form",
      {
        "data-slot": "agent-editor",
        class: cn("flex h-full flex-col gap-4", props.class),
        onSubmit: function (e) {
          e.preventDefault();
          if (!nameInput.value.trim()) return;
          if (props.onSave)
            props.onSave({
              id: initial.id,
              name: nameInput.value.trim(),
              initials: initialsInput.value.trim() || undefined,
              description: descInput.value.trim() || undefined,
              tone: state.tone,
              model: state.model || undefined,
              systemPrompt: promptArea.value.trim() || undefined,
              allowedTools: toolsInput.value
                .split(",")
                .map(function (t) {
                  return t.trim();
                })
                .filter(Boolean),
              skillIds: state.skillsSelected.slice(),
              modes: state.modes.length > 0 ? state.modes.slice() : undefined,
            });
        },
      },
      h(
        "div",
        { class: "grid grid-cols-[1fr_auto] gap-3" },
        formField("Name", nameInput),
        formField("Initials", initialsInput)
      ),
      formField("Description", descInput),
      formField("Tone", toneSelect),
      formField("System prompt override", promptArea, "Leave empty to inherit the workspace default."),
      formField("Allowed tools", toolsInput),
      (props.skills || []).length > 0 ? formField("Linked skills", skillsWrap) : null,
      formField(
        "Active modes",
        modesWrap,
        state.modes.length === 0
          ? "Empty = global (available in every mode)."
          : "Only visible in: " + state.modes.join(", ") + "."
      ),
      h(
        "footer",
        { class: "flex items-center justify-between gap-2 border-border/40 border-t pt-4" },
        h("div", null, props.onDelete ? Button({ variant: "ghost", children: "Delete", onClick: props.onDelete }) : null),
        h(
          "div",
          { class: "flex items-center gap-2" },
          props.onCancel ? Button({ variant: "secondary", children: "Cancel", onClick: props.onCancel }) : null,
          Button({ type: "submit", children: initial.id ? "Save changes" : "Create agent" })
        )
      )
    );
  }

  /* ── rule-editor / skill-editor ───────────────────────────────────── */

  function genericEditor(props, opts) {
    props = props || {};
    var initial = props.initial || {};
    var nameInput = h("input", {
      type: "text",
      placeholder: opts.namePlaceholder,
      class: "h-9 rounded-md border border-input bg-card px-3 text-body-sm",
    });
    nameInput.value = initial.name || "";
    var descInput = h("textarea", {
      rows: "3",
      placeholder: opts.descPlaceholder,
      class: "min-h-[6rem] w-full rounded-md border border-input bg-card px-3 py-2 text-body-sm",
    });
    descInput.value = initial.description || "";
    var bodyArea = h("textarea", {
      rows: "10",
      class: "min-h-[12rem] flex-1 w-full rounded-md border border-input bg-card px-3 py-2 font-mono text-code-sm",
    });
    bodyArea.value = initial[opts.bodyField] || "";
    var tagsWrap = h("div", { class: "flex flex-wrap gap-1.5" });
    (initial.tags || []).forEach(function (t) {
      tagsWrap.appendChild(
        h(
          "span",
          { class: "inline-flex items-center rounded-full bg-muted px-2.5 py-1 font-mono text-label" },
          t
        )
      );
    });
    var enabledToggle = h("input", { type: "checkbox", checked: initial.enabled !== false ? "" : null, class: "size-4 accent-primary" });

    return h(
      "form",
      {
        "data-slot": opts.slot,
        class: cn("flex h-full flex-col gap-4", props.class),
        onSubmit: function (e) {
          e.preventDefault();
          if (props.onSave)
            props.onSave({
              id: initial.id,
              name: nameInput.value.trim(),
              description: descInput.value.trim(),
              enabled: enabledToggle.checked,
            });
        },
      },
      formField(opts.nameLabel, nameInput),
      formField("Description", descInput),
      formField(opts.bodyLabel, bodyArea),
      formField("Tags", tagsWrap),
      h(
        "label",
        { class: "flex items-center gap-3 text-body-sm text-muted-foreground" },
        enabledToggle,
        "Enabled"
      ),
      h(
        "footer",
        { class: "flex items-center justify-between gap-2 border-border/40 border-t pt-4" },
        h("div", null, props.onDelete ? Button({ variant: "ghost", children: "Delete", onClick: props.onDelete }) : null),
        h(
          "div",
          { class: "flex items-center gap-2" },
          props.onCancel ? Button({ variant: "secondary", children: "Cancel", onClick: props.onCancel }) : null,
          Button({ type: "submit", children: opts.saveLabel })
        )
      )
    );
  }

  function RuleEditor(props) {
    return genericEditor(props, {
      slot: "rule-editor",
      nameLabel: "Rule name",
      namePlaceholder: "Always run tests before commit",
      descPlaceholder: "What this rule enforces…",
      bodyLabel: "Rule content",
      bodyField: "content",
      saveLabel: "Save rule",
    });
  }

  function SkillEditor(props) {
    return genericEditor(props, {
      slot: "skill-editor",
      nameLabel: "Skill name",
      namePlaceholder: "release-notes",
      descPlaceholder: "Drafts release notes from git history…",
      bodyLabel: "SKILL.md",
      bodyField: "body",
      saveLabel: "Save skill",
    });
  }

  /* ── lists (cron jobs / mcp servers / skills) ─────────────────────── */

  function CronJobsList(props) {
    props = props || {};
    var jobs = props.jobs || [];
    return h(
      "div",
      { "data-slot": "cron-jobs-list", class: cn("grid gap-3", props.class) },
      h(
        "div",
        { class: "flex items-baseline justify-between" },
        h("h3", { class: "font-display text-title-md tracking-tight" }, props.title || "Scheduled jobs"),
        h(
          "span",
          { class: "flex items-center gap-3 font-mono text-label text-muted-foreground" },
          String(jobs.length) + " jobs"
        )
      ),
      h(
        "div",
        { class: "grid grid-cols-1 gap-3 md:grid-cols-2" },
        jobs.map(function (job) {
          return Tk.CronJobCard({ job: job, onToggle: props.onToggle, onRun: props.onRun });
        })
      )
    );
  }

  function McpServerList(props) {
    props = props || {};
    var servers = props.servers || [];
    var mode = props.mode || "grid";
    return h(
      "div",
      { "data-slot": "mcp-server-list", class: cn("grid gap-3", props.class) },
      h(
        "div",
        { class: "flex flex-wrap items-center justify-between gap-3" },
        h("h3", { class: "font-display text-title-md tracking-tight" }, props.title || "MCP servers"),
        h(
          "div",
          { class: "inline-flex rounded-lg border border-border/60 bg-muted p-0.5" },
          ["grid", "list"].map(function (m) {
            return h(
              "button",
              {
                type: "button",
                class: cn(
                  "rounded-md px-2.5 py-1 font-mono text-label transition-colors",
                  mode === m ? "bg-card text-foreground shadow-sm" : "text-muted-foreground"
                ),
                onClick: function () {
                  if (props.onViewChange) props.onViewChange(m);
                },
              },
              m
            );
          })
        )
      ),
      h(
        "div",
        { class: cn("grid grid-cols-1 gap-3", mode === "grid" && "md:grid-cols-2") },
        servers.map(function (s) {
          return Tk.McpServerCard({ server: s, onToggle: props.onToggle });
        })
      )
    );
  }

  function SkillsList(props) {
    props = props || {};
    var skills = props.skills || [];
    var query = "";
    var listWrap = h("div", { class: "grid grid-cols-1 gap-3 md:grid-cols-2" });
    function renderList() {
      while (listWrap.firstChild) listWrap.removeChild(listWrap.firstChild);
      skills
        .filter(function (s) {
          return (
            !query ||
            String(s.name || "")
              .toLowerCase()
              .indexOf(query.toLowerCase()) !== -1
          );
        })
        .forEach(function (s) {
          listWrap.appendChild(h("div", null, Tk.SkillCard({ skill: s, onClick: props.onOpen })));
        });
    }
    var searchInput = h("input", {
      type: "text",
      placeholder: "Search skills…",
      "aria-label": "Search skills",
      class: "h-9 w-64 rounded-lg border border-input bg-card pl-8 pr-3 text-body-sm",
    });
    searchInput.addEventListener("input", function () {
      query = searchInput.value;
      renderList();
    });
    renderList();
    return h(
      "div",
      { "data-slot": "skills-list", class: cn("grid gap-3", props.class) },
      h(
        "div",
        { class: "flex flex-wrap items-center justify-between gap-3" },
        h("h3", { class: "font-display text-title-md tracking-tight" }, "Skills"),
        h("div", { class: "relative" }, searchInput)
      ),
      listWrap
    );
  }

  /* ── preview-panel ────────────────────────────────────────────────── */

  function PreviewPanel(props) {
    props = props || {};
    return h(
      "div",
      {
        "data-slot": "preview-panel",
        class: cn("flex flex-col overflow-hidden rounded-xl border border-border/40 bg-card", props.class),
      },
      props.header,
      h("div", { class: "flex-1 overflow-hidden bg-background" }, props.children),
      props.footer
        ? h(
            "div",
            { class: "max-h-48 overflow-auto border-border/40 border-t bg-card" },
            props.footer
          )
        : null
    );
  }

  /* ── slide-deck ───────────────────────────────────────────────────── */

  function SlideDeck(props) {
    props = props || {};
    var slides = props.slides || [];
    if (typeof slides === "string") {
      slides = slides.split(/\n---+\n/).filter(Boolean);
    }
    var index = props.initialIndex || 0;
    var deck = h(
      "div",
      {
        "data-slot": "slide-deck",
        class: cn("grid gap-3", props.class),
        role: "region",
        "aria-label": props["aria-label"] || "Slide deck",
      }
    );

    var stage = h("div", null);
    var counter = h(
      "span",
      { class: "font-mono text-label text-muted-foreground tabular-nums" },
      String(index + 1) + " / " + String(slides.length)
    );

    function renderSlide() {
      while (stage.firstChild) stage.removeChild(stage.firstChild);
      if (slides[index]) {
        stage.appendChild(Tk.Slide({ markdown: slides[index], aspectRatio: props.aspectRatio }));
      }
      counter.textContent = String(index + 1) + " / " + String(slides.length);
    }

    function nav(delta) {
      var next = Math.max(0, Math.min(slides.length - 1, index + delta));
      if (next !== index) {
        index = next;
        renderSlide();
        if (props.onIndexChange) props.onIndexChange(index, slides[index]);
      }
    }

    deck.appendChild(stage);
    deck.appendChild(
      h(
        "div",
        { class: "flex items-center justify-between gap-2" },
        Button({
          variant: "secondary",
          size: "sm",
          children: [icons.chevronLeft("size-3.5"), "Prev"],
          onClick: function () {
            nav(-1);
          },
        }),
        counter,
        Button({
          variant: "secondary",
          size: "sm",
          children: ["Next", icons.chevronRight("size-3.5")],
          onClick: function () {
            nav(1);
          },
        })
      )
    );
    renderSlide();
    return deck;
  }

  /* ── stability-bundle-viewer ──────────────────────────────────────── */

  function StabilityBundleViewer(props) {
    props = props || {};
    var bundle = props.bundle || {};
    var sections = bundle.sections || [];
    return h(
      "div",
      {
        "data-slot": "stability-bundle-viewer",
        class: cn("rounded-lg border border-border bg-card", props.class),
      },
      h(
        "button",
        {
          type: "button",
          class:
            "flex w-full items-center justify-between px-3 py-2 text-left font-medium text-sm hover:bg-muted/30",
          onClick: function (e) {
            var body = e.currentTarget.nextSibling;
            if (body.style.display === "none") body.style.display = "";
            else body.style.display = "none";
          },
        },
        h("span", null, bundle.title || "Stability bundle"),
        icons.chevronDown("size-3.5")
      ),
      h(
        "div",
        { class: "border-border border-t px-3 py-2" },
        h(
          "div",
          { class: "flex flex-wrap gap-2" },
          (bundle.artifacts || []).map(function (a) {
            return h(
              "div",
              { class: "flex items-start gap-3 rounded border border-border bg-card p-3" },
              h("div", { class: "flex-1" },
                h("div", { class: "flex items-baseline gap-2" },
                  h("span", { class: "font-medium text-sm" }, a.name || ""),
                  h("span", { class: "text-muted-foreground text-xs" }, a.version || "")),
                a.description ? h("p", { class: "mt-1 text-sm text-muted-foreground" }, a.description) : null),
              a.sha ? h("code", { class: "rounded border border-border px-2 py-1 text-xs font-mono" }, a.sha.slice(0, 8)) : null
            );
          })
        ),
        sections.length
          ? h(
              "div",
              { class: "mt-2 overflow-x-auto rounded bg-muted/30 p-2" },
              h(
                "table",
                { class: "w-full text-xs" },
                h(
                  "thead",
                  null,
                  h(
                    "tr",
                    null,
                    ["Section", "Bytes"].map(function (c) {
                      return h("th", { class: "text-left text-muted-foreground py-1 pr-2 font-mono" }, c);
                    })
                  )
                ),
                h(
                  "tbody",
                  null,
                  sections.map(function (s) {
                    return h(
                      "tr",
                      null,
                      h("td", { class: "py-1 pr-2 font-mono" }, s.name || ""),
                      h("td", { class: "py-1 font-mono" }, String(s.bytes || 0))
                    );
                  })
                )
              )
            )
          : null
      )
    );
  }

  /* ── usage-meter ──────────────────────────────────────────────────── */

  function UsageMeter(props) {
    props = props || {};
    var metrics = props.metrics || [];
    var compact = !!props.compact;
    var max = 1;
    metrics.forEach(function (m) {
      max = Math.max(max, m.value || 0);
    });
    return h(
      "div",
      {
        "data-slot": "usage-meter",
        class: cn("rounded-xl border border-border/40 bg-card p-4", props.class),
      },
      compact
        ? null
        : h(
            "div",
            { class: "flex items-baseline justify-between gap-3" },
            h("h4", { class: "font-display text-title-md tracking-tight" }, props.title || "Usage"),
            h("span", { class: "shrink-0" }, props.action || null)
          ),
      h(
        "div",
        { class: compact ? "grid gap-1.5" : "mt-3 grid gap-1.5" },
        metrics.map(function (m) {
          var pct = Math.round(((m.value || 0) / max) * 100);
          return h(
            "div",
            { class: "grid gap-1" },
            compact
              ? null
              : h(
                  "div",
                  { class: "flex items-baseline justify-between gap-3 text-body-sm" },
                  h("span", { class: "truncate text-muted-foreground" }, m.label || ""),
                  h("span", { class: "font-mono text-label text-foreground tabular-nums" }, String(m.value || 0))
                ),
            h(
              "div",
              { class: "h-1.5 w-full overflow-hidden rounded-full bg-muted" },
              h("div", {
                class: cn("h-full rounded-full", m.tone === "warning" ? "bg-warning" : m.tone === "danger" ? "bg-destructive" : "bg-primary"),
                style: { width: Math.max(2, pct) + "%" },
              })
            )
          );
        })
      )
    );
  }

  Object.assign(Tk, {
    ApprovalCard: ApprovalCard,
    ChatComposer: ChatComposer,
    AgentComposer: AgentComposer,
    ChatMessage: ChatMessage,
    ChatMessageRoot: ChatMessageRoot,
    ChatMessageContent: ChatMessageContent,
    ChatMessageResponse: ChatMessageResponse,
    ChatMessageActions: ChatMessageActions,
    ChatMessageAction: ChatMessageAction,
    ChatMessageToolbar: ChatMessageToolbar,
    ChatMessageBranch: ChatMessageBranch,
    ChatMessageBranchContent: ChatMessageBranchContent,
    ChatMessageBranchSelector: ChatMessageBranchSelector,
    ChatMessageBranchPrevious: ChatMessageBranchPrevious,
    ChatMessageBranchNext: ChatMessageBranchNext,
    ChatMessageBranchPage: ChatMessageBranchPage,
    renderPart: renderPart,
    TextPart: TextPart,
    ReasoningPart: ReasoningPart,
    FilePart: FilePart,
    SourceUrlPart: SourceUrlPart,
    SourceDocumentPart: SourceDocumentPart,
    DataPart: DataPart,
    CodeBlock: CodeBlock,
    ToolCallPart: ToolCallPart,
    AgentToolRenderer: AgentToolRenderer,
    defaultClassifyTool: defaultClassifyTool,
    defaultToolRegistry: defaultToolRegistry,
    AgentStream: AgentStream,
    AgentTimeline: AgentTimeline,
    TextPrompt: TextPrompt,
    ChoicePrompt: ChoicePrompt,
    MultiSelectPrompt: MultiSelectPrompt,
    ConfirmPrompt: ConfirmPrompt,
    PermissionModal: PermissionModal,
    AgentEditor: AgentEditor,
    RuleEditor: RuleEditor,
    SkillEditor: SkillEditor,
    CronJobsList: CronJobsList,
    McpServerList: McpServerList,
    SkillsList: SkillsList,
    PreviewPanel: PreviewPanel,
    SlideDeck: SlideDeck,
    StabilityBundleViewer: StabilityBundleViewer,
    UsageMeter: UsageMeter,
    detectTrigger: detectTrigger,
  });
  if (typeof module !== "undefined" && module.exports) module.exports = Tk;
})(typeof window !== "undefined" ? window : globalThis);
