(() => {
  "use strict";

  const STORAGE_KEY = "android-agent-desktop-theme";
  const MODES = new Set(["system", "light", "dark"]);
  const media = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)")
    : null;

  let mode = "dark";
  let resolved = "dark";

  function normalizeMode(value) {
    return MODES.has(value) ? value : "dark";
  }

  function resolveMode(value, prefersLight = Boolean(media?.matches)) {
    const normalized = normalizeMode(value);
    if (normalized === "system") return prefersLight ? "light" : "dark";
    return normalized;
  }

  function readMode() {
    try {
      return normalizeMode(localStorage.getItem(STORAGE_KEY));
    } catch (_) {
      return "dark";
    }
  }

  function apply(nextMode = mode, { persist = false, announce = true } = {}) {
    mode = normalizeMode(nextMode);
    resolved = resolveMode(mode);
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
    // TheoKit UI (@theokit/ui) tokens switch dark values via the `.dark`
    // class rather than data-theme — mirror both so vendor tokens follow.
    document.documentElement.classList.toggle("dark", resolved === "dark");
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, mode);
      } catch (_) {
        /* Theme persistence is a convenience, never a startup blocker. */
      }
    }
    if (announce) {
      window.dispatchEvent(new CustomEvent("android-agent-theme-change", {
        detail: { mode, resolved },
      }));
    }
    return resolved;
  }

  function setMode(nextMode) {
    return apply(nextMode, { persist: true });
  }

  function onSystemThemeChanged() {
    if (mode === "system") apply(mode);
  }

  mode = readMode();
  apply(mode, { announce: false });
  media?.addEventListener?.("change", onSystemThemeChanged);

  window.ThemeManager = {
    modes: ["system", "light", "dark"],
    normalizeMode,
    resolveMode,
    setMode,
    getMode: () => mode,
    getResolved: () => resolved,
  };
})();
