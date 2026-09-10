/* My 3D Workbench — views-printers.js: printer fleet + per-printer cost overrides. */
"use strict";

/* The cost-calculation settings a printer may override (same keys as global
   Settings; order mirrors my3d_workbench/cost.py COST_SETTING_KEYS). */
const PRINTER_SETTING_KEYS = [
  ["material_efficiency_factor", "Material efficiency factor"],
  ["labor_hourly_rate", "Labor rate (per hour)"],
  ["printer_cost", "Printer cost"],
  ["additional_upfront_cost", "Additional upfront cost"],
  ["annual_maintenance_cost", "Annual maintenance"],
  ["estimated_life_years", "Estimated life (years)"],
  ["estimated_uptime_fraction", "Uptime fraction (0–1)"],
  ["power_consumption_watts", "Power draw (watts)"],
  ["electricity_cost_kwh", "Electricity cost (per kWh)"],
  ["printer_cost_buffer_factor", "Cost buffer factor"],
  ["print_time_rate_override", "Fixed machine rate (override)"],
];

let printToken = 0;

function viewPrinters() {
  const app = $("#app");
  app.innerHTML = `
    <div class="toolbar">
      <div>
        <h1>Printers</h1>
        <div class="view-sub">Your machine fleet — each printer can override the global cost settings
          for cost previews on the Models tab.</div>
      </div>
      <div class="spacer"></div>
      <span class="count-pill" id="print-count">…</span>
      <button class="primary" data-on="add">+ Add printer</button>
    </div>
    <div class="grid" id="print-grid"><div class="loading" style="padding:12px 0">Loading…</div></div>
  `;
  wire(app, { add: () => printerForm().catch((err) => toast(err.message, "err")) });
  fillPrintGrid();
}

async function fillPrintGrid() {
  const grid = $("#print-grid"), pill = $("#print-count");
  if (!grid || !pill) return;
  const token = ++printToken;
  let list;
  try {
    list = await api.get("/api/printers");
  } catch (err) {
    if (token === printToken)
      grid.innerHTML = `<div class="loading" style="grid-column:1/-1">Couldn't load printers — ${esc(err.message)}</div>`;
    return;
  }
  if (token !== printToken) return;
  App.state.printers = list;                 // cache for the Models preview dropdowns
  pill.textContent = `${list.length} printer${list.length === 1 ? "" : "s"}`;
  grid.innerHTML = list.map(printCard).join("")
    || `<div class="loading" style="grid-column:1/-1">No printers yet — add one to enable per-printer cost previews.</div>`;
  grid.querySelectorAll(".card").forEach(wirePrintCard);
}

function printCard(p) {
  const n = Object.keys(p.settings || {}).length;
  return `
  <div class="card" data-id="${p.id}" data-on="edit">
    <div class="thumb-placeholder">🖨️</div>
    <div class="card-body">
      <div class="card-title">${esc(p.model_name)}
        <span class="badge badge-type">${esc(p.manufacturer || "—")}</span></div>
      <div class="card-meta">bed ${esc(p.bed_size || "—")}</div>
      <div class="chip-row">
        <span class="chip" title="Machine rate this printer produces (global settings + its overrides)">${money(p.machine_rate)}/hr</span>
        <span class="chip" title="Number of cost settings overridden">${n} override${n === 1 ? "" : "s"}</span>
      </div>
      ${p.notes ? `<div class="card-meta" style="margin-top:6px">${esc(p.notes)}</div>` : ""}
      <div class="card-actions">
        <button class="ghost icon" data-on="edit" title="Edit">✏️</button>
        <button class="ghost icon" data-on="del" title="Delete">🗑</button>
      </div>
    </div>
  </div>`;
}

