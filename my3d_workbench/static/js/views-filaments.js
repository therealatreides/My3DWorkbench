/* My 3D Workbench — views-filaments.js: list, detail (with related models), create/edit. */
"use strict";

const FILAMENT_TYPES = ["PLA", "PETG", "ASA", "ABS", "TPU", "NYLON", "PC", "CPETG", "RESIN"];

/* -------------------- list view (with live search + sort) -------------------- */
let filGridToken = 0;
let filSearchTimer = null;

/* -------------------- multi-select (bulk delete) --------------------
   A transient mode: toggle it on, click cards to pick them, then delete the
   whole selection in one request. Deliberately in-memory (no localStorage)
   — a "pending delete" list surviving a reload would be a trap, so a fresh
   page always starts with a clean slate. "Select all" covers the cards
   currently in view (i.e. after search/filters), never hidden spools. */
let filSelectMode = false;
const filSelected = new Set();
window.filSelState = () => ({ on: filSelectMode, n: filSelected.size, ids: [...filSelected] });

/* Sort options shown in the toolbar. Value format: "<field>_<dir>".
   Applied client-side over the current (search-filtered) result set. */
const FIL_SORTS = [
  ["", "Sort: added order"],
  ["color_asc", "Color A→Z"], ["color_desc", "Color Z→A"],
  ["brand_asc", "Brand A→Z"], ["brand_desc", "Brand Z→A"],
  ["type_asc", "Type A→Z"], ["type_desc", "Type Z→A"],
];
const FIL_SORT_FIELDS = { color: "color_name", brand: "brand", type: "type" };

/* Persisted-view-state storage keys.
   One-shot migration from the legacy "spool.*" names: any saved value is
   carried over to the new key and the old key dropped, so the rename is
   invisible to users who already have a sort mode / filters persisted. */
function migrateSpoolStorageKeys() {
  try {
    [["spool.filSort", "my3dworkbench.filSort"],
     ["spool.filFilters", "my3dworkbench.filFilters"]].forEach(([old, next]) => {
        if (localStorage.getItem(old) != null && localStorage.getItem(next) == null) {
          localStorage.setItem(next, localStorage.getItem(old));
          localStorage.removeItem(old);
        }
      });
  } catch (e) { /* private mode etc. — nothing persisted to migrate */ }
}
migrateSpoolStorageKeys();

function filSortMode() {
  try { return localStorage.getItem("my3dworkbench.filSort") || ""; } catch (e) { return ""; }
}
function rememberFilSort(mode) {
  App.state.filSort = mode;
  try {
    if (mode) localStorage.setItem("my3dworkbench.filSort", mode);
    else localStorage.removeItem("my3dworkbench.filSort");
  } catch (e) { /* private mode etc. — in-memory state alone is enough */ }
}

/* Pure: returns a sorted copy; never mutates the input.
   Blank/missing values always sort last (both directions); ties break
   by color name for a readable, stable order. */
function sortFilaments(list, mode) {
  const out = list.slice();
  const parts = (mode || "").split("_");
  const key = FIL_SORT_FIELDS[parts[0]];
  if (!key) return out;
  const mult = parts[1] === "desc" ? -1 : 1;
  const lc = (s, t) => s.localeCompare(t, undefined, { sensitivity: "base", numeric: true });
  out.sort((a, b) => {
    const av = a[key] == null ? "" : String(a[key]);
    const bv = b[key] == null ? "" : String(b[key]);
    if (av !== bv) {
      if (av === "") return 1;    // blank brand (or similar) always last
      if (bv === "") return -1;
      return mult * lc(av, bv);
    }
    return lc(String(a.color_name || ""), String(b.color_name || ""));
  });
  return out;
}
window.sortFilaments = sortFilaments;   // exposed for tests / console

/* -------------------- filters: brand / color / type / stock -------------------
   Values come from the data itself (GET /api/filaments/facets), so the
   dropdowns never offer a brand/color/type no spool actually has. State is
   remembered across reloads, exactly like the sort mode. */
