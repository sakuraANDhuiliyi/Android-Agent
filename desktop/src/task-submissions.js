(function (root) {
  "use strict";
  const STORAGE_KEY = "android-agent-task-submissions-v1";
  const MAX_BYTES = 2 * 1024 * 1024;
  const text = value => typeof value === "string" && Boolean(value.trim());
  const requestKey = value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/.test(value);
  const positive = value => typeof value === "number" && Number.isFinite(value) && value > 0;
  const scopeKey = scope => JSON.stringify([scope.server, scope.user, scope.project, scope.conversation]);
  const plainScope = scope => ({ server: scope.server, user: scope.user, project: scope.project, conversation: scope.conversation });
  function server(value) {
    try { const url = new URL(value); if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
      return url.href.replace(/\/+$/, ''); } catch (_) { return null; }
  }
  const validScope = scope => scope && server(scope.server) === scope.server && [scope.user, scope.project, scope.conversation].every(text);
  function freezeBody(value) {
    const body = JSON.parse(JSON.stringify(value));
    if (!body || Array.isArray(body) || !text(body.prompt) || body.prompt.length > 100000
        || Object.keys(body).some(key => !['prompt', 'provider', 'auto_fallback', 'run_mode', 'feedback_requested', 'contexts'].includes(key))
        || ('provider' in body && body.provider !== null && typeof body.provider !== 'string')
        || ('run_mode' in body && body.run_mode !== null && !['workspace', 'read_only', 'ask'].includes(body.run_mode))
        || ['auto_fallback', 'feedback_requested'].some(key => key in body && typeof body[key] !== 'boolean')
        || ('contexts' in body && (!Array.isArray(body.contexts) || body.contexts.length > 20))) throw new Error("任务请求格式无效");
    const freeze = object => { if (object && typeof object === 'object') { Object.values(object).forEach(freeze); Object.freeze(object); } };
    freeze(body); return body;
  }
  function normalize(data, scope, key, { lookup = false } = {}) {
    const ack = data?.submission, job = data?.job;
    if (data?.schema_version !== 1 || ack?.schema_version !== 1 || !requestKey(key) || ack.request_key !== key
        || ack.project_id !== scope.project || ack.conversation_id !== scope.conversation
        || !text(ack.job_id) || !text(ack.turn_id) || !positive(ack.created_at)
        || ((!lookup || Object.prototype.hasOwnProperty.call(data, "conversation_id")) && data.conversation_id !== scope.conversation)
        || job?.id !== ack.job_id || job.project_id !== ack.project_id || job.conversation_id !== ack.conversation_id || job.turn_id !== ack.turn_id) return null;
    return { submission: ack, job };
  }

  class Store {
    constructor(client, identity, { storage = null, uuid = () => root.crypto.randomUUID() } = {}) {
      this.client = client; this.identity = identity; this.storage = storage; this.uuid = uuid;
      this.adoptions = new Map(); this.records = new Map(); this.holds = new Map(); this.states = new Map(); this.operations = new Map();
      this.lookupEpochs = new Map(); this.preparing = new Set(); this.listeners = new Set(); this.bindings = new Map(); this.loads = new Map();
      this.ready = false; this.storageError = null; this.visibilityEpoch = 0; this.foreground = true;
      this.restore();
      this.blur = () => { this.visibilityEpoch++; this.foreground = false; this.emit(); };
      this.focus = () => { this.foreground = root.document?.visibilityState !== 'hidden'; this.emit(); };
      this.visibility = () => root.document?.visibilityState === 'hidden' ? this.blur() : this.focus();
      root.addEventListener?.('blur', this.blur); root.addEventListener?.('focus', this.focus);
      root.document?.addEventListener('visibilitychange', this.visibility);
    }
    restore() {
      try {
        if (!this.storage || typeof this.storage.getItem !== "function" || typeof this.storage.setItem !== "function") throw new Error("Storage unavailable");
        const raw = this.storage.getItem(STORAGE_KEY);
        if (raw && new TextEncoder().encode(raw).length > MAX_BYTES) throw new Error('overflow');
        const data = raw == null ? { schema_version: 1, pending: [], manual_attach_scopes: [] } : JSON.parse(raw);
        if (data.schema_version !== 1 || !Array.isArray(data.pending) || data.pending.length > 10 || !Array.isArray(data.manual_attach_scopes)) throw new Error('invalid cache');
        const records = new Map(), holds = new Map();
        for (const item of data.pending) {
          if (!validScope(item.scope) || !requestKey(item.request_key) || !positive(item.created_at) || records.has(scopeKey(item.scope))) throw new Error('invalid intent');
          records.set(scopeKey(item.scope), { ...item, scope: plainScope(item.scope), body: freezeBody(item.body) });
        }
        for (const scope of data.manual_attach_scopes) { if (!validScope(scope)) throw new Error('invalid hold'); holds.set(scopeKey(scope), plainScope(scope)); }
        this.records = records; this.holds = holds; this.ready = true; this.storageError = null; this.bindings.clear();
      } catch (_) { this.ready = false; this.storageError = '无法读取本机待确认提交，已暂停新任务提交与自动连接；原记录未改动。'; }
      this.emit(); return this.ready;
    }
    commit(records, holds) {
      if (!this.ready) throw new Error(this.storageError);
      if (records.size > 10) throw new Error('待确认提交已达 10 条，请先核对旧提交');
      const value = JSON.stringify({ schema_version: 1, pending: [...records.values()], manual_attach_scopes: [...holds.values()] });
      if (new TextEncoder().encode(value).length > MAX_BYTES) throw new Error('本机提交记录达到容量上限，请先核对旧提交');
      try { this.storage.setItem(STORAGE_KEY, value); }
      catch (_) { throw new Error('本机保存失败，任务尚未确认；请恢复存储后重试原请求'); }
      this.records = records; this.holds = holds;
    }
    scope(project, conversation) {
      const identity = this.identity(), base = server(this.client.baseUrl);
      return identity?.connected && text(identity.userId) && base && text(project) && text(conversation)
        ? { server: base, user: identity.userId, project, conversation, session: this.client.sessionVersion } : null;
    }
    current(scope) { const who = this.identity(); return Boolean(scope && who?.connected && who.userId === scope.user
      && server(this.client.baseUrl) === scope.server && this.client.sessionVersion === scope.session); }
    pending(scope) { return scope ? this.records.get(scopeKey(scope)) : null; }
    state(scope) { return scope ? this.states.get(scopeKey(scope)) || {} : {}; }
    busy(scope) { const operation = scope && this.operations.get(scopeKey(scope)); return Boolean(operation && operation.session === scope.session); }
    preparingKey(project) { return JSON.stringify([server(this.client.baseUrl), this.identity()?.userId, project]); }
    blocked(project, conversation) {
      const scope = this.scope(project, conversation);
      return !this.ready || this.preparing.has(this.preparingKey(project)) || Boolean(scope && (this.pending(scope) || this.busy(scope)));
    }
    canAutoAttach(scope) { return this.current(scope) && this.ready && !this.pending(scope) && !this.holds.has(scopeKey(scope)) && !this.adoptions.has(scopeKey(scope)) && !this.preparing.has(this.preparingKey(scope.project)); }
    adopted(scope, jobId) { if (scope && this.current(scope) && this.adoptions.get(scopeKey(scope)) === jobId) this.adoptions.delete(scopeKey(scope)); }
    subscribe(callback) { this.listeners.add(callback); return () => this.listeners.delete(callback); }
    emit() { for (const callback of this.listeners) callback(); }
    live(scope, guard, epoch) { return this.current(scope) && this.foreground && this.visibilityEpoch === epoch && guard(); }
    submitInitial(snapshot, { isCurrent = () => true, onConversationResolved } = {}) {
      const body = freezeBody(snapshot.body), project = snapshot.projectId, initialConversation = snapshot.conversationId;
      const who = this.identity(), base = server(this.client.baseUrl), session = this.client.sessionVersion, epoch = this.visibilityEpoch;
      if (!who?.connected || !text(who.userId) || !base || !text(project) || !this.foreground) return Promise.reject(new Error('请连接并选择项目'));
      if (this.blocked(project, initialConversation)) return Promise.reject(new Error(this.storageError || '请先核对该会话的待确认提交'));
      const owner = who.userId, preparingKey = this.preparingKey(project);
      this.preparing.add(preparingKey); this.emit();
      const current = () => this.identity()?.userId === owner && this.identity()?.connected && server(this.client.baseUrl) === base
        && this.client.sessionVersion === session && this.foreground && this.visibilityEpoch === epoch && isCurrent();
      return (async () => {
        try {
          let conversation = initialConversation;
          if (!conversation) {
            const created = await this.client.createConversation(project, '新对话');
            if (!current()) return null;
            if (!text(created?.id) || created.project_id !== project) throw new Error('无法确认新会话归属，任务尚未提交');
            conversation = created.id; onConversationResolved?.(created);
          }
          if (!current()) return null;
          const scope = this.scope(project, conversation);
          if (this.pending(scope)) throw new Error('该会话已有待确认提交');
          const pending = { scope: plainScope(scope), request_key: this.uuid(), body, created_at: Date.now() / 1000 };
          if (!requestKey(pending.request_key)) throw new Error('提交标识无效');
          const records = new Map(this.records); records.set(scopeKey(scope), pending); this.commit(records, this.holds); this.emit();
          return await this.post(scope, isCurrent);
        } finally { this.preparing.delete(preparingKey); this.emit(); }
      })();
    }
    post(scope, guard = () => true) {
      if (!this.current(scope) || !this.ready || !this.foreground) return Promise.resolve(null);
      const key = scopeKey(scope), pending = this.pending(scope);
      if (!pending) return Promise.resolve(null);
      const existing = this.operations.get(key);
      if (existing?.session === scope.session) return existing.promise;
      const epoch = this.visibilityEpoch;
      try { this.commit(this.records, this.holds); } catch (error) { this.states.set(key, { phase: 'unconfirmed', error: error.message }); this.emit(); return Promise.resolve(null); }
      const operation = { session: scope.session };
      this.operations.set(key, operation); this.states.set(key, { phase: 'sending' }); this.emit();
      operation.promise = Promise.resolve().then(() => {
        if (!this.live(scope, guard, epoch) || this.pending(scope)?.request_key !== pending.request_key) return null;
        return this.client.askConversation(scope.conversation, { ...pending.body, request_key: pending.request_key });
      }).then(data => {
        if (!this.live(scope, guard, epoch) || this.pending(scope)?.request_key !== pending.request_key) return null;
        const result = normalize(data, scope, pending.request_key);
        if (!result) throw new Error('提交回执不完整，请使用原请求核对');
        const records = new Map(this.records), holds = new Map(this.holds); records.delete(key); holds.delete(key);
        this.commit(records, holds); this.adoptions.set(key, result.job.id); this.states.delete(key); return result;
      }).catch(error => {
        if (!this.live(scope, guard, epoch) || this.pending(scope)?.request_key !== pending.request_key) return null;
        const rejected = [400, 403, 404, 409, 413, 422].includes(error.status);
        this.states.set(key, { phase: rejected ? 'rejected' : 'unconfirmed', error: error.status === 401
          ? '登录已过期，重新登录同一账号后可核对原提交' : String(error.message || '连接中断') });
        return null;
      }).finally(() => {
        if (this.operations.get(key) === operation) {
          this.operations.delete(key);
          if (this.states.get(key)?.phase === 'sending') this.states.set(key, { phase: 'unconfirmed' });
        }
        this.emit();
      });
      return operation.promise;
    }
    lookup(scope, guard = () => true) {
      if (!this.current(scope) || !this.ready || !this.foreground || !this.pending(scope)) return Promise.resolve(null);
      const key = scopeKey(scope), pending = this.pending(scope), epoch = this.visibilityEpoch;
      const generation = (this.lookupEpochs.get(key) || 0) + 1; this.lookupEpochs.set(key, generation);
      const loadKey = JSON.stringify([key, scope.session, epoch, generation]);
      const live = () => this.lookupEpochs.get(key) === generation && this.live(scope, guard, epoch);
      const request = this.client.lookupTaskSubmission(scope.conversation, pending.request_key).then(data => {
        if (!live() || this.pending(scope)?.request_key !== pending.request_key) return null;
        const candidate = normalize(data, scope, pending.request_key, { lookup: true });
        if (!candidate) throw new Error('关联任务信息无法确认');
        const state = { ...this.state(scope), candidate }; delete state.lookupError;
        this.states.set(key, state); this.emit(); return candidate;
      }).catch(error => {
        if (live() && this.pending(scope)?.request_key === pending.request_key) {
          const state = { ...this.state(scope) }; delete state.candidate;
          state.lookupError = error.status === 404 ? '暂未查到关联任务，原提交仍待确认' : '暂时无法核对关联任务';
          this.states.set(key, state); this.emit();
        }
        return null;
      }).finally(() => this.loads.delete(loadKey));
      this.loads.set(loadKey, request); return request;
    }
    bind(id, scope, guard = () => true, viewEpoch = 0) {
      const binding = scope && `${scopeKey(scope)}:${scope.session}:${this.visibilityEpoch}:${this.foreground}:${viewEpoch}`;
      if (this.bindings.get(id) === binding) return;
      this.bindings.set(id, binding);
      if (scope && this.foreground) void this.lookup(scope, guard);
    }
    removeLocal(scope) {
      if (!this.current(scope) || this.busy(scope) || !this.pending(scope)) return false;
      const records = new Map(this.records), holds = new Map(this.holds), key = scopeKey(scope);
      records.delete(key); holds.set(key, plainScope(scope)); this.commit(records, holds); this.states.delete(key); this.emit(); return true;
    }
    dispose() { root.removeEventListener?.('blur', this.blur); root.removeEventListener?.('focus', this.focus);
      root.document?.removeEventListener('visibilitychange', this.visibility); this.listeners.clear(); }
  }

  const dialogs = new Map();
  function closeCandidate(host) { dialogs.get(host)?.close(); }
  function showCandidate(host, store, scope, result, guard) {
    dialogs.get(host)?.close();
    if (!store.current(scope) || !guard()) return;
    const dialog = document.createElement('dialog'); dialog.className = 'message-blocker-dialog'; dialog.setAttribute('aria-label', '关联任务详情');
    const add = (tag, value) => { const node = document.createElement(tag); node.textContent = value; dialog.appendChild(node); return node; };
    const job = result.job;
    add('h3', '关联任务详情'); add('p', '已找到关联任务，原提交待确认。此处仅查看。');
    add('p', `任务 ${job.id} · 轮次 ${job.turn_id}`);
    add('p', `状态：${root.DeliveryUI?.labels?.[job.display_status || job.status] || '未知'}`);
    add('h4', '任务请求'); add('pre', job.prompt || '未提供任务请求'); add('h4', '错误信息'); add('pre', job.error_message || '未提供错误信息');
    if (root.DeliveryUI?.render) dialog.appendChild(root.DeliveryUI.render(job));
    const closeButton = add('button', '关闭'); closeButton.type = 'button'; closeButton.className = 'ghost-btn';
    let off; const close = () => { off?.(); dialog.remove(); dialogs.delete(host); root.removeEventListener?.('blur', close); };
    const check = () => { if (!store.current(scope) || !store.pending(scope) || !guard() || !host.isConnected || !host.getClientRects().length || !store.foreground) close(); };
    closeButton.onclick = close; dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialogs.set(host, { close, check }); off = store.subscribe(check); root.addEventListener?.('blur', close);
    document.body.appendChild(dialog); dialog.showModal();
  }
  function render(host, store, scope, { guard = () => () => true, confirmed = () => {} } = {}) {
    if (!host) return;
    dialogs.get(host)?.check();
    const pending = store.current(scope) && store.pending(scope), state = store.state(scope), held = scope && store.holds.has(scopeKey(scope));
    host.hidden = store.ready && !pending && !held;
    host.replaceChildren();
    const note = document.createElement('p'); note.setAttribute('role', 'status'); host.appendChild(note);
    if (!store.ready) {
      note.textContent = store.storageError;
      const retry = document.createElement('button'); retry.textContent = '重试读取本机记录'; retry.type = 'button'; retry.className = 'ghost-btn sm'; retry.onclick = () => store.restore(); host.appendChild(retry); return;
    }
    if (!pending) { note.textContent = '此会话需要手动选择任务，当前不会自动连接最新任务。'; return; }
    note.textContent = store.busy(scope) ? '任务提交中…' : state.candidate ? '已找到关联任务，原提交待确认'
      : state.phase === 'rejected' ? '提交未被接受，原请求已保留' : '任务提交结果待确认';
    if (state.error) note.textContent += ` · ${state.error}`;
    if (state.lookupError) note.textContent += ` · ${state.lookupError}`;
    const prompt = document.createElement('p'); prompt.className = 'message-receipt-text'; prompt.textContent = pending.body.prompt; host.appendChild(prompt);
    const action = (label, callback) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'ghost-btn sm'; button.textContent = label;
      button.disabled = store.busy(scope); button.onclick = async () => { button.disabled = true; try { await callback(guard()); } catch (error) { note.textContent = error.message; }
        finally { if (button.isConnected) button.disabled = store.busy(scope); } }; host.appendChild(button); };
    action('核对并重试原提交', async current => { const result = await store.post(scope, current); if (result && current()) await confirmed(result); });
    action('刷新关联任务', current => store.lookup(scope, current));
    if (state.candidate) action('查看关联任务', async current => { const result = await store.lookup(scope, current); if (result && current()) showCandidate(host, store, scope, result, current); });
    action('仅移除本机记录', current => {
      if (!root.confirm('仅移除此设备的提交记录，不会取消服务端可能已创建的任务。之后再次发送会创建新任务，可能重复执行。继续移除？')) return;
      if (current()) store.removeLocal(scope);
    });
  }
  const api = { closeCandidate, Store, normalize, freezeBody, scopeKey, STORAGE_KEY, MAX_BYTES, render };
  root.TaskSubmissions = api; if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
