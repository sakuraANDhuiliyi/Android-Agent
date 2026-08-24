(() => {
  "use strict";

  const ACTIVE_STATUSES = new Set([
    "queued",
    "running",
    "awaiting_approval",
    "paused",
    "cancel_requested",
  ]);

  function jobProjectId(job) {
    return job?.project_id || job?.projectId || "";
  }

  function jobConversationId(job) {
    return job?.conversation_id || job?.conversationId || "";
  }

  function approvalPayload(approval) {
    return approval?.payload && typeof approval.payload === "object" ? approval.payload : {};
  }

  function isDestructive(item) {
    const approval = item?.approval || item || {};
    const payload = approvalPayload(approval);
    const risk = String(approval.risk || payload.risk || "").toLowerCase();
    const kind = String(approval.kind || payload.kind || "").toLowerCase();
    return risk === "destructive" || [
      "process",
      "run_command",
      "delete",
      "install",
      "worktree_finalize",
      "recovery_tool_replay",
    ].some((value) => kind.includes(value));
  }

  function kindLabel(item) {
    const approval = item?.approval || item || {};
    const payload = approvalPayload(approval);
    const kind = String(approval.kind || payload.kind || "tool").toLowerCase();
    if (kind.includes("network") || kind.includes("web") || kind.includes("download")) return "网络访问";
    if (kind.includes("process") || kind.includes("command") || kind.includes("gradle")) return "运行命令";
    if (kind.includes("install")) return "安装应用";
    if (kind.includes("file") || kind.includes("write") || kind.includes("edit")) return "修改文件";
    return "工具操作";
  }

  function intent(item) {
    const approval = item?.approval || item || {};
    const payload = approvalPayload(approval);
    const command = payload.command || (Array.isArray(payload.argv) ? payload.argv.join(" ") : "");
    if (command) return String(command);
    if (payload.url) return String(payload.url);
    if (payload.path) return String(payload.path);
    if (Array.isArray(payload.target_paths) && payload.target_paths.length) {
      return payload.target_paths.join(", ");
    }
    return String(payload.message || approval.message || "查看操作范围后决定是否允许");
  }

  function buildApprovalInbox({ jobs = [], approvalsByJob = {}, projects = [], conversations = [] } = {}) {
    const projectNames = new Map(projects.map((project) => [project.id, project.name || project.id]));
    const conversationTitles = new Map(
      conversations.map((conversation) => [conversation.id, conversation.title || conversation.id]),
    );
    const items = [];
    for (const job of jobs) {
      if (!ACTIVE_STATUSES.has(job?.status)) continue;
      const projectId = jobProjectId(job);
      const conversationId = jobConversationId(job);
      const approvals = approvalsByJob[job.id] || [];
      for (const approval of approvals) {
        if (approval.status && approval.status !== "pending") continue;
        items.push({
          id: `${job.id}:${approval.id}`,
          jobId: job.id,
          projectId,
          projectName: projectNames.get(projectId) || projectId || "未知项目",
          conversationId,
          conversationTitle: conversationTitles.get(conversationId) || "",
          prompt: job.prompt || "",
          approval,
          createdAt: Number(approval.created_at || approval.createdAt || 0),
        });
      }
    }
    return items.sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id));
  }

  function relativeTime(timestamp, nowSeconds = Date.now() / 1000) {
    if (!timestamp) return "刚刚";
    const seconds = Math.max(0, Math.round(nowSeconds - Number(timestamp)));
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return `${Math.floor(seconds / 86400)} 天前`;
  }

  window.ApprovalInbox = {
    ACTIVE_STATUSES,
    buildApprovalInbox,
    isDestructive,
    kindLabel,
    intent,
    relativeTime,
  };
})();