const FIL_FILTER_KEYS = ["brand", "color", "type", "stock"];
const FIL_STOCK_OPTIONS = [["", "All stock"], ["in", "In stock"], ["out", "Out of stock"]];

function filFilters() {
  if (!App.state.filFilters || typeof App.state.filFilters !== "object") {
    App.state.filFilters = { brand: "", color: "", type: "", stock: "" };
  }
  return App.state.filFilters;
}
function anyFilFilterActive() {
  const f = filFilters();
  return FIL_FILTER_KEYS.some((k) => f[k]);
}
function rememberFilFilters() {
  try {
    if (anyFilFilterActive()) localStorage.setItem("my3dworkbench.filFilters", JSON.stringify(filFilters()));
    else localStorage.removeItem("my3dworkbench.filFilters");
  } catch (e) { /* private mode etc. — in-memory state alone is enough */ }
}
/* Restore persisted filters (best effort); anything not a legal value is
   dropped, and brand/color/type choices are validated against the facets
   once they load (loadFilFacets). */
function restoreFilFilters() {
  const f = filFilters();
  try {
    const raw = localStorage.getItem("my3dworkbench.filFilters");
    if (raw) {
      const saved = JSON.parse(raw);
      FIL_FILTER_KEYS.forEach((k) => {
        f[k] = (saved && typeof saved[k] === "string") ? saved[k] : "";
      });
    }
  } catch (e) { /* corrupt storage — fall back to defaults */ }
  if (f.stock && !FIL_STOCK_OPTIONS.some(([v]) => v === f.stock)) f.stock = "";
}

/* URL query string for the list endpoint: search + every active filter. */
function filListQuery(q) {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
  const f = filFilters();
  FIL_FILTER_KEYS.forEach((k) => { if (f[k]) p.set(k, f[k]); });
  const s = p.toString();
  return s ? "?" + s : "";
}
window.filListQuery = filListQuery;   // exposed for tests / console