function wirePrintCard(el) {
  el.addEventListener("click", (e) => {
    const target = e.target.closest("[data-on]");
    if (!target) return;
    const id = el.dataset.id, action = target.dataset.on;
    if (action === "del") {
      if (!confirm("Delete this printer? Its cost-setting overrides will be removed; " +
                   "model previews fall back to the global settings.")) return;
      api.del("/api/printers/" + id).then(() => {
        toast("Printer deleted.");
        if (String(App.state.previewPrinter) === String(id)) {
          App.state.previewPrinter = ""; App.state.previewPrinterName = "";
        }
        viewPrinters();
      }).catch((err) => toast(err.message, "err"));
    } else {
      // printerForm is async (fetches global settings for the hints) — keep
      // the rejection chained so the toast still fires for that path.
      api.get("/api/printers/" + id)
        .then((row) => printerForm(row))
        .catch((err) => toast(err.message, "err"));
    }
  });
}

/* -------------------- create / edit form -------------------- */
async function printerForm(existing) {
  const p = existing || {};
  // Global values, used as "inherit" hints next to each override input.
  // Must be awaited BEFORE the field markup is rendered — the hints are
  // computed synchronously from this data (cached in App.state so the
  // Settings tab's fetches aren't repeated on every form open).
  if (!App.state.settings) {
    try {
      const s = await api.get("/api/settings");
      App.state.settings = s.settings || [];
    } catch { /* hints simply omitted */ }
  }
  const globalMap = {};
  (App.state.settings || []).forEach((row) => { globalMap[row.key] = row.value; });

  const settingsFields = PRINTER_SETTING_KEYS.map(([key, label]) => {
    const g = globalMap[key];
    const hint = (globalMap[key] !== undefined)
      ? `global: ${esc(g)} · blank = inherit global`
      : "blank = inherit global";
    return `<div class="field"><label>${label}</label>
      <input name="${key}" type="number" step="any" min="0"
        value="${p.settings && p.settings[key] !== undefined ? esc(p.settings[key]) : ""}"
        placeholder="inherit global">
      <span class="hint">${hint}</span></div>`;
  }).join("");

  const m = openModal(`
    <div class="modal-head">
      <h2>${p.id ? "Edit printer" : "Add printer"}</h2>
      <button class="icon" data-close>✕</button>
    </div>
    <form class="form" id="print-form">
      <div class="form-grid">
        <div class="field"><label>Printer model *</label>
          <input name="model_name" required maxlength="255" value="${esc(p.model_name || "")}" placeholder="e.g. P1S"></div>
        <div class="field"><label>Manufacturer</label>
          <input name="manufacturer" maxlength="255" value="${esc(p.manufacturer || "")}" placeholder="e.g. Bambu Lab"></div>
        <div class="field"><label>Bed size</label>
          <input name="bed_size" maxlength="255" value="${esc(p.bed_size || "")}" placeholder="e.g. 256 × 256 × 256 mm"></div>
      </div>
      <div class="field" style="margin-top:14px"><label>Notes</label>
        <textarea name="notes" maxlength="2000" rows="2">${esc(p.notes || "")}</textarea></div>

      <div class="section" style="margin-top:18px">
        <h3>Cost settings — overrides for this printer
          <span style="text-transform:none;letter-spacing:0;font-weight:500">
            (blank uses the global setting)</span></h3>
        <div class="form-grid">${settingsFields}</div>
      </div>

      <div class="modal-foot" style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px">
        <button type="button" data-close>Cancel</button>
        <button type="submit" class="primary">${p.id ? "Save changes" : "Add printer"}</button>
      </div>
    </form>
  `, true);

  $("#print-form", m).addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);           // all 11 setting keys present; blank = inherit
    const url = p.id ? `/api/printers/${p.id}` : "/api/printers";
    const done = () => {
      toast(p.id ? "Printer updated." : "Printer added.");
      closeModal();
      viewPrinters();
    };
    (p.id ? api.put(url, fd) : api.post(url, fd)).then(done).catch((err) => toast(err.message, "err"));
  });
}
