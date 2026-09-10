/* My 3D Workbench — core.js: tiny API client + shared UI helpers. All view files build on this. */
"use strict";

const App = {
  state: {
    currency: "USD",       // set at bootstrap
    filaments: null,       // cached filament list (for the model form)
    filFilters: null,      // filaments page filters {brand,color,type,stock} (lazy, see views-filaments.js)
    printers: null,        // cached printer list (for the Models preview dropdowns)
    previewPrinter: "",    // printer id selected for cost previews ("" = global settings)
    previewPrinterName: "",
  },
};

/* ---------------- API client ---------------- */
const api = {
  async req(path, opts = {}) {
    const init = { method: opts.method || "GET" };
    if (opts.body instanceof FormData) {
      init.body = opts.body;    // browser sets multipart boundary itself
    } else if (opts.body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, init);
    const text = await res.text();
    let data = null;
    if (text) { try { data = JSON.parse(text); } catch { data = text; } }
    if (!res.ok) {
      const msg = (data && data.error) ? data.error : `Request failed (${res.status})`;
      const err = new Error(msg);
      if (data && typeof data === "object") err.payload = data;   // e.g. skipped rows from CSV import
      throw err;
    }
    return data;
  },
  get: (p) => api.req(p),
  post: (p, b) => api.req(p, { method: "POST", body: b }),
  put: (p, b) => api.req(p, { method: "PUT", body: b }),
  del: (p, b) => api.req(p, { method: "DELETE", body: b }),   // body: optional JSON (bulk actions)
};

/* ---------------- DOM / format helpers ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function money(n, currency) {
  const code = (currency && currency !== "USD")
    ? currency
    : (App.state.currency && App.state.currency !== "USD" ? App.state.currency : "USD");
  const sym = code === "USD" ? "$" : code + " ";
  const v = Number(n);
  if (!isFinite(v)) return "—";
  return sym + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function grams(n) {
  const v = Number(n || 0);
  if (v >= 1000) return (v / 1000).toFixed(2).replace(/\.?0+$/, "") + " kg";
  return Math.round(v) + " g";
}
function hours(h) {
  const v = Number(h || 0);
  return v === 0 ? "—" : (v < 1 ? Math.round(v * 60) + " min" : v.toFixed(2).replace(/\.00$/, "") + " hr");
}

/* ---------------- toast + confirm ---------------- */
function toast(msg, kind = "ok") {
  const root = $("#toast-root");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  root.appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

/* ---------------- modal machinery ---------------- */
function openModal(html, wide = false) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-backdrop">
      <div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div>
    </div>`;
  const backdrop = $(".modal-backdrop", root);
  backdrop.addEventListener("mousedown", (e) => {
    if (e.target === backdrop) closeModal();   // click outside to dismiss
  });
  root.querySelectorAll("[data-close]").forEach((b) =>
    b.addEventListener("click", (e) => { e.preventDefault(); closeModal(); }));
  return $(".modal", root);
}
function closeModal() { $("#modal-root").innerHTML = ""; }

/* Single global Esc listener — no per-modal binding, no leaks. */
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* Attach event handlers by name → callback, keeps view code declarative */
function wire(modal, handlers) {
  modal.querySelectorAll("[data-on]").forEach((el) => {
    const name = el.dataset.on;
    if (handlers[name]) el.addEventListener("click", (e) => handlers[name](e, el));
  });
}

/* ---------------- misc ---------------- */
function badgePurpose(p) { return `<span class="badge badge-purpose-${esc(p)}">${esc(p)}</span>`; }
function purposeOfModel(m) { return badgePurpose(m.purpose); }
function stockBadge(f) {
  return f.in_stock
    ? `<span class="badge badge-stock-in">In stock · ${esc(grams(f.current_stock_g))}</span>`
    : `<span class="badge badge-stock-out">Out of stock</span>`;
}
function thumbHtml(f) {
  return f.picture_url
    ? `<img class="thumb" src="${esc(f.picture_url)}" alt="">`
    : `<div class="thumb-placeholder">🧵</div>`;
}
function flashLoading() { $("#app").innerHTML = '<div class="loading">Loading…</div>'; }