function viewFilaments() {
  filSelectMode = false;    // fresh visit = fresh slate, even if state lingers
  filSelected.clear();
  restoreFilFilters();
  const app = $("#app");
  app.innerHTML = `
    <div class="toolbar">
      <div>
        <h1>Filaments</h1>
        <div class="view-sub">Your spool inventory — in stock and out of stock alike.</div>
      </div>
      <div class="spacer"></div>
      <button class="ghost" data-on="import">📥 Import CSV</button>
      <button class="primary" data-on="add">+ Add filament</button>
      <button class="ghost" id="fil-select-toggle" data-on="select"
              title="Select several spools and delete them in one go">☑ Select…</button>
      <div class="search-row">
        <input class="search" id="fil-search" type="search" placeholder="Search color or brand…"
               value="${esc(App.state.filSearch || "")}" autocomplete="off">
        <select class="sort" id="fil-sort" title="Sort the list (independent of the search)">
          ${FIL_SORTS.map(([v, label]) =>
            `<option value="${v}"${v === filSortMode() ? " selected" : ""}>${label}</option>`).join("")}
        </select>
        <span class="count-pill" id="fil-count">…</span>
      </div>
      <div class="filter-row">
        <label class="filter-item" title="Narrow to one brand">Brand
          <select class="filter" id="fil-f-brand"></select></label>
        <label class="filter-item" title="Narrow to one color name">Color
          <select class="filter" id="fil-f-color"></select></label>
        <label class="filter-item" title="Narrow to one material type">Type
          <select class="filter" id="fil-f-type"></select></label>
        <label class="filter-item" title="In stock (grams left) vs. out of stock">Stock
          <select class="filter" id="fil-f-stock"></select></label>
        <button class="ghost" id="fil-clear-filters" data-on="clearf" hidden>Clear filters</button>
      </div>
    </div>
    <div class="sel-bar" id="fil-selbar" hidden>
      <span class="count-pill" id="fil-selcount">0 of 0 selected</span>
      <button class="ghost" data-on="selall" title="Every filament currently in view">Select all</button>
      <button class="ghost" data-on="selclear" id="fil-selclear" hidden>Clear</button>
      <div class="spacer"></div>
      <button class="danger" data-on="seldel" id="fil-seldel" disabled>🗑 Delete 0</button>
      <button class="ghost" data-on="select">✕ Done</button>
    </div>
    <div class="grid" id="fil-grid"><div class="loading">Loading…</div></div>
  `;
  const input = $("#fil-search");
  const sels = {
    brand: $("#fil-f-brand"), color: $("#fil-f-color"),
    type: $("#fil-f-type"), stock: $("#fil-f-stock"),
  };
  const queue = () => {
    clearTimeout(filSearchTimer);
    filSearchTimer = setTimeout(() => fillFilGrid(input.value), 180);
  };
  input.addEventListener("input", queue);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; App.state.filSearch = ""; fillFilGrid(""); }
  });
  if (!document.querySelector("#modal-root .modal-backdrop")) input.focus();   // don't steal focus from an open modal
  const applyFilters = () => {
    rememberFilFilters();
    const any = anyFilFilterActive();
    $("#fil-clear-filters", app).hidden = !any;
    fillFilGrid(input.value);
  };
  for (const key of Object.keys(sels)) {
    sels[key].addEventListener("change", () => {
      filFilters()[key] = sels[key].value;
      applyFilters();
    });
  }
  const sortSel = $("#fil-sort");
  sortSel.addEventListener("change", () => {
    rememberFilSort(sortSel.value);
    if (App.state.filaments) {
      renderFilGrid(App.state.filaments, App.state.filSearch || "");   // re-sort loaded list — no re-fetch
    } else {
      fillFilGrid(input.value);
    }
  });
  wire(app, { add: () => filamentForm(),
              import: () => filamentImportModal(),
              select: () => filToggleSelectMode(),
              selall: () => {
                (App.state.filaments || []).forEach((f) => filSelected.add(f.id));
                filPaintSelection();
              },
              selclear: () => { filSelected.clear(); filPaintSelection(); },
              seldel: filBulkDelete,
              clearf: () => {
                const f = filFilters();
                FIL_FILTER_KEYS.forEach((k) => { f[k] = ""; });
                for (const key of Object.keys(sels)) sels[key].value = "";
                rememberFilFilters();
                $("#fil-clear-filters", app).hidden = true;
                fillFilGrid(input.value);
              } });
  // Stock options are fixed; brand/color/type options come from the data.
  sels.stock.innerHTML = FIL_STOCK_OPTIONS.map(([v, label]) =>
    `<option value="${v}">${esc(label)}</option>`).join("");
  sels.stock.value = filFilters().stock || "";
  $("#fil-clear-filters", app).hidden = !anyFilFilterActive();
  loadFilFacets(sels);
  fillFilGrid(App.state.filSearch || "");
}

/* Populate the brand/color/type dropdowns from GET /api/filaments/facets
   and drop any remembered choice the data no longer supports. */
async function loadFilFacets(sels) {
  let fac;
  try {
    fac = await api.get("/api/filaments/facets");
  } catch (e) {
    toast("Couldn't load filter options (" + e.message + ").", "err");
    return;
  }
  const cur = filFilters();
  const brands = fac.brands || [], colors = fac.colors || [], types = fac.types || [];
  let dropped = false;
  if (cur.brand && !brands.includes(cur.brand)) { cur.brand = ""; dropped = true; }
  if (cur.color && !colors.includes(cur.color)) { cur.color = ""; dropped = true; }
  if (cur.type && !types.includes(cur.type)) { cur.type = ""; dropped = true; }
  const options = { brand: [brands, "All brands"], color: [colors, "All colors"], type: [types, "All types"] };
  for (const key of Object.keys(options)) {
    const [values, placeholder] = options[key];
    sels[key].innerHTML = `<option value="">${esc(placeholder)}</option>` +
      values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    sels[key].value = cur[key] || "";
  }
  if (dropped) {
    // The first list fetch (fired in parallel on page load) already went out
    // with the now-dead value — re-fetch so grid and dropdowns agree.
    rememberFilFilters();
    const clearBtn = $("#fil-clear-filters");
    if (clearBtn) clearBtn.hidden = !anyFilFilterActive();
    const search = $("#fil-search");
    if (search && $("#fil-grid")) fillFilGrid(search.value);
  }
}

