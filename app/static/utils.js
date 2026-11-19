export const $ = (selector) => document.querySelector(selector);
export const $$ = (selector) => [...document.querySelectorAll(selector)];

export const fmtMoney = (v) =>
  "¥" + Number(v || 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
export const fmtNum = (v) => Number(v || 0).toLocaleString("zh-CN");

export function toast(message) {
  const el = document.createElement("div");
  el.className = "toast show";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.classList.remove("show"), 2400);
  setTimeout(() => el.remove(), 2800);
}

export function adminHeaders(extra = {}) {
  const token = window.localStorage.getItem("atlas_admin_token") || "";
  return token ? { ...extra, "X-Admin-Token": token } : extra;
}

export async function api(url, options = {}) {
  const next = { ...options };
  const method = String(next.method || "GET").toUpperCase();
  const protectedRead = method === "GET" && (
    url.startsWith("/api/data-platform") ||
    url === "/api/model/config" ||
    url.startsWith("/api/evaluation")
  );
  if (["POST", "PUT", "DELETE", "PATCH"].includes(method) || protectedRead) {
    next.headers = adminHeaders(next.headers || {});
  }
  const res = await fetch(url, next);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${res.status})`);
  }
  return res.json();
}

export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]));
}
