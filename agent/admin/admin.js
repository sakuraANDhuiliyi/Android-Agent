"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { token: sessionStorage.getItem("android-agent-admin-token") || "", accounts: [], selected: null, dialogAction: null };
const dateTime = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });

function text(tag, value, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}

function formatDate(value) {
  if (!value) return "从未";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateTime.format(date);
}

function errorMessage(payload, fallback) {
  const message = payload?.error?.message ?? payload?.detail?.message ?? payload?.detail;
  if (typeof message === "string") return message;
  return fallback;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Authorization": `Bearer ${state.token}`, ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
  });
  let payload = null;
  if (response.status !== 204) {
    try { payload = await response.json(); } catch { payload = null; }
  }
  if (!response.ok) {
    if (response.status === 401) logout(false);
    throw new Error(errorMessage(payload, `请求失败 (${response.status})`));
  }
  return payload;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.add("hidden"), 2600);
}

function logout(showMessage = true) {
  state.token = "";
  state.selected = null;
  sessionStorage.removeItem("android-agent-admin-token");
  $("#appShell").classList.add("hidden");
  closeDrawer();
  $("#loginGate").classList.remove("hidden");
  $("#adminToken").value = "";
  if (showMessage) $("#loginError").textContent = "已安全退出";
}

async function loadDashboard() {
  const [overview, accountData] = await Promise.all([api("/api/admin/overview"), api(`/api/admin/accounts?query=${encodeURIComponent($("#searchInput").value)}`)]);
  $("#metricAccounts").textContent = overview.accounts;
  $("#metricTokens").textContent = overview.active_tokens;
  $("#metricVerified").textContent = overview.verified_accounts;
  $("#metricDisabled").textContent = overview.disabled_accounts;
  state.accounts = accountData.accounts;
  renderAccounts();
}

function statusBadge(account) {
  if (account.disabled) return text("span", "已禁用", "badge disabled");
  if (!account.email_verified) return text("span", "待验证", "badge pending");
  return text("span", "正常", "badge");
}

function renderAccounts() {
  const body = $("#accountRows");
  body.replaceChildren();
  $("#accountCount").textContent = `共 ${state.accounts.length} 个账号`;
  $("#emptyState").classList.toggle("hidden", state.accounts.length > 0);
  for (const account of state.accounts) {
    const row = document.createElement("tr");
    const accountCell = document.createElement("td");
    const wrap = document.createElement("div"); wrap.className = "account-cell";
    wrap.append(text("div", (account.display_name || account.email || "A").slice(0, 1).toUpperCase(), "avatar"));
    const identity = document.createElement("div");
    identity.append(text("strong", account.display_name || "未命名账号"), text("span", account.email || account.user_id));
    wrap.append(identity); accountCell.append(wrap);
    const status = document.createElement("td"); status.append(statusBadge(account));
    row.append(accountCell, status, text("td", String(account.active_tokens)), text("td", formatDate(account.last_seen_at)));
    const action = document.createElement("td");
    const button = text("button", "管理", "row-button"); button.type = "button"; button.dataset.userId = account.user_id;
    action.append(button); row.append(action); body.append(row);
  }
}

function metaCard(label, value) {
  const card = document.createElement("div"); card.className = "meta-card";
  card.append(text("span", label), text("strong", value)); return card;
}

function actionButton(label, action, style = "ghost") {
  const button = text("button", label, `button ${style}`); button.type = "button"; button.dataset.action = action; return button;
}

async function openAccount(userId) {
  state.selected = await api(`/api/admin/accounts/${encodeURIComponent(userId)}`);
  renderDetail();
  $("#detailDrawer").classList.remove("hidden");
  $("#drawerBackdrop").classList.remove("hidden");
}

