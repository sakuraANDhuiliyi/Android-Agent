(function (root) {
  "use strict";

  let visibilityEpoch = 0;
  root.document?.addEventListener("visibilitychange", () => { if (root.document.visibilityState === "hidden") visibilityEpoch += 1; });
  root.addEventListener?.("blur", () => { visibilityEpoch += 1; });
  const STORAGE_KEY = "android-agent-message-outbox-v1";
  const QUEUE_STORAGE_KEY = "android-agent-message-queue-outbox-v1";
  const TYPES = new Set(["steer", "follow_up"]);
  const STATES = new Set(["pending", "consumed", "follow_up_created", "unapplied", "blocked", "withdrawn", "unknown"]);
  const LABELS = { sending: "发送中…", unconfirmed: "送达结果未确认", rejected: "发送未被接受", pending: "已接收，等待处理", consumed: "已加入本轮上下文", follow_up_created: "后续任务已创建", unapplied: "本轮结束前未加入上下文", blocked: "后续任务未创建", withdrawn: "追问已撤回", unknown: "回执状态未知" };
  const REASONS = { awaiting_safe_boundary: "等待下一个安全边界", awaiting_parent_completion: "等待本轮完成", awaiting_dispatch: "等待创建后续任务", parent_paused: "前序任务已暂停", parent_failed: "前序任务失败", parent_canceled: "前序任务已取消", parent_interrupted: "前序任务中断", turn_finished_before_consumption: "本轮已结束", legacy_missing_receipt: "旧记录缺少关联回执" };
  const BLOCKED_REASONS = new Set(["parent_failed", "parent_canceled", "parent_interrupted"]);
  const clearBlocker = row => { if (row) { row.blocking_job_id = null; row.blocking_turn_id = null; } };
  const hasBlocker = row => row?.phase === "received" && row.type === "follow_up" && row.delivery_state === "blocked"
    && BLOCKED_REASONS.has(row.reason) && nonempty(row.blocking_job_id) && nonempty(row.blocking_turn_id);
  const stamp = value => typeof value === "number" && Number.isFinite(value) && value > 0;
  const nonempty = value => typeof value === "string" && Boolean(value.trim());
  const revision = value => Number.isSafeInteger(value) && value >= 0;
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
    const hasRevision = revision(raw.revision) && (raw.revision === 0 ? raw.edited_at === null : stamp(raw.edited_at));
    const blocking = raw.type === "follow_up" && state === "blocked" && BLOCKED_REASONS.has(raw.reason)
      && hasRevision && nonempty(raw.blocking_job_id) && nonempty(raw.blocking_turn_id);
    return { ...raw, blocking_job_id: blocking ? raw.blocking_job_id : null, blocking_turn_id: blocking ? raw.blocking_turn_id : null, delivery_state: state, phase: "received", retryable: false,
      revision: hasRevision ? raw.revision : null, edited_at: hasRevision ? raw.edited_at : null,
      can_edit: raw.can_edit === true && hasRevision && raw.type === "follow_up" && state === "pending",
      can_withdraw: raw.can_withdraw === true && raw.type === "follow_up" && ["pending", "blocked"].includes(state),
      reason: Object.hasOwn(REASONS, raw.reason) ? raw.reason : null };
  }

  function normalizeEdit(raw, scope, messageId) {
    if (raw?.schema_version !== 1 || raw.task_id !== scope.job || raw.message_id !== messageId
        || !nonempty(raw.edit_key) || !revision(raw.expected_revision) || !revision(raw.revision)
        || raw.revision !== raw.expected_revision + 1 || !stamp(raw.created_at) || !nonempty(raw.payload?.text)) return null;
    return raw;
  }

  const idsValid = ids => Array.isArray(ids) && ids.length <= 10000 && ids.every(id => Number.isSafeInteger(id) && id > 0) && new Set(ids).size === ids.length;
  const queueVersion = value => typeof value === "string" && /^q1:[0-9a-f]{64}$/.test(value);
  const sameIds = (a, b) => a.length === b.length && a.every((id, index) => id === b[index]);
  function normalizeQueue(raw, messages, scope) {
    if (raw?.schema_version !== 1 || raw.task_id !== scope.job || !revision(raw.order_revision)
        || !(raw.version === null || queueVersion(raw.version)) || typeof raw.can_reorder !== "boolean"
        || !idsValid(raw.message_ids) || !idsValid(raw.pending_message_ids) || !Array.isArray(messages)) return null;
    const followUps = messages.filter(row => row?.type === "follow_up").map(row => normalize(row, scope));
    if (followUps.some(row => !row) || !idsValid(followUps.map(row => row.id))
        || followUps.length !== raw.message_ids.length || followUps.some(row => !raw.message_ids.includes(row.id))) return null;
    const pending = new Set(raw.pending_message_ids);
    if (!sameIds(raw.message_ids.filter(id => pending.has(id)), raw.pending_message_ids)) return null;
    if (raw.can_reorder && (raw.reason !== null || !queueVersion(raw.version) || pending.size < 2 || followUps.some(row => !revision(row.revision)
        || row.delivery_state === "unknown" || pending.has(row.id) !== (row.delivery_state === "pending")))) return null;
    if (raw.can_reorder) {
      let reachedPending = false;
      const byId = new Map(followUps.map(row => [row.id, row]));
      for (const id of raw.message_ids) {
        if (pending.has(id)) reachedPending = true;
        else if (reachedPending && byId.get(id).delivery_state !== "withdrawn") return null;
      }
    }
    return { ...raw, message_ids: [...raw.message_ids], pending_message_ids: [...raw.pending_message_ids] };
  }
  function normalizeReorder(raw, scope, intent) {
    if (raw?.schema_version !== 1 || raw.task_id !== scope.job || raw.reorder_key !== intent.reorder_key
        || raw.expected_version !== intent.expected_version || !revision(raw.order_revision) || raw.order_revision === 0
        || !stamp(raw.created_at) || !idsValid(raw.message_ids) || !sameIds(raw.message_ids, intent.message_ids)) return null;
    return raw;
  }

  class Store {
    constructor(client, identity, { storage = null, uuid = () => root.crypto.randomUUID() } = {}) {
      this.client = client; this.identity = identity; this.storage = storage; this.uuid = uuid;
      this.records = new Map(); this.queues = new Map(); this.listeners = new Set(); this.requests = new Map(); this.loads = new Map(); this.watches = new Map();
      this.timer = null; this.persistenceError = false; this.queuePersistenceError = false;
      try {
        const saved = JSON.parse(storage?.getItem(STORAGE_KEY) || "[]");
        for (const item of Array.isArray(saved) ? saved.slice(0, 100) : []) {
          const s = item.scope;
          if (!s || ![s.server, s.user, s.project, s.conversation, s.job].every(nonempty)
              || !nonempty(item.message_key) || !TYPES.has(item.type) || !nonempty(bodyText(item.payload))
              || bodyText(item.payload).length > 24000 || !stamp(item.created_at)) continue;
          let record;
          if (item.kind === "withdraw" || item.kind === "edit" || item.kind === "edit_draft") {
            if (item.type !== "follow_up" || !Number.isSafeInteger(item.id) || item.id <= 0) continue;
            // Cached intent is not proof of current withdrawal eligibility.
            record = { scope: s, id: item.id, message_key: item.message_key, type: item.type,
              payload: { text: bodyText(item.payload) }, created_at: item.created_at, phase: "received", retryable: false,
              delivery_state: "unknown", can_withdraw: false, can_edit: false, revision: null };
            if (item.kind === "withdraw") record.withdrawPhase = "unconfirmed";
            else if (item.kind === "edit_draft") {
              if (typeof item.draft?.text !== "string" || item.draft.text.length > 24000 || !revision(item.draft.version)
                  || !revision(item.draft.baseRevision)) continue;
              record.editor = { ...item.draft, open: true };
            } else {
              const intent = item.edit;
              if (!nonempty(intent?.edit_key) || !revision(intent.expected_revision) || !nonempty(intent.payload?.text)
                  || intent.payload.text.length > 24000) continue;
              record.editIntent = { edit_key: intent.edit_key, expected_revision: intent.expected_revision,
                payload: { text: intent.payload.text }, phase: "unconfirmed", draftVersion: 0 };
              record.editor = { text: intent.payload.text, baseRevision: intent.expected_revision, version: 0, open: true };
              if (typeof item.draft?.text === "string" && item.draft.text.length <= 24000 && revision(item.draft.version)
                  && revision(item.draft.baseRevision)) record.editor = { ...item.draft, open: item.draft.open !== false };
              if (revision(intent.draftVersion)) record.editIntent.draftVersion = intent.draftVersion;
            }
          } else {
            if (item.kind != null || !nonempty(item.payload?.text)) continue;
            record = { scope: s, message_key: item.message_key, type: item.type, payload: { text: item.payload.text },
              created_at: item.created_at, phase: "unconfirmed", retryable: true };
          }
          this.bucket(s).set(record.message_key, record);
        }
      } catch (_) { /* Corrupt or disabled storage never causes a resend. */ }
      try {
        const saved = JSON.parse(storage?.getItem(QUEUE_STORAGE_KEY) || "[]");
        for (const entry of Array.isArray(saved) ? saved.slice(0, 100) : []) {
          const scope = entry.scope, pending = entry.pending;
          if (!scope || ![scope.server, scope.user, scope.project, scope.conversation, scope.job].every(nonempty)
              || !nonempty(pending?.reorder_key) || pending.reorder_key.length > 200 || !queueVersion(pending.expected_version)
              || !idsValid(pending.message_ids) || pending.message_ids.length < 2) continue;
          this.queue(scope).pending = { reorder_key: pending.reorder_key, expected_version: pending.expected_version,
            message_ids: [...pending.message_ids], phase: "unconfirmed" };
        }
      } catch (_) { /* Cached ordering never grants capability or sends requests. */ }
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
    emit() { this.save(); this.saveQueueIntents(); for (const listener of this.listeners) listener(); }
    save() {
      const pending = [...this.records.values()].flatMap(map => [...map.values()]).filter(row => row.phase !== "received" && row.retryable || row.withdrawPhase || row.editIntent || row.editor);
      const rows = pending.map(row => ({ scope: { server: row.scope.server, user: row.scope.user, project: row.scope.project, conversation: row.scope.conversation, job: row.scope.job },
        ...(row.withdrawPhase ? { kind: "withdraw", id: row.id } : {}),
        ...(row.editor && !row.editIntent && !row.withdrawPhase ? { kind: "edit_draft", id: row.id,
          draft: { text: row.editor.text, baseRevision: row.editor.baseRevision, version: row.editor.version, open: row.editor.open } } : {}),
        ...(row.editIntent ? { kind: "edit", id: row.id, edit: { edit_key: row.editIntent.edit_key,
          expected_revision: row.editIntent.expected_revision, payload: row.editIntent.payload, draftVersion: row.editIntent.draftVersion },
          ...(row.editor ? { draft: { text: row.editor.text, baseRevision: row.editor.baseRevision, version: row.editor.version, open: row.editor.open } } : {}) } : {}),
        message_key: row.message_key, type: row.type, payload: row.payload, created_at: row.created_at }));
      try { this.storage?.setItem(STORAGE_KEY, JSON.stringify(rows)); this.persistenceError = false; }
      catch (_) { this.persistenceError = true; }
    }
    queue(scope) {
      const key = keyFor(scope);
      if (!this.queues.has(key)) this.queues.set(key, { scope: { ...scope }, session: scope.session,
        epoch: 0, viewEpoch: 0, valid: false, snapshot: null, pending: null, active: new Set(), error: null });
      const value = this.queues.get(key);
      if (value.session !== scope.session) {
        value.session = scope.session; value.epoch++; value.valid = false; value.active.clear();
        if (value.pending) value.pending.phase = "unconfirmed";
      }
      return value;
    }
    saveQueueIntents() {
      const saved = [...this.queues.values()].filter(value => value.pending).map(value => ({
        scope: { server: value.scope.server, user: value.scope.user, project: value.scope.project, conversation: value.scope.conversation, job: value.scope.job },
        pending: { reorder_key: value.pending.reorder_key, expected_version: value.pending.expected_version, message_ids: value.pending.message_ids },
      }));
      try { this.storage?.setItem(QUEUE_STORAGE_KEY, JSON.stringify(saved)); this.queuePersistenceError = false; }
      catch (_) { this.queuePersistenceError = true; }
    }
    invalidateQueue(scope) { const queue = this.queue(scope); queue.epoch++; queue.valid = false; }
    mutationStart(scope, id) { this.invalidateQueue(scope); this.queue(scope).active.add(id); }
    mutationDone(scope, id) {
      // An old session's finally cannot revoke a new session's current snapshot.
      const queue = this.queues.get(keyFor(scope)); queue?.active.delete(id);
      if (!this.current(scope)) return;
      this.invalidateQueue(scope);
      if (queue?.snapshot || queue?.pending || [...this.watches.values()].some(value => keyFor(value.scope) === keyFor(scope))) void this.reconcile(scope);
    }
    queueReady(scope) {
      if (!this.current(scope)) return false;
      const queue = this.queue(scope);
      return queue.valid && queue.visibleVersion === visibilityEpoch && root.document?.visibilityState !== "hidden" && queue.snapshot?.can_reorder === true && !queue.pending && queue.active.size === 0
        && !this.list(scope).some(row => row.type === "follow_up" && (row.retryable || row.editIntent || row.withdrawPhase));
    }
    moveQueue(scope, messageId, direction) {
      if (!this.queueReady(scope) || ![-1, 1].includes(direction)) return Promise.resolve(false);
      const queue = this.queue(scope), ids = [...queue.snapshot.pending_message_ids], index = ids.indexOf(messageId);
      if (index < 0 || index + direction < 0 || index + direction >= ids.length) return Promise.resolve(false);
      if ([...this.queues.values()].filter(value => value.pending).length >= 100) return Promise.reject(new Error("待确认排序已达上限，请先核对旧任务"));
      [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
      queue.pending = { reorder_key: this.uuid(), expected_version: queue.snapshot.version, message_ids: ids, phase: "unconfirmed" };
      return this.submitReorder(scope);
    }
    submitReorder(scope) {
      if (!this.current(scope)) return Promise.resolve(false);
      const queue = this.queue(scope), intent = queue.pending;
      if (!intent) return Promise.resolve(false);
      const id = `reorder:${scope.session}:${keyFor(scope)}:${intent.reorder_key}`;
      if (this.requests.has(id)) return this.requests.get(id);
      intent.phase = "sending"; intent.requestId = id; queue.error = null; this.emit();
      if (this.queuePersistenceError) {
        intent.phase = "unconfirmed"; delete intent.requestId; queue.error = "本地保存失败，排序尚未发送；恢复存储后可核对并重试";
        this.emit(); return Promise.resolve(false);
      }
      this.mutationStart(scope, id); this.emit();
      const visibleVersion = visibilityEpoch, viewEpoch = queue.viewEpoch;
      const request = this.client.reorderJobMessages(scope.job, { reorder_key: intent.reorder_key,
        expected_version: intent.expected_version, message_ids: [...intent.message_ids] }).then(data => {
        if (!this.current(scope)) return false;
        if (visibleVersion !== visibilityEpoch || viewEpoch !== queue.viewEpoch || root.document?.visibilityState === "hidden") return false;
        if (!this.envelope(data, scope) || !normalizeReorder(data.reorder, scope, intent)) throw new Error("排序回执无法确认，请使用原请求核对");
        if (queue.pending?.reorder_key !== intent.reorder_key) return false;
        queue.pending = null; queue.error = "该次排序已保存，正在核对当前队列";
        // The ACK is historical. Only a fresh GET may establish current order.
        return true;
      }).catch(error => {
        if (!this.current(scope) || queue.pending?.reorder_key !== intent.reorder_key
            || visibleVersion !== visibilityEpoch || viewEpoch !== queue.viewEpoch) return false;
        intent.phase = "unconfirmed";
        queue.error = "排序结果待确认，可核对并重试原排序";
        if ([400, 401, 403, 404, 409, 422].includes(error.status)) {
          queue.pending = null;
          queue.error = error.status === 409 ? "队列已变化，本次排序未保存；请核对当前顺序后重新选择" : "排序未被接受，请刷新后核对";
        }
        return false;
      }).finally(() => {
        this.requests.delete(id);
        if (queue.pending?.requestId === id) { queue.pending.phase = "unconfirmed"; delete queue.pending.requestId; }
        this.mutationDone(scope, id); this.emit();
      });
      this.requests.set(id, request); return request;
    }
    async retryReorder(scope) {
      const visibleVersion = visibilityEpoch, viewEpoch = this.queue(scope).viewEpoch;
      await this.reconcile(scope);
      if (visibleVersion !== visibilityEpoch || viewEpoch !== this.queue(scope).viewEpoch || root.document?.visibilityState === "hidden") return false;
      return this.submitReorder(scope); // Already accepted requests remain replayable after eligibility changes.
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
    pendingCount() { return [...this.records.values()].reduce((n, map) => n + [...map.values()].filter(row => row.retryable || row.withdrawPhase || row.editIntent || row.editor).length, 0); }
    merge(scope, raw, acknowledged = false) {
      const next = normalize(raw, scope);
      const prior = this.bucket(scope).get(raw?.message_key);
      if (!next) {
        if (prior?.id === raw?.id && raw?.task_id === scope.job) clearBlocker(prior);
        return false;
      }
      if (prior && prior.type !== next.type) return false;
      // A GET may contain redacted text. Keep the frozen local intent until
      // its explicit original-body POST is accepted by server idempotency.
      if (prior && prior.phase !== "received" && !acknowledged && !sameBody(prior, next)) return false;
      if (prior?.phase === "received" && prior.id !== next.id) return false;
      if (revision(prior?.revision) && !revision(next.revision)) {
        prior.can_edit = false; prior.can_withdraw = false; clearBlocker(prior); return true;
      }
      if (revision(prior?.revision) && next.revision < prior.revision) { clearBlocker(prior); return true; }
      if (prior?.phase === "received" && (settled(prior.delivery_state) && next.delivery_state !== prior.delivery_state
          && !(prior.delivery_state === "blocked" && next.delivery_state === "withdrawn")
          || prior.delivery_state !== "unknown" && next.delivery_state === "unknown")) {
        // Even a degraded receipt can revoke a previously offered capability.
        prior.can_withdraw = prior.can_withdraw && next.can_withdraw;
        prior.can_edit = prior.can_edit && next.can_edit;
        clearBlocker(prior);
        return true;
      }
      const withdrawal = prior?.withdrawPhase && !["withdrawn", "follow_up_created"].includes(next.delivery_state)
        ? { withdrawPhase: prior.withdrawPhase, withdrawError: prior.withdrawError, withdrawRequestId: prior.withdrawRequestId } : {};
      this.bucket(scope).set(next.message_key, { ...next, ...withdrawal,
        ...(prior?.editor ? { editor: prior.editor } : {}), ...(prior?.editIntent ? { editIntent: prior.editIntent } : {}),
        ...(prior?.editError ? { editError: prior.editError } : {}), scope: { ...scope } }); return true;
    }
    envelope(data, scope) { return data?.schema_version === 1 && data.job_id === scope.job; }
    submit(scope, messageKey) {
      if (!this.current(scope)) return Promise.resolve(false);
      const row = this.bucket(scope).get(messageKey);
      if (!row || !row.retryable) return Promise.resolve(false);
      const id = `${scope.session}:${keyFor(scope)}:${messageKey}`;
      if (this.requests.has(id)) return this.requests.get(id);
      this.mutationStart(scope, id);
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
        this.mutationDone(scope, id); this.emit();
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
      if (!row || row.phase !== "received" || !row.can_withdraw || row.editIntent || row.editor || !Number.isSafeInteger(row.id) || row.id <= 0) return Promise.resolve(false);
      if (!row.withdrawPhase && this.pendingCount() >= 100) return Promise.reject(new Error("待确认操作已达上限，请先核对旧记录"));
      this.mutationStart(scope, id);
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
        this.mutationDone(scope, id); this.emit();
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
    openEditor(scope, messageKey) {
      const row = this.bucket(scope).get(messageKey);
      if (!this.current(scope) || !row || (!row.editor && !row.can_edit) || row.withdrawPhase) return;
      if (!row.editor && this.pendingCount() >= 100) throw new Error("本地草稿已达上限，请先核对或取消旧草稿");
      row.editor ||= { text: bodyText(row.payload), baseRevision: row.revision, version: 0 };
      row.editor.open = true; this.emit();
    }
    updateEditor(scope, messageKey, text) {
      const row = this.bucket(scope).get(messageKey);
      if (!this.current(scope) || !row?.editor || text.length > 24000) return;
      row.editor.text = text; row.editor.version++;
      this.save();
    }
    closeEditor(scope, messageKey) {
      const row = this.bucket(scope).get(messageKey);
      if (!this.current(scope) || !row?.editor) return;
      if (row.editIntent) row.editor.open = false; else delete row.editor;
      this.emit();
    }
    rebaseEditor(scope, messageKey) {
      const row = this.bucket(scope).get(messageKey);
      if (!this.current(scope) || !row?.editor || !row.can_edit || row.editIntent) return;
      row.editor.baseRevision = row.revision; row.editor.version++; row.editError = null; this.emit();
    }
    saveEdit(scope, messageKey) {
      const row = this.bucket(scope).get(messageKey);
      if (!this.current(scope) || !row?.can_edit || !row.editor?.open || row.withdrawPhase || row.editIntent) return Promise.resolve(false);
      if (!nonempty(row.editor.text) || row.editor.text.length > 24000) return Promise.reject(new Error("请输入 1–24000 字符的追问正文"));
      if (row.editor.baseRevision !== row.revision) return Promise.reject(new Error("此消息已更新，请核对当前正文后继续编辑"));
      row.editIntent = { edit_key: this.uuid(), expected_revision: row.editor.baseRevision,
        payload: { text: row.editor.text }, draftVersion: row.editor.version, phase: "unconfirmed" };
      row.editError = null;
      return this.submitEdit(scope, messageKey);
    }
    submitEdit(scope, messageKey) {
      const row = this.bucket(scope).get(messageKey), intent = row?.editIntent;
      if (!this.current(scope) || !intent) return Promise.resolve(false);
      const id = `edit:${scope.session}:${keyFor(scope)}:${row.id}:${intent.edit_key}`;
      if (this.requests.has(id)) return this.requests.get(id);
      intent.phase = "sending"; intent.requestId = id; row.editError = null; this.emit();
      if (this.persistenceError) {
        intent.phase = "unconfirmed"; row.editError = "本地保存失败，编辑尚未发送；恢复存储后可核对并重试";
        delete intent.requestId; this.emit(); return Promise.resolve(false);
      }
      this.mutationStart(scope, id); this.emit();
      const request = this.client.editJobMessage(scope.job, row.id, { edit_key: intent.edit_key,
        expected_revision: intent.expected_revision, payload: { ...intent.payload } }).then(data => {
        if (!this.current(scope)) return false;
        const edit = normalizeEdit(data?.edit, scope, row.id), message = normalize(data?.message, scope);
        if (!this.envelope(data, scope) || !edit || edit.edit_key !== intent.edit_key || edit.expected_revision !== intent.expected_revision
            || message?.id !== row.id || message.message_key !== messageKey || message.type !== "follow_up"
            || !revision(message.revision) || message.revision < edit.revision || !this.merge(scope, message, true)) {
          throw new Error("服务器回执无法确认编辑结果，请使用原请求核对");
        }
        const current = this.bucket(scope).get(messageKey);
        if (current?.editIntent?.edit_key !== intent.edit_key) return false;
        delete current.editIntent; current.editError = null;
        if (current.editor?.version === intent.draftVersion) delete current.editor;
        else if (current.editor) current.editor.baseRevision = edit.revision;
        return true;
      }).catch(async error => {
        if (!this.current(scope)) return false;
        const current = this.bucket(scope).get(messageKey);
        if (current?.editIntent?.edit_key !== intent.edit_key) return false;
        intent.phase = "unconfirmed";
        current.editError = String(error.message || "连接中断");
        if ([400, 403, 404, 409, 422].includes(error.status)) {
          delete current.editIntent; current.can_edit = false;
          current.editError = error.status === 409 ? "保存冲突，草稿已保留，请核对当前正文" : "编辑未被接受，草稿已保留";
          if (error.status === 409) await this.reconcile(scope);
        }
        return false;
      }).finally(() => {
        this.requests.delete(id);
        const current = this.bucket(scope).get(messageKey);
        if (current?.editIntent?.requestId === id) {
          current.editIntent.phase = "unconfirmed"; delete current.editIntent.requestId;
        }
        this.mutationDone(scope, id); this.emit();
      });
      this.requests.set(id, request); return request;
    }
    async retryEdit(scope, messageKey) {
      await this.reconcile(scope);
      // A successful earlier edit may now be followed by another revision,
      // withdrawal or child creation. Its immutable ACK remains retrievable.
      return this.submitEdit(scope, messageKey);
    }
    remove(scope, messageKey) {
      const row = this.bucket(scope).get(messageKey);
      if (!row || row.phase === "sending" || row.phase === "received") return;
      this.bucket(scope).delete(messageKey); this.emit();
    }
    reconcile(scope) {
      if (!this.current(scope)) return Promise.resolve(false);
      const queue = this.queue(scope), epoch = queue.epoch, visibleVersion = visibilityEpoch;
      const id = `${scope.session}:${keyFor(scope)}:${epoch}`;
      if (this.loads.has(id)) return this.loads.get(id);
      const request = this.client.jobMessages(scope.job, { includeConsumed: true }).then(data => {
        if (!this.current(scope)) return false;
        // Preserve unchanged row snapshots still captured by DOM handlers.
        // Missing/unsupported rows revoke navigation; valid rows merge normally.
        if (!this.envelope(data, scope) || !Array.isArray(data.messages)) {
          for (const row of this.list(scope)) clearBlocker(row);
          if (queue.epoch === epoch) queue.valid = false;
          this.emit(); return false;
        }
        const seen = new Set();
        for (const row of data.messages) if (this.merge(scope, row)) seen.add(row.message_key);
        for (const row of this.list(scope)) if (!seen.has(row.message_key)) clearBlocker(row);
        if (queue.epoch === epoch && visibleVersion === visibilityEpoch && root.document?.visibilityState !== "hidden") {
          const next = normalizeQueue(data.queue, data.messages, scope);
          const follows = data.messages.filter(row => row.type === "follow_up");
          const consistent = next && follows.every(raw => {
            const known = this.bucket(scope).get(raw.message_key);
            return known && ["id", "type", "revision", "delivery_state", "consumed_at", "withdrawn_at", "follow_up_job_id", "follow_up_turn_id"]
              .every(key => (known[key] ?? null) === (raw[key] ?? null));
          }) && this.list(scope).filter(row => row.type === "follow_up" && row.id && row.phase === "received")
            .every(row => next.message_ids.includes(row.id));
          queue.valid = Boolean(consistent && (!queue.snapshot || next.order_revision >= queue.snapshot.order_revision));
          if (queue.valid) { queue.snapshot = next; queue.visibleVersion = visibleVersion;
            if (queue.error === "该次排序已保存，正在核对当前队列") queue.error = "该次排序已保存";
          }
        }
        this.emit(); return true;
      }).catch(() => false).finally(() => this.loads.delete(id));
      this.loads.set(id, request); return request;
    }
    watch(id, scope, active = false) {
      const old = this.watches.get(id);
      if (!scope) {
        if (old?.scope && this.current(old.scope)) { this.invalidateQueue(old.scope); this.queue(old.scope).viewEpoch++; }
        this.watches.delete(id);
        if (!this.watches.size && this.timer) { clearInterval(this.timer); this.timer = null; }
        return;
      }
      const changedScope = !old || old.scope.session !== scope.session || keyFor(old.scope) !== keyFor(scope);
      if (changedScope) {
        if (old?.scope && this.current(old.scope)) { this.invalidateQueue(old.scope); this.queue(old.scope).viewEpoch++; }
        this.queue(scope).viewEpoch++;
      }
      const binding = `${scope.session}:${keyFor(scope)}:${active}`;
      this.watches.set(id, { scope, active, binding });
      if (old?.binding !== binding) { this.invalidateQueue(scope); this.reconcile(scope); }
      if (!this.timer) this.timer = setInterval(() => {
        if (root.document?.visibilityState === "hidden") return;
        const scopes = new Map();
        for (const value of this.watches.values()) if (value.active || this.queue(value.scope).pending || this.list(value.scope).some(row => row.retryable || row.withdrawPhase || row.editIntent || row.delivery_state === "pending")) scopes.set(keyFor(value.scope), value.scope);
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
    blocker(scope, row) {
      const targetId = row?.blocking_job_id, targetTurn = row?.blocking_turn_id, visibleVersion = visibilityEpoch;
      const matches = () => {
        const current = this.bucket(scope).get(row?.message_key);
        return this.current(scope) && visibleVersion === visibilityEpoch && root.document?.visibilityState !== "hidden"
          && hasBlocker(current) && current.id === row?.id
          && current.blocking_job_id === targetId && current.blocking_turn_id === targetTurn;
      };
      if (!scope || !hasBlocker(row) || !matches()) return Promise.reject(new Error("缺少有效阻塞任务回执，请刷新后核对"));
      const requestId = `blocker:${scope.session}:${keyFor(scope)}:${row.id}:${targetId}:${targetTurn}`;
      if (this.requests.has(requestId)) return this.requests.get(requestId);
      const request = this.client.job(targetId).then(data => {
        if (!matches()) return null;
        const job = data?.job;
        if (!job || job.id !== targetId || job.project_id !== scope.project || job.conversation_id !== scope.conversation
            || job.turn_id !== targetTurn) throw new Error("阻塞任务归属与回执不一致");
        return job;
      }).finally(() => this.requests.delete(requestId));
      this.requests.set(requestId, request); return request;
    }
    dispose() { if (this.timer) clearInterval(this.timer); this.timer = null; this.watches.clear(); this.listeners.clear(); }
  }

  const blockerDialogs = new Map();
  function showBlocker(host, store, scope, row, job, isSelected = () => true) {
    blockerDialogs.get(host)?.close();
    const targetId = job.id, targetTurn = job.turn_id;
    const valid = () => {
      const current = store.list(scope).find(item => item.id === row.id && item.message_key === row.message_key);
      return store.current(scope) && isSelected() && host.isConnected && host.getClientRects().length > 0
        && document.visibilityState !== "hidden" && hasBlocker(current)
        && current.blocking_job_id === targetId && current.blocking_turn_id === targetTurn;
    };
    if (!valid()) return;
    const dialog = document.createElement("dialog"); dialog.className = "message-blocker-dialog";
    dialog.setAttribute("aria-label", "阻塞任务详情");
    const add = (tag, text, cls) => { const element = document.createElement(tag); element.textContent = text; if (cls) element.className = cls; dialog.appendChild(element); return element; };
    add("h3", "阻塞任务详情");
    add("p", `任务 ${job.id} · 轮次 ${job.turn_id}`, "message-blocker-identity");
    const statuses = root.DeliveryUI?.labels || {}, statusKey = job.display_status || job.status;
    const status = Object.hasOwn(statuses, statusKey) ? statuses[statusKey] : "状态未提供";
    add("p", `任务状态：${status}`, "message-blocker-status");
    add("h4", "任务请求"); add("pre", nonempty(job.prompt) ? job.prompt : "未提供任务请求", "message-blocker-prompt");
    add("h4", "错误信息"); add("pre", nonempty(job.error_message) ? job.error_message : "未提供错误信息", "message-blocker-error");
    if (root.DeliveryUI?.render) dialog.appendChild(root.DeliveryUI.render(job));
    const closeButton = add("button", "关闭", "ghost-btn"); closeButton.type = "button";
    let unsubscribe, observer, closed = false;
    const close = () => {
      if (closed) return; closed = true;
      unsubscribe?.(); observer?.disconnect(); document.removeEventListener("visibilitychange", check);
      root.removeEventListener?.("blur", close); blockerDialogs.delete(host);
      if (dialog.open) dialog.close(); dialog.remove();
    };
    const check = () => { if (!valid()) close(); };
    blockerDialogs.set(host, { close, check });
    closeButton.addEventListener("click", close); dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    dialog.addEventListener("close", close, { once: true });
    document.body.appendChild(dialog); dialog.showModal();
    unsubscribe = store.subscribe(check); document.addEventListener("visibilitychange", check); root.addEventListener?.("blur", close);
    observer = new MutationObserver(check);
    for (let parent = host; parent; parent = parent.parentElement) observer.observe(parent, { attributes: true, attributeFilter: ["hidden", "class", "style"] });
  }

  function render(host, store, scope, { openChild, openBlocker } = {}) {
    for (const dialog of blockerDialogs.values()) dialog.check();
    if (!host) return;
    const records = store.list(scope), queue = scope ? store.queue(scope) : null;
    const ready = Boolean(scope && store.queueReady(scope));
    const followUps = new Map(records.filter(row => row.type === "follow_up" && row.id).map(row => [row.id, row]));
    const ordered = (queue?.snapshot?.message_ids || []).map(id => followUps.get(id)).filter(Boolean);
    const known = new Set(ordered.map(row => row.message_key));
    const rows = [...records.filter(row => row.type === "steer"), ...ordered,
      ...records.filter(row => row.type === "follow_up" && !known.has(row.message_key))];
    host.hidden = !rows.length && !queue?.pending;
    const signature = JSON.stringify([scope && keyFor(scope), scope?.session, store.persistenceError,
      queue && [queue.valid, queue.snapshot, queue.pending, queue.error, queue.active.size, ready], rows]);
    if (host._messageSignature === signature) return;
    host._messageSignature = signature;
    const body = host.querySelector(".message-receipts-body");
    if (!body) return;
    const focusedAction = body.contains(document.activeElement) && document.activeElement.tagName === "BUTTON"
      ? { key: document.activeElement.closest(".message-receipt")?.dataset.messageKey, text: document.activeElement.textContent } : null;
    const focused = body.contains(document.activeElement) && document.activeElement.classList.contains("message-edit-text")
      ? { key: document.activeElement.closest(".message-receipt").dataset.messageKey,
        start: document.activeElement.selectionStart, end: document.activeElement.selectionEnd } : null;
    body.replaceChildren();
    if (queue?.snapshot || queue?.pending) {
      const note = document.createElement("p"); note.className = "message-queue-status"; note.setAttribute("role", "status");
      const reasons = { not_enough_pending: "至少两条待执行追问才能调整顺序", parent_failed: "前序任务失败，暂不可排序",
        parent_canceled: "前序任务已取消，暂不可排序", parent_interrupted: "前序任务中断，暂不可排序",
        legacy_missing_receipt: "旧记录缺少关联证据", unknown_status: "任务状态未知", invalid_queue: "队列无法确认" };
      note.textContent = queue.pending ? (queue.pending.phase === "sending" ? "正在保存队列顺序…" : "排序结果待确认")
        : queue.valid ? (reasons[queue.snapshot.reason] || "追问按服务端队列顺序展示") : "队列顺序待核对，暂不可调整";
      if (queue.error) note.textContent += ` · ${queue.error}`;
      body.appendChild(note);
      if (queue.pending?.phase === "unconfirmed") {
        const retry = document.createElement("button"); retry.type = "button"; retry.className = "ghost-btn sm";
        retry.textContent = "核对并重试排序";
        retry.addEventListener("click", async () => { retry.disabled = true; try { await store.retryReorder(scope); }
          catch (error) { note.textContent = error.message; } finally { retry.disabled = false; } });
        body.appendChild(retry);
      }
    }
    let group;
    for (const row of rows) {
      const nextGroup = row.type === "steer" ? "引导记录" : known.has(row.message_key) ? "服务端追问队列" : "未确认队位的追问";
      if (group !== nextGroup) {
        group = nextGroup; const label = document.createElement("h4"); label.className = "message-queue-group";
        label.textContent = group; body.appendChild(label);
      }
      const section = document.createElement("div"); section.className = "message-receipt";
      section.dataset.messageId = row.id || "";
      section.dataset.messageKey = row.message_key; section.dataset.state = row.phase === "received" ? row.delivery_state : row.phase;
      const text = document.createElement("p"); text.className = "message-receipt-text"; text.textContent = `${row.type === "steer" ? "引导" : "追问"} · ${bodyText(row.payload)}`;
      const status = document.createElement("small"); status.textContent = LABELS[section.dataset.state] || LABELS.unknown;
      if (REASONS[row.reason]) status.textContent += ` · ${REASONS[row.reason]}`;
      if (row.error) status.textContent += ` · ${row.error}`;
      if (row.withdrawPhase) status.textContent += row.withdrawPhase === "sending" ? " · 撤回中…" : " · 撤回结果待确认";
      if (row.withdrawError) status.textContent += ` · ${row.withdrawError}`;
      if (row.edited_at) status.textContent += " · 已编辑";
      if (row.editIntent) status.textContent += row.editIntent.phase === "sending" ? " · 保存中…" : " · 编辑结果待确认";
      if (row.editError) status.textContent += ` · ${row.editError}`;
      section.append(text, status);
      const action = (label, callback) => {
        const button = document.createElement("button"); button.type = "button"; button.className = "ghost-btn sm"; button.textContent = label;
        button.addEventListener("click", async () => { button.disabled = true; try { await callback(); } catch (error) { status.textContent = error.message; } finally { button.disabled = false; } });
        section.appendChild(button);
        return button;
      };
      const pendingIndex = queue?.snapshot?.pending_message_ids.indexOf(row.id) ?? -1;
      if (pendingIndex >= 0) {
        section.dataset.queuePosition = String(pendingIndex + 1);
        const up = action("上移追问", () => store.moveQueue(scope, row.id, -1));
        const down = action("下移追问", () => store.moveQueue(scope, row.id, 1));
        up.disabled = !ready || pendingIndex === 0;
        down.disabled = !ready || pendingIndex === queue.snapshot.pending_message_ids.length - 1;
      }
      if (row.retryable && row.phase !== "sending") action("核对并重试", () => store.retry(scope, row.message_key));
      if (row.phase === "unconfirmed" || row.phase === "rejected") action("移除本地记录", () => store.remove(scope, row.message_key));
      if (row.withdrawPhase === "unconfirmed") action("核对并重试撤回", () => store.retryWithdrawal(scope, row.message_key));
      else if (row.phase === "received" && row.can_withdraw && !row.withdrawPhase && !row.editIntent && !row.editor) action("撤回追问", () => store.withdraw(scope, row.message_key));
      if (row.editIntent?.phase === "unconfirmed") action("核对并重试编辑", () => store.retryEdit(scope, row.message_key));
      if (row.editor && !row.editor.open) action("展开编辑草稿", () => store.openEditor(scope, row.message_key));
      else if (row.can_edit && !row.editor && !row.withdrawPhase && !row.editIntent) action("编辑追问", () => store.openEditor(scope, row.message_key));
      if (row.editor?.open) {
        const note = document.createElement("small"); note.textContent = "仅保存成功后生效，队列位置不变。"; section.appendChild(note);
        const field = document.createElement("textarea"); field.className = "message-edit-text"; field.setAttribute("aria-label", "编辑追问正文");
        field.maxLength = 24000; field.rows = 3; field.value = row.editor.text; section.appendChild(field);
        const save = action("保存修改", () => store.saveEdit(scope, row.message_key));
        const disableSave = () => { save.disabled = !row.can_edit || Boolean(row.editIntent || row.withdrawPhase)
          || row.editor.baseRevision !== row.revision || !nonempty(field.value); };
        disableSave();
        field.addEventListener("input", () => { store.updateEditor(scope, row.message_key, field.value); disableSave(); });
        if (row.can_edit && row.editor.baseRevision !== row.revision && !row.editIntent) action("以最新版本继续编辑", () => store.rebaseEditor(scope, row.message_key));
        action(row.editIntent ? "收起编辑" : "取消编辑", () => store.closeEditor(scope, row.message_key));
      }
      if (hasBlocker(row) && openBlocker) action("查看阻塞任务", () => openBlocker(scope, row));
      if (row.delivery_state === "follow_up_created" && openChild) action("查看后续任务", () => openChild(scope, row));
      body.appendChild(section);
      if (focusedAction?.key === row.message_key) {
        const button = [...section.querySelectorAll("button")].find(item => item.textContent === focusedAction.text && !item.disabled);
        button?.focus({ preventScroll: true });
      }
      if (focused?.key === row.message_key) {
        const field = section.querySelector(".message-edit-text");
        if (field) { field.focus({ preventScroll: true }); field.setSelectionRange(focused.start, focused.end); }
      }
    }
    if (store.persistenceError) {
      const note = document.createElement("small"); note.textContent = "本地保存不可用，未确认的消息仅保留在本次窗口。"; body.appendChild(note);
    }
  }

  const api = { Store, normalize, normalizeEdit, normalizeQueue, normalizeReorder, render, showBlocker, keyFor, STORAGE_KEY, QUEUE_STORAGE_KEY };
  root.JobMessages = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
