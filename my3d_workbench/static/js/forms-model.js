/* My 3D Workbench — forms-model.js: create/edit model form (multi-filament selection). */
"use strict";

async function modelForm(existing) {
  const m = existing || {};
  const filaments = await api.get("/api/filaments");   // always fresh
  App.state.filaments = filaments;

  const picked = new Set((m.filaments || []).map((f) => f.id));
  const gramsOf = (id) => {
    const f = (m.filaments || []).find((x) => x.id === id);
    return f && f.grams != null ? String(f.grams) : "";
  };

  const modal = openModal(`
    <div class="modal-head">
      <h2>${m.id ? "Edit model" : "Add model"}</h2>
      <button class="icon" data-close>✕</button>
    </div>
    <form class="form" id="model-form">
      <div class="form-grid">
        <div class="field"><label>Name *</label>
          <input name="name" required maxlength="200" value="${esc(m.name || "")}" placeholder="e.g. Hex wrench set"></div>
        <div class="field"><label>Purpose</label>
          <select name="purpose">
            <option value="Personal" ${m.purpose === "Personal" ? "selected" : ""}>Personal</option>
            <option value="Profit" ${m.purpose === "Profit" ? "selected" : ""}>Profit</option>
          </select></div>
        <div class="field"><label>Filament needed (g) *</label>
          <input name="filament_amount_g" type="number" min="0" step="1" required value="${m.filament_amount_g ?? ""}">
          <span class="hint">Total across all selected filaments</span></div>
        <div class="field"><label>Print time (hr) *</label>
          <input name="print_time_hr" type="number" min="0" step="0.01" required value="${m.print_time_hr ?? ""}"></div>
        <div class="field"><label>Labor (min) *</label>
          <input name="labor_min" type="number" min="0" step="1" required value="${m.labor_min ?? 0}"></div>
        <div class="field"><label>Picture</label>
          <input name="picture" type="file" accept="image/*">
          <span class="hint">${m.picture_url ? "Currently on file." : "PNG / JPEG / GIF / WEBP, ≤ 10 MB"}</span></div>
      </div>

      <div class="field"><label>Description</label>
        <textarea name="description" maxlength="2000" rows="2" placeholder="What it is, design notes, source file location…">${esc(m.description || "")}</textarea></div>

      <div class="field">
        <label>Filaments required</label>
        <div class="fil-picker" id="fil-picker">
          ${filaments.length ? filaments.map((f) => `
            <div class="fil-opt ${picked.has(f.id) ? "picked" : ""}">
              <label>
                <input type="checkbox" class="fil-check" value="${f.id}" ${picked.has(f.id) ? "checked" : ""}>
                <span>${esc(f.color_name)} <b>${esc(f.type)}</b>
                  <span class="dot ${f.in_stock ? "in" : "out"}" title="${f.in_stock ? "in stock" : "out of stock"}"></span></span>
                <span class="fil-price">${money(f.cost_per_kg)}/kg</span>
              </label>
              <input class="g-input" type="number" min="0" step="0.1" placeholder="g (even)"
                     data-for="${f.id}" value="${picked.has(f.id) ? gramsOf(f.id) : ""}">
            </div>`).join("")
            : `<div class="loading" style="padding:10px">No filaments in inventory — add some in the Filaments tab first.</div>`}
        </div>
        <span class="hint">Out-of-stock filaments are still selectable. Unticked gram fields are split evenly across the checked filaments when costing.</span>
      </div>

      <div class="field">
        <label>Materials <span class="hint">(optional line items — dowels, magnets, shipping boxes…; up to 50 rows)</span></label>
        <div class="mat-rows" id="mat-rows"></div>
        <div style="display:flex;gap:10px;align-items:center;margin-top:6px">
          <button type="button" class="ghost" id="mat-add">+ Add material</button>
          <span class="hint" id="mat-subtotal"></span>
        </div>
      </div>

      <div class="form-grid">
        <div class="field"><label>Notes</label>
          <textarea name="notes" maxlength="2000" rows="2">${esc(m.notes || "")}</textarea></div>
      </div>

      <div class="modal-foot" style="display:flex;justify-content:flex-end;gap:8px">
        <button type="button" data-close>Cancel</button>
        <button type="submit" class="primary">${m.id ? "Save changes" : "Create model"}</button>
      </div>
    </form>
  `, true);

  /* show/hide the gram input as filaments are checked/unchecked */
  modal.querySelectorAll(".fil-check").forEach((c) => c.addEventListener("change", () => {
    c.closest(".fil-opt").classList.toggle("picked", c.checked);
  }));

  /* --- materials line items (dowels, magnets, boxes…) --- */
  const matRows = (m.materials || []).slice().map((x) => ({
    description: x.description || "",
    quantity: (x.quantity != null) ? Number(x.quantity) : 1,
    unit_cost: (x.unit_cost != null) ? Number(x.unit_cost) : 0,
  }));
  const matBox = $("#mat-rows", modal), matSub = $("#mat-subtotal", modal);
  const matTotalOf = (r) => r.quantity * r.unit_cost;
  const refreshSubtotal = () => {
    const sub = matRows.reduce((s, r) => s + matTotalOf(r), 0);
    matSub.textContent = sub > 0
      ? `+ ${money(sub)} per model — included in total cost & suggested prices`
      : "";
  };
  const renderMat = () => {
    matBox.innerHTML = matRows.length ? matRows.map((r, i) => `
      <div class="mat-row" data-i="${i}">
        <input class="mat-desc" maxlength="200" placeholder="e.g. wood dowels" value="${esc(r.description)}">
        <input class="mat-qty" type="number" min="0" step="any" value="${r.quantity}" title="Quantity">
        <input class="mat-unit" type="number" min="0" step="0.01" value="${r.unit_cost}" title="Cost per unit">
        <span class="mat-total">${money(matTotalOf(r))}</span>
        <button type="button" class="ghost icon mat-x" title="Remove this material">✕</button>
      </div>`).join("")
      : `<div class="loading" style="padding:8px 0">No extra materials — printing cost only.</div>`;
    const addBtn = $("#mat-add", modal);
    addBtn.disabled = matRows.length >= 50;
    addBtn.title = addBtn.disabled ? "Maximum 50 material rows" : "";
    refreshSubtotal();
  };
  renderMat();

  $("#mat-add", modal).addEventListener("click", () => {
    if (matRows.length >= 50) { toast("Maximum 50 material rows per model.", "err"); return; }
    matRows.push({ description: "", quantity: 1, unit_cost: 0 });
    renderMat();
    const descs = matBox.querySelectorAll(".mat-desc");
    if (descs.length) descs[descs.length - 1].focus();
  });
  matBox.addEventListener("input", (e) => {
    const rowEl = e.target.closest(".mat-row");
    if (!rowEl) return;
    const i = Number(rowEl.dataset.i);
    const qty = parseFloat(rowEl.querySelector(".mat-qty").value);
    const unit = parseFloat(rowEl.querySelector(".mat-unit").value);
    matRows[i].description = rowEl.querySelector(".mat-desc").value;
    matRows[i].quantity = (isFinite(qty) && qty >= 0) ? qty : 0;
    matRows[i].unit_cost = (isFinite(unit) && unit >= 0) ? unit : 0;
    rowEl.querySelector(".mat-total").textContent = money(matTotalOf(matRows[i]));
    refreshSubtotal();
  });
  matBox.addEventListener("click", (e) => {
    const btn = e.target.closest(".mat-x");
    if (!btn) return;
    matRows.splice(Number(btn.closest(".mat-row").dataset.i), 1);
    renderMat();
  });

  /* --- submit --- */
  $("#model-form", modal).addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.target;
    const fd = new FormData(form);

    // filament associations — only checked filaments, only positive grams
    const pickedIds = [...modal.querySelectorAll(".fil-check:checked")].map((c) => Number(c.value));
    const gramsMap = {};
    modal.querySelectorAll(".fil-check:checked").forEach((c) => {
      const inp = modal.querySelector(`.g-input[data-for="${c.value}"]`);
      const v = inp ? parseFloat(inp.value) : NaN;
      if (isFinite(v) && v > 0) gramsMap[c.value] = v;
    });

    fd.set("filament_ids", pickedIds.join(","));
    fd.set("filament_grams", JSON.stringify(gramsMap));

    // materials — drop blank rows, keep order; "[]" means "no extras" (full replace)
    const mats = matRows
      .filter((r) => r.description.trim() !== "")
      .map((r) => ({ description: r.description.trim(), quantity: r.quantity, unit_cost: r.unit_cost }));
    fd.set("materials", JSON.stringify(mats));

    const url = m.id ? `/api/models/${m.id}` : "/api/models";
    const promise = m.id ? api.put(url, fd) : api.post(url, fd);
    promise.then(() => {
      toast(m.id ? "Model updated." : "Model created.");
      closeModal();
      viewModels();
    }).catch((err) => toast(err.message, "err"));
  });
}