/* -------------------- bulk CSV import -------------------- */
function filamentImportModal() {
  const m = openModal(`
    <div class="modal-head">
      <h2>Import filaments from CSV</h2>
      <button class="icon" data-close>✕</button>
    </div>
    <p style="margin:0 0 4px; font-size:14px">
      One filament per row. Recognized headers: <b>Color</b>*, <b>Type</b>*,
      <b>Brand</b>, <b>Spool weight (g)</b>*, <b>Cost per spool</b>*, <b>Stock (g)</b>,
      <b>Purchase link</b>, <b>Notes</b> — <b>*</b> required.
      Other spellings are fine too (e.g. “Name”, “Material”, “Price”, “Weight (g)”).
    </p>
    <p class="hint" style="margin-top:0">
      Imports are <b>additive</b> — they never modify or delete existing filaments, and
      rows with problems are skipped with an explanation instead of failing the whole file.
    </p>
    <p style="margin:2px 0 10px">
      <a href="/api/filaments/import/template" target="_blank" rel="noopener">⬇ Download a CSV template</a>
    </p>
    <div class="file-drop" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <input type="file" id="imp-file" accept=".csv,text/csv,text/comma-separated-values" hidden>
      <button class="ghost" data-on="browse">Choose a CSV file…</button>
      <span class="count-pill" id="imp-filename">no file selected</span>
    </div>
    <div id="imp-result"></div>
    <div class="modal-foot">
      <button class="ghost" data-close>Close</button>
      <button class="primary" data-on="run" id="imp-run">Import CSV</button>
    </div>
  `, true);

  const fileInput = $("#imp-file", m);
  const runBtn = $("#imp-run", m);
  const pill = $("#imp-filename", m);

  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    pill.textContent = f ? `${f.name} (${(f.size / 1024).toFixed(1)} kB)` : "no file selected";
    $("#imp-result", m).innerHTML = "";
  });

  wire(m, {
    browse: () => fileInput.click(),
    run: async () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) { toast("Choose a CSV file first.", "err"); return; }
      runBtn.disabled = true;
      runBtn.textContent = "Importing…";
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await api.post("/api/filaments/import", fd);
        renderImportResult(m, res, true);
        viewFilaments();                // refresh the list AND the filter options behind the modal
      } catch (e) {
        renderImportResult(m, (e.payload || {}), false);
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = "Import CSV";
      }
    },
  });
}

function renderImportResult(m, data, success) {
  const el = $("#imp-result", m);
  const imported = data.imported || 0;
  const skipCount = (data.skipped || []).length;
  const unknown = (data.unknown_headers || []);
  let html = "";
  if (success) {
    if (skipCount) {
      html += `<div class="imp-summary warn">Imported ${imported} filament${imported === 1 ? "" : "s"} —
        ${skipCount} row${skipCount === 1 ? "" : "s"} skipped.</div>`;
    } else {
      html += `<div class="imp-summary ok">Imported ${imported} filament${imported === 1 ? "" : "s"}. 🎉</div>`;
    }
  } else {
    html += `<div class="imp-summary err">${esc(data.error || "Import failed.")}</div>`;
  }
  if (unknown.length) {
    html += `<div class="hint" style="margin-top:8px">Unrecognized column${unknown.length === 1 ? "" : "s"} (ignored):
      ${unknown.map((u) => `“${esc(u)}”`).join(", ")}.</div>`;
  }
  if (skipCount) {
    html += `<div class="imp-skip"><table>
      <tr><th>Row</th><th>Problem</th></tr>
      ${(data.skipped || []).map((s) =>
        `<tr><td class="rowno">${esc(s.row)}</td><td>${esc(s.reason)}</td></tr>`).join("")}
    </table></div>`;
  }
  el.innerHTML = html;
}

