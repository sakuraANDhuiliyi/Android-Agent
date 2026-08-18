(() => {
  "use strict";

  /**
   * Desktop performance / UX helpers shared by the Agent panel and tests.
   * Pure functions: no DOM, no Electron APIs.
   */

  const KEEP_TURNS_DEFAULT = 40;
  const KEEP_TURNS_STEP = 40;
  const MAX_MARKDOWN_CHARS = 80_000;
  const MAX_TOOL_OUTPUT_CHARS = 32_000;
  const MAX_DIFF_CHARS = 1_500_000;
  const TOAST_MIN_INTERVAL_MS = 2500;
  const DRAFT_STORAGE_KEY = "android-agent-drafts";

  function windowTurns(turns, keep = KEEP_TURNS_DEFAULT) {
    const list = Array.isArray(turns) ? turns : [];
    const limit = Math.max(1, Number(keep) || KEEP_TURNS_DEFAULT);
    if (list.length <= limit) {
      return { hidden: 0, turns: list, keep: limit };
    }
    return {
      hidden: list.length - limit,
      turns: list.slice(-limit),
      keep: limit,
    };
  }

  function truncateText(text, maxChars, notice) {
    const value = text == null ? "" : String(text);
    const limit = Math.max(0, Number(maxChars) || 0);
    if (!limit || value.length <= limit) {
      return { text: value, truncated: false };
    }
    const suffix = notice || `\n\n…已截断（原 ${value.length} 字符）`;
    return { text: value.slice(0, limit) + suffix, truncated: true };
  }

  function isBinaryText(text) {
    if (text == null) return false;
    const sample = String(text).slice(0, 8192);
    return sample.includes("\u0000");
  }

  function shouldUseDiffNotice({ original, modified, binary, path } = {}) {
    if (binary) return { notice: true, reason: "binary" };
    const orig = original == null ? "" : String(original);
    const mod = modified == null ? "" : String(modified);
    if (isBinaryText(orig) || isBinaryText(mod)) {
      return { notice: true, reason: "binary" };
    }
    if (orig.length + mod.length > MAX_DIFF_CHARS) {
      return { notice: true, reason: "too_large" };
    }
    const name = String(path || "").toLowerCase();
    if (/\.(png|jpe?g|gif|webp|ico|pdf|zip|apk|so|dylib|exe|bin|woff2?)$/.test(name)) {
      return { notice: true, reason: "binary" };
    }
    return { notice: false, reason: "" };
  }

  function diffNoticeMessage({ reason, path } = {}) {
    const loc = path ? `\n路径: ${path}` : "";
    if (reason === "too_large") {
      return `Diff 过大，未载入文本编辑器。${loc}\n请在工作区打开文件或缩小改动后再审查。`;
    }
    return `二进制文件，不提供文本 Diff。${loc}`;
  }

  function restoreConfirmMessage({ fileCount, conflicts, kind } = {}) {
    const kindLabel =
      { before_turn: "任务开始前", after_turn: "任务完成后" }[kind] || kind || "检查点";
    const count =
      fileCount == null ? "工作区对应文件" : `${Number(fileCount)} 个文件`;
    const lines = [
      `恢复「${kindLabel}」检查点会覆盖 ${count}。`,
      "未纳入检查点的未保存改动可能丢失。",
    ];
    const list = Array.isArray(conflicts) ? conflicts : [];
    if (list.length) {
      const paths = list
        .map((c) => (c && (c.path || c)) || "")
        .filter(Boolean)
        .slice(0, 8);
      lines.push("", `当前工作区有 ${list.length} 处冲突，无法安全恢复：`);
      for (const p of paths) lines.push(`· ${p}`);
      if (list.length > paths.length) {
        lines.push(`· …另有 ${list.length - paths.length} 个文件`);
      }
      lines.push("", "请先处理冲突后再恢复。");
      return { text: lines.join("\n"), blocked: true, conflictCount: list.length };
    }
    lines.push("", "确定继续吗？");
    return { text: lines.join("\n"), blocked: false, conflictCount: 0 };
  }

  function formatRestoreError(detail) {
    if (!detail || typeof detail !== "object") {
      return typeof detail === "string" ? detail : "恢复失败";
    }
    const conflicts = Array.isArray(detail.conflicts) ? detail.conflicts : [];
    if (detail.error === "conflict" || conflicts.length) {
      const paths = conflicts
        .map((c) => (c && c.path) || "")
        .filter(Boolean)
        .slice(0, 6);
      const extra = conflicts.length > paths.length ? ` 等 ${conflicts.length} 个` : "";
      return `无法恢复：工作区有未保存冲突${paths.length ? "（" + paths.join("、") + extra + "）" : ""}`;
    }
    if (typeof detail.message === "string" && detail.message) return detail.message;
    if (typeof detail.error === "string") return `恢复失败: ${detail.error}`;
    return "恢复失败";
  }

  function createToastThrottle({ minIntervalMs = TOAST_MIN_INTERVAL_MS, now } = {}) {
    let lastAt = 0;
    let lastMsg = "";
    const clock = typeof now === "function" ? now : () => Date.now();
    return function shouldShow(msg) {
      const text = String(msg || "");
      const t = clock();
      if (text === lastMsg && t - lastAt < minIntervalMs) return false;
      lastAt = t;
      lastMsg = text;
      return true;
    };
  }

  function draftStorageKey(projectId, conversationId) {
    return `${projectId || ""}::${conversationId || ""}`;
  }

  function readDraftMap(storage) {
    if (!storage || typeof storage.getItem !== "function") return {};
    try {
      const raw = storage.getItem(DRAFT_STORAGE_KEY);
      const data = raw ? JSON.parse(raw) : {};
      return data && typeof data === "object" && !Array.isArray(data) ? data : {};
    } catch (_) {
      return {};
    }
  }

  function writeDraft(storage, projectId, conversationId, text) {
    if (!storage || typeof storage.setItem !== "function") return;
    const map = readDraftMap(storage);
    const key = draftStorageKey(projectId, conversationId);
    const value = String(text || "");
    if (!value) delete map[key];
    else map[key] = value;
    const keys = Object.keys(map);
    if (keys.length > 40) {
      for (const extra of keys.slice(0, keys.length - 40)) delete map[extra];
    }
    storage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(map));
  }

  function readDraft(storage, projectId, conversationId) {
    const map = readDraftMap(storage);
    return map[draftStorageKey(projectId, conversationId)] || "";
  }

  const api = {
    KEEP_TURNS_DEFAULT,
    KEEP_TURNS_STEP,
    MAX_MARKDOWN_CHARS,
    MAX_TOOL_OUTPUT_CHARS,
    MAX_DIFF_CHARS,
    TOAST_MIN_INTERVAL_MS,
    DRAFT_STORAGE_KEY,
    windowTurns,
    truncateText,
    isBinaryText,
    shouldUseDiffNotice,
    diffNoticeMessage,
    restoreConfirmMessage,
    formatRestoreError,
    createToastThrottle,
    draftStorageKey,
    readDraft,
    writeDraft,
  };

  if (typeof window !== "undefined") window.DesktopPerf = api;
  if (typeof globalThis !== "undefined") globalThis.DesktopPerf = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
