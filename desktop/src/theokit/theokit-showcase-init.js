/*
 * theokit-showcase-init.js — bootstrap for theokit-showcase.html: applies the
 * saved theme, wires the theme toggle, and mounts the component gallery.
 */
(function () {
  "use strict";
  var saved = null;
  try { saved = window.localStorage.getItem("theokit-showcase-theme"); } catch (e) {}
  if (saved === "dark" || (!saved && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
    document.documentElement.classList.add("dark");
  }
  document.getElementById("btnShowcaseTheme").addEventListener("click", function () {
    var dark = document.documentElement.classList.toggle("dark");
    try { window.localStorage.setItem("theokit-showcase-theme", dark ? "dark" : "light"); } catch (e) {}
  });

  var root = document.getElementById("showcaseRoot");
  try {
    root.appendChild(window.TheokitShowcase.build());
    var comps = Object.keys(window.Theokit).filter(function (k) {
      return /^[A-Z]/.test(k) && typeof window.Theokit[k] === "function";
    });
    document.getElementById("showcaseMeta").textContent =
      "Theokit vanilla JS 迁移组件库 · " + comps.length + " components · @theokit/ui 1.4.1";
  } catch (e) {
    var pre = document.createElement("pre");
    pre.style.color = "var(--destructive)";
    pre.style.fontSize = "12px";
    pre.textContent = "showcase render error: " + (e && e.stack || e);
    root.appendChild(pre);
  }
})();
