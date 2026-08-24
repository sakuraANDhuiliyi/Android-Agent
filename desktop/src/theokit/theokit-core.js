/*
 * theokit-core.js — DOM helpers for the TheoKit vanilla component library.
 * h() builds elements declaratively; cn() joins class names; toNode() and
 * append() normalize the ReactNode-like prop values the React library accepts.
 */
(function (root) {
  "use strict";

  var Tk = root.Theokit = root.Theokit || {};
  var icons = Tk.icons || {};

  function cn() {
    var out = [];
    for (var i = 0; i < arguments.length; i++) {
      var v = arguments[i];
      if (!v) continue;
      if (typeof v === "string") out.push(v);
      else if (Array.isArray(v)) out.push(cn.apply(null, v));
      else if (typeof v === "object")
        Object.keys(v).forEach(function (k) {
          if (v[k]) out.push(k);
        });
    }
    return out.join(" ");
  }

  function toNode(value) {
    if (value == null || value === false || value === true) return null;
    if (typeof value === "string" || typeof value === "number") {
      return root.document.createTextNode(String(value));
    }
    if (typeof value === "function") return toNode(value());
    if (Array.isArray(value)) {
      var frag = root.document.createDocumentFragment();
      value.forEach(function (v) {
        var n = toNode(v);
        if (n) frag.appendChild(n);
      });
      return frag;
    }
    if (value.nodeType) return value;
    return null;
  }

  function append(parent, value) {
    var n = toNode(value);
    if (n) parent.appendChild(n);
    return parent;
  }

  /*
   * h(tag, attrs, ...children) — attrs: { class, data-*, onClick|on*, ... }.
   * onXxx handlers attach via addEventListener with the lowercased event name
   * ("onClick" -> "click", "onValueChange" -> "valuechange" custom hook aside).
   */
  function h(tag, attrs) {
    var doc = root.document;
    var el =
      tag === "svg" || tag === "path" || tag === "g" || tag === "rect" || tag === "circle"
        ? doc.createElementNS("http://www.w3.org/2000/svg", tag)
        : doc.createElement(tag);
    var kids = [];
    for (var i = 2; i < arguments.length; i++) kids.push(arguments[i]);

    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var val = attrs[key];
        if (val == null || val === false) return;
        if (key === "class" || key === "className") {
          el.setAttribute("class", cn(val));
        } else if (key === "dataset" && typeof val === "object") {
          Object.keys(val).forEach(function (d) {
            el.dataset[d] = val[d];
          });
        } else if (key === "style" && typeof val === "object") {
          Object.assign(el.style, val);
        } else if (key.slice(0, 2) === "on" && typeof val === "function") {
          var evName = key.slice(2, 3).toLowerCase() + key.slice(3);
          el.addEventListener(evName, val);
        } else if (val === true) {
          el.setAttribute(key, "");
        } else {
          el.setAttribute(key, String(val));
        }
      });
    }
    kids.forEach(function (k) {
      append(el, k);
    });
    return el;
  }

  /* Render a lucide icon by prop: icon name string, factory fn, or null. */
  function renderIcon(spec, cls) {
    if (!spec) return null;
    if (typeof spec === "string") return icons.icon(spec, cls);
    if (typeof spec === "function") return spec(cls);
    return null;
  }

  function text(value) {
    return value == null ? "" : String(value);
  }

  /* Button styled after @usetheo/ui Button — used across composites. */
  var BTN_BASE =
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-body-sm font-medium transition-colors " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50";
  var BTN_SIZES = {
    sm: "h-8 px-3",
    md: "h-9 px-4",
    lg: "h-10 px-5",
    icon: "size-9",
    "icon-sm": "size-7",
  };
  var BTN_VARIANTS = {
    primary: "bg-primary text-primary-foreground hover:bg-primary/90",
    secondary: "border border-border bg-card text-card-foreground hover:bg-muted/50",
    ghost: "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
    destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
    outline: "border border-border bg-transparent hover:bg-muted/50",
  };

  function Button(props) {
    props = props || {};
    var variant = props.variant || "primary";
    var size = props.size || "md";
    var btn = h(
      "button",
      {
        type: props.type || "button",
        class: cn(BTN_BASE, BTN_SIZES[size], BTN_VARIANTS[variant], props.class),
        "data-slot": props["data-slot"],
        "aria-label": props.label,
        title: props.title,
        disabled: props.disabled ? "" : null,
        onClick: props.onClick,
      },
      props.children
    );
    return btn;
  }

  Tk.cn = cn;
  Tk.toNode = toNode;
  Tk.append = append;
  Tk.h = h;
  Tk.renderIcon = renderIcon;
  Tk.text = text;
  Tk.Button = Button;
  if (typeof module !== "undefined" && module.exports) module.exports = Tk;
})(typeof window !== "undefined" ? window : globalThis);
