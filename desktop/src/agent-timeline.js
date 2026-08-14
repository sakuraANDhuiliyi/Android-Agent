(() => {
  "use strict";

  /**
   * Agent timeline renderer — the single rendering authority for the Agent
   * panel. Consumes normalized TimelineItems from timeline.js, groups them
   * into TurnViewModels (user prompt / collapsible Worked session / final
   * answer) and reconciles them into the DOM.
   *
   * Security: all model/tool/approval payloads are untrusted. Everything is
   * built with DOM APIs and text nodes — no innerHTML with untrusted content,
   * no script/style/iframe/object, no javascript:/data: URLs, external links
   * get rel="noopener noreferrer".
   *
   * State: expansion state lives in JavaScript view-state maps (per turnId /
   * itemKey), never only in DOM.hidden, so streaming updates and DOM patches
   * never lose it.
   */

  const STATUS_LABEL = {
    running: "运行中",
    success: "成功",
    failed: "失败",
    waiting_approval: "等待批准",
    canceled: "已取消",
    rejected: "已拒绝",
    timeout: "已超时",
    done: "完成",
    streaming: "生成中",
    queued: "排队中",
    pending: "待处理",
    succeeded: "已完成",
    interrupted: "已中断",
    approved: "已允许",
    paused: "已暂停",
  };

  const TURN_STATUS_LABEL = {
    running: "运行中",
    awaiting_approval: "等待审批",
    succeeded: "已完成",
    failed: "失败",
    canceled: "已取消",
    interrupted: "已中断",
    paused: "已暂停",
    queued: "排队中",
    working: "工作中",
  };

  const TOOL_LABEL = {
    run_command: "运行命令",
    run_gradle: "Gradle 构建",
    write_file: "写入文件",
    str_replace: "修改文件",
    read_file: "读取文件",
    list_files: "列出文件",
    search_code: "搜索代码",
    search_files: "搜索文件",
    apply_patch: "应用补丁",
    web_search: "网络搜索",
    download_file: "下载文件",
    git_status: "Git 状态",
    git_diff: "Git Diff",
  };

  const APPROVAL_TITLE = {
    process: "运行命令",
    network: "访问网络",
    download_file: "下载文件",
    workspace_outside: "写入工作区外",
    background_process: "启动后台进程",
    mcp_tool: "调用外部 MCP 工具",
    recovery_tool_replay: "重放中断的工具调用",
    hook: "Hook 请求操作",
    hook_action: "Hook 请求操作",
    worktree_finalize: "合并 Worktree 改动",
    tool: "执行工具",
  };

  const RISK_LABEL = {
    read: "只读",
    workspace_write: "工作区写入",
    network: "网络",
    process: "进程",
    destructive: "破坏性",
  };

  const DECISION_LABEL = {
    approved: "已允许",
    rejected: "已拒绝",
    timeout: "已超时",
    canceled: "已取消",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function formatTime(ts) {
    if (!ts) return "";
    const date = new Date(ts > 1e12 ? ts : ts * 1000);
    if (Number.isNaN(date.getTime())) return "";
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    const ss = String(date.getSeconds()).padStart(2, "0");
    return `${hh}:${mm}:${ss}`;
  }

  function formatDuration(ms) {
    if (ms == null || Number.isNaN(Number(ms))) return "";
    const v = Number(ms);
    if (v < 1000) return `${v}ms`;
    return `${(v / 1000).toFixed(1)}s`;
  }

  /** Human duration like "4 分 19 秒" for Worked-session headers. */
  function formatWorked(ms) {
    if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
    const totalSec = Math.round(ms / 1000);
    if (totalSec < 1) return "不到 1 秒";
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) return `${h} 小时 ${m} 分`;
    if (m > 0) return s > 0 ? `${m} 分 ${s} 秒` : `${m} 分`;
    return `${s} 秒`;
  }

  function formatBytes(n) {
    const v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return "";
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
    return `${(v / 1024 / 1024).toFixed(1)} MB`;
  }

  function toolLabel(name) {
    if (!name) return "工具";
    if (TOOL_LABEL[name]) return TOOL_LABEL[name];
    if (String(name).startsWith("mcp__")) return "MCP 工具";
    return String(name);
  }

  function toolSummary(item) {
    const name = item.content.name || "";
    const input = item.content.input;
    if (!input || typeof input !== "object") return toolLabel(name);
    if (Array.isArray(input.argv)) return input.argv.join(" ");
    if (typeof input.command === "string") return input.command;
    if (typeof input.path === "string") return input.path;
    if (typeof input.pattern === "string") return input.pattern;
    if (typeof input.query === "string") return input.query;
    if (typeof input.task === "string") return `gradle ${input.task}`;
    if (typeof input.url === "string") return input.url;
    return toolLabel(name);
  }

  // ————————————————————————————————————————————————
  // Safe Markdown renderer (DOM API only, no innerHTML)
  // Supports: paragraphs + soft breaks, H1–H4, nested ordered/unordered
  // lists, task lists, bold/italic/strikethrough, inline code, fenced code
  // blocks, blockquotes, tables, http(s)/mailto links, project file links.
  // opts.tolerant=true is used while streaming: unclosed fences/bold/code
  // auto-close instead of leaking raw markers or breaking the structure.
  // ————————————————————————————————————————————————

  const FILE_LINK_RE =
    /([\w@./\\-]+\.(?:kt|kts|java|xml|gradle|py|js|jsx|ts|tsx|json|md|css|html|sh|toml|properties|yaml|yml|txt))(?::(\d+))?/g;
  const SAFE_LINK_PROTOCOL = /^(https?:|mailto:)/i;

  function isSafeUrl(url) {
    const u = String(url || "").trim();
    if (!u) return false;
    // Reject javascript:, data:, vbscript: and any other non-whitelisted scheme.
    return SAFE_LINK_PROTOCOL.test(u);
  }

  function appendPlainWithFileLinks(container, text, callbacks) {
    if (!callbacks || typeof callbacks.openFile !== "function") {
      container.appendChild(document.createTextNode(text));
      return;
    }
    FILE_LINK_RE.lastIndex = 0;
    let last = 0;
    let match;
    while ((match = FILE_LINK_RE.exec(text)) !== null) {
      // Skip when clearly part of a URL.
      const before = text.slice(Math.max(0, match.index - 8), match.index);
      if (/https?:\/\/[^\s]*$/.test(before)) continue;
      if (match.index > last) container.appendChild(document.createTextNode(text.slice(last, match.index)));
      const pathText = match[1];
      const line = match[2] ? Number(match[2]) : 0;
      const a = el("a", "md-file", match[0]);
      a.href = "#";
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        callbacks.openFile(pathText, line);
      });
      container.appendChild(a);
      last = match.index + match[0].length;
    }
    if (last < text.length) container.appendChild(document.createTextNode(text.slice(last)));
  }

  /**
   * Recursive inline parser. Handles `code`, **bold**, *italic*, ~~strike~~,
   * [label](url). Never emits raw markup characters for unclosed constructs
   * in tolerant mode; in strict mode unmatched markers stay literal text.
   */
  function parseInline(text, container, callbacks, opts) {
    const tolerant = Boolean(opts && opts.tolerant);
    let i = 0;
    let buf = "";
    const flush = () => {
      if (buf) {
        appendPlainWithFileLinks(container, buf, callbacks);
        buf = "";
      }
    };
    while (i < text.length) {
      const ch = text[i];
      // Inline code
      if (ch === "`") {
        const close = text.indexOf("`", i + 1);
        if (close > i) {
          flush();
          container.appendChild(el("code", "md-code", text.slice(i + 1, close)));
          i = close + 1;
          continue;
        }
        if (tolerant) {
          flush();
          container.appendChild(el("code", "md-code", text.slice(i + 1)));
          i = text.length;
          continue;
        }
        buf += ch;
        i += 1;
        continue;
      }
      // Bold **…**
      if (ch === "*" && text[i + 1] === "*") {
        const close = text.indexOf("**", i + 2);
        if (close > i + 1) {
          flush();
          const strong = el("strong");
          parseInline(text.slice(i + 2, close), strong, callbacks, opts);
          container.appendChild(strong);
          i = close + 2;
          continue;
        }
        if (tolerant) {
          flush();
          const strong = el("strong");
          parseInline(text.slice(i + 2), strong, callbacks, opts);
          container.appendChild(strong);
          i = text.length;
          continue;
        }
        buf += ch;
        i += 1;
        continue;
      }
      // Strikethrough ~~…~~
      if (ch === "~" && text[i + 1] === "~") {
        const close = text.indexOf("~~", i + 2);
        if (close > i + 1) {
          flush();
          const del = el("del");
          parseInline(text.slice(i + 2, close), del, callbacks, opts);
          container.appendChild(del);
          i = close + 2;
          continue;
        }
        buf += ch;
        i += 1;
        continue;
      }
      // Italic *…* (single). Avoid list-marker false positives at line start
      // by requiring a non-space after the marker.
      if (ch === "*" && text[i + 1] && text[i + 1] !== " " && text[i + 1] !== "*") {
        let close = -1;
        for (let j = i + 1; j < text.length; j += 1) {
          if (text[j] === "*" && text[j - 1] !== "*" && text[j + 1] !== "*") {
            close = j;
            break;
          }
        }
        if (close > i + 1) {
          flush();
          const em = el("em");
          parseInline(text.slice(i + 1, close), em, callbacks, opts);
          container.appendChild(em);
          i = close + 1;
          continue;
        }
        buf += ch;
        i += 1;
        continue;
      }
      // Link [label](url)
      if (ch === "[") {
        const closeLabel = text.indexOf("](", i + 1);
        if (closeLabel > i) {
          const closeUrl = text.indexOf(")", closeLabel + 2);
          if (closeUrl > closeLabel + 2) {
            const label = text.slice(i + 1, closeLabel);
            const url = text.slice(closeLabel + 2, closeUrl).trim();
            flush();
            if (isSafeUrl(url)) {
              const a = el("a", "md-link", label);
              a.href = url;
              a.rel = "noopener noreferrer";
              a.target = "_blank";
              container.appendChild(a);
            } else {
              // Unsafe scheme (javascript:, data:…): render label as text.
              appendPlainWithFileLinks(container, label, callbacks);
            }
            i = closeUrl + 1;
            continue;
          }
        }
        buf += ch;
        i += 1;
        continue;
      }
      buf += ch;
      i += 1;
    }
    flush();
  }

  function renderCodeBlock(container, lang, body, callbacks) {
    const block = el("div", "md-codeblock");
    const head = el("div", "md-codeblock-head");
    head.appendChild(el("span", "md-codeblock-lang", lang || "text"));
    const copyBtn = el("button", "md-copy", "复制");
    copyBtn.type = "button";
    copyBtn.addEventListener("click", () => {
      if (callbacks && typeof callbacks.copyText === "function") callbacks.copyText(body.join("\n"));
    });
    head.appendChild(copyBtn);
    block.appendChild(head);
    const pre = el("pre", "md-pre");
    pre.textContent = body.join("\n");
    block.appendChild(pre);
    container.appendChild(block);
  }

  function renderTable(container, rows) {
    // rows: { header: [cells], align: [...], body: [[cells]] }
    const wrap = el("div", "md-table-wrap");
    const table = el("table", "md-table");
    const thead = el("thead");
    const headRow = el("tr");
    rows.header.forEach((cell, idx) => {
      const th = el("th");
      parseInline(cell, th, null, {});
      if (rows.align[idx]) th.style.textAlign = rows.align[idx];
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const row of rows.body) {
      const tr = el("tr");
      rows.header.forEach((_, idx) => {
        const td = el("td");
        parseInline(row[idx] != null ? row[idx] : "", td, null, {});
        if (rows.align[idx]) td.style.textAlign = rows.align[idx];
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    container.appendChild(wrap);
  }

  function splitTableRow(line) {
    let t = line.trim();
    if (t.startsWith("|")) t = t.slice(1);
    if (t.endsWith("|")) t = t.slice(0, -1);
    return t.split("|").map((c) => c.trim());
  }

  function isTableSeparator(line) {
    if (!line || line.indexOf("|") === -1 && line.indexOf("-") === -1) return false;
    const cells = splitTableRow(line);
    if (!cells.length) return false;
    return cells.every((c) => /^:?-{2,}:?$/.test(c.trim()) || /^:-+:?$/.test(c.trim()));
  }

  const LIST_ITEM_RE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;

  /**
   * Parse a run of list lines into nested ul/ol. Supports nesting by
   * indentation and task-list checkboxes (- [ ] / - [x]).
   */
  function renderListBlock(container, lines, callbacks, opts) {
    // Build a flat sequence of {indent, ordered, text, task} then nest.
    const entries = [];
    for (const line of lines) {
      const m = line.match(LIST_ITEM_RE);
      if (!m) continue;
      const indent = m[1].replace(/\t/g, "  ").length;
      const ordered = /^\d/.test(m[2]);
      let text = m[3];
      let task = null;
      const taskMatch = text.match(/^\[([ xX])\]\s+(.*)$/);
      if (taskMatch) {
        task = taskMatch[1].toLowerCase() === "x";
        text = taskMatch[2];
      }
      entries.push({ indent, ordered, text, task });
    }
    if (!entries.length) return;

    // Multiple sibling root lists are possible (e.g. a bullet list directly
    // followed by a numbered list at the same indent).
    const roots = [];
    const stack = [];
    const newRoot = (entry) => {
      const r = { ordered: entry.ordered, indent: entry.indent, node: null, children: [] };
      roots.push(r);
      stack.length = 0;
      stack.push(r);
      return r;
    };
    newRoot(entries[0]);
    for (const entry of entries) {
      let top = stack[stack.length - 1];
      while (stack.length > 1 && entry.indent < top.indent) {
        stack.pop();
        top = stack[stack.length - 1];
      }
      // Back at the root level with a different marker kind -> new root list.
      if (stack.length === 1 && entry.indent <= top.indent && entry.ordered !== top.ordered) {
        top = newRoot(entry);
      }
      if (entry.indent > top.indent && top.children.length) {
        // Nest under the previous item.
        const parent = top.children[top.children.length - 1];
        const sub = { ordered: entry.ordered, indent: entry.indent, node: null, children: [], parent };
        parent.sublists.push(sub);
        stack.push(sub);
      } else {
        top.children.push({ text: entry.text, task: entry.task, sublists: [] });
      }
    }

    const buildList = (group) => {
      const list = el(group.ordered ? "ol" : "ul", "md-list");
      for (const child of group.children) {
        const li = el("li", "md-li");
        if (child.task != null) {
          li.classList.add("md-task");
          const box = el("span", child.task ? "md-task-box checked" : "md-task-box");
          box.setAttribute("aria-hidden", "true");
          box.textContent = child.task ? "✓" : "";
          li.appendChild(box);
        }
        const span = el("span", "md-li-text");
        parseInline(child.text, span, callbacks, opts);
        li.appendChild(span);
        for (const sub of child.sublists) li.appendChild(buildList(sub));
        list.appendChild(li);
      }
      return list;
    };
    for (const r of roots) container.appendChild(buildList(r));
  }

  function renderMarkdown(container, text, callbacks, opts = {}) {
    container.textContent = "";
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    let i = 0;
    let paragraph = [];

    const flushParagraph = () => {
      if (!paragraph.length) return;
      const p = el("p", "md-p");
      // Soft breaks: a single newline inside a paragraph stays a break
      // (CSS white-space: pre-line renders it) without breaking inline markup.
      parseInline(paragraph.join("\n"), p, callbacks, opts);
      container.appendChild(p);
      paragraph = [];
    };

    while (i < lines.length) {
      const line = lines[i];

      // Fenced code block (``` or ~~~). While streaming an unclosed fence
      // simply consumes the rest — the structure never breaks.
      const fence = line.match(/^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/);
      if (fence) {
        flushParagraph();
        const marker = fence[1][0];
        const lang = fence[2] || "";
        const body = [];
        i += 1;
        const closeRe = new RegExp(`^ {0,3}\\${marker}{3,}\\s*$`);
        while (i < lines.length && !closeRe.test(lines[i])) {
          body.push(lines[i]);
          i += 1;
        }
        i += 1; // skip closing fence (or run past EOF while streaming)
        renderCodeBlock(container, lang, body, callbacks);
        continue;
      }

      // Heading H1–H4 (deeper levels clamp to H4 visual size via CSS).
      const heading = line.match(/^ {0,3}(#{1,6})\s+(.*)$/);
      if (heading) {
        flushParagraph();
        const level = Math.min(Math.max(heading[1].length, 1), 4);
        const h = el(`h${level}`, `md-h md-h${level}`);
        parseInline(heading[2].replace(/\s+#+\s*$/, ""), h, callbacks, opts);
        container.appendChild(h);
        i += 1;
        continue;
      }

      // Blockquote (may span multiple lines, may contain paragraphs).
      if (/^ {0,3}>\s?/.test(line)) {
        flushParagraph();
        const quote = [];
        while (i < lines.length && /^ {0,3}>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^ {0,3}>\s?/, ""));
          i += 1;
        }
        const q = el("blockquote", "md-quote");
        renderMarkdown(q, quote.join("\n"), callbacks, opts);
        container.appendChild(q);
        continue;
      }

      // Table: a | line followed by a separator line.
      if (
        line.indexOf("|") !== -1 &&
        i + 1 < lines.length &&
        isTableSeparator(lines[i + 1])
      ) {
        flushParagraph();
        const header = splitTableRow(line);
        const align = splitTableRow(lines[i + 1]).map((c) => {
          const t = c.trim();
          if (t.startsWith(":") && t.endsWith(":")) return "center";
          if (t.endsWith(":")) return "right";
          return "";
        });
        i += 2;
        const body = [];
        while (i < lines.length && lines[i].indexOf("|") !== -1 && lines[i].trim() !== "") {
          body.push(splitTableRow(lines[i]));
          i += 1;
        }
        renderTable(container, { header, align, body });
        continue;
      }

      // Lists (nested + task lists).
      if (LIST_ITEM_RE.test(line)) {
        flushParagraph();
        const listLines = [];
        while (i < lines.length) {
          const l = lines[i];
          if (LIST_ITEM_RE.test(l)) {
            listLines.push(l);
            i += 1;
          } else if (/^\s{2,}\S/.test(l) && listLines.length) {
            // Indented continuation of the previous item.
            listLines[listLines.length - 1] += `\n${l.trim()}`;
            i += 1;
          } else {
            break;
          }
        }
        renderListBlock(container, listLines, callbacks, opts);
        continue;
      }

      // Horizontal rule.
      if (/^ {0,3}(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        flushParagraph();
        container.appendChild(el("hr", "md-hr"));
        i += 1;
        continue;
      }

      if (line.trim() === "") {
        flushParagraph();
        i += 1;
        continue;
      }
      paragraph.push(line);
      i += 1;
    }
    flushParagraph();
  }

  // ————————————————————————————————————————————————
  // Item renderers. `vs` is the view-state object owning expansion maps:
  //   vs.isItemExpanded(key, fallback) / vs.setItemExpanded(key, value)
  // Renderers never keep expansion state in the DOM alone.
  // ————————————————————————————————————————————————

  function renderUserMessage(item, callbacks) {
    const root = el("div", "tl-item tl-user");
    root.dataset.key = item.key;
    const body = el("div", "tl-user-body");
    body.textContent = item.content.text || "";
    root.appendChild(body);
    const actions = el("div", "tl-row-actions");
    const copy = el("button", "tl-icon-btn", "复制");
    copy.type = "button";
    copy.setAttribute("aria-label", "复制消息");
    copy.addEventListener("click", () => callbacks.copyText(item.content.text || ""));
    actions.appendChild(copy);
    root.appendChild(actions);
    return root;
  }

  function renderAssistantMessage(item, callbacks) {
    const root = el("div", "tl-item tl-assistant");
    root.dataset.key = item.key;
    const body = el("div", "tl-assistant-body md");
    if (item.status === "streaming" && !item.content.text) {
      body.appendChild(el("span", "tl-muted", "正在生成…"));
    } else {
      renderMarkdown(body, item.content.text, callbacks, {
        tolerant: item.status === "streaming",
      });
    }
    root.appendChild(body);
    if (item.status === "streaming") {
      const caret = el("span", "tl-stream-caret");
      caret.setAttribute("aria-hidden", "true");
      root.appendChild(caret);
    }
    return root;
  }

  /** In-place patch for a streaming assistant node: no DOM rebuild, no jitter. */
  function patchStreamingAssistant(node, item, callbacks) {
    const body = node.querySelector(".tl-assistant-body");
    if (!body) return false;
    if (item.status !== "streaming") return false;
    if (!item.content.text) return true;
    renderMarkdown(body, item.content.text, callbacks, { tolerant: true });
    if (!node.querySelector(".tl-stream-caret")) {
      const caret = el("span", "tl-stream-caret");
      caret.setAttribute("aria-hidden", "true");
      node.appendChild(caret);
    }
    return true;
  }

  function renderStatusGroup(item, callbacks, vs) {
    const root = el("div", "tl-item tl-status");
    root.dataset.key = item.key;
    const messages = item.content.messages || [];
    const latest = messages[messages.length - 1] || "正在工作";
    const head = el("button", "tl-status-head");
    head.type = "button";
    if (item.metadata.open) {
      const spinner = el("span", "tl-spinner");
      spinner.setAttribute("aria-hidden", "true");
      head.appendChild(spinner);
    }
    head.appendChild(el("span", "tl-status-text", latest));
    const expandable = messages.length > 1;
    if (expandable) head.appendChild(el("span", "tl-status-count", `共 ${messages.length} 条`));
    root.appendChild(head);
    if (expandable) {
      const expanded = vs ? vs.isItemExpanded(item.key, false) : false;
      const detail = el("div", "tl-status-detail");
      detail.hidden = !expanded;
      head.setAttribute("aria-expanded", String(expanded));
      for (const m of messages.slice(0, -1)) detail.appendChild(el("div", "tl-status-line", m));
      root.appendChild(detail);
      head.addEventListener("click", () => {
        const next = detail.hidden;
        detail.hidden = !next;
        head.setAttribute("aria-expanded", String(next));
        if (vs) vs.setItemExpanded(item.key, next);
      });
    } else {
      // Single non-expandable line: no clickable chevron, not a toggle.
      head.disabled = true;
      head.classList.add("is-static");
    }
    return root;
  }

  function renderPlan(item, callbacks, vs) {
    const root = el("div", "tl-item tl-plan");
    root.dataset.key = item.key;
    const head = el("button", "tl-plan-head");
    head.type = "button";
    head.appendChild(el("span", "tl-plan-title", "计划"));
    const steps = Array.isArray(item.content.steps) ? item.content.steps : [];
    if (steps.length) {
      const done = steps.filter((s) => s.status === "done" || s.status === "completed").length;
      head.appendChild(el("span", "tl-plan-progress", `${done}/${steps.length}`));
    }
    const expanded = vs ? vs.isItemExpanded(item.key, item.status !== "done") : item.status !== "done";
    head.setAttribute("aria-expanded", String(expanded));
    root.appendChild(head);
    const body = el("div", "tl-plan-body");
    body.hidden = !expanded;
    if (steps.length) {
      for (const step of steps) {
        const row = el("div", `tl-plan-step ${step.status || "pending"}`);
        const marker = el("span", "tl-plan-marker");
        marker.setAttribute("aria-hidden", "true");
        row.appendChild(marker);
        row.appendChild(el("span", "tl-plan-step-text", step.title || step.text || ""));
        body.appendChild(row);
      }
    } else if (item.content.text) {
      body.appendChild(el("div", "tl-plan-text", item.content.text));
    }
    head.addEventListener("click", () => {
      const next = body.hidden;
      body.hidden = !next;
      head.setAttribute("aria-expanded", String(next));
      if (vs) vs.setItemExpanded(item.key, next);
    });
    root.appendChild(body);
    return root;
  }

  const LONG_OUTPUT_CHARS = 1200;

  function renderTool(item, callbacks, vs) {
    const root = el("div", `tl-item tl-tool is-${item.status}`);
    root.dataset.key = item.key;
    const head = el("button", "tl-tool-head");
    head.type = "button";
    const dot = el("span", "tl-tool-dot");
    dot.setAttribute("aria-hidden", "true");
    head.appendChild(dot);
    head.appendChild(el("span", "tl-tool-name", toolLabel(item.content.name)));
    const summaryText = item.status === "failed" && item.metadata.errorType
      ? `${toolSummary(item)} · ${item.metadata.errorType}`
      : toolSummary(item);
    head.appendChild(el("span", "tl-tool-summary", summaryText));
    head.appendChild(el("span", "tl-tool-state", STATUS_LABEL[item.status] || item.status));
    const dur = formatDuration(item.metadata.durationMs);
    if (dur) head.appendChild(el("span", "tl-tool-duration", dur));
    root.appendChild(head);

    // Default: collapsed; failed tools show their one-line error in the head.
    // User overrides always win across re-renders.
    const expanded = vs ? vs.isItemExpanded(item.key, false) : false;
    head.setAttribute("aria-expanded", String(expanded));

    const detail = el("div", "tl-tool-detail");
    detail.hidden = !expanded;

    const input = item.content.input;
    if (input && typeof input === "object" && Object.keys(input).length) {
      if (Array.isArray(input.argv) && input.argv.length) {
        const cmdRow = el("div", "tl-kv");
        cmdRow.appendChild(el("span", "tl-k", "命令"));
        cmdRow.appendChild(el("code", "tl-cmd", input.argv.join(" ")));
        detail.appendChild(cmdRow);
        if (typeof input.cwd === "string" && input.cwd) {
          const cwdRow = el("div", "tl-kv");
          cwdRow.appendChild(el("span", "tl-k", "工作目录"));
          cwdRow.appendChild(el("code", "tl-cmd", input.cwd));
          detail.appendChild(cwdRow);
        }
      }
      const argsHead = el("div", "tl-kv");
      argsHead.appendChild(el("span", "tl-k", "参数"));
      const argsPre = el("pre", "tl-pre");
      try {
        argsPre.textContent = JSON.stringify(input, null, 2);
      } catch (_) {
        argsPre.textContent = String(input);
      }
      const copy = el("button", "md-copy", "复制");
      copy.type = "button";
      copy.addEventListener("click", () => callbacks.copyText(argsPre.textContent));
      argsHead.appendChild(copy);
      detail.appendChild(argsHead);
      detail.appendChild(argsPre);
    }

    if (item.content.output != null && item.content.output !== "") {
      const outHead = el("div", "tl-kv");
      outHead.appendChild(el("span", "tl-k", "输出"));
      const copy = el("button", "md-copy", "复制");
      copy.type = "button";
      const outPre = el("pre", "tl-pre tl-output");
      const outputText =
        typeof item.content.output === "string"
          ? item.content.output
          : (() => {
              try {
                return JSON.stringify(item.content.output, null, 2);
              } catch (_) {
                return String(item.content.output);
              }
            })();
      outPre.textContent = outputText;
      if (outputText.length > LONG_OUTPUT_CHARS) outPre.classList.add("is-long");
      copy.addEventListener("click", () => callbacks.copyText(outputText));
      outHead.appendChild(copy);
      detail.appendChild(outHead);
      detail.appendChild(outPre);
    }

    if (item.metadata.errorType) {
      const errRow = el("div", "tl-kv");
      errRow.appendChild(el("span", "tl-k", "错误类型"));
      errRow.appendChild(el("code", "tl-cmd", item.metadata.errorType));
      detail.appendChild(errRow);
    }

    const metaRow = el("div", "tl-tool-meta");
    if (item.timestamp) metaRow.appendChild(el("span", null, `开始 ${formatTime(item.timestamp)}`));
    if (item.metadata.durationMs != null) metaRow.appendChild(el("span", null, `耗时 ${formatDuration(item.metadata.durationMs)}`));
    detail.appendChild(metaRow);

    const pathText = input && typeof input.path === "string" ? input.path : null;
    if (pathText && typeof callbacks.openFile === "function") {
      const openBtn = el("button", "ghost-btn sm", "打开文件");
      openBtn.type = "button";
      openBtn.addEventListener("click", () => callbacks.openFile(pathText, 0));
      detail.appendChild(openBtn);
    }

    head.addEventListener("click", () => {
      const next = detail.hidden;
      detail.hidden = !next;
      head.setAttribute("aria-expanded", String(next));
      if (vs) vs.setItemExpanded(item.key, next);
    });
    root.appendChild(detail);
    return root;
  }

  function approvalRiskBadge(content) {
    const risk = content.risk || "process";
    return el("span", `tl-risk is-${risk}`, RISK_LABEL[risk] || risk);
  }

  function renderApprovalDetailRows(body, item) {
    const c = item.content || {};
    const rows = [];
    if (typeof c.command === "string" && c.command) rows.push(["命令", c.command, "code"]);
    else if (Array.isArray(c.argv) && c.argv.length) rows.push(["命令", c.argv.join(" "), "code"]);
    if (typeof c.cwd === "string" && c.cwd) rows.push(["工作目录", c.cwd, "code"]);
    if (typeof c.url === "string" && c.url) rows.push(["URL", c.url, "code"]);
    if (typeof c.path === "string" && c.path) rows.push(["目标路径", c.path, "code"]);
    if (Array.isArray(c.target_paths) && c.target_paths.length) rows.push(["目标路径", c.target_paths.join("\n"), "code"]);
    if (Array.isArray(c.domains) && c.domains.length) rows.push(["域名", c.domains.join(", "), "text"]);
    if (c.max_bytes != null) rows.push(["大小上限", formatBytes(c.max_bytes) || String(c.max_bytes), "text"]);
    if (typeof c.mcp_server === "string" && c.mcp_server) rows.push(["MCP Server", c.mcp_server, "code"]);
    if (typeof c.mcp_tool === "string" && c.mcp_tool) rows.push(["MCP Tool", c.mcp_tool, "code"]);
    if (typeof c.tool_name === "string" && c.tool_name && !c.mcp_tool) rows.push(["工具", c.tool_name, "code"]);
    if (typeof c.query === "string" && c.query) rows.push(["查询", c.query, "text"]);
    if (c.interrupted_tool_call_id) rows.push(["原 tool_call_id", String(c.interrupted_tool_call_id), "code"]);
    for (const [label, value, kind] of rows) {
      const row = el("div", "tl-kv");
      row.appendChild(el("span", "tl-k", label));
      row.appendChild(el(kind === "code" ? "code" : "span", kind === "code" ? "tl-cmd" : null, value));
      body.appendChild(row);
    }
    const rest = c.input && typeof c.input === "object" ? c.input : null;
    if (rest && Object.keys(rest).length) {
      const row = el("div", "tl-kv");
      row.appendChild(el("span", "tl-k", "参数"));
      body.appendChild(row);
      const pre = el("pre", "tl-pre");
      try {
        pre.textContent = JSON.stringify(rest, null, 2);
      } catch (_) {
        pre.textContent = String(rest);
      }
      body.appendChild(pre);
    }
  }

  function renderApproval(item, callbacks) {
    const kind = item.metadata.kind || item.content.requested_capability || "tool";
    const root = el("div", `tl-item tl-approval is-${item.status}`);
    root.dataset.key = item.key;
    root.dataset.approvalId = item.approvalId || "";

    const title = el("div", "tl-approval-title");
    title.tabIndex = -1;
    title.appendChild(approvalRiskBadge(item.content));
    title.appendChild(el("span", "tl-approval-name", APPROVAL_TITLE[kind] || "需要审批的操作"));
    if (item.timestamp) title.appendChild(el("span", "tl-approval-time", formatTime(item.timestamp)));
    root.appendChild(title);

    const body = el("div", "tl-approval-body");
    const reason = item.content.reason || item.content.message || "该操作需要你的确认才会执行。";
    body.appendChild(el("p", "tl-approval-reason", reason));
    renderApprovalDetailRows(body, item);
    root.appendChild(body);

    if (item.status === "pending") {
      if (item.metadata.resolveError) {
        body.appendChild(el("p", "tl-approval-error", `操作失败：${item.metadata.resolveError}（可重试）`));
      }
      const actions = el("div", "tl-approval-actions");
      const destructive = item.content.risk === "destructive";
      const reject = el("button", "ghost-btn sm", "拒绝");
      reject.type = "button";
      const allow = el("button", destructive ? "danger-btn sm" : "primary-btn sm", "允许一次");
      allow.type = "button";
      reject.addEventListener("click", () => callbacks.resolveApproval(item, false));
      allow.addEventListener("click", () => callbacks.resolveApproval(item, true));
      actions.appendChild(reject);
      actions.appendChild(allow);
      root.appendChild(actions);
    } else {
      const stamp = el("div", "tl-approval-stamp", DECISION_LABEL[item.status] || item.status);
      if (item.metadata.resolvedAt) {
        stamp.appendChild(el("span", "tl-approval-time", ` · ${formatTime(item.metadata.resolvedAt)}`));
      }
      root.appendChild(stamp);
    }
    return root;
  }

  /**
   * Diff-review readiness of a changes card, computed from real state only:
   *  - preparing: the turn is still active (after checkpoint not yet built)
   *  - ready: terminal lifecycle reported diff_status=ready
   *  - unavailable: lifecycle reported diff_status=unavailable (with reason)
   *  - empty: turn finished without file changes
   *  - unknown: legacy data without checkpoint info (probe on click)
   */
  function changesDiffState(item, turn) {
    const files = Array.isArray(item.content.files) ? item.content.files : [];
    const diffStatus = item.metadata.diffStatus || (turn && turn.diffStatus) || null;
    const active = Boolean(turn && (turn.status === "running" || turn.status === "awaiting_approval" || turn.status === "queued" || turn.status === "paused"));
    if (active) return { state: "preparing" };
    if (diffStatus === "ready") return { state: files.length ? "ready" : "empty" };
    if (diffStatus === "unavailable") {
      return { state: "unavailable", reason: item.metadata.diffReason || (turn && turn.diffReason) || "缺少检查点数据" };
    }
    if (diffStatus === "preparing") return { state: "preparing" };
    if (!files.length && turn && turn.finishedAt) return { state: "empty" };
    if (files.length) return { state: "unknown" };
    return { state: "empty" };
  }

  function renderChanges(item, callbacks, vs, turn) {
    const root = el("div", "tl-item tl-changes");
    root.dataset.key = item.key;
    const files = Array.isArray(item.content.files) ? item.content.files : [];
    const counts = { added: 0, modified: 0, deleted: 0, renamed: 0 };
    for (const f of files) {
      if (counts[f.change] !== undefined) counts[f.change] += 1;
      else counts.modified += 1;
    }
    const head = el("div", "tl-changes-head");
    head.appendChild(el("span", "tl-changes-title", files.length ? `改动 ${files.length} 个文件` : "文件改动"));
    const stats = el("span", "tl-changes-stats");
    const parts = [];
    if (counts.added) parts.push(`新增 ${counts.added}`);
    if (counts.modified) parts.push(`修改 ${counts.modified}`);
    if (counts.deleted) parts.push(`删除 ${counts.deleted}`);
    if (counts.renamed) parts.push(`重命名 ${counts.renamed}`);
    stats.textContent = parts.join(" · ");
    head.appendChild(stats);

    const diff = changesDiffState(item, turn);
    item.metadata.diffState = diff.state;
    if (diff.state === "ready" || diff.state === "unknown") {
      const review = el("button", diff.state === "ready" ? "primary-btn sm" : "ghost-btn sm", "审查改动");
      review.type = "button";
      review.addEventListener("click", () => callbacks.reviewChanges(item, turn));
      head.appendChild(review);
    } else if (diff.state === "preparing") {
      const btn = el("button", "ghost-btn sm is-busy", "正在准备改动审查…");
      btn.type = "button";
      btn.disabled = true;
      const spinner = el("span", "tl-spinner");
      spinner.setAttribute("aria-hidden", "true");
      btn.prepend(spinner);
      head.appendChild(btn);
    }
    root.appendChild(head);

    if (diff.state === "empty") {
      root.appendChild(el("div", "tl-changes-empty", "本轮没有文件改动"));
    } else if (diff.state === "unavailable") {
      root.appendChild(el("div", "tl-changes-empty", `改动审查不可用：${diff.reason || "缺少检查点"}`));
    }
    if (item.metadata.reviewNote) {
      root.appendChild(el("div", "tl-changes-note", item.metadata.reviewNote));
    }

    if (files.length) {
      const expanded = vs ? vs.isItemExpanded(item.key, false) : false;
      const toggle = el("button", "tl-changes-toggle", expanded ? "收起文件列表" : `展开文件列表（${files.length}）`);
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(expanded));
      const list = el("div", "tl-changes-files");
      list.hidden = !expanded;
      for (const f of files.slice(0, 100)) {
        const row = el("button", `tl-change-file is-${f.change || "modified"}`);
        row.type = "button";
        row.appendChild(el("span", "tl-change-kind", { added: "A", modified: "M", deleted: "D", renamed: "R" }[f.change] || "M"));
        const label = f.change === "renamed" && f.old_path ? `${f.old_path} → ${f.path}` : f.path || "";
        row.appendChild(el("span", "tl-change-path", label));
        row.title = label;
        row.addEventListener("click", () => callbacks.openFile(f.path, 0));
        list.appendChild(row);
      }
      if (files.length > 100) list.appendChild(el("div", "tl-muted", `… 其余 ${files.length - 100} 个文件`));
      toggle.addEventListener("click", () => {
        const next = list.hidden;
        list.hidden = !next;
        toggle.setAttribute("aria-expanded", String(next));
        toggle.textContent = next ? `展开文件列表（${files.length}）` : "收起文件列表";
        if (vs) vs.setItemExpanded(item.key, next);
      });
      root.appendChild(toggle);
      root.appendChild(list);
    }
    return root;
  }

  function renderCheckpoint(item, callbacks) {
    const root = el("div", "tl-item tl-checkpoint");
    root.dataset.key = item.key;
    const kindLabel = { before_turn: "任务开始前", after_turn: "任务完成后" }[item.content.kind] || item.content.kind || "检查点";
    const head = el("div", "tl-checkpoint-head");
    head.appendChild(el("span", "tl-checkpoint-title", `检查点 · ${kindLabel}`));
    if (item.timestamp) head.appendChild(el("span", "tl-muted", formatTime(item.timestamp)));
    if (item.content.fileCount != null) head.appendChild(el("span", "tl-muted", `${item.content.fileCount} 个文件`));
    if (item.content.kind === "before_turn" || item.content.kind === "after_turn") {
      const restore = el("button", "ghost-btn sm", "恢复到此检查点");
      restore.type = "button";
      restore.addEventListener("click", () => callbacks.restoreCheckpoint(item));
      head.appendChild(restore);
    }
    root.appendChild(head);
    return root;
  }

  function renderUsage(item) {
    const root = el("div", "tl-item tl-usage");
    root.dataset.key = item.key;
    if (item.type === "provider_change") {
      const from = item.content.from_provider || item.content.from_model || "";
      const to = item.content.to_provider || item.content.to_model || "";
      root.appendChild(el("span", "tl-muted", `已切换提供商 ${from} → ${to}`));
    } else {
      const usage = item.content.usage || item.content;
      const total = usage.total_tokens ?? usage.totalTokens;
      const text = total != null ? `本轮约 ${total} tokens` : item.content.message || "用量已记录";
      root.appendChild(el("span", "tl-muted", text));
    }
    return root;
  }

  function renderLifecycle(item) {
    const root = el("div", `tl-item tl-lifecycle is-${item.status}`);
    root.dataset.key = item.key;
    const label = STATUS_LABEL[item.status] || item.status;
    root.appendChild(el("span", "tl-lifecycle-label", `任务${label}`));
    if (item.content && item.content.message) {
      root.appendChild(el("span", "tl-muted", item.content.message));
    }
    if (item.timestamp) root.appendChild(el("span", "tl-muted", formatTime(item.timestamp)));
    return root;
  }

  function renderError(item) {
    const root = el("div", "tl-item tl-error");
    root.dataset.key = item.key;
    root.appendChild(el("span", "tl-error-text", item.content.text || "发生错误"));
    return root;
  }

  function renderItem(item, callbacks, vs, turn) {
    switch (item.type) {
      case "user_message":
        return renderUserMessage(item, callbacks);
      case "assistant_message":
        return renderAssistantMessage(item, callbacks);
      case "status":
        return renderStatusGroup(item, callbacks, vs);
      case "plan":
        return renderPlan(item, callbacks, vs);
      case "tool":
        return renderTool(item, callbacks, vs);
      case "approval":
        return renderApproval(item, callbacks);
      case "changes":
        return renderChanges(item, callbacks, vs, turn);
      case "checkpoint":
        return renderCheckpoint(item, callbacks);
      case "usage":
      case "provider_change":
        return renderUsage(item);
      case "lifecycle":
        return renderLifecycle(item);
      case "error":
        return renderError(item);
      default:
        return renderStatusGroup(item, callbacks, vs);
    }
  }

  // ————————————————————————————————————————————————
  // Turn view model. Groups normalized TimelineItems into turns:
  //   TurnViewModel {
  //     key, turnId, taskId, status, startedAt, finishedAt, durationMs,
  //     userMessage, workItems, finalMessages, changes, lifecycle,
  //     summary, diffStatus, diffReason, isCurrent
  //   }
  // The work session (everything except the user prompt and final answers)
  // is collapsible; final answers and the user prompt always stay visible.
  // ————————————————————————————————————————————————

  const ACTIVE_TURN_STATUSES = new Set(["running", "awaiting_approval", "queued", "paused", "working"]);
  const TERMINAL_TURN_STATUSES = new Set(["succeeded", "failed", "canceled", "interrupted"]);

  const READ_TOOLS = new Set(["read_file", "list_files", "git_status", "git_diff"]);
  const SEARCH_TOOLS = new Set(["search_code", "search_files", "web_search"]);
  const WRITE_TOOLS = new Set(["write_file", "str_replace", "apply_patch", "download_file"]);
  const COMMAND_TOOLS = new Set(["run_command", "run_gradle"]);

  function toMs(ts) {
    if (ts == null) return null;
    const v = Number(ts);
    if (!Number.isFinite(v)) return null;
    return v > 1e12 ? v : v * 1000;
  }

  function isStreamingAssistant(item) {
    return item.type === "assistant_message" && item.status === "streaming";
  }

  function isFinalAssistant(item) {
    return (
      item.type === "assistant_message" &&
      (item.metadata.isFinal === true || isStreamingAssistant(item))
    );
  }

  function isNoiseItem(item) {
    // Lifecycle noise with no user value: never rendered.
    if (item.type === "lifecycle" && ["turn_started", "queued", "claimed"].includes(item.status)) return true;
    if (item.type === "status") {
      const messages = item.content.messages || [];
      if (!messages.length) return true;
    }
    return false;
  }

  function buildTurns(items) {
    const turns = [];
    const turnByTurnId = new Map();
    // jobId -> the turn that most recently received activity for that job.
    // Items without a turn_id attach to this "current" turn (compat key);
    // the first event that DOES carry a turn_id adopts the job's pending
    // turn instead of splitting it (live -> canonical identity merge).
    const turnByJob = new Map();
    let orphanTurn = null;
    for (const item of Array.isArray(items) ? items : []) {
      if (!item || typeof item !== "object") continue;
      let turn = null;
      if (item.turnId) {
        turn = turnByTurnId.get(item.turnId) || null;
        if (!turn && item.jobId) {
          const cur = turnByJob.get(item.jobId);
          if (cur && !cur.turnId) turn = cur; // adoption: same turn, id learned later
        }
      } else if (item.jobId) {
        turn = turnByJob.get(item.jobId) || null;
      } else {
        turn = orphanTurn;
      }
      if (!turn) {
        const tkey = item.turnId
          ? `t:${item.turnId}`
          : item.jobId
            ? `j:${item.jobId}`
            : "_orphan";
        turn = {
          key: tkey,
          turnId: item.turnId || null,
          taskId: item.jobId || null,
          status: "unknown",
          startedAt: null,
          finishedAt: null,
          durationMs: null,
          userMessage: null,
          workItems: [],
          finalMessages: [],
          changes: null,
          lifecycle: null,
          summary: "",
          diffStatus: null,
          diffReason: null,
          isCurrent: false,
        };
        turns.push(turn);
        if (item.turnId) turnByTurnId.set(item.turnId, turn);
        else if (item.jobId) turnByJob.set(item.jobId, turn);
        else orphanTurn = turn;
      }
      if (item.turnId) {
        if (!turn.turnId) turn.turnId = item.turnId;
        turnByTurnId.set(item.turnId, turn);
      }
      if (item.jobId) {
        if (!turn.taskId) turn.taskId = item.jobId;
        turnByJob.set(item.jobId, turn);
      }
      const ms = toMs(item.timestamp);
      if (ms != null) {
        if (turn.startedAt == null || ms < turn.startedAt) turn.startedAt = ms;
        if (turn.finishedAt == null || ms > turn.finishedAt) turn.finishedAt = ms;
      }

      if (item.type === "user_message") {
        if (!turn.userMessage) turn.userMessage = item;
        else turn.workItems.push(item);
      } else if (item.type === "lifecycle") {
        turn.lifecycle = item;
        if (item.metadata.diffStatus) turn.diffStatus = item.metadata.diffStatus;
        if (item.metadata.diffReason) turn.diffReason = item.metadata.diffReason;
        if (TERMINAL_TURN_STATUSES.has(item.status) && ms != null) turn.finishedAt = ms;
      } else if (item.type === "changes") {
        turn.changes = item;
        turn.workItems.push(item);
        if (item.metadata.diffStatus) turn.diffStatus = item.metadata.diffStatus;
        if (item.metadata.diffReason) turn.diffReason = item.metadata.diffReason;
      } else if (isFinalAssistant(item)) {
        turn.finalMessages.push(item);
      } else if (!isNoiseItem(item)) {
        turn.workItems.push(item);
      }
    }

    for (const turn of turns) {
      // Status: terminal lifecycle wins, then pending approvals, then activity.
      const life = turn.lifecycle ? turn.lifecycle.status : null;
      if (TERMINAL_TURN_STATUSES.has(life)) {
        turn.status = life;
      } else if (turn.workItems.some((i) => i.type === "approval" && i.status === "pending")) {
        turn.status = "awaiting_approval";
      } else if (
        turn.workItems.some(
          (i) =>
            (i.type === "tool" && (i.status === "running" || i.status === "waiting_approval")) ||
            (i.type === "status" && i.metadata.open),
        ) ||
        turn.finalMessages.some(isStreamingAssistant)
      ) {
        turn.status = "running";
      } else if (life === "turn_started" || life === "queued") {
        turn.status = "running";
      } else if (turn.finalMessages.length || turn.workItems.length) {
        turn.status = life || "succeeded";
      }
      if (turn.startedAt != null && turn.finishedAt != null && turn.finishedAt >= turn.startedAt) {
        turn.durationMs = turn.finishedAt - turn.startedAt;
      }
      turn.summary = computeTurnSummary(turn);
    }
    if (turns.length) turns[turns.length - 1].isCurrent = ACTIVE_TURN_STATUSES.has(turns[turns.length - 1].status);
    return turns;
  }

  /** Collapsed-turn summary computed ONLY from real tool/changes/approval data. */
  function computeTurnSummary(turn) {
    let reads = 0;
    let searches = 0;
    let commands = 0;
    let writes = 0;
    let approvals = 0;
    let pendingApprovals = 0;
    let tests = 0;
    let gradleFailed = false;
    let commandFailed = false;
    for (const item of turn.workItems) {
      if (item.type === "tool") {
        const name = item.content.name || "";
        if (READ_TOOLS.has(name)) reads += 1;
        else if (SEARCH_TOOLS.has(name)) searches += 1;
        else if (WRITE_TOOLS.has(name)) writes += 1;
        else if (COMMAND_TOOLS.has(name)) {
          commands += 1;
          const summary = toolSummary(item);
          if (/\btest\b|pytest|connectedAndroidTest|unitTest/i.test(summary)) tests += 1;
          if (item.status === "failed") {
            if (name === "run_gradle") gradleFailed = true;
            else commandFailed = true;
          }
        }
      } else if (item.type === "approval") {
        approvals += 1;
        if (item.status === "pending") pendingApprovals += 1;
      }
    }
    // Prefer the authoritative changes card for write counts.
    const changeFiles = turn.changes && Array.isArray(turn.changes.content.files) ? turn.changes.content.files.length : 0;
    if (changeFiles > 0) writes = changeFiles;

    const parts = [];
    if (gradleFailed) parts.push("构建失败");
    if (reads) parts.push(`查看 ${reads} 个文件`);
    if (searches) parts.push(`搜索 ${searches} 次`);
    if (commands) parts.push(`执行 ${commands} 条命令`);
    if (writes) parts.push(`修改 ${writes} 个文件`);
    if (tests) parts.push(`运行测试 ${tests} 次`);
    if (approvals) parts.push(pendingApprovals ? `${pendingApprovals} 个审批待处理` : `${approvals} 次审批`);
    if (!parts.length && commandFailed) parts.push("命令执行失败");
    if (!parts.length) {
      if (turn.status === "failed") parts.push("任务失败");
      else if (turn.status === "canceled") parts.push("任务已停止");
      else if (turn.status === "interrupted") parts.push("任务被中断");
    }
    return parts.slice(0, 4).join(" · ");
  }

  // ————————————————————————————————————————————————
  // View state: expansion state lives HERE (never only in DOM.hidden), keyed
  // by turn key / item key, persisted per conversation with a version. DOM
  // reconciliation, streaming updates and approval resolves can never reset
  // it. User overrides always win over default policies.
  // ————————————————————————————————————————————————

  const STORAGE_KEY = "agentTimeline.viewState.v1";
  const MAX_STORED_CONVERSATIONS = 20;
  const MAX_STORED_ITEM_STATES = 400;

  function readStoredState() {
    try {
      if (typeof localStorage === "undefined") return {};
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && parsed.conversations ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function writeStoredState(state) {
    try {
      if (typeof localStorage === "undefined") return;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (_) {
      /* storage full/unavailable: expansion state stays in memory */
    }
  }

  function createViewState(conversationId) {
    const turnOverride = new Map(); // turnKey -> boolean (explicit user choice)
    const sessionKept = new Map(); // turnKey -> boolean (kept after finishing this session)
    const itemExpanded = new Map(); // itemKey -> boolean
    const convId = conversationId || "_";

    // Hydrate from persisted state of this conversation.
    const stored = readStoredState();
    const conv = stored.conversations && stored.conversations[convId];
    if (conv) {
      for (const [k, v] of Object.entries(conv.turns || {})) turnOverride.set(k, Boolean(v));
      for (const [k, v] of Object.entries(conv.items || {})) itemExpanded.set(k, Boolean(v));
    }

    let saveTimer = null;
    function persistSoon() {
      if (saveTimer) return;
      const schedule = typeof setTimeout === "function" ? setTimeout : () => {};
      saveTimer = schedule(() => {
        saveTimer = null;
        try {
          const state = readStoredState();
          state.conversations = state.conversations || {};
          const turns = {};
          for (const [k, v] of turnOverride) turns[k] = v;
          const items = {};
          let count = 0;
          for (const [k, v] of itemExpanded) {
            if (count >= MAX_STORED_ITEM_STATES) break;
            items[k] = v;
            count += 1;
          }
          state.conversations[convId] = { turns, items, updatedAt: Date.now() };
          // Prune stale conversations beyond the cap (oldest first).
          const ids = Object.keys(state.conversations);
          if (ids.length > MAX_STORED_CONVERSATIONS) {
            ids
              .sort((a, b) => (state.conversations[a].updatedAt || 0) - (state.conversations[b].updatedAt || 0))
              .slice(0, ids.length - MAX_STORED_CONVERSATIONS)
              .forEach((id) => delete state.conversations[id]);
          }
          writeStoredState(state);
        } catch (_) {
          /* ignore */
        }
      }, 300);
    }

    return {
      conversationId: convId,

      /** Effective expansion of a turn: user override > session keep > default. */
      isTurnExpanded(turn, isLast) {
        if (turnOverride.has(turn.key)) return turnOverride.get(turn.key);
        if (sessionKept.has(turn.key)) return sessionKept.get(turn.key);
        return defaultTurnExpanded(turn, isLast);
      },

      setTurnExpanded(turnKey, value) {
        turnOverride.set(turnKey, Boolean(value));
        persistSoon();
      },

      /** A turn finished during THIS session keeps whatever the user saw. */
      keepSessionState(turnKey, currentValue) {
        if (!turnOverride.has(turnKey)) sessionKept.set(turnKey, Boolean(currentValue));
      },

      isItemExpanded(itemKey, fallback) {
        return itemExpanded.has(itemKey) ? itemExpanded.get(itemKey) : Boolean(fallback);
      },

      setItemExpanded(itemKey, value) {
        itemExpanded.set(itemKey, Boolean(value));
        persistSoon();
      },
    };
  }

  /**
   * Default expansion policy:
   *  - running / awaiting_approval / queued turns: expanded
   *  - failed / interrupted turns: expanded (error summary visible; long tool
   *    outputs stay collapsed because tools default to collapsed)
   *  - the most recent turn: expanded
   *  - older finished turns: collapsed
   */
  function defaultTurnExpanded(turn, isLast) {
    if (ACTIVE_TURN_STATUSES.has(turn.status)) return true;
    if (turn.status === "failed" || turn.status === "interrupted") return true;
    if (isLast) return true;
    return false;
  }

  // ————————————————————————————————————————————————
  // Timeline view. Reconciles TurnViewModels into the DOM:
  //  - turn nodes are keyed and patched, never bulk-rebuilt
  //  - unchanged items (same metadata.version) keep their DOM node
  //  - streaming assistants are patched in place (no rebuild per delta)
  //  - rebuilds restore focus + scrollTop; expansion comes from view state
  //  - collapsed turns defer work-body DOM creation entirely
  //  - updates are coalesced with requestAnimationFrame
  // ————————————————————————————————————————————————

  function itemVersion(item) {
    return (item.metadata && item.metadata.version) || 0;
  }

  function cssIdFromKey(key) {
    return String(key).replace(/[^a-zA-Z0-9_-]/g, "-");
  }

  function describeFocusTarget(active, root) {
    // Best-effort selector for restoring focus after a node rebuild.
    if (!active || !active.classList || !active.classList.length) return null;
    const sel = active.tagName.toLowerCase() + "." + Array.from(active.classList).join(".");
    return root.querySelectorAll(sel).length === 1 ? sel : null;
  }

  function createTimelineView(container, callbacks) {
    let latestItems = [];
    let vs = createViewState(null);
    const turnNodeByKey = new Map(); // turnKey -> section element
    const itemNodeByKey = new Map(); // itemKey -> { node, version }
    let prevActiveKeys = new Set();
    let rafPending = false;
    let flashTimer = null;
    // Optional non-turn node (e.g. the "load earlier" button) that always
    // stays above the first turn.
    let headerNode = null;

    // —— low-level reconciliation helpers ——

    function rebuildItemNode(oldNode, item, turn) {
      const active = typeof document !== "undefined" ? document.activeElement : null;
      const focusSel = active && oldNode.contains(active) ? describeFocusTarget(active, oldNode) : null;
      const scrolls = [];
      oldNode.querySelectorAll(".tl-pre").forEach((sc, i) => {
        if (sc.scrollTop) scrolls.push([i, sc.scrollTop]);
      });
      const newNode = renderItem(item, callbacks, vs, turn);
      if (oldNode.parentNode) oldNode.parentNode.replaceChild(newNode, oldNode);
      const entry = itemNodeByKey.get(item.key);
      if (entry) {
        entry.node = newNode;
        entry.version = itemVersion(item);
        entry.item = item;
      } else {
        itemNodeByKey.set(item.key, { node: newNode, version: itemVersion(item), item });
      }
      const pres = newNode.querySelectorAll(".tl-pre");
      for (const [i, top] of scrolls) {
        if (pres[i]) pres[i].scrollTop = top;
      }
      if (focusSel) {
        const target = newNode.querySelector(focusSel);
        if (target && typeof target.focus === "function") target.focus();
      }
      return newNode;
    }

    /**
     * Reconcile an ordered item sequence into `parent`, reusing keyed nodes.
     * Streaming assistant nodes are patched in place; everything else is only
     * rebuilt when its version changed.
     */
    function reconcileSequence(parent, items, turn) {
      let prevEl = null;
      for (const item of items) {
        let entry = itemNodeByKey.get(item.key);
        let node = entry ? entry.node : null;
        // Object identity guards against a RESET store that re-creates items
        // under the same key with the same version (stale node otherwise).
        const sameItem = entry ? entry.item === item : false;
        if (node && node.parentNode === parent && sameItem) {
          const version = itemVersion(item);
          if (isStreamingAssistant(item) && node.classList && node.classList.contains("tl-assistant")) {
            if (entry.version !== version) {
              patchStreamingAssistant(node, item, callbacks);
              entry.version = version;
            }
          } else if (entry.version !== version) {
            node = rebuildItemNode(node, item, turn);
          }
        } else {
          // Any stale node left under this key is swept by the trailing
          // cleanup below once the fresh node takes its sequence slot.
          node = renderItem(item, callbacks, vs, turn);
          itemNodeByKey.set(item.key, { node, version: itemVersion(item), item });
        }
        const expectedPrev = prevEl;
        if (node.parentNode !== parent || node.previousSibling !== expectedPrev) {
          parent.insertBefore(node, expectedPrev ? expectedPrev.nextSibling : parent.firstChild);
        }
        prevEl = node;
      }
      // Drop stale trailing item nodes no longer in the sequence.
      let child = prevEl ? prevEl.nextSibling : parent.firstChild;
      while (child) {
        const next = child.nextSibling;
        if (child.classList && child.classList.contains("tl-item")) parent.removeChild(child);
        child = next;
      }
    }

    // —— turn nodes ——

    function createTurnNode(turn) {
      const node = el("section", `tl-turn is-${turn.status}`);
      node.dataset.turnKey = turn.key;
      if (turn.turnId) node.dataset.turnId = turn.turnId;

      node.appendChild(el("div", "tl-turn-user"));

      const work = el("div", "tl-work");
      const head = el("button", "tl-work-head");
      head.type = "button";
      const bodyId = `tlWorkBody-${cssIdFromKey(turn.key)}`;
      head.setAttribute("aria-controls", bodyId);
      head.appendChild(el("span", "tl-work-status"));
      head.appendChild(el("span", "tl-work-label"));
      head.appendChild(el("span", "tl-work-summary"));
      const chevron = el("span", "tl-chevron");
      chevron.setAttribute("aria-hidden", "true");
      head.appendChild(chevron);
      const body = el("div", "tl-work-body");
      body.id = bodyId;
      work.appendChild(head);
      work.appendChild(body);
      node.appendChild(work);

      node.appendChild(el("div", "tl-turn-final"));

      head.addEventListener("click", () => {
        const next = body.hidden;
        vs.setTurnExpanded(turn.key, next);
        renderNow();
      });
      return node;
    }

    function patchTurnUser(node, turn) {
      const area = node.querySelector(":scope > .tl-turn-user");
      if (!area) return;
      if (turn.userMessage) {
        area.hidden = false;
        reconcileSequence(area, [turn.userMessage], turn);
      } else {
        area.hidden = true;
        area.textContent = "";
      }
    }

    function patchTurnWork(node, turn, expanded) {
      const work = node.querySelector(":scope > .tl-work");
      if (!work) return;
      const hasWork = turn.workItems.length > 0;
      work.hidden = !hasWork;
      if (!hasWork) return;
      const head = work.querySelector(".tl-work-head");
      const body = work.querySelector(".tl-work-body");

      node.className = `tl-turn is-${turn.status}`;
      const statusEl = head.querySelector(".tl-work-status");
      statusEl.className = `tl-work-status is-${turn.status}`;
      statusEl.textContent = TURN_STATUS_LABEL[turn.status] || turn.status;

      const label = head.querySelector(".tl-work-label");
      const active = ACTIVE_TURN_STATUSES.has(turn.status);
      if (active) {
        label.textContent = turn.durationMs != null ? `已工作 ${formatWorked(turn.durationMs)}` : "工作中…";
      } else if (turn.durationMs != null) {
        label.textContent = `工作了 ${formatWorked(turn.durationMs)}`;
      } else {
        label.textContent = "工作过程";
      }

      const summary = head.querySelector(".tl-work-summary");
      summary.textContent = turn.summary || "";
      summary.hidden = !turn.summary;

      head.setAttribute("aria-expanded", String(expanded));
      body.hidden = !expanded;
      if (expanded) {
        reconcileSequence(body, turn.workItems, turn);
      } else if (body.childNodes.length) {
        // Collapsed: defer DOM — drop children until expanded again.
        body.textContent = "";
      }
    }

    function patchTurnFinal(node, turn) {
      const area = node.querySelector(":scope > .tl-turn-final");
      if (!area) return;
      if (turn.finalMessages.length) {
        area.hidden = false;
        reconcileSequence(area, turn.finalMessages, turn);
      } else {
        area.hidden = true;
        area.textContent = "";
      }
    }

    // —— main render ——

    function renderNow() {
      if (!container) return;
      const turns = buildTurns(latestItems);

      // Turns that finished during this session keep the expansion the user
      // currently sees (user overrides still win).
      const nowActive = new Set();
      for (const turn of turns) {
        if (ACTIVE_TURN_STATUSES.has(turn.status)) nowActive.add(turn.key);
      }
      for (const key of prevActiveKeys) {
        if (!nowActive.has(key)) {
          const turn = turns.find((t) => t.key === key);
          if (turn) {
            const node = turnNodeByKey.get(key);
            const body = node ? node.querySelector(".tl-work-body") : null;
            const current = body ? !body.hidden : vs.isTurnExpanded(turn, false);
            vs.keepSessionState(key, current);
          }
        }
      }
      prevActiveKeys = nowActive;

      const liveItemKeys = new Set();
      let prevNode = null;
      turns.forEach((turn, idx) => {
        if (turn.userMessage) liveItemKeys.add(turn.userMessage.key);
        for (const i of turn.workItems) liveItemKeys.add(i.key);
        for (const i of turn.finalMessages) liveItemKeys.add(i.key);

        let node = turnNodeByKey.get(turn.key);
        if (!node) {
          node = createTurnNode(turn);
          turnNodeByKey.set(turn.key, node);
        }
        if (turn.turnId && !node.dataset.turnId) node.dataset.turnId = turn.turnId;
        const isLast = idx === turns.length - 1;
        const expanded = vs.isTurnExpanded(turn, isLast);
        patchTurnUser(node, turn);
        patchTurnWork(node, turn, expanded);
        patchTurnFinal(node, turn);

        const expectedPrev = prevNode;
        if (node.parentNode !== container || node.previousSibling !== expectedPrev) {
          const anchor = expectedPrev
            ? expectedPrev.nextSibling
            : headerNode && headerNode.parentNode === container
              ? headerNode.nextSibling
              : container.firstChild;
          container.insertBefore(node, anchor);
        }
        prevNode = node;
      });

      // Remove stale turn nodes.
      for (const [key, node] of Array.from(turnNodeByKey.entries())) {
        if (!turns.some((t) => t.key === key)) {
          if (node.parentNode) node.parentNode.removeChild(node);
          turnNodeByKey.delete(key);
        }
      }
      // Remove stale item cache entries (rekeyed / removed entities).
      for (const key of Array.from(itemNodeByKey.keys())) {
        if (!liveItemKeys.has(key)) itemNodeByKey.delete(key);
      }
    }

    function scheduleRender(immediate) {
      if (immediate) {
        renderNow();
        return;
      }
      if (rafPending) return;
      rafPending = true;
      const raf =
        typeof requestAnimationFrame === "function"
          ? requestAnimationFrame
          : (fn) => setTimeout(fn, 30);
      raf(() => {
        rafPending = false;
        renderNow();
      });
    }

    function flashNode(node) {
      if (!node) return;
      node.classList.add("tl-flash");
      if (flashTimer) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => node.classList.remove("tl-flash"), 1600);
    }

    const view = {
      /** Feed the latest normalized items; rendering is rAF-coalesced. */
      update(items, opts) {
        latestItems = Array.isArray(items) ? items : [];
        scheduleRender(Boolean(opts && opts.immediate));
      },

      /** Synchronous re-render (used after toggles/tests). */
      refresh() {
        renderNow();
      },

      reset() {
        latestItems = [];
        prevActiveKeys = new Set();
        turnNodeByKey.clear();
        itemNodeByKey.clear();
        if (container) container.textContent = "";
        if (headerNode && container) container.appendChild(headerNode);
      },

      /** Keep a non-turn node (load-earlier button) above all turns. */
      setHeaderNode(node) {
        headerNode = node || null;
      },

      /** Scope persisted expansion state to a conversation. */
      setConversationId(conversationId) {
        vs = createViewState(conversationId);
      },

      getViewState() {
        return vs;
      },

      /** Expand the owning turn, scroll to the approval and focus it. */
      focusApproval(approvalId) {
        if (!approvalId) return;
        const item = latestItems.find((i) => i.type === "approval" && i.approvalId === approvalId);
        if (!item) return;
        const turns = buildTurns(latestItems);
        const turn = turns.find(
          (t) => t.workItems.includes(item) || t.finalMessages.includes(item) || t.userMessage === item,
        );
        if (turn) {
          vs.setTurnExpanded(turn.key, true);
          renderNow();
        }
        const node = container ? container.querySelector(`[data-key="approval:${cssEscape(approvalId)}"]`) : null;
        if (node) {
          node.scrollIntoView({ behavior: "smooth", block: "center" });
          flashNode(node);
          const title = node.querySelector(".tl-approval-title");
          if (title && typeof title.focus === "function") title.focus();
        }
      },

      /** Locate a historical turn: expand, scroll, briefly highlight. */
      revealTurn(turnId, opts = {}) {
        const turns = buildTurns(latestItems);
        const turn = turns.find((t) => t.turnId === turnId || t.key === turnId || t.taskId === turnId);
        if (!turn) return false;
        vs.setTurnExpanded(turn.key, true);
        renderNow();
        const node = turnNodeByKey.get(turn.key);
        if (!node) return false;
        let target = node;
        if (opts.itemKey) {
          const entry = itemNodeByKey.get(opts.itemKey);
          if (entry && entry.node) target = entry.node;
        }
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        flashNode(node);
        return true;
      },

      setTurnExpanded(turnKey, value) {
        vs.setTurnExpanded(turnKey, value);
        renderNow();
      },

      getTurnKeys() {
        return Array.from(turnNodeByKey.keys());
      },

      _debug: { turnNodeByKey, itemNodeByKey, buildTurns: () => buildTurns(latestItems) },
    };
    return view;
  }

  function cssEscape(value) {
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(String(value));
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  // ————————————————————————————————————————————————
  // Approval dock (sticky pending-approval reminder at the panel bottom).
  // ————————————————————————————————————————————————

  function renderApprovalDock(host, pending, cbs = {}) {
    if (!host) return;
    host.textContent = "";
    const list = Array.isArray(pending) ? pending : [];
    if (!list.length) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    // One full-width button: pulsing dot + pending count + jump CTA.
    const btn = el("button", "tl-dock-btn");
    btn.type = "button";
    btn.appendChild(el("span", "tl-dock-dot"));
    btn.appendChild(
      el(
        "span",
        "tl-dock-label",
        list.length > 1 ? `${list.length} 个操作等待批准` : "1 个操作等待批准",
      ),
    );
    btn.appendChild(el("span", "tl-dock-cta", "查看"));
    btn.addEventListener("click", () => {
      if (typeof cbs.onJump === "function") cbs.onJump(list[0].approvalId);
    });
    host.appendChild(btn);
  }

  // ————————————————————————————————————————————————
  // Legacy unified-diff helpers (kept for old payloads; the new review flow
  // reads exact before/after blobs from the checkpoint API instead).
  // ————————————————————————————————————————————————

  function stripDiffPrefix(p) {
    let t = String(p || "").trim();
    if (t.startsWith('"') && t.endsWith('"')) t = t.slice(1, -1);
    if (/^[ab]\//.test(t)) t = t.slice(2);
    return t;
  }

  function splitUnifiedDiff(diff) {
    const text = String(diff || "");
    if (!text.trim()) return [];
    const files = [];
    const sections = text.split(/(?=^diff --git )/m);
    for (const section of sections) {
      if (!section.trim()) continue;
      const minus = section.match(/^--- (.*)$/m);
      const plus = section.match(/^\+\+\+ (.*)$/m);
      const header = section.match(/^diff --git (.+?) (.+)$/m);
      let oldPath = minus ? stripDiffPrefix(minus[1]) : header ? stripDiffPrefix(header[1]) : "";
      let newPath = plus ? stripDiffPrefix(plus[1]) : header ? stripDiffPrefix(header[2]) : "";
      const oldDev = minus ? minus[1].trim() === "/dev/null" : false;
      const newDev = plus ? plus[1].trim() === "/dev/null" : false;
      const change = newDev ? "deleted" : oldDev ? "added" : "modified";
      const path = newDev ? oldPath : newPath || oldPath;
      files.push({ path, oldPath, change, patch: section });
    }
    return files;
  }

  function parseHunks(patch) {
    const hunks = [];
    const lines = String(patch || "").split("\n");
    let i = 0;
    while (i < lines.length) {
      const m = lines[i].match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
      if (!m) {
        i += 1;
        continue;
      }
      const hunk = { oldStart: Number(m[1]), newStart: Number(m[3]), oldLines: [], newLines: [] };
      i += 1;
      while (i < lines.length && !/^@@ /.test(lines[i]) && !/^diff --git /.test(lines[i])) {
        const l = lines[i];
        if (l.startsWith("+")) hunk.newLines.push(l.slice(1));
        else if (l.startsWith("-")) hunk.oldLines.push(l.slice(1));
        else if (l.startsWith(" ")) {
          hunk.oldLines.push(l.slice(1));
          hunk.newLines.push(l.slice(1));
        } else if (l === "\\ No newline at end of file") {
          /* marker only */
        } else break;
        i += 1;
      }
      hunks.push(hunk);
    }
    return hunks;
  }

  function findBlock(lines, block, hint) {
    if (!block.length) return Math.max(0, Math.min(hint, lines.length));
    const matches = (at) => {
      if (at < 0 || at + block.length > lines.length) return false;
      for (let k = 0; k < block.length; k += 1) {
        if (lines[at + k] !== block[k]) return false;
      }
      return true;
    };
    const maxD = lines.length;
    for (let d = 0; d <= maxD; d += 1) {
      if (matches(hint + d)) return hint + d;
      if (d > 0 && matches(hint - d)) return hint - d;
    }
    return -1;
  }

  /**
   * Reconstruct the ORIGINAL content from the current (modified) content and
   * a forward unified diff. Best-effort legacy fallback: returns null when a
   * hunk cannot be located. The checkpoint blob API supersedes this.
   */
  function reverseApplyPatch(modified, patch) {
    try {
      const hunks = parseHunks(patch);
      if (!hunks.length) return String(modified || "");
      let lines = String(modified || "").split("\n");
      for (let h = hunks.length - 1; h >= 0; h -= 1) {
        const hunk = hunks[h];
        const idx = findBlock(lines, hunk.newLines, hunk.newStart - 1);
        if (idx < 0) return null;
        lines.splice(idx, hunk.newLines.length, ...hunk.oldLines);
      }
      return lines.join("\n");
    } catch (_) {
      return null;
    }
  }

  const api = {
    createTimelineView,
    renderApprovalDock,
    renderMarkdown,
    buildTurns,
    splitUnifiedDiff,
    reverseApplyPatch,
    _internal: { parseInline, parseHunks, computeTurnSummary, defaultTurnExpanded, formatWorked },
  };
  if (typeof window !== "undefined") window.AgentTimeline = api;
  if (typeof globalThis !== "undefined") globalThis.AgentTimeline = api;
})();
