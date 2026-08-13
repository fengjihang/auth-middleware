"use strict";

// ---------- token 存储 ----------
const ACCESS_KEY = "am_access";
const REFRESH_KEY = "am_refresh";

const getTokens = () => ({
  access: localStorage.getItem(ACCESS_KEY),
  refresh: localStorage.getItem(REFRESH_KEY),
});
const setTokens = (access, refresh) => {
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
  updateStatus();
};
const clearTokens = () => {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  updateStatus();
};

// ---------- 工具 ----------
const $ = (id) => document.getElementById(id);
const val = (id) => $(id).value.trim();

function showResult(id, data, isError) {
  const el = $(id);
  el.style.display = "block";
  el.textContent =
    typeof data === "string" ? data : JSON.stringify(data, null, 2);
  el.className = "result" + (isError ? " result-err" : "");
}

function updateStatus() {
  const { access } = getTokens();
  const badge = $("status-badge");
  if (access) {
    badge.textContent = "已登录";
    badge.className = "badge badge-ok";
  } else {
    badge.textContent = "未登录";
    badge.className = "badge badge-off";
  }
}

// 统一 fetch 封装：自动带 Bearer，展示 X-Request-ID，解析错误
async function api(method, path, body = null, auth = true) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const { access } = getTokens();
    if (access) headers["Authorization"] = "Bearer " + access;
  }
  const opts = { method, headers };
  if (body !== null) opts.body = JSON.stringify(body);

  const resp = await fetch(path, opts);
  const rid = resp.headers.get("X-Request-ID");
  if (rid) $("request-id").textContent = rid;

  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    data = text;
  }
  if (!resp.ok) {
    const detail = data && data.detail ? data.detail : "HTTP " + resp.status;
    throw new Error(detail);
  }
  return data;
}

// ---------- 1. 认证 ----------
$("btn-register").onclick = async () => {
  try {
    const r = await api(
      "POST",
      "/api/v1/auth/register",
      { email: val("reg-email"), password: val("reg-password"), display_name: val("reg-name") },
      false
    );
    showResult("reg-result", r);
  } catch (e) {
    showResult("reg-result", e.message, true);
  }
};

$("btn-login").onclick = async () => {
  try {
    const r = await api(
      "POST",
      "/api/v1/auth/login",
      { email: val("login-email"), password: val("login-password") },
      false
    );
    setTokens(r.access_token, r.refresh_token);
    showResult("login-result", r);
  } catch (e) {
    showResult("login-result", e.message, true);
  }
};

$("btn-refresh").onclick = async () => {
  try {
    const { refresh } = getTokens();
    if (!refresh) {
      showResult("login-result", "无 refresh token，请先登录", true);
      return;
    }
    const r = await api(
      "POST",
      "/api/v1/auth/refresh",
      { refresh_token: refresh },
      false
    );
    setTokens(r.access_token, r.refresh_token);
    showResult("login-result", r);
  } catch (e) {
    showResult("login-result", e.message, true);
  }
};

// ---------- 2. 当前用户 / 资料 ----------
$("btn-me").onclick = async () => {
  try {
    const r = await api("GET", "/api/v1/auth/me");
    showResult("me-result", r);
  } catch (e) {
    showResult("me-result", e.message, true);
  }
};

$("btn-profile-get").onclick = async () => {
  try {
    const r = await api("GET", "/api/v1/rbac/profile");
    $("profile-name").value = r.display_name || "";
    showResult("profile-result", r);
  } catch (e) {
    showResult("profile-result", e.message, true);
  }
};

$("btn-profile-put").onclick = async () => {
  try {
    const r = await api("PUT", "/api/v1/rbac/profile", {
      display_name: val("profile-name"),
    });
    showResult("profile-result", r);
  } catch (e) {
    showResult("profile-result", e.message, true);
  }
};

// ---------- 3. 越权演示 ----------
// 403 是符合预期的"被拦截"，用中性样式展示，不标红
$("btn-admin").onclick = async () => {
  try {
    const r = await api("GET", "/api/v1/rbac/admin/users");
    showResult("admin-result", r);
  } catch (e) {
    showResult("admin-result", "已拦截（符合预期）: " + e.message);
  }
};

// ---------- 4. 审计查询 ----------
function renderAudit(r) {
  $("audit-result").style.display = "none";
  $("audit-info").textContent = `共 ${r.total} 条 · 第 ${r.page}/${r.pages} 页`;
  const items = r.items || [];
  if (!items.length) {
    $("audit-table").innerHTML = '<div class="hint">无记录</div>';
    return;
  }
  const rows = items
    .map((it) => {
      const t = (it.created_at || "").replace("T", " ").slice(0, 19);
      const ok = it.allowed
        ? '<span class="pill pill-yes">通过</span>'
        : '<span class="pill pill-no">拒绝</span>';
      return `<tr><td>${t}</td><td>${it.user_email || "—"}</td><td>${it.action || "—"}</td><td>${it.resource || "—"}</td><td>${ok}</td></tr>`;
    })
    .join("");
  $("audit-table").innerHTML = `
    <table>
      <thead><tr><th>时间</th><th>用户</th><th>动作</th><th>资源</th><th>结果</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

$("btn-audit").onclick = async () => {
  try {
    const params = new URLSearchParams();
    params.set("page", val("audit-page") || "1");
    params.set("limit", val("audit-limit") || "20");
    const action = val("audit-action");
    if (action) params.set("action", action);
    const allowed = $("audit-allowed").value;
    if (allowed !== "") params.set("allowed", allowed);
    let df = val("audit-from");
    if (df && df.length === 16) df += ":00";
    if (df) params.set("date_from", df);
    let dt = val("audit-to");
    if (dt && dt.length === 16) dt += ":00";
    if (dt) params.set("date_to", dt);

    const r = await api("GET", "/api/v1/admin/audit-logs?" + params.toString());
    renderAudit(r);
  } catch (e) {
    $("audit-table").innerHTML = "";
    showResult("audit-result", e.message, true);
  }
};

// ---------- 5. 会话管理 ----------
$("btn-logout").onclick = async () => {
  try {
    const { access, refresh } = getTokens();
    await api(
      "POST",
      "/api/v1/auth/logout",
      { access_token: access, refresh_token: refresh },
      false
    );
    clearTokens();
    showResult("logout-result", "已登出（当前令牌已吊销）");
  } catch (e) {
    showResult("logout-result", e.message, true);
  }
};

$("btn-logout-all").onclick = async () => {
  try {
    await api("POST", "/api/v1/auth/logout-all");
    clearTokens();
    showResult("logout-result", "已全量登出（该用户所有会话失效）");
  } catch (e) {
    showResult("logout-result", e.message, true);
  }
};

// ---------- 初始化 ----------
updateStatus();
