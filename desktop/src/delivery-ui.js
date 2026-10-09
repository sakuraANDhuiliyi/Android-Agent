/* Display server-recorded facts for this job, never infer tests from chat/APKs. */
(function(root) {
  "use strict";
  const labels = { queued: "等待执行", running: "正在执行", awaiting_approval: "等待你的审批", paused: "已暂停", cancel_requested: "正在停止 · 等待执行进程退出", succeeded: "本轮已结束", failed: "执行失败", canceled: "已停止", interrupted: "执行中断" };
  const states = { not_run: "未运行", passed: "通过", failed: "失败", canceled: "已取消", interrupted: "已中断", unknown: "未验证", no_tests: "没有测试用例", skipped: "全部跳过" };
  const reasons = { not_run: "本任务尚未运行此项", legacy_evidence: "旧版记录缺少本次验证证据", no_fresh_report: "未发现本次运行的新测试报告", invalid_report: "测试报告无法验证", no_tests: "报告中没有测试用例", all_skipped: "所有用例均已跳过", command_failed: "验证命令执行失败", canceled: "验证已取消", interrupted: "验证执行中断", inputs_changed: "验证期间输入已变化", inputs_unknown: "缺少完整的输入版本记录" };
  const sources = { match: "与本轮结束时的输入一致", changed: "本轮结束前输入已变化，需重新验证", unknown: "输入版本关联未验证" };
  const terminal = new Set(["succeeded", "failed", "canceled", "interrupted"]);
  const validNumber = value => typeof value === "number" && Number.isFinite(value);
  const validTime = value => validNumber(value) && value > 0 && !Number.isNaN(new Date(value * 1000).getTime());

  function countsFor(value) {
    if (!value || typeof value !== "object") return null;
    const keys = ["total", "passed", "failed", "skipped"];
    if (!keys.every(key => Number.isSafeInteger(value[key]) && value[key] >= 0)) return null;
    if (value.passed + value.failed + value.skipped !== value.total) return null;
    return Object.fromEntries(keys.map(key => [key, value[key]]));
  }

  function execution(raw, task, label, unitTests = false) {
    const unknown = { key: unitTests ? "unit_tests" : "build", label, state: "unknown", value: states.unknown,
      available: false, sourceMatch: "unknown", sourceLabel: sources.unknown, evidenceTime: null, durationMs: null, counts: null, reasonLabel: "验证记录不足" };
    if (!raw || raw.task !== task || !Object.hasOwn(states, raw.state)
        || (!unitTests && ["no_tests", "skipped"].includes(raw.state))) return unknown;
    const hasEvidence = typeof raw.run_id === "string" && raw.run_id.trim().length > 0 && validTime(raw.evidence_time);
    if (raw.state !== "not_run" && raw.state !== "unknown" && !hasEvidence) return unknown;
    const counts = countsFor(raw.counts);
    if (unitTests && raw.counts != null && !counts) return unknown;
    if (unitTests && raw.state === "passed" && (!counts || counts.passed === 0 || counts.failed !== 0)) return unknown;
    if (unitTests && raw.state === "no_tests" && (!counts || counts.total !== 0)) return unknown;
    if (unitTests && raw.state === "skipped" && (!counts || counts.total === 0 || counts.skipped !== counts.total)) return unknown;
    const sourceMatch = Object.hasOwn(sources, raw.source_match) ? raw.source_match : "unknown";
    const resultLabel = raw.state === "passed" && sourceMatch !== "match" ? "此前执行通过" : states[raw.state];
    const value = unitTests && counts && ["passed", "failed", "skipped"].includes(raw.state)
      ? `${resultLabel} · ${counts.passed} 通过 / ${counts.failed} 失败 / ${counts.skipped} 跳过`
      : resultLabel;
    return { ...unknown, state: raw.state, value, counts,
      reasonLabel: Object.hasOwn(reasons, raw.reason) ? reasons[raw.reason] : raw.state === "unknown" ? "验证记录不足" : null,
      available: raw.state === "passed" && sourceMatch === "match",
      sourceMatch, sourceLabel: sources[sourceMatch],
      evidenceTime: hasEvidence ? raw.evidence_time : null,
      durationMs: validNumber(raw.duration_ms) && raw.duration_ms >= 0 ? raw.duration_ms : null };
  }

  function describe(job = {}) {
    if (!job || typeof job !== "object" || Array.isArray(job)) job = {};
    const status = job.display_status || (job.cancel_requested && !terminal.has(job.status) ? "cancel_requested" : job.status);
    const candidate = job.verification;
    const verification = candidate && candidate.schema_version === 1 && candidate.scope === "job"
      && typeof job.id === "string" && candidate.job_id === job.id ? candidate : null;
    const build = execution(verification?.build, "assembleDebug", "构建");
    const tests = execution(verification?.unit_tests, "testDebugUnitTest", "单元测试", true);
    const fact = (key, label, available, value) => ({ key, label, available, state: available ? "available" : "unknown", value: available ? value : "未验证" });
    return {
      label: labels[status] || "等待任务状态", terminal: terminal.has(status),
      checks: [build, tests,
        fact("apk", "APK 产物", job.has_apk === true, "已生成"),
        // Device installation needs a verified receipt. Schema v1 carries none.
        fact("installation", "设备安装与运行", false),
        fact("changes", "文件改动", Array.isArray(job.changed_files) && job.changed_files.length > 0, "可审阅"),
        fact("log", "构建日志", job.has_build_log === true, "已记录"),
      ],
      evidenceTime: Math.max(build.evidenceTime || 0, tests.evidenceTime || 0) || null,
      note: "记录本任务最近一次验证。输入关联仅比较验证时与本轮结束时的版本，不代表之后的工作区；APK 生成不代表已安装或运行。",
    };
  }

  function formatTime(seconds) {
    return validTime(seconds) ? new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false }) : "时间未记录";
  }

  function render(job, doc = document) {
    const info = describe(job);
    const el = (tag, content, cls) => { const node = doc.createElement(tag); node.textContent = content; if (cls) node.className = cls; return node; };
    if (!info.terminal) return el("div", info.label, "run-phase");
    const card = el("section", "", "delivery-card"); card.setAttribute("aria-label", "本任务最近一次验证");
    const head = el("header", "");
    head.append(el("strong", "本任务最近一次验证"), el("span", String(job.id || "").slice(0, 40), "delivery-id"));
    const recorded = el("p", `运行时间：${formatTime(info.evidenceTime)}`, "delivery-time");
    const checks = el("div", "", "delivery-checks");
    for (const check of info.checks) {
      const row = el("div", "", "delivery-check");
      row.dataset.state = check.state; row.dataset.check = check.key; row.dataset.available = String(check.available);
      const status = el("div", "", "delivery-check-status");
      status.append(el("span", check.available ? "✓" : check.state === "failed" ? "×" : "○"), el("b", check.label), el("small", check.value));
      row.append(status);
      if (check.reasonLabel) row.append(el("p", check.reasonLabel, "delivery-reason"));
      if (check.sourceMatch) {
        row.dataset.sourceMatch = check.sourceMatch;
        row.append(el("p", check.sourceLabel, "delivery-source"));
        if (check.evidenceTime) row.append(el("p", `${formatTime(check.evidenceTime)}${check.durationMs != null ? ` · ${(check.durationMs / 1000).toFixed(1)} 秒` : ""}`, "delivery-evidence-time"));
      }
      checks.append(row);
    }
    card.append(head, recorded, checks, el("p", info.note, "delivery-note"));
    return card;
  }
  root.DeliveryUI = { describe, render, labels };
  if (typeof module !== "undefined") module.exports = root.DeliveryUI;
})(typeof window !== "undefined" ? window : globalThis);
