(function (root) {
  "use strict";

  const STORAGE_KEY = "android-agent-message-outbox-v1";
  const TYPES = new Set(["steer", "follow_up"]);
  const STATES = new Set(["pending", "consumed", "follow_up_created", "unapplied", "blocked", "withdrawn", "unknown"]);
  const LABELS = { sending: "发送中…", unconfirmed: "送达结果未确认", rejected: "发送未被接受", pending: "已接收，等待处理", consumed: "已加入本轮上下文", follow_up_created: "后续任务已创建", unapplied: "本轮结束前未加入上下文", blocked: "后续任务未创建", withdrawn: "追问已撤回", unknown: "回执状态未知" };
  const REASONS = { awaiting_safe_boundary: "等待下一个安全边界", awaiting_parent_completion: "等待本轮完成", awaiting_dispatch: "等待创建后续任务", parent_paused: "前序任务已暂停", parent_failed: "前序任务失败", parent_canceled: "前序任务已取消", parent_interrupted: "前序任务中断", turn_finished_before_consumption: "本轮已结束", legacy_missing_receipt: "旧记录缺少关联回执" };
  const stamp = value => typeof value === "number" && Number.isFinite(value) && value > 0;
  const nonempty = value => typeof value === "string" && Boolean(value.trim());
  const keyFor = scope => JSON.stringify([scope.server, scope.user, scope.project, scope.conversation, scope.job]);
  const bodyText = payload => typeof payload?.text === "string" ? payload.text : typeof payload?.prompt === "string" ? payload.prompt : typeof payload?.content === "string" ? payload.content : "";
  const sameBody = (a, b) => a.type === b.type && JSON.stringify(a.payload) === JSON.stringify(b.payload);
  const settled = state => ["consumed", "follow_up_created", "unapplied", "blocked", "withdrawn"].includes(state);

  function normalize(raw, scope) {
    if (raw?.schema_version !== 1 || raw.task_id !== scope.job || !Number.isSafeInteger(raw.id) || raw.id <= 0
        || !nonempty(raw.message_key) || !TYPES.has(raw.type) || !raw.payload || typeof raw.payload !== "object"
        || !bodyText(raw.payload) || !stamp(raw.created_at)) return null;
    let state = STATES.has(raw.delivery_state) ? raw.delivery_state : "unknown";
    const hasContext = raw.context_message_id != null;
    const hasChild = raw.follow_up_job_id != null || raw.follow_up_turn_id != null;
    if (["pending", "blocked", "unapplied"].includes(state) && (raw.consumed_at != null || hasContext || hasChild)) state = "unknown";
    if (state === "blocked" && raw.type !== "follow_up" || state === "unapplied" && raw.type !== "steer") state = "unknown";
    if (state === "consumed" && (raw.type !== "steer" || !stamp(raw.consumed_at) || !nonempty(raw.context_message_id) || hasChild)) state = "unknown";
    if (state === "follow_up_created" && (raw.type !== "follow_up" || !stamp(raw.consumed_at)
        || !nonempty(raw.follow_up_job_id) || raw.follow_up_job_id === scope.job || !nonempty(raw.follow_up_turn_id) || hasContext)) state = "unknown";
    if (state === "withdrawn" && (raw.type !== "follow_up" || raw.consumed_at != null || hasContext || hasChild
        || !stamp(raw.withdrawn_at) || raw.can_withdraw !== false)) state = "unknown";
    if (state !== "withdrawn" && raw.withdrawn_at != null) state = "unknown";
    return { ...raw, delivery_state: state, phase: "received", retryable: false,
      can_withdraw: raw.can_withdraw === true && raw.type === "follow_up" && ["pending", "blocked"].includes(state),
      reason: Object.hasOwn(REASONS, raw.reason) ? raw.reason : null };
  }

  class Store {
    constructor(client, identity, { storage = null, uuid = () => root.crypto.randomUUID() } = {}) {
      this.client = client; this.identity = identity; this.storage = storage; this.uuid = uuid;
      this.records = new Map(); this.listeners = new Set(); this.requests = new Map(); this.loads = new Map(); this.watches = new Map();
      this.timer = null; this.persistenceError = false;
      try {
        const saved = JSON.parse(storage?.getItem(STORAGE_KEY) || "[]");
        for (const item of Array.isArray(saved) ? saved.slice(0, 100) : []) {
          const s = item.scope;
          if (!s || ![s.server, s.user, s.project, s.conversation, s.job].every(nonempty)
              || !nonempty(item.message_key) || !TYPES.has(item.type) || !nonempty(bodyText(item.payload))
              || bodyText(item.payload).length > 24000 || !stamp(item.created_at)) continue;
          let record;
          if (item.kind === "withdraw") {
            if (item.type !== "follow_up" || !Number.isSafeInteger(item.id) || item.id <= 0) continue;
            // Cached intent is not proof of current withdrawal eligibility.
            record = { scope: s, id: item.id, message_key: item.message_key, type: item.type,
              payload: { text: bodyText(item.payload) }, created_at: item.created_at, phase: "received", retryable: false,
              delivery_state: "unknown", can_withdraw: false, withdrawPhase: "unconfirmed" };
          } else {
            if (item.kind != null || !nonempty(item.payload?.text)) continue;
            record = { scope: s, message_key: item.message_key, type: item.type, payload: { text: item.payload.text },
              created_at: item.created_at, phase: "unconfirmed", retryable: true };
          }
          this.bucket(s).set(record.message_key, record);
        }
      } catch (_) { /* Corrupt or disabled storage never causes a resend. */ }
    }

    scope(job) {
      const who = this.identity();
      if (!who?.connected || !nonempty(who.userId) || !job?.id || !job.project_id || !job.conversation_id) return null;
      return { server: this.client.baseUrl, user: who.userId, project: String(job.project_id), conversation: String(job.conversation_id),
        job: String(job.id), session: this.client.sessionVersion };
    }
    current(scope) {
      const who = this.identity();
      return Boolean(scope && who?.connected && who.userId === scope.user && this.client.baseUrl === scope.server && this.client.sessionVersion === scope.session);
    }
    bucket(scope) {
      const key = keyFor(scope);
      if (!this.records.has(key)) this.records.set(key, new Map());
      return this.records.get(key);
    }
    list(scope) { return scope ? [...this.bucket(scope).values()].sort((a, b) => a.created_at - b.created_at || (a.id || 0) - (b.id || 0)) : []; }
    sending(scope) { return this.list(scope).some(row => row.phase === "sending"); }
    subscribe(callback) { this.listeners.add(callback); return () => this.listeners.delete(callback); }
    emit() { this.save(); for (const listener of this.listeners) listener(); }
    save() {
      const pending = [...this.records.values()].flatMap(map => [...map.values()]).filter(row => row.phase !== "received" && row.retryable || row.withdrawPhase);
      const rows = pending.map(row => ({ scope: { server: row.scope.server, user: row.scope.user, project: row.scope.project, conversation: row.scope.conversation, job: row.scope.job },
        ...(row.withdrawPhase ? { kind: "withdraw", id: row.id } : {}),
        message_key: row.message_key, type: row.type, payload: row.payload, created_at: row.created_at }));
      try { this.storage?.setItem(STORAGE_KEY, JSON.stringify(rows)); this.persistenceError = false; }
      catch (_) { this.persistenceError = true; }
    }
    create(scope, type, text) {
      if (!this.current(scope) || !TYPES.has(type) || !nonempty(text)) throw new Error("请连接并选择当前任务");
      if (text.length > 24000) throw new Error("追加消息不能超过 24000 字符");
      const pending = this.pendingCount();
      if (pending >= 100) throw new Error("待确认消息已达上限，请先核对或移除旧消息");
      const row = { scope: { ...scope }, message_key: this.uuid(), type, payload: { text }, created_at: Date.now() / 1000,
        phase: "unconfirmed", retryable: true };
      this.bucket(scope).set(row.message_key, row); this.emit(); return row;
    }
    pendingCount() { return [...this.records.values()].reduce((n, map) => n + [...map.values()].filter(row => row.retryable || row.withdrawPhase).length, 0); }
    merge(scope, raw, acknowledged = false) {
      const next = normalize(raw, scope);
      if (!next) return false;
      const prior = this.bucket(scope).get(next.message_key);
      if (prior && prior.type !== next.type) return false;
      // A GET may contain redacted text. Keep the frozen local intent until
      // its explicit original-body POST is accepted by server idempotency.
      if (prior && prior.phase !== "received" && !acknowledged && !sameBody(prior, next)) return false;
      if (prior?.phase === "received" && prior.id !== next.id) return false;
      if (prior?.phase === "received" && (settled(prior.delivery_state) && next.delivery_state !== prior.delivery_state
          && !(prior.delivery_state === "blocked" && next.delivery_state === "withdrawn")
          || prior.delivery_state !== "unknown" && next.delivery_state === "unknown")) {
        // Even a degraded receipt can revoke a previously offered capability.
        prior.can_withdraw = prior.can_withdraw && next.can_withdraw;
        return true;
      }
      const withdrawal = prior?.withdrawPhase && !["withdrawn", "follow_up_created"].includes(next.delivery_state)
        ? { withdrawPhase: prior.withdrawPhase, withdrawError: prior.withdrawError, withdrawRequestId: prior.withdrawRequestId } : {};
      this.bucket(scope).set(next.message_key, { ...next, ...withdrawal, scope: { ...scope } }); return true;
    }
    envelope(data, scope) { return data?.schema_version === 1 && data.job_id === scope.job; }
    submit(scope, messageKey) {
      if (!this.current(scope)) return Promise.resolve(false);
      const row = this.bucket(scope).get(messageKey);
      if (!row || !row.retryable) return Promise.resolve(false);
      const id = `${scope.session}:${keyFor(scope)}:${messageKey}`;
      if (this.requests.has(id)) return this.requests.get(id);
      Object.assign(row, { phase: "sending", error: null }); this.emit();
      const request = this.client.sendJobMessage(scope.job, row.type, { ...row.payload }, row.message_key).then(data => {
        if (!this.current(scope)) return false;
        if (!this.envelope(data, scope) || data.message?.message_key !== messageKey || !this.merge(scope, data.message, true)) {
          throw new Error("服务器回执无法确认，请核对后重试");
        }
        return true;
      }).catch(error => {
        if (this.current(scope) && !["received", "rejected"].includes(this.bucket(scope).get(messageKey)?.phase)) {
          const retryable = ![400, 403, 404, 409, 422].includes(error.status);
          Object.assign(row, { phase: retryable ? "unconfirmed" : "rejected", retryable, error: String(error.message || "连接中断") });
        }
        return false;
      }).finally(() => {
        this.requests.delete(id);
        if (row.phase === "sending") row.phase = "unconfirmed";
        this.emit();
      });
      this.requests.set(id, request); return request;
    }
    async retry(scope, messageKey) {
      // Reconcile first: a failed response may have hidden a successful POST.
      await this.reconcile(scope);
      if (!this.current(scope)) return false;
      return this.submit(scope, messageKey);
    }
    withdraw(scope, messageKey) {
      if (!this.current(scope)) return Promise.resolve(false);
      const row = this.bucket(scope).get(messageKey);
      const id = `withdraw:${scope.session}:${keyFor(scope)}:${row?.id}`;
      if (this.requests.has(id)) return this.requests.get(id);
      if (!row || row.phase !== "received" || !row.can_withdraw || !Number.isSafeInteger(row.id) || row.id <= 0) return Promise.resolve(false);
      if (!row.withdrawPhase && this.pendingCount() >= 100) return Promise.reject(new Error("待确认操作已达上限，请先核对旧记录"));
      Object.assign(row, { withdrawPhase: "sending", withdrawError: null, withdrawRequestId: id }); this.emit();
      const request = this.client.withdrawJobMessage(scope.job, row.id).then(data => {
        if (!this.current(scope)) return false;
        const receipt = normalize(data?.message, scope);
        if (!this.envelope(data, scope) || receipt?.id !== row.id || receipt.message_key !== messageKey
            || receipt.delivery_state !== "withdrawn" || !this.merge(scope, receipt, true)) {
          throw new Error("服务器回执无法确认撤回结果");
        }
        return true;
      }).catch(async error => {
        if (!this.current(scope)) return false;
        let current = this.bucket(scope).get(messageKey);
        if (current?.delivery_state === "withdrawn") return true;
        if (current?.id !== row.id || !current.withdrawPhase) return false;
        current.withdrawPhase = "unconfirmed";
        current.withdrawError = error.status === 409 ? "撤回未获确认，正在核对最新回执" : String(error.message || "连接中断");
        if ([400, 403, 404, 422].includes(error.status)) {
          delete current.withdrawPhase; current.can_withdraw = false; current.withdrawError = "撤回未被接受，请刷新任务后核对";
        }
        this.emit();
        // Conflict may mean a child was created. Only GET may update the UI;
        // never infer child identity from an error or cancel/resume that child.
        if (error.status === 409) await this.reconcile(scope);
        return false;
      }).finally(() => {
        this.requests.delete(id);
        const current = this.bucket(scope).get(messageKey);
        if (current?.id === row.id && current.withdrawRequestId === id) {
          if (current.withdrawPhase === "sending") current.withdrawPhase = "unconfirmed";
          delete current.withdrawRequestId;
        }
        this.emit();
      });
      this.requests.set(id, request); return request;
    }
    async retryWithdrawal(scope, messageKey) {
      if (!await this.reconcile(scope) || !this.current(scope)) return false;
      const row = this.bucket(scope).get(messageKey);
      if (row?.delivery_state === "withdrawn") return true;
      if (row?.withdrawPhase && !row.can_withdraw) {
        row.withdrawError = "服务器当前未确认可撤回，请稍后核对"; this.emit(); return false;
      }
      return this.withdraw(scope, messageKey);
    }
    remove(scope, messageKey) {
      const row = this.bucket(scope).get(messageKey);
      if (!row || row.phase === "sending" || row.phase === "received") return;
      this.bucket(scope).delete(messageKey); this.emit();
    }
    reconcile(scope) {
      if (!this.current(scope)) return Promise.resolve(false);
      const id = `${scope.session}:${keyFor(scope)}`;
      if (this.loads.has(id)) return this.loads.get(id);
      const request = this.client.jobMessages(scope.job, { includeConsumed: true }).then(data => {
        if (!this.current(scope) || !this.envelope(data, scope) || !Array.isArray(data.messages)) return false;
        for (const row of data.messages) this.merge(scope, row);
        this.emit(); return true;
      }).catch(() => false).finally(() => this.loads.delete(id));
      this.loads.set(id, request); return request;
    }
    watch(id, scope, active = false) {
      const old = this.watches.get(id);
      if (!scope) {
        this.watches.delete(id);
        if (!this.watches.size && this.timer) { clearInterval(this.timer); this.timer = null; }
        return;
      }
      const binding = `${scope.session}:${keyFor(scope)}:${active}`;
      this.watches.set(id, { scope, active, binding });
      if (old?.binding !== binding) this.reconcile(scope);
      if (!this.timer) this.timer = setInterval(() => {
        if (root.document?.visibilityState === "hidden") return;
        const scopes = new Map();
        for (const value of this.watches.values()) if (value.active || this.list(value.scope).some(row => row.retryable || row.withdrawPhase || row.delivery_state === "pending")) scopes.set(keyFor(value.scope), value.scope);
        for (const value of scopes.values()) this.reconcile(value);
      }, 2000);
    }
    async child(scope, row) {
      if (!this.current(scope) || row.delivery_state !== "follow_up_created") throw new Error("缺少后续任务回执");
      const data = await this.client.job(row.follow_up_job_id);
      const job = data?.job;
      if (!this.current(scope)) return null;
      if (!job || job.id !== row.follow_up_job_id || job.project_id !== scope.project || job.conversation_id !== scope.conversation
          || job.turn_id !== row.follow_up_turn_id) throw new Error("后续任务归属与回执不一致");
      return job;
    }
    dispose() { if (this.timer) clearInterval(this.timer); this.timer = null; this.watches.clear(); this.listeners.clear(); }
  }

  function render(host, store, scope, { openChild } = {}) {
    if (!host) return;
    const rows = store.list(scope);
    host.hidden = !rows.length;
    const signature = JSON.stringify([scope && keyFor(scope), scope?.session, store.persistenceError, rows]);
    if (host._messageSignature === signature) return;
    host._messageSignature = signature;
    const body = host.querySelector(".message-receipts-body");
    if (!body) return;
    body.replaceChildren();
    for (const row of rows) {
      const section = document.createElement("div"); section.className = "message-receipt";
      section.dataset.messageKey = row.message_key; section.dataset.state = row.phase === "received" ? row.delivery_state : row.phase;
      const text = document.createElement("p"); text.className = "message-receipt-text"; text.textContent = `${row.type === "steer" ? "引导" : "追问"} · ${bodyText(row.payload)}`;
      const status = document.createElement("small"); status.textContent = LABELS[section.dataset.state] || LABELS.unknown;
      if (REASONS[row.reason]) status.textContent += ` · ${REASONS[row.reason]}`;
      if (row.error) status.textContent += ` · ${row.error}`;
      if (row.withdrawPhase) status.textContent += row.withdrawPhase === "sending" ? " · 撤回中…" : " · 撤回结果待确认";
      if (row.withdrawError) status.textContent += ` · ${row.withdrawError}`;
      section.append(text, status);
      const action = (label, callback) => {
        const button = document.createElement("button"); button.type = "button"; button.className = "ghost-btn sm"; button.textContent = label;
        button.addEventListener("click", async () => { button.disabled = true; try { await callback(); } catch (error) { status.textContent = error.message; } finally { button.disabled = false; } });
        section.appendChild(button);
      };
      if (row.retryable && row.phase !== "sending") action("核对并重试", () => store.retry(scope, row.message_key));
      if (row.phase === "unconfirmed" || row.phase === "rejected") action("移除本地记录", () => store.remove(scope, row.message_key));
      if (row.withdrawPhase === "unconfirmed") action("核对并重试撤回", () => store.retryWithdrawal(scope, row.message_key));
      else if (row.phase === "received" && row.can_withdraw && !row.withdrawPhase) action("撤回追问", () => store.withdraw(scope, row.message_key));
      if (row.delivery_state === "follow_up_created" && openChild) action("查看后续任务", () => openChild(scope, row));
      body.appendChild(section);
    }
    if (store.persistenceError) {
      const note = document.createElement("small"); note.textContent = "本地保存不可用，未确认的消息仅保留在本次窗口。"; body.appendChild(note);
    }
  }

  const api = { Store, normalize, render, keyFor, STORAGE_KEY };
  root.JobMessages = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