async function fillFilGrid(q) {
  const grid = $("#fil-grid"), pill = $("#fil-count");
  if (!grid || !pill) return;
  const token = ++filGridToken;
  grid.innerHTML = '<div class="loading">Searching…</div>';
  let list;
  try {
    list = await api.get("/api/filaments" + filListQuery(q));
  } catch (err) {
    if (token === filGridToken) {
      grid.innerHTML = emptyGrid("Couldn't load filaments — " + err.message);
      pill.textContent = "—";
    }
    return;
  }
  if (token !== filGridToken) return;   // a newer search is in flight — discard this
  App.state.filSearch = q;
  App.state.filaments = list;
  renderFilGrid(list, q);
}

/* Render + sort the given list (assumes #fil-grid / #fil-count exist).
   Called after every fetch, and directly on sort changes. */
function renderFilGrid(list, q) {
  const grid = $("#fil-grid"), pill = $("#fil-count");
  if (!grid || !pill) return;
  const shown = sortFilaments(list, filSortMode());
  pill.textContent = q ? `${shown.length} match${shown.length === 1 ? "" : "es"}` : `${shown.length} spools`;
  grid.innerHTML = shown.map(filCard).join("")
    || emptyGrid(filEmptyMsg(q));
  grid.querySelectorAll(".card").forEach(wireFilCard);
}

/* What “empty” means depends on why it's empty: a search, the filters,
   or a genuinely fresh database. */
function filEmptyMsg(q) {
  if (q) return `No filaments match “${esc(q)}”${anyFilFilterActive() ? " and your filters" : ""}.`;
  if (anyFilFilterActive()) return "No filaments match the current filters.";
  return "No filaments yet — add your first spool.";
}

function filCard(f) {
  const picked = filSelected.has(f.id);
  const selectAttrs = filSelectMode
    ? ` role="checkbox" tabindex="0" aria-checked="${picked ? "true" : "false"}" aria-label="Select ${esc(f.color_name)}"`
    : "";
  return `
  <div class="card${filSelectMode && picked ? " selected" : ""}" data-id="${f.id}" data-on="open"${selectAttrs}>
    ${filSelectMode ? `<div class="fil-check" aria-hidden="true"></div>` : ""}
    ${thumbHtml(f)}
    <div class="card-body">
      <div class="card-title">${esc(f.color_name)}
        <span class="badge badge-type">${esc(f.type)}</span></div>
      <div class="card-meta">${esc(f.brand || "—")} · spool ${esc(grams(f.spool_weight_g))}<br>
        ${money(f.cost_per_kg)}/kg · ${money(f.cost_per_spool)}/spool</div>
      ${stockBadge(f)}
      <div class="card-actions">
        <button class="ghost icon" data-on="open" title="Details">👁</button>
        <button class="ghost icon" data-on="edit" title="Edit">✏️</button>
        <button class="ghost icon" data-on="del" title="Delete">🗑</button>
      </div>
    </div>
  </div>`;
}
function emptyGrid(msg) {
  return `<div style="grid-column:1/-1" class="loading">${esc(msg)}</div>`;
}

/* Card-level wiring: stop so the action buttons don't bubble into "open" */
function wireFilCard(el) {
  if (filSelectMode) {
    // Keyboard parity for the checkbox role (card is focusable in select mode).
    el.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") { e.preventDefault(); filToggleCardSelection(el.dataset.id); }
    });
  }
  el.addEventListener("click", (e) => {
    if (filSelectMode) { filToggleCardSelection(el.dataset.id); return; }   // one click = pick/drop
    const target = e.target.closest("[data-on]");
    if (!target) return;
    const id = el.dataset.id;
    const action = target.dataset.on;
    if (action === "del") {
      if (!confirm("Delete this filament? Models linked to it will keep their other filaments.")) return;
      api.del("/api/filaments/" + id).then(() => { toast("Filament deleted."); viewFilaments(); })
        .catch((e) => toast(e.message, "err"));
    } else if (action === "edit") {
      api.get("/api/filaments/" + id).then((f) => filamentForm(f)).catch((e) => toast(e.message, "err"));
    } else {
      api.get("/api/filaments/" + id).then(filamentDetail).catch((e) => toast(e.message, "err"));
    }
  });
}

/* -------------------- select mode: toggle, paint, bulk delete -------------------- */