function renderDetail() {
  const account = state.selected;
  $("#detailName").textContent = account.display_name || "未命名账号";
  const body = $("#detailBody"); body.replaceChildren();
  const meta = document.createElement("div"); meta.className = "detail-meta";
  meta.append(metaCard("邮箱", account.email || "—"), metaCard("用户 ID", account.user_id), metaCard("创建时间", formatDate(account.created_at)), metaCard("账号状态", account.disabled ? "已禁用" : account.email_verified ? "正常" : "待验证"));
  body.append(meta);
  const actions = document.createElement("div"); actions.className = "action-grid";
  actions.append(actionButton("编辑名称", "edit"), actionButton(account.email_verified ? "取消邮箱验证" : "标记邮箱已验证", "verify"), actionButton(account.disabled ? "启用账号" : "禁用账号", "toggle", account.disabled ? "primary" : "danger"), actionButton("重置密码", "password"));
  body.append(actions);

  const tokenSection = document.createElement("section"); tokenSection.className = "detail-section";
  const title = document.createElement("div"); title.className = "section-title";
  title.append(text("h3", `Token 与设备 · ${account.active_tokens}`), actionButton("＋ 签发 Token", "issue", "primary")); tokenSection.append(title);
  if (!account.tokens.length) tokenSection.append(text("p", "暂无 Token。签发后仅显示一次完整值。", "muted"));
  for (const token of account.tokens) {
    const row = document.createElement("div"); row.className = `token-row${token.active ? "" : " revoked"}`;
    row.append(text("div", "⌁", "token-icon"));
    const info = document.createElement("div"); info.className = "token-info";
    info.append(text("strong", `${token.device_name} · ${token.token_hint}`), text("span", `${token.platform || token.device_type} · 最近 ${formatDate(token.last_seen_at)}${token.active ? "" : ` · 已撤销 ${formatDate(token.revoked_at)}`}`)); row.append(info);
    if (token.active) { const revoke = actionButton("撤销", "revoke"); revoke.dataset.sessionId = token.session_id; row.append(revoke); }
    tokenSection.append(row);
  }
  body.append(tokenSection);
  if (account.active_tokens) body.append(actionButton("撤销全部活跃 Token", "revoke-all", "danger"));
  const danger = document.createElement("section"); danger.className = "danger-zone"; danger.append(text("h3", "危险操作"), text("p", "注销会清除账号凭据、项目数据、终端与构建产物，无法恢复。", "muted"), actionButton("永久注销账号", "delete", "danger")); body.append(danger);
}

function closeDrawer() {
  $("#detailDrawer").classList.add("hidden"); $("#drawerBackdrop").classList.add("hidden");
}

function openDialog({ eyebrow, title, submit = "确认", danger = false, fields = [], action }) {
  $("#dialogEyebrow").textContent = eyebrow;
  $("#dialogTitle").textContent = title;
  $("#dialogSubmit").textContent = submit;
  $("#dialogSubmit").className = `button ${danger ? "danger" : "primary"}`;
  $("#dialogError").textContent = "";
  const container = $("#dialogFields"); container.replaceChildren();
  for (const field of fields) {
    const label = document.createElement("label");
    if (field.type === "checkbox") {
      label.className = "check-label";
      const input = document.createElement("input"); input.type = "checkbox"; input.name = field.name; input.checked = Boolean(field.value);
      label.append(input, text("span", field.label));
    } else {
      label.append(text("span", field.label));
      const input = document.createElement("input"); input.type = field.type || "text"; input.name = field.name; input.value = field.value || ""; input.placeholder = field.placeholder || ""; input.required = field.required !== false; input.autocomplete = "off"; label.append(input);
    }
    container.append(label);
  }
  state.dialogAction = action;
  $("#actionDialog").showModal();
  container.querySelector("input")?.focus();
}

async function refreshSelected(message) {
  await Promise.all([loadDashboard(), openAccount(state.selected.user_id)]);
  if (message) toast(message);
}

