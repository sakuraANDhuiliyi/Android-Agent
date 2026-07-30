(() => {
  "use strict";

  /**
   * Minimal Agent HTTP client shared by the desktop AI panel.
   */
  class AgentApi {
    constructor() {
      this.baseUrl = "http://127.0.0.1:8000";
      this.token = "";
    }

    configure({ baseUrl, token } = {}) {
      if (baseUrl) this.baseUrl = String(baseUrl).replace(/\/+$/, "");
      if (token !== undefined) this.token = String(token || "");
    }

    headers(extra = {}) {
      const headers = { Accept: "application/json", ...extra };
      if (this.token) headers.Authorization = `Bearer ${this.token}`;
      return headers;
    }

    async request(path, options = {}) {
      const headers = this.headers(options.headers || {});
      if (options.body && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
      const res = await fetch(`${this.baseUrl}${path}`, {
        ...options,
        headers,
        body:
          options.body && typeof options.body !== "string"
            ? JSON.stringify(options.body)
            : options.body,
      });
      if (res.status === 204) return null;
      const text = await res.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch (_) {
        data = { detail: text };
      }
      if (!res.ok) {
        const detail =
          (data && (data.detail || data.message)) || res.statusText || "请求失败";
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      return data;
    }

    health() {
      return this.request("/api/health");
    }

    pair(registrationToken) {
      return this.request("/api/pair", {
        method: "POST",
        headers: { "X-Registration-Token": registrationToken },
        body: {},
      });
    }

    models() {
      return this.request("/api/models");
    }

    projects() {
      return this.request("/api/projects");
    }

    createProject(body) {
      return this.request("/api/projects", { method: "POST", body });
    }

    ask(projectId, body) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/ask`, {
        method: "POST",
        body,
      });
    }

    conversations(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/conversations`);
    }

    createConversation(projectId, title = "新对话") {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/conversations`, {
        method: "POST",
        body: { title },
      });
    }

    getConversation(conversationId) {
      return this.request(`/api/conversations/${encodeURIComponent(conversationId)}`);
    }

    askConversation(conversationId, body) {
      return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/ask`, {
        method: "POST",
        body,
      });
    }

    clearSession(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/session`, {
        method: "DELETE",
      });
    }

    getSession(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/session`);
    }

    job(jobId) {
      return this.request(`/api/jobs/${encodeURIComponent(jobId)}`);
    }

    jobs(projectId, conversationId) {
      const params = new URLSearchParams();
      if (projectId) params.set("project_id", projectId);
      if (conversationId) params.set("conversation_id", conversationId);
      const q = params.toString() ? `?${params}` : "";
      return this.request(`/api/jobs${q}`);
    }

    cancel(jobId) {
      return this.request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
        body: {},
      });
    }

    resolveApproval(jobId, approvalId, approved) {
      return this.request(
        `/api/jobs/${encodeURIComponent(jobId)}/approvals/${encodeURIComponent(approvalId)}`,
        { method: "POST", body: { approved: Boolean(approved) } },
      );
    }

    listApprovals(jobId) {
      return this.request(`/api/jobs/${encodeURIComponent(jobId)}/approvals`);
    }

    renameConversation(id, title) {
      return this.request(`/api/conversations/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: { title },
      });
    }

    archiveConversation(id) {
      return this.request(`/api/conversations/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
    }

    restoreConversation(id) {
      return this.request(`/api/conversations/${encodeURIComponent(id)}/restore`, {
        method: "POST",
      });
    }

    listArchivedConversations(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/conversations?archived=1`);
    }

    sendJobMessage(jobId, type, payload = {}, messageKey = null) {
      const randomPart =
        globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
          ? globalThis.crypto.randomUUID()
          : `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
      return this.request(`/api/jobs/${encodeURIComponent(jobId)}/messages`, {
        method: "POST",
        body: {
          message_key: messageKey || `${type}:${Date.now()}:${randomPart}`,
          type,
          payload,
        },
      });
    }

    pauseJob(jobId) {
      return this.request(`/api/jobs/${encodeURIComponent(jobId)}/pause`, { method: "POST" });
    }

    resumeJob(jobId) {
      return this.request(`/api/jobs/${encodeURIComponent(jobId)}/resume`, { method: "POST" });
    }

    steerJob(jobId, text) {
      return this.sendJobMessage(jobId, "steer", { text });
    }

    followUpJob(jobId, text) {
      return this.sendJobMessage(jobId, "follow_up", { text });
    }

    search(projectId, query) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/search?q=${encodeURIComponent(query)}`);
    }

    repoMap(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/symbols`);
    }

    indexStatus(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/index/status`);
    }

    rebuildIndex(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/index/rebuild`, { method: "POST" });
    }

    checkpoints(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/checkpoints`);
    }

    restoreCheckpoint(projectId, checkpointId, filePath = null) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/checkpoints/${encodeURIComponent(checkpointId)}/restore`, {
        method: "POST",
        body: { path: filePath },
      });
    }

    turnDiff(projectId, turnId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/diff?turn_id=${encodeURIComponent(turnId)}`);
    }

    // —— Terminals ——
    listTerminals(projectId) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/terminals`);
    }

    createTerminal(projectId, body) {
      return this.request(`/api/projects/${encodeURIComponent(projectId)}/terminals`, {
        method: "POST",
        body,
      });
    }

    getTerminal(terminalId) {
      return this.request(`/api/terminals/${encodeURIComponent(terminalId)}`);
    }

    terminalInput(terminalId, data) {
      return this.request(`/api/terminals/${encodeURIComponent(terminalId)}/input`, {
        method: "POST",
        body: { data },
      });
    }

    terminalResize(terminalId, cols, rows) {
      return this.request(`/api/terminals/${encodeURIComponent(terminalId)}/resize`, {
        method: "POST",
        body: { cols, rows },
      });
    }

    deleteTerminal(terminalId) {
      return this.request(`/api/terminals/${encodeURIComponent(terminalId)}`, { method: "DELETE" });
    }

    websocketTicket(resourceType, resourceId) {
      return this.request("/api/ws/tickets", {
        method: "POST",
        body: { resource_type: resourceType, resource_id: resourceId },
      });
    }

    watchTerminal(terminalId, onEvent, { afterSeq = 0 } = {}) {
      const url = new URL(`${this.baseUrl.replace(/^http/, "ws")}/api/ws/terminals/${encodeURIComponent(terminalId)}`);
      if (afterSeq) url.searchParams.set("after_seq", String(afterSeq));
      let ws = null;
      let closed = false;
      let cursor = afterSeq;
      let reconnectTimer = null;

      const connect = async () => {
        if (closed) return;
        try {
          const auth = await this.websocketTicket("terminal", terminalId);
          if (closed) return;
          const u = new URL(url.toString());
          u.searchParams.set("ticket", auth.ticket);
          u.searchParams.set("after_seq", String(cursor));
          ws = new WebSocket(u.toString());
        } catch (_) {
          return;
        }
        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data);
            if (data.type === "done") {
              onEvent({ kind: "done", status: data.status, exit_code: data.exit_code });
              return;
            }
            if (data.seq) cursor = data.seq;
            onEvent({ kind: "output", ...data });
          } catch (_) {
            /* ignore */
          }
        };
        ws.onerror = () => {};
        ws.onclose = () => {
          if (closed) return;
          clearTimeout(reconnectTimer);
          reconnectTimer = setTimeout(connect, 1500);
        };
      };
      connect();
      return {
        close() {
          closed = true;
          clearTimeout(reconnectTimer);
          try {
            ws && ws.close();
          } catch (_) {}
        },
      };
    }

    /**
     * Stream job events over WebSocket with cursor-based reconnect.
     * Falls back to polling on WebSocket failure.
     * @param {string} jobId
     * @param {(event: object) => void} onEvent
     * @param {{ afterEventId?: number }} options
     * @returns {{ close: () => void }}
     */
    watchJob(jobId, onEvent, options = {}) {
      const url = new URL(`${this.baseUrl.replace(/^http/, "ws")}/api/ws/jobs/${encodeURIComponent(jobId)}`);
      let afterEventId = options.afterEventId || 0;
      let ws = null;
      let closed = false;
      let finished = false;
      let pollTimer = null;
      let reconnectTimer = null;

      const stopPoll = () => {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      };

      const markDone = (payload) => {
        if (finished) return;
        finished = true;
        stopPoll();
        clearTimeout(reconnectTimer);
        onEvent(payload);
        try {
          ws && ws.close();
        } catch (_) {
          /* ignore */
        }
      };

      const startPoll = () => {
        if (finished || closed || pollTimer) return;
        let lastCount = 0;
        pollTimer = setInterval(async () => {
          if (closed || finished) return;
          try {
            const data = await this.job(jobId);
            const job = data.job;
            const events = job.events || [];
            while (lastCount < events.length) {
              const ev = events[lastCount];
              onEvent({ kind: "event", event: ev, job });
              if (ev.id) afterEventId = Math.max(afterEventId, ev.id);
              lastCount += 1;
            }
            onEvent({ kind: "job", job });
            if (
              job.status !== "queued" &&
              job.status !== "running" &&
              job.status !== "awaiting_approval" &&
              job.status !== "paused"
            ) {
              markDone({
                kind: "done",
                status: job.status,
                result: job.result || job.final_message,
                error: job.error || job.error_message,
                job,
              });
            }
          } catch (err) {
            onEvent({ kind: "error", error: err.message });
          }
        }, 200);
      };

      const connect = async () => {
        if (closed || finished) return;
        try {
          const auth = await this.websocketTicket("job", jobId);
          if (closed || finished) return;
          const u = new URL(url.toString());
          u.searchParams.set("ticket", auth.ticket);
          if (afterEventId) u.searchParams.set("after_event_id", String(afterEventId));
          ws = new WebSocket(u.toString());
        } catch (_) {
          startPoll();
          return;
        }

        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data);
            if (data.type === "done") {
              markDone({
                kind: "done",
                status: data.status,
                result: data.result,
                error: data.error,
                job: null,
              });
              return;
            }
            if (data.id) afterEventId = Math.max(afterEventId, data.id);
            onEvent({ kind: "event", event: data, job: null });
          } catch (_) {
            /* ignore malformed */
          }
        };

        ws.onerror = () => {
          /* poll covers gaps */
        };

        ws.onclose = () => {
          if (closed || finished) return;
          clearTimeout(reconnectTimer);
          reconnectTimer = setTimeout(connect, 1500);
        };
      };

      connect();
      startPoll();

      return {
        close() {
          closed = true;
          finished = true;
          stopPoll();
          clearTimeout(reconnectTimer);
          try {
            ws && ws.close();
          } catch (_) {
            /* ignore */
          }
        },
      };
    }
  }

  window.AgentApi = AgentApi;
})();
