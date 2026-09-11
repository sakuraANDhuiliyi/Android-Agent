/* Present only facts carried by this job; a successful turn is not a tested APK. */
(function(root) {
  "use strict";
  const labels = { queued: "等待执行", running: "正在执行", awaiting_approval: "等待你的审批", paused: "已暂停", cancel_requested: "正在停止 · 等待执行进程退出", succeeded: "本轮已结束", failed: "执行失败", canceled: "已停止", interrupted: "执行中断" };
  function describe(job) {
    const status = job.display_status || (job.cancel_requested && !["succeeded", "failed", "canceled", "interrupted"].includes(job.status) ? "cancel_requested" : job.status);
    return {
      label: labels[status] || "等待任务状态",
      terminal: ["succeeded", "failed", "canceled", "interrupted"].includes(status),
      checks: [
        { label: "文件改动", available: Array.isArray(job.changed_files) && job.changed_files.length > 0, value: "可审阅" },
        { label: "APK 产物", available: job.has_apk === true, value: "已生成" },
        { label: "构建日志", available: job.has_build_log === true, value: "已记录" },
        { label: "测试与安装", available: false, value: "未验证" },
      ],
    };
  }
  function render(job, doc = document) {
    const info = describe(job);
    const el = (tag, content, cls) => { const n = doc.createElement(tag); n.textContent = content; if (cls) n.className = cls; return n; };
    if (!info.terminal) return el("div", info.label, "run-phase");
    const card = el("section", "", "delivery-card"); card.setAttribute("aria-label", "本轮交付状态");
    const head = el("header", "");
    head.append(el("strong", "本轮交付"), el("span", String(job.id || "").slice(0, 12), "delivery-id"));
    const checks = el("div", "", "delivery-checks");
    for (const check of info.checks) {
      const row = el("div", "", "delivery-check"); row.dataset.state = check.available ? "available" : "unknown";
      row.append(el("span", check.available ? "✓" : "○"), el("b", check.label), el("small", check.available ? check.value : "未验证")); checks.append(row);
    }
    card.append(head, checks, el("p", "依据本轮任务记录展示。APK 与当前代码的版本关联、测试和设备运行结果尚待验证。"));
    return card;
  }
  root.DeliveryUI = { describe, render, labels };
  if (typeof module !== "undefined") module.exports = root.DeliveryUI;
})(typeof window !== "undefined" ? window : globalThis);
