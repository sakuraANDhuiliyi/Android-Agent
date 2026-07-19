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

    /**
     * Stream job events over WebSocket; falls back to caller polling on failure.
     * @param {string} jobId
     * @param {(event: object) => void} onEvent
     * @returns {{ close: () => void }}
     */
    watchJob(jobId, onEvent) {
      const url = new URL(`${this.baseUrl.replace(/^http/, "ws")}/api/ws/jobs/${encodeURIComponent(jobId)}`);
      if (this.token) url.searchParams.set("token", this.token);

      let ws = null;
      let closed = false;
      let finished = false;
      let pollTimer = null;

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
              onEvent({ kind: "event", event: events[lastCount], job });
              lastCount += 1;
            }
            onEvent({ kind: "job", job });
            if (job.status !== "queued" && job.status !== "running") {
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
        }, 1000);
      };

      try {
        ws = new WebSocket(url.toString());
      } catch (_) {
        startPoll();
        return {
          close() {
            closed = true;
            finished = true;
            stopPoll();
          },
        };
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
          onEvent({ kind: "event", event: data, job: null });
        } catch (_) {
          /* ignore malformed */
        }
      };

      ws.onerror = () => {
        if (!closed && !finished && !pollTimer) startPoll();
      };

      ws.onclose = () => {
        if (!closed && !finished && !pollTimer) startPoll();
      };

      return {
        close() {
          closed = true;
          finished = true;
          stopPoll();
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
