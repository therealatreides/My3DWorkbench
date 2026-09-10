/* My 3D Workbench — views-models.js: list, cost-breakdown detail (mirrors the
   Calculation sheet), and create/edit with multi-filament selection. */
"use strict";

/* -------------------- list view (with live search) -------------------- */
let modGridToken = 0;
let modSearchTimer = null;

function viewModels() {
  const app = $("#app");
  app.innerHTML = `
    <div class="toolbar">
      <div>
        <h1>Models</h1>
        <div class="view-sub">Every printable part, its filaments, and the full cost breakdown.</div>
      </div>
      <div class="spacer"></div>
      <button class="primary" data-on="add">+ Add model</button>
      <div class="search-row">
        <input class="search" id="mod-search" type="search" placeholder="Search by name…"
               value="${esc(App.state.modSearch || "")}" autocomplete="off">
        <select id="mod-printer" class="search" title="Preview all costs with a printer's settings"
                style="flex:none;width:min(230px,100%)">
          <option value="">Costs: default settings</option>
        </select>
        <span class="count-pill" id="mod-count">…</span>
      </div>
    </div>
    <div class="grid" id="mod-grid"><div class="loading">Loading…</div></div>
  `;
  const input = $("#mod-search");
  const queue = () => {
    clearTimeout(modSearchTimer);
    modSearchTimer = setTimeout(() => fillModGrid(input.value), 180);
  };
  input.addEventListener("input", queue);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; App.state.modSearch = ""; fillModGrid(""); }
  });
  if (!document.querySelector("#modal-root .modal-backdrop")) input.focus();   // don't steal focus from an open modal
  wire(app, { add: () => modelForm().catch((err) => toast(err.message, "err")) });

  // Printer preview selector: costs across the whole grid are recomputed
  // with the chosen printer's overrides (?printer=<id> on the API).
  const sel = $("#mod-printer");
  const populatePrinterSelect = (list) => {
    App.state.printers = list;
    const keep = App.state.previewPrinter || "";
    if (keep && !list.some((p) => String(p.id) === String(keep))) {
      App.state.previewPrinter = ""; App.state.previewPrinterName = "";
    }
    sel.innerHTML = `<option value="">Costs: default settings</option>` + list.map((p) =>
      `<option value="${p.id}" ${String(p.id) === String(App.state.previewPrinter) ? "selected" : ""}>${esc((p.manufacturer ? p.manufacturer + " " : "") + p.model_name)}</option>`).join("");
    sel.value = App.state.previewPrinter || "";
  };
  api.get("/api/printers").then(populatePrinterSelect).catch(() => { /* dropdown stays at default */ });
  sel.addEventListener("change", () => {
    App.state.previewPrinter = sel.value;
    const pr = (App.state.printers || []).find((p) => String(p.id) === sel.value);
    App.state.previewPrinterName = pr ? (pr.manufacturer ? pr.manufacturer + " " : "") + pr.model_name : "";
    fillModGrid(App.state.modSearch || "");
  });
  fillModGrid(App.state.modSearch || "");
}

async function fillModGrid(q) {
  const grid = $("#mod-grid"), pill = $("#mod-count");
  if (!grid || !pill) return;
  const token = ++modGridToken;
  grid.innerHTML = '<div class="loading">Searching…</div>';
  const params = [];
  if (q) params.push("q=" + encodeURIComponent(q));
  if (App.state.previewPrinter) params.push("printer=" + encodeURIComponent(App.state.previewPrinter));
  const qs = params.length ? "?" + params.join("&") : "";
  let list;
  try {
    list = await api.get("/api/models" + qs);
  } catch (err) {
    if (token === modGridToken) {
      grid.innerHTML = `<div class="loading" style="grid-column:1/-1">Couldn't load models — ${esc(err.message)}</div>`;
      pill.textContent = "—";
    }
    return;
  }
  if (token !== modGridToken) return;   // a newer search is in flight — discard this
  App.state.modSearch = q;
  const ctx = App.state.previewPrinterName ? ` · ${App.state.previewPrinterName}` : "";
  pill.textContent = (q ? `${list.length} match${list.length === 1 ? "" : "es"}` : `${list.length} models`) + ctx;
  grid.innerHTML = list.map(modelCard).join("")
    || `<div class="loading" style="grid-column:1/-1">${q ? `No models match “${esc(q)}”.` : "No models yet — add your first part."}</div>`;
  grid.querySelectorAll(".card").forEach(wireModelCard);
}

