// Shared utilities: Vietnamese number formatting, cookies, CSRF, toasts, confirm dialog, htmx, chart tooltips.
// No build step; the global functions are reused by the Alpine components (shell.js, forms.js, ai.js).

/* ------------------------------------------------------------------ Cookies, storage */
function setCookie(name, value) {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=31536000; samesite=lax`;
}

function getCookie(name) {
  const m = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

// localStorage may be blocked (private mode) – always wrap in try/catch.
const uiStore = {
  get(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } },
  set(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* ignore */ } },
};

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

function readJSON(id, fallback) {
  const el = document.getElementById(id);
  if (!el) return fallback;
  try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
}

function debounce(fn, ms) {
  let t;
  return function (...args) { clearTimeout(t); t = setTimeout(() => fn.apply(this, args), ms); };
}

// Diacritic-insensitive matching: "dien luc" matches "Điện lực".
function foldVi(s) {
  return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
}

/* ------------------------------------------------------------------ Amounts, quantities */
function formatVnd(n) {
  if (n === null || n === undefined || n === "" || Number.isNaN(Number(n))) return "";
  const v = Math.trunc(Number(n));
  const sign = v < 0 ? "-" : "";
  return sign + Math.abs(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

// Parse a typed amount: "10.000.000", "10tr", "1,5tr", "500k", "2 tỷ" -> integer dong (or null).
function parseVnd(text) {
  if (text === null || text === undefined) return null;
  if (typeof text === "number") return Math.round(text);
  let s = String(text).trim().toLowerCase().replace(/[đ₫\s]/g, "");
  if (!s) return null;
  let neg = false;
  if (s.startsWith("-")) { neg = true; s = s.slice(1); }
  const m = s.match(/^([\d.,]+)(k|n|tr|m|tỷ|ty|b)?$/);
  if (!m) return null;
  const mult = { k: 1e3, n: 1e3, tr: 1e6, m: 1e6, "tỷ": 1e9, ty: 1e9, b: 1e9 }[m[2]] || 1;
  let num = m[1];
  let v;
  if (mult > 1) {
    v = Math.round(parseFloat(num.replace(/\./g, "").replace(",", ".")) * mult);
  } else {
    num = num.replace(/[.,]/g, "");
    v = num ? parseInt(num, 10) : NaN;
  }
  if (Number.isNaN(v)) return null;
  return neg ? -v : v;
}

// Vietnamese-style quantity / unit price: "1.234,5" -> 1234.5 ; "2,5" -> 2.5 ; "1.5" -> 1.5 ; "1.500" -> 1500.
function parseQty(text) {
  if (text === null || text === undefined) return null;
  if (typeof text === "number") return text;
  let s = String(text).trim().replace(/\s/g, "");
  if (!s) return null;
  if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
  else if (/^-?\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
  const v = parseFloat(s);
  return Number.isNaN(v) ? null : v;
}

function formatQty(n, maxDecimals = 3) {
  if (n === null || n === undefined || n === "" || Number.isNaN(Number(n))) return "";
  const v = Number(n);
  const fixed = v.toFixed(maxDecimals).replace(/\.?0+$/, "");
  const [int, dec] = fixed.split(".");
  const sign = int.startsWith("-") ? "-" : "";
  const body = int.replace("-", "").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return sign + body + (dec ? "," + dec : "");
}

/* ------------------------------------------------------------------ Toasts, confirmation */
// window.toast("Đã lưu", "success" | "error" | "warning" | "info", { title, timeout })
// Toasts raised before the toaster initializes (e.g. a server flash while Alpine is starting) are queued
// and drained by the toaster on init – otherwise events dispatched with no listener would be lost.
window.__toastQueue = window.__toastQueue || [];
function toast(message, kind = "success", opts = {}) {
  const detail = { message, kind, ...opts };
  if (!window.__toasterReady) { window.__toastQueue.push(detail); return; }
  window.dispatchEvent(new CustomEvent("ui-toast", { detail }));
}
window.toast = toast;

// await uiConfirm("Hủy chứng từ này?", { title, confirmLabel, danger: true }) -> true/false
function uiConfirm(message, opts = {}) {
  return new Promise((resolve) => {
    window.dispatchEvent(new CustomEvent("ui-confirm", { detail: { message, resolve, ...opts } }));
  });
}
window.uiConfirm = uiConfirm;

// data-confirm="…" on a <form>, submit button or <a> → ask again with the app's dialog.
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement)) return;
  const btn = e.submitter;
  const msg = (btn && btn.dataset.confirm) || form.dataset.confirm;
  if (!msg || form.__confirmed) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  const danger = (btn && "danger" in btn.dataset) || "danger" in form.dataset;
  uiConfirm(msg, { danger, confirmLabel: (btn && btn.dataset.confirmLabel) || form.dataset.confirmLabel }).then((ok) => {
    if (!ok) return;
    form.__confirmed = true;
    try { form.requestSubmit(btn || undefined); } finally { form.__confirmed = false; }
  });
}, true);

document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("a[data-confirm]");
  if (!a || a.__confirmed) return;
  e.preventDefault();
  uiConfirm(a.dataset.confirm, { danger: "danger" in a.dataset }).then((ok) => {
    if (ok) { a.__confirmed = true; a.click(); a.__confirmed = false; }
  });
}, true);

/* ------------------------------------------------------------------ htmx */
document.addEventListener("DOMContentLoaded", () => {
  if (!window.htmx) return;
  // Allow swapping 422 (business error) and 403 responses to show the message in the page.
  htmx.config.responseHandling = [
    { code: "204", swap: false },
    { code: "[23]..", swap: true },
    { code: "422", swap: true },
    { code: "403", swap: true },
    { code: "[45]..", swap: false, error: true },
  ];
  document.body.addEventListener("htmx:confirm", (e) => {
    if (!e.detail.question) return;
    e.preventDefault();
    const danger = e.detail.elt && "danger" in e.detail.elt.dataset;
    uiConfirm(e.detail.question, { danger }).then((ok) => { if (ok) e.detail.issueRequest(true); });
  });
  // Server sends HX-Trigger: {"toast": {"message": "…", "kind": "success"}}
  document.body.addEventListener("toast", (e) => {
    const d = e.detail || {};
    if (d.message) toast(d.message, d.kind || "success", d);
  });
  document.body.addEventListener("htmx:responseError", (e) => {
    const s = e.detail.xhr ? e.detail.xhr.status : "";
    toast(document.documentElement.lang === "en" ? `Request failed (${s})` : `Có lỗi xảy ra (mã ${s})`, "error");
  });
  document.body.addEventListener("htmx:sendError", () => {
    toast(document.documentElement.lang === "en" ? "Cannot reach the server" : "Không kết nối được máy chủ", "error");
  });
});

/* ------------------------------------------------------------------ Chart tooltips */
// Elements inside .chart carry data-tip='{"title": "...", "rows": [{"label": "...", "value": "...", "color": "var(--series-1)", "kind": "bar|line"}]}'
(function () {
  let tip = null;
  const ensure = () => {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      tip.setAttribute("role", "tooltip");
      tip.hidden = true;
      document.body.appendChild(tip);
    }
    return tip;
  };
  const render = (data) => {
    const t = ensure();
    t.replaceChildren();
    if (data.title) {
      const h = document.createElement("div");
      h.className = "mb-1 font-medium text-fg-2";
      h.textContent = data.title;
      t.appendChild(h);
    }
    (data.rows || []).forEach((r) => {
      const row = document.createElement("div");
      row.className = "flex items-center gap-2 py-0.5";
      const key = document.createElement("span");
      key.className = r.kind === "bar" ? "legend-swatch" : "legend-line";
      key.style.background = r.color || "currentColor";
      const val = document.createElement("span");
      val.className = "font-semibold tabular-nums text-fg";
      val.textContent = r.value;
      const lab = document.createElement("span");
      lab.className = "text-fg-3";
      lab.textContent = r.label;
      row.append(key, val, lab);
      t.appendChild(row);
    });
    t.hidden = false;
  };
  const place = (x, y) => {
    const t = ensure();
    const w = t.offsetWidth, h = t.offsetHeight;
    let left = x + 14, top = y + 14;
    if (left + w > window.innerWidth - 8) left = x - w - 14;
    if (top + h > window.innerHeight - 8) top = y - h - 14;
    t.style.left = `${Math.max(8, left)}px`;
    t.style.top = `${Math.max(8, top)}px`;
  };
  const target = (el) => el && el.closest && el.closest(".chart [data-tip]");
  document.addEventListener("pointermove", (e) => {
    const el = target(e.target);
    if (!el) { if (tip) tip.hidden = true; return; }
    try { render(JSON.parse(el.dataset.tip)); place(e.clientX, e.clientY); } catch (err) { /* ignore */ }
  });
  document.addEventListener("focusin", (e) => {
    const el = target(e.target);
    if (!el) return;
    const r = el.getBoundingClientRect();
    try { render(JSON.parse(el.dataset.tip)); place(r.left + r.width / 2, r.top); } catch (err) { /* ignore */ }
  });
  document.addEventListener("focusout", () => { if (tip) tip.hidden = true; });
  window.addEventListener("scroll", () => { if (tip) tip.hidden = true; }, true);
})();