function filToggleSelectMode() {
  filSelectMode = !filSelectMode;
  if (!filSelectMode) filSelected.clear();
  filPaintSelection();
}

function filToggleCardSelection(id) {
  const key = Number(id);
  if (filSelected.has(key)) filSelected.delete(key);
  else filSelected.add(key);
  const el = $(`.card[data-id="${key}"]`);
  if (el) {
    el.classList.toggle("selected", filSelected.has(key));
    if (el.getAttribute("role") === "checkbox") el.setAttribute("aria-checked", filSelected.has(key) ? "true" : "false");
  }
  paintFilSelBar();
}

/* Re-render the grid so checkboxes appear/disappear with the mode, and sync
   the toggle button, the selection bar, and every card's selected state.
   (Selection survives sort/filter re-renders — filCard reads filSelected.) */
function filPaintSelection() {
  const grid = $("#fil-grid");
  if (grid) {
    grid.classList.toggle("cards-select", filSelectMode);
    renderFilGrid(App.state.filaments || [], App.state.filSearch || "");
  }
  const tog = $("#fil-select-toggle");
  if (tog) {
    tog.textContent = filSelectMode ? "✕ Exit select" : "☑ Select…";
    tog.classList.toggle("primary", filSelectMode);
  }
  const bar = $("#fil-selbar");
  if (bar) bar.hidden = !filSelectMode;
  paintFilSelBar();
}

function paintFilSelBar() {
  const bar = $("#fil-selbar");
  if (!bar) return;
  const n = filSelected.size;
  const total = (App.state.filaments || []).length;
  $("#fil-selcount", bar).textContent = `${n} of ${total} selected`;
  const del = $("#fil-seldel", bar);
  del.disabled = n === 0;
  del.textContent = `🗑 Delete ${n}`;
  $("#fil-selclear", bar).hidden = n === 0;
}

async function filBulkDelete() {
  const ids = [...filSelected];
  if (!ids.length) { toast("Pick at least one filament first.", "err"); return; }
  if (!confirm(`Delete ${ids.length} filament${ids.length === 1 ? "" : "s"}? `
    + "Models that use them keep their remaining filaments. This can't be undone.")) return;
  const btn = $("#fil-seldel");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Deleting…";
  try {
    const res = await api.del("/api/filaments/bulk", { ids });
    const n = (res && Number.isInteger(res.deleted)) ? res.deleted : ids.length;
    toast(`Deleted ${n} filament${n === 1 ? "" : "s"}.`);
    filSelectMode = false;      // viewFilaments() below re-renders and resets the UI
    filSelected.clear();
    viewFilaments();
  } catch (e) {
    toast(e.message, "err");
    btn.disabled = false;
    btn.textContent = label;
  }
}

/* -------------------- detail view -------------------- */
function filamentDetail(f) {
  const m = openModal(`
    <div class="modal-head">
      <h2>${esc(f.color_name)} <span class="badge badge-type">${esc(f.type)}</span></h2>
      <button class="icon" data-close>✕</button>
    </div>
    <div class="detail-hero">
      ${f.picture_url ? `<img src="${esc(f.picture_url)}" alt="">` : `<div class="ph">🧵</div>`}
      <dl class="kv" style="flex:1">
        <dt>Brand</dt><dd>${esc(f.brand || "—")}</dd>
        <dt>Spool weight</dt><dd>${esc(grams(f.spool_weight_g))}</dd>
        <dt>Cost</dt><dd>${money(f.cost_per_spool)} / spool = <b>${money(f.cost_per_kg)} / kg</b></dd>
        <dt>Stock</dt><dd>${stockBadge(f)}</dd>
        <dt>Purchase link</dt><dd>${f.purchase_link ? `<a href="${esc(f.purchase_link)}" target="_blank" rel="noopener">${esc(f.purchase_link)}</a>` : "—"}</dd>
        ${f.notes ? `<dt>Notes</dt><dd>${esc(f.notes)}</dd>` : ""}
      </dl>
    </div>

    <div class="section">
      <h3>Related models (${f.related_models.length})</h3>
      <div class="mini-list">
        ${f.related_models.map((m) => `
          <div class="mini-row">
            <span><a href="#/models" data-on="gomodel" data-id="${m.id}">${esc(m.name)}</a>
              ${purposeOfModel(m)}
              ${m.allocated_grams ? ` · ${esc(grams(m.allocated_grams))} of this filament` : ""}</span>
            <span style="color:var(--muted)">${esc(hours(m.print_time_hr))} print</span>
          </div>`).join("") || `<div class="loading" style="padding:14px 0">No models use this filament yet.</div>`}
      </div>
    </div>

    <div class="modal-foot">
      <button data-close>Close</button>
      <button class="primary" data-on="edit">Edit</button>
      <button class="danger" data-on="del">Delete</button>
    </div>
  `, true);

  wire(m, {
    gomodel: (e, el) => { e.preventDefault(); closeModal(); location.hash = "#/models/" + el.dataset.id; },
    edit: () => { closeModal(); filamentForm(f); },
    del: () => {
      if (!confirm("Delete this filament?")) return;
      api.del("/api/filaments/" + f.id).then(() => {
        toast("Filament deleted."); closeModal(); viewFilaments();
      }).catch((e) => toast(e.message, "err"));
    },
  });
}