function modelCard(m) {
  const chips = (m.filaments || []).slice(0, 4).map((f) =>
    `<span class="chip"><span class="dot"></span>${esc(f.color_name)} ${esc(f.type)}</span>`).join("");
  const more = (m.filaments || []).length > 4
    ? `<span class="chip">+${m.filaments.length - 4}</span>` : "";
  return `
  <div class="card" data-id="${m.id}" data-on="open">
    ${m.picture_url ? `<img class="thumb" src="${esc(m.picture_url)}" alt="">` : `<div class="thumb-placeholder">📦</div>`}
    <div class="card-body">
      <div class="card-title">${esc(m.name)} ${purposeOfModel(m.purpose)}</div>
      <div class="card-meta">
        ${m.filaments.length ? `${m.filaments.length} filament${m.filaments.length > 1 ? "s" : ""}` : "no filaments set"}
        · ${esc(hours(m.print_time_hr))} print · labor ${esc(Math.round(Number(m.labor_min || 0)))} min
      </div>
      <div class="chip-row">${chips}${more}</div>
      <div style="margin-top:10px;font-weight:800;font-size:16px">
        ${money(m.cost.total_cost, m.cost.currency)}
        <span style="font-weight:500;color:var(--muted);font-size:12px">total cost</span>
      </div>
      <div class="card-actions">
        <button class="ghost icon" data-on="open">👁</button>
        <button class="ghost icon" data-on="edit">✏️</button>
        <button class="ghost icon" data-on="del">🗑</button>
      </div>
    </div>
  </div>`;
}

function wireModelCard(el) {
  el.addEventListener("click", (e) => {
    const target = e.target.closest("[data-on]");
    if (!target) return;
    const id = el.dataset.id, action = target.dataset.on;
    if (action === "del") {
      if (!confirm("Delete this model?")) return;
      api.del("/api/models/" + id).then(() => { toast("Model deleted."); viewModels(); })
        .catch((err) => toast(err.message, "err"));
    } else if (action === "edit") {
      api.get("/api/models/" + id).then((mm) => modelForm(mm))
        .catch((err) => toast(err.message, "err"));
    } else {
      api.get("/api/models/" + id).then(modelDetail).catch((err) => toast(err.message, "err"));
    }
  });
}

/* -------------------- detail view -------------------- */
function modelDetail(m) {
  const b = m.breakdown;
  const cur = b.currency;
  const bRow = (label, val, sub) => `
    <tr><td>${label}</td><td>${val}${sub ? ` <small style="color:var(--muted);font-weight:500">${sub}</small>` : ""}</td></tr>`;

  const modal = openModal(`
    <div class="modal-head">
      <h2>${esc(m.name)} ${purposeOfModel(m.purpose)}</h2>
      <button class="icon" data-close>✕</button>
    </div>
    <div class="detail-hero">
      ${m.picture_url ? `<img src="${esc(m.picture_url)}" alt="">` : `<div class="ph">📦</div>`}
      <dl class="kv" style="flex:1">
        <dt>Filament per print</dt><dd>${esc(grams(m.filament_amount_g))}</dd>
        <dt>Print time</dt><dd>${esc(hours(m.print_time_hr))}</dd>
        <dt>Labor</dt><dd>${esc(Math.round(Number(m.labor_min || 0)))} min @ ${money(b.labor_rate)}/hr</dd>
        ${m.description ? `<dt>Description</dt><dd>${esc(m.description)}</dd>` : ""}
      </dl>
    </div>

    <div class="section">
      <h3>Associated filaments</h3>
      ${b.filaments.length ? `<div class="mini-list">${b.filaments.map((f) => `
        <div class="mini-row"><span>${esc(f.color_name)} ${esc(f.type)}
          <span style="color:var(--muted)">(${esc(grams(f.grams))} @ ${money(f.cost_per_kg, cur)}/kg)</span></span>
        <span>${money(f.cost, cur)}</span></div>`).join("")}</div>`
        : `<div class="loading" style="padding:10px 0">No filaments linked.</div>`}
    </div>

    <div class="section">
      <h3>Extra materials <span class="hint">— non-print costs per model</span></h3>
      ${(b.materials || []).length ? `<div class="mini-list">${b.materials.map((mm) => `
        <div class="mini-row"><span>${esc(mm.description)}
          ${mm.quantity && mm.quantity !== 1
              ? `<span style="color:var(--muted)">(${esc(mm.quantity)} × ${money(mm.unit_cost, cur)})</span>`
              : `<span style="color:var(--muted)">@ ${money(mm.unit_cost, cur)} each</span>`}</span>
        <span>${money(mm.cost, cur)}</span></div>`).join("")}
        <div class="mini-row" style="font-weight:700"><span>Materials total</span><span>${money(b.materials_cost || 0, cur)}</span></div>
      </div>`
        : `<div class="loading" style="padding:10px 0">None — printing cost only. Add line items (dowels, magnets, boxes…) from <b>Edit</b>.</div>`}
    </div>

    <div class="section">
      <h3>Cost breakdown</h3>
      <table class="cost-table">
        <tr><td>Printed-part material</td><td>${money(b.part_material_cost, cur)}
            <small style="color:var(--muted);font-weight:500">× efficiency ${b.efficiency_factor}</small></td></tr>
        ${bRow("Labor", money(b.labor_cost, cur))}
        ${bRow("Machine (print)" + (b.machine_rate_source === "override" ? " *" : ""),
               money(b.machine_cost, cur), `${money(b.machine_rate, cur)}/hr`)}
        ${(b.materials_cost || 0) > 0 ? bRow("Extra materials",
               money(b.materials_cost, cur), `${(b.materials || []).length} line item(s)`) : ""}
        <tr class="grand"><td>Total cost</td><td>${money(b.total_cost, cur)}</td></tr>
      </table>

      <h3 style="margin-top:16px;font-size:13px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)">Suggested prices (cost ÷ (1 − margin))</h3>
      <div class="price-grid">
        <div class="price-box"><div class="lab">50% margin</div><div class="val">${money(b.suggested["50"], cur)}</div></div>
        <div class="price-box"><div class="lab">60% margin</div><div class="val">${money(b.suggested["60"], cur)}</div></div>
        <div class="price-box"><div class="lab">70% margin</div><div class="val">${money(b.suggested["70"], cur)}</div></div>
      </div>
    </div>

    <div class="section">
      <h3>Preview on a printer</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <select id="preview-printer" class="search" style="width:min(230px,100%)">
          <option value="">Choose a printer…</option>
        </select>
        <span class="hint">Recomputes this model's costs using that printer's setting overrides.</span>
      </div>
      <div id="preview-result"></div>
    </div>

    <div class="modal-foot">
      <button data-close>Close</button>
      <button class="primary" data-on="edit">Edit</button>
      <button class="danger" data-on="del">Delete</button>
    </div>
  `, true);

  wire(modal, {
    edit: () => { closeModal(); modelForm(m).catch((err) => toast(err.message, "err")); },
    del: () => {
      if (!confirm("Delete this model?")) return;
      api.del("/api/models/" + m.id).then(() => { toast("Model deleted."); closeModal(); viewModels(); })
        .catch((err) => toast(err.message, "err"));
    },
  });

  /* --- per-printer preview --- */
  const pSel = $("#preview-printer", modal), pBox = $("#preview-result", modal);
  const showPreview = (pid) => {
    if (!pid) { pBox.innerHTML = ""; return; }
    loadModelPreview(m, pid, pBox);
  };
  const populatePreviewSelect = (list) => {
    App.state.printers = list;
    const keep = App.state.previewPrinter || "";
    pSel.innerHTML = `<option value="">Choose a printer…</option>` + list.map((p) =>
      `<option value="${p.id}" ${String(p.id) === String(keep) ? "selected" : ""}>${esc((p.manufacturer ? p.manufacturer + " " : "") + p.model_name)}</option>`).join("");
    pSel.value = String(keep && list.some((p) => String(p.id) === String(keep)) ? keep : "");
    showPreview(pSel.value);
  };
  if (App.state.printers && App.state.printers.length) populatePreviewSelect(App.state.printers);
  else api.get("/api/printers").then(populatePreviewSelect)
    .catch((err) => { pBox.innerHTML = `<div class="loading" style="padding:10px 0">Couldn't load printers — ${esc(err.message)}</div>`; });
  pSel.addEventListener("change", () => {
    App.state.previewPrinter = pSel.value;
    const pr = (App.state.printers || []).find((p) => String(p.id) === pSel.value);
    App.state.previewPrinterName = pr ? (pr.manufacturer ? pr.manufacturer + " " : "") + pr.model_name : "";
    showPreview(pSel.value);
  });
}

