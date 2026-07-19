(() => {
  "use strict";

  const LAYOUT_RE = /[/\\]res[/\\]layout(?:-[^/\\]+)?[/\\][^/\\]+\.xml$/i;
  const NS = {
    android: "http://schemas.android.com/apk/res/android",
    app: "http://schemas.android.com/apk/res-auto",
  };

  const WIDGET_TAGS = new Set([
    "TextView",
    "Button",
    "ImageButton",
    "EditText",
    "ImageView",
    "CheckBox",
    "RadioButton",
    "Switch",
    "ToggleButton",
    "ProgressBar",
    "SeekBar",
    "Spinner",
    "View",
    "Space",
    "WebView",
  ]);

  const CONTAINER_TAGS = new Set([
    "LinearLayout",
    "RelativeLayout",
    "FrameLayout",
    "ConstraintLayout",
    "GridLayout",
    "ScrollView",
    "HorizontalScrollView",
    "NestedScrollView",
    "androidx.core.widget.NestedScrollView",
    "RecyclerView",
    "ListView",
    "androidx.constraintlayout.widget.ConstraintLayout",
    "androidx.appcompat.widget.LinearLayoutCompat",
    "com.google.android.material.card.MaterialCardView",
    "CardView",
  ]);

  function isLayoutPath(filePath) {
    return Boolean(filePath && LAYOUT_RE.test(String(filePath).replace(/\\/g, "/")));
  }

  function localName(node) {
    if (!node || node.nodeType !== 1) return "";
    const name = node.localName || node.nodeName || "";
    const parts = name.split(".");
    return parts[parts.length - 1];
  }

  function attr(el, name, fallback = "") {
    if (!el || el.nodeType !== 1) return fallback;
    const raw =
      el.getAttributeNS?.(NS.android, name) ||
      el.getAttribute(`android:${name}`) ||
      el.getAttribute(name);
    return raw == null || raw === "" ? fallback : raw;
  }

  function parseDp(value, fallback = 0) {
    if (value == null || value === "") return fallback;
    const m = String(value).trim().match(/^(-?\d+(?:\.\d+)?)\s*(dp|dip|sp|px)?$/i);
    if (!m) return fallback;
    return Number(m[1]);
  }

  function cssSize(value, axis = "width") {
    const v = String(value || "").trim();
    if (!v || v === "wrap_content") return "auto";
    if (v === "match_parent" || v === "fill_parent") return "100%";
    if (v.endsWith("dp") || v.endsWith("dip") || v.endsWith("sp") || v.endsWith("px")) {
      return `${parseDp(v)}px`;
    }
    if (v === "0dp" || v === "0dip") return axis === "weight" ? "0" : "0px";
    return "auto";
  }

  function parseColor(value) {
    if (!value) return null;
    const v = String(value).trim();
    if (/^#[0-9a-fA-F]{3,8}$/.test(v)) return v;
    return null;
  }

  function gravityToCss(gravity) {
    const g = String(gravity || "").toLowerCase();
    const style = {};
    if (g.includes("center_horizontal") || (g.includes("center") && !g.includes("vertical") && !g.includes("horizontal"))) {
      style.justifyContent = g.includes("center_vertical") || g === "center" ? "center" : style.justifyContent;
      style.alignItems = g.includes("center_horizontal") || g === "center" ? "center" : style.alignItems;
      style.textAlign = "center";
    }
    if (g.includes("center_vertical") || g === "center") style.alignItems = style.alignItems || "center";
    if (g.includes("center_horizontal") || g === "center") {
      style.justifyContent = style.justifyContent || "center";
      style.textAlign = "center";
    }
    if (g.includes("end") || g.includes("right")) {
      style.justifyContent = "flex-end";
      style.textAlign = "right";
    }
    if (g.includes("start") || g.includes("left")) {
      style.justifyContent = "flex-start";
      style.textAlign = "left";
    }
    if (g.includes("bottom")) style.alignItems = "flex-end";
    if (g.includes("top")) style.alignItems = "flex-start";
    return style;
  }

  function applyBoxStyle(target, el) {
    const width = attr(el, "layout_width", "wrap_content");
    const height = attr(el, "layout_height", "wrap_content");
    const weight = attr(el, "layout_weight");
    const padding = attr(el, "padding");
    const padL = attr(el, "paddingLeft") || attr(el, "paddingStart");
    const padR = attr(el, "paddingRight") || attr(el, "paddingEnd");
    const padT = attr(el, "paddingTop");
    const padB = attr(el, "paddingBottom");
    const margin = attr(el, "layout_margin");
    const marL = attr(el, "layout_marginLeft") || attr(el, "layout_marginStart");
    const marR = attr(el, "layout_marginRight") || attr(el, "layout_marginEnd");
    const marT = attr(el, "layout_marginTop");
    const marB = attr(el, "layout_marginBottom");
    const bg = parseColor(attr(el, "background"));
    const tint = parseColor(attr(el, "backgroundTint"));
    const elevation = attr(el, "elevation");

    if (width === "0dp" && weight) {
      target.style.flex = `${parseFloat(weight) || 1} 1 0`;
      target.style.width = "auto";
      target.style.minWidth = "0";
    } else {
      target.style.width = cssSize(width);
    }

    if (height === "0dp" && weight) {
      target.style.flex = `${parseFloat(weight) || 1} 1 0`;
      target.style.height = "auto";
      target.style.minHeight = "0";
    } else {
      target.style.height = cssSize(height);
    }

    if (weight && width !== "0dp" && height !== "0dp") {
      target.style.flex = `${parseFloat(weight) || 1}`;
    }

    if (padding) target.style.padding = `${parseDp(padding)}px`;
    if (padL) target.style.paddingLeft = `${parseDp(padL)}px`;
    if (padR) target.style.paddingRight = `${parseDp(padR)}px`;
    if (padT) target.style.paddingTop = `${parseDp(padT)}px`;
    if (padB) target.style.paddingBottom = `${parseDp(padB)}px`;
    if (margin) target.style.margin = `${parseDp(margin)}px`;
    if (marL) target.style.marginLeft = `${parseDp(marL)}px`;
    if (marR) target.style.marginRight = `${parseDp(marR)}px`;
    if (marT) target.style.marginTop = `${parseDp(marT)}px`;
    if (marB) target.style.marginBottom = `${parseDp(marB)}px`;

    if (tint) {
      target.style.background = tint;
    } else if (bg) {
      target.style.background = bg;
    }

    if (elevation) {
      const e = parseDp(elevation, 0);
      if (e > 0) target.style.boxShadow = `0 ${Math.max(1, e / 2)}px ${e}px rgba(0,0,0,0.25)`;
    }

    const colSpan = attr(el, "layout_columnSpan");
    const rowSpan = attr(el, "layout_rowSpan");
    const colWeight = attr(el, "layout_columnWeight");
    const col = attr(el, "layout_column");
    const row = attr(el, "layout_row");
    if (col !== "" && col != null && /^\d+$/.test(col)) {
      const start = parseInt(col, 10) + 1;
      const span = Math.max(1, parseInt(colSpan || "1", 10) || 1);
      target.style.gridColumn = `${start} / span ${span}`;
    } else if (colSpan) {
      target.style.gridColumn = `span ${Math.max(1, parseInt(colSpan, 10) || 1)}`;
    }
    if (row !== "" && row != null && /^\d+$/.test(row)) {
      const start = parseInt(row, 10) + 1;
      const span = Math.max(1, parseInt(rowSpan || "1", 10) || 1);
      target.style.gridRow = `${start} / span ${span}`;
    } else if (rowSpan) {
      target.style.gridRow = `span ${Math.max(1, parseInt(rowSpan, 10) || 1)}`;
    }
    if (colWeight) target.style.minWidth = "0";
  }

  function resolveText(raw, strings) {
    if (raw == null) return "";
    const v = String(raw);
    const m = v.match(/^@string\/([A-Za-z0-9_.]+)$/);
    if (m && strings && strings[m[1]] != null) return strings[m[1]];
    if (v.startsWith("@")) return v.slice(v.lastIndexOf("/") + 1);
    return v;
  }

  function createWidget(tag, el, strings) {
    let node;
    const text = resolveText(attr(el, "text") || attr(el, "hint"), strings);
    const textSize = attr(el, "textSize");
    const textColor = parseColor(attr(el, "textColor"));
    const textStyle = attr(el, "textStyle");
    const hint = resolveText(attr(el, "hint"), strings);
    const gravity = gravityToCss(attr(el, "gravity"));

    switch (tag) {
      case "Button":
      case "ImageButton":
        node = document.createElement("button");
        node.type = "button";
        node.className = "ap-btn";
        node.textContent = text || (tag === "ImageButton" ? "▢" : "Button");
        break;
      case "EditText":
        node = document.createElement("input");
        node.className = "ap-input";
        node.type = "text";
        node.value = text || "";
        node.placeholder = hint || "输入…";
        node.readOnly = true;
        break;
      case "CheckBox":
      case "RadioButton":
      case "Switch":
      case "ToggleButton": {
        node = document.createElement("label");
        node.className = "ap-check";
        const input = document.createElement("input");
        input.type = tag === "RadioButton" ? "radio" : "checkbox";
        input.disabled = true;
        input.checked = attr(el, "checked") === "true";
        node.appendChild(input);
        node.appendChild(document.createTextNode(` ${text || tag}`));
        break;
      }
      case "ImageView":
        node = document.createElement("div");
        node.className = "ap-image";
        node.textContent = "🖼";
        node.title = attr(el, "src") || "ImageView";
        break;
      case "ProgressBar":
      case "SeekBar":
        node = document.createElement("div");
        node.className = "ap-progress";
        node.innerHTML = '<div class="ap-progress-bar"></div>';
        break;
      case "Space":
        node = document.createElement("div");
        node.className = "ap-space";
        break;
      case "View":
        node = document.createElement("div");
        node.className = "ap-view";
        break;
      case "Spinner":
        node = document.createElement("div");
        node.className = "ap-spinner";
        node.textContent = text || "▾ 选项";
        break;
      case "WebView":
        node = document.createElement("div");
        node.className = "ap-webview";
        node.textContent = "WebView";
        break;
      default:
        node = document.createElement("div");
        node.className = "ap-text";
        node.textContent = text || "";
        break;
    }

    applyBoxStyle(node, el);
    Object.assign(node.style, gravity);
    if (textSize) node.style.fontSize = `${parseDp(textSize, 14)}px`;
    if (textColor) node.style.color = textColor;
    if (textStyle && textStyle.includes("bold")) node.style.fontWeight = "700";
    if (textStyle && textStyle.includes("italic")) node.style.fontStyle = "italic";

    const id = attr(el, "id");
    if (id) node.dataset.androidId = id;
    return node;
  }

  function createContainer(tag, el) {
    const node = document.createElement("div");
    node.className = `ap-container ap-${tag.toLowerCase()}`;
    applyBoxStyle(node, el);

    if (tag === "LinearLayout" || tag === "LinearLayoutCompat") {
      const orientation = attr(el, "orientation", "horizontal");
      node.style.display = "flex";
      node.style.flexDirection = orientation === "vertical" ? "column" : "row";
      node.style.flexWrap = "nowrap";
      Object.assign(node.style, gravityToCss(attr(el, "gravity")));
    } else if (tag === "GridLayout") {
      const cols = Math.max(1, parseInt(attr(el, "columnCount", "1"), 10) || 1);
      node.style.display = "grid";
      node.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
      node.style.gap = "8px";
      node.style.alignItems = "stretch";
    } else if (tag === "ScrollView" || tag === "HorizontalScrollView") {
      node.style.display = "block";
      node.style.overflow = tag === "HorizontalScrollView" ? "auto hidden" : "auto";
    } else if (tag === "FrameLayout" || tag === "CardView" || tag.includes("CardView")) {
      node.style.display = "flex";
      node.style.flexDirection = "column";
      node.style.position = "relative";
    } else {
      // RelativeLayout / ConstraintLayout / unknown → vertical stack approximation
      node.style.display = "flex";
      node.style.flexDirection = "column";
      node.style.gap = "4px";
    }

    if (tag === "RecyclerView" || tag === "ListView") {
      node.classList.add("ap-list-placeholder");
      const placeholder = document.createElement("div");
      placeholder.className = "ap-list-item";
      placeholder.textContent = `${tag}（列表占位）`;
      node.appendChild(placeholder);
    }

    return node;
  }

  function widgetKind(tag) {
    if (WIDGET_TAGS.has(tag)) return tag;
    if (/Button$/i.test(tag) || tag === "MaterialButton") return "Button";
    if (/EditText$/i.test(tag) || tag === "TextInputEditText") return "EditText";
    if (/TextView$/i.test(tag) || tag === "MaterialTextView") return "TextView";
    if (/ImageView$/i.test(tag)) return "ImageView";
    if (/CheckBox$/i.test(tag)) return "CheckBox";
    if (/Switch$/i.test(tag) || tag === "SwitchCompat") return "Switch";
    return null;
  }

  function applyVisibility(node, el) {
    const vis = attr(el, "visibility", "visible");
    if (vis === "gone") {
      node.style.display = "none";
    } else if (vis === "invisible") {
      node.style.visibility = "hidden";
    }
  }

  function walk(el, strings, warnings) {
    if (!el || el.nodeType !== 1) return null;
    const tag = localName(el);

    if (tag === "include") {
      const layout = attr(el, "layout") || el.getAttribute("layout") || "";
      const stub = document.createElement("div");
      stub.className = "ap-include";
      stub.textContent = `include ${layout || "?"}`;
      applyBoxStyle(stub, el);
      applyVisibility(stub, el);
      warnings.push(`未展开 <include>：${layout || "?"}`);
      return stub;
    }

    if (CONTAINER_TAGS.has(tag) || CONTAINER_TAGS.has(el.localName || "")) {
      const short =
        tag.includes("ConstraintLayout")
          ? "ConstraintLayout"
          : tag.includes("LinearLayout")
            ? "LinearLayout"
            : tag.includes("CardView")
              ? "CardView"
              : tag.includes("NestedScrollView")
                ? "ScrollView"
                : tag;
      if (short === "ConstraintLayout") {
        warnings.push("ConstraintLayout 以简化纵向堆叠预览");
      }
      const node = createContainer(short, el);
      applyVisibility(node, el);
      for (const child of Array.from(el.children)) {
        const rendered = walk(child, strings, warnings);
        if (rendered) node.appendChild(rendered);
      }
      return node;
    }

    const kind = widgetKind(tag);
    if (kind) {
      const node = createWidget(kind, el, strings);
      applyVisibility(node, el);
      return node;
    }

    // Unknown custom view — still try to render children
    const fallback = document.createElement("div");
    fallback.className = "ap-unknown";
    fallback.dataset.tag = tag;
    applyBoxStyle(fallback, el);
    applyVisibility(fallback, el);
    const label = document.createElement("div");
    label.className = "ap-unknown-label";
    label.textContent = tag;
    fallback.appendChild(label);
    for (const child of Array.from(el.children)) {
      const rendered = walk(child, strings, warnings);
      if (rendered) fallback.appendChild(rendered);
    }
    warnings.push(`自定义控件近似显示：${tag}`);
    return fallback;
  }

  function parseStringsXml(xmlText) {
    const map = {};
    if (!xmlText) return map;
    try {
      const doc = new DOMParser().parseFromString(xmlText, "application/xml");
      if (doc.querySelector("parsererror")) return map;
      for (const node of Array.from(doc.querySelectorAll("string"))) {
        const name = node.getAttribute("name");
        if (name) map[name] = node.textContent || "";
      }
    } catch (_) {
      /* ignore */
    }
    return map;
  }

  async function loadStringsNearLayout(layoutPath, readFile, joinPath) {
    if (!layoutPath || !readFile || !joinPath) return {};
    const normalized = layoutPath.replace(/\\/g, "/");
    const marker = "/res/";
    const idx = normalized.lastIndexOf(marker);
    if (idx < 0) return {};
    const resRoot = normalized.slice(0, idx + marker.length);
    const candidates = ["values/strings.xml", "values-zh/strings.xml", "values-zh-rCN/strings.xml"];
    for (const rel of candidates) {
      try {
        const abs = await joinPath(resRoot, rel);
        const data = await readFile(abs);
        const map = parseStringsXml(data?.content || data || "");
        if (Object.keys(map).length) return map;
      } catch (_) {
        /* try next */
      }
    }
    return {};
  }

  function renderXml(xmlText, { strings = {} } = {}) {
    const warnings = [];
    const parser = new DOMParser();
    const doc = parser.parseFromString(xmlText || "", "application/xml");
    const err = doc.querySelector("parsererror");
    if (err) {
      return {
        ok: false,
        error: err.textContent || "XML 解析失败",
        root: null,
        warnings,
      };
    }
    const rootEl = doc.documentElement;
    if (!rootEl) {
      return { ok: false, error: "空布局", root: null, warnings };
    }
    const root = walk(rootEl, strings, warnings);
    if (root) {
      root.classList.add("ap-root");
      if (!root.style.height || root.style.height === "auto") {
        root.style.minHeight = "100%";
      }
      if (!root.style.width || root.style.width === "auto") {
        root.style.width = "100%";
      }
    }
    return { ok: true, error: null, root, warnings: unique(warnings) };
  }

  function unique(list) {
    return [...new Set(list.filter(Boolean))];
  }

  window.LayoutPreview = {
    isLayoutPath,
    renderXml,
    parseStringsXml,
    loadStringsNearLayout,
  };
})();