/* -------------------- create / edit form -------------------- */
function filamentForm(existing) {
  const f = existing || {};
  const m = openModal(`
    <div class="modal-head">
      <h2>${f.id ? "Edit filament" : "Add filament"}</h2>
      <button class="icon" data-close>✕</button>
    </div>
    <form class="form" id="fil-form">
      <div class="form-grid">
        <div class="field"><label>Color name *</label>
          <input name="color_name" required maxlength="100" value="${esc(f.color_name || "")}" placeholder="e.g. Cobalt Blue"></div>
        <div class="field"><label>Type *</label>
          <input name="type" list="fil-types" required maxlength="50" value="${esc(f.type || "")}" placeholder="PLA">
          <datalist id="fil-types">${FILAMENT_TYPES.map((t) => `<option value="${t}">`).join("")}</datalist></div>
        <div class="field"><label>Brand</label>
          <input name="brand" maxlength="200" value="${esc(f.brand || "")}"></div>
        <div class="field"><label>Purchase link</label>
          <input name="purchase_link" type="url" maxlength="500" value="${esc(f.purchase_link || "")}" placeholder="https://…"></div>
        <div class="field"><label>Spool weight (g) *</label>
          <input name="spool_weight_g" type="number" min="1" step="1" required value="${f.spool_weight_g ?? 1000}"></div>
        <div class="field"><label>Cost per spool *</label>
          <input name="cost_per_spool" type="number" min="0" step="0.01" required value="${f.cost_per_spool ?? ""}"></div>
        <div class="field"><label>Current stock (g)</label>
          <input name="current_stock_g" type="number" min="0" step="1" value="${f.current_stock_g ?? 0}">
          <span class="hint">0 = out of stock</span></div>
        <div class="field"><label>Picture</label>
          <input name="picture" type="file" accept="image/*">
          <span class="hint">${f.picture_url ? "Currently on file." : "PNG / JPEG / GIF / WEBP, ≤ 10 MB"}</span></div>
      </div>
      <div class="field"><label>Notes</label>
        <textarea name="notes" maxlength="2000" rows="2">${esc(f.notes || "")}</textarea></div>
      <div class="modal-foot" style="display:flex;justify-content:flex-end;gap:8px;margin-top:4px">
        <button type="button" data-close>Cancel</button>
        <button type="submit" class="primary">${f.id ? "Save changes" : "Add filament"}</button>
      </div>
    </form>
  `);

  $("#fil-form", m).addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);              // includes the picture file
    const url = f.id ? `/api/filaments/${f.id}` : "/api/filaments";
    const promise = f.id
      ? api.put(url, fd)                              // FormData PUT
      : api.post(url, fd);
    promise.then(() => {
      toast(f.id ? "Filament updated." : "Filament added.");
      closeModal();
      viewFilaments();
    }).catch((err) => toast(err.message, "err"));
  });
}