/* -------------------- printer-preview helper -------------------- */
async function loadModelPreview(m, pid, box) {
  box.innerHTML = '<div class="loading" style="padding:10px 0">Calculating…</div>';
  let d;
  try {
    d = await api.get(`/api/models/${m.id}?printer=${encodeURIComponent(pid)}`);
  } catch (err) {
    box.innerHTML = `<div class="loading" style="padding:10px 0">Preview failed — ${esc(err.message)}</div>`;
    return;
  }
  const base = m.breakdown, pb = d.printer_preview.breakdown, pr = d.printer_preview.printer;
  const cur = pb.currency;
  const arrow = (a, b) => {
    const diff = b - a;
    return `${money(a, cur)} → <b>${money(b, cur)}</b>` +
      (diff !== 0 ? ` <small style="color:${diff > 0 ? "var(--red)" : "var(--green)"};font-weight:600">(${diff > 0 ? "+" : "−"}${money(Math.abs(diff), cur)})</small>` : "");
  };
  box.innerHTML = `
    <div style="margin-top:10px;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--panel)">
      <div style="font-weight:700;margin-bottom:8px">🖨️ ${esc((pr.manufacturer ? pr.manufacturer + " " : "") + pr.model_name)}
        <span style="color:var(--muted);font-weight:500">${esc(pr.bed_size || "")}</span></div>
      <div class="mini-list">
        <div class="mini-row"><span>Machine rate</span><span>${arrow(base.machine_rate, pb.machine_rate)}/hr</span></div>
        <div class="mini-row"><span>Machine cost</span><span>${arrow(base.machine_cost, pb.machine_cost)}</span></div>
        <div class="mini-row"><span>Total cost</span><span>${arrow(base.total_cost, pb.total_cost)}</span></div>
        <div class="mini-row"><span>Suggested · 50%</span><span>${arrow(base.suggested["50"], pb.suggested["50"])}</span></div>
      </div>
    </div>`;
}