function handleDetailAction(button) {
  const account = state.selected; const action = button.dataset.action;
  if (action === "edit") openDialog({ eyebrow: "ACCOUNT", title: "修改显示名称", fields: [{ name: "display_name", label: "显示名称", value: account.display_name }], action: async values => { await api(`/api/admin/accounts/${account.user_id}`, { method: "PATCH", body: JSON.stringify({ display_name: values.display_name }) }); await refreshSelected("账号名称已更新"); } });
  if (action === "verify") openDialog({ eyebrow: "EMAIL STATUS", title: account.email_verified ? "取消邮箱验证？" : "标记邮箱已验证？", submit: "确认更新", action: async () => { await api(`/api/admin/accounts/${account.user_id}`, { method: "PATCH", body: JSON.stringify({ email_verified: !account.email_verified }) }); await refreshSelected("邮箱状态已更新"); } });
  if (action === "toggle") openDialog({ eyebrow: "ACCESS CONTROL", title: account.disabled ? "重新启用这个账号？" : "禁用账号并撤销全部 Token？", submit: account.disabled ? "启用账号" : "禁用账号", danger: !account.disabled, action: async () => { await api(`/api/admin/accounts/${account.user_id}`, { method: "PATCH", body: JSON.stringify({ disabled: !account.disabled }) }); await refreshSelected(account.disabled ? "账号已启用" : "账号已禁用，Token 已撤销"); } });
  if (action === "password") openDialog({ eyebrow: "SECURITY", title: "重置账号密码", submit: "重置并下线", danger: true, fields: [{ name: "new_password", label: "新密码", type: "password", placeholder: "8–128 位，至少两类字符" }], action: async values => { await api(`/api/admin/accounts/${account.user_id}/reset-password`, { method: "POST", body: JSON.stringify({ new_password: values.new_password }) }); await refreshSelected("密码已重置，全部 Token 已撤销"); } });
  if (action === "issue") openDialog({ eyebrow: "NEW CREDENTIAL", title: "签发 API Token", submit: "签发 Token", fields: [{ name: "name", label: "用途名称", placeholder: "例如：CI 构建机 / 我的平板" }], action: async values => { const result = await api(`/api/admin/accounts/${account.user_id}/tokens`, { method: "POST", body: JSON.stringify({ name: values.name }) }); $("#revealedToken").textContent = result.token; $("#tokenReveal").classList.remove("hidden"); await refreshSelected(); } });
  if (action === "revoke") openDialog({ eyebrow: "REVOKE TOKEN", title: "立即撤销这个 Token？", submit: "撤销 Token", danger: true, action: async () => { await api(`/api/admin/accounts/${account.user_id}/tokens/${button.dataset.sessionId}`, { method: "DELETE" }); await refreshSelected("Token 已撤销"); } });
  if (action === "revoke-all") openDialog({ eyebrow: "GLOBAL LOGOUT", title: "让全部设备立即下线？", submit: "撤销全部 Token", danger: true, action: async () => { await api(`/api/admin/accounts/${account.user_id}/revoke-all`, { method: "POST" }); await refreshSelected("全部 Token 已撤销"); } });
  if (action === "delete") openDialog({ eyebrow: "PERMANENT DELETE", title: "永久注销账号", submit: "永久注销", danger: true, fields: [{ name: "confirm", label: `输入邮箱 ${account.email} 以确认`, placeholder: account.email }], action: async values => { if (values.confirm !== account.email) throw new Error("输入的邮箱不一致"); await api(`/api/admin/accounts/${account.user_id}`, { method: "DELETE" }); closeDrawer(); await loadDashboard(); toast("账号及其数据已永久注销"); } });
}

$("#loginForm").addEventListener("submit", async event => {
  event.preventDefault(); $("#loginError").textContent = "";
  state.token = $("#adminToken").value.trim();
  try { await loadDashboard(); sessionStorage.setItem("android-agent-admin-token", state.token); $("#loginGate").classList.add("hidden"); $("#appShell").classList.remove("hidden"); }
  catch (error) { state.token = ""; $("#loginError").textContent = error.message; }
});

$("#logoutButton").addEventListener("click", () => logout());
$("#closeDrawer").addEventListener("click", closeDrawer); $("#drawerBackdrop").addEventListener("click", closeDrawer);
$("#accountRows").addEventListener("click", event => { const button = event.target.closest("[data-user-id]"); if (button) openAccount(button.dataset.userId).catch(error => toast(error.message)); });
$("#detailBody").addEventListener("click", event => { const button = event.target.closest("[data-action]"); if (button) handleDetailAction(button); });
let searchTimer; $("#searchInput").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadDashboard().catch(error => toast(error.message)), 250); });
$("#createAccountButton").addEventListener("click", () => openDialog({ eyebrow: "NEW ACCOUNT", title: "创建账号", submit: "创建账号", fields: [{ name: "email", label: "邮箱", type: "email", placeholder: "name@example.com" }, { name: "display_name", label: "显示名称", required: false }, { name: "password", label: "初始密码", type: "password", placeholder: "8–128 位，至少两类字符" }, { name: "email_verified", label: "将邮箱标记为已验证", type: "checkbox", value: true }], action: async values => { const account = await api("/api/admin/accounts", { method: "POST", body: JSON.stringify({ email: values.email, display_name: values.display_name, password: values.password, email_verified: values.email_verified }) }); await loadDashboard(); await openAccount(account.user_id); toast("账号已创建"); } }));

$("#actionForm").addEventListener("submit", async event => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const submit = $("#dialogSubmit"); submit.disabled = true; $("#dialogError").textContent = "";
  const data = new FormData(event.currentTarget); const values = Object.fromEntries(data.entries());
  for (const input of $("#dialogFields").querySelectorAll('input[type="checkbox"]')) values[input.name] = input.checked;
  try { await state.dialogAction(values); $("#actionDialog").close(); }
  catch (error) { $("#dialogError").textContent = error.message; }
  finally { submit.disabled = false; }
});

$("#copyTokenButton").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("#revealedToken").textContent); toast("Token 已复制"); } catch { toast("复制失败，请手动选择 Token"); } });
$("#closeReveal").addEventListener("click", () => { $("#revealedToken").textContent = ""; $("#tokenReveal").classList.add("hidden"); });

if (state.token) {
  loadDashboard().then(() => { $("#loginGate").classList.add("hidden"); $("#appShell").classList.remove("hidden"); }).catch(() => logout(false));
}
