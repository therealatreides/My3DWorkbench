/* My 3D Workbench — views-settings.js: global cost defaults + derived machine-rate panel. */
"use strict";

let settingsData = null;

async function viewSettings() {
  flashLoading();
  const data = await api.get("/api/settings");
  settingsData = data;
  // Keep the printer-form's "global: …" hints in sync with this tab (the
  // form caches this list in App.state to avoid a second fetch).
  App.state.settings = data.settings || [];
  renderSettings(data);
}

function renderSettings(data) {
  const rate = data.machine_rate;
  const curRow = data.settings.find((s) => s.key === "currency_code");
  const cur = (curRow && curRow.value) || "USD";
  const app = $("#app");
  app.innerHTML = `
    <div class="toolbar">
      <div>
        <h1>Settings</h1>
        <div class="view-sub">Global assumptions used by the cost engine — seeded from the
          “Adv. Inputs” sheet. Individual printers can override any of these on the <a href="#/printers">Printers</a> tab.</div>
      </div>
      <div class="spacer"></div>
      <button class="primary" data-on="save">Save changes</button>
    </div>

    <div class="rate-card">
      <div class="rate-line">
        <span>Current machine rate:</span>
        <span class="rate-big">${money(rate.rate_per_hr, cur)} <small>per print-hour</small></span>
        <span class="badge badge-type">${rate.source === "override" ? "manual override" : "auto-computed"}</span>
      </div>
      ${rate.source === "computed" && rate.components ? `
      <div class="rate-comps">
        <span class="chip">investment ${money(rate.components.total_investment, cur)}</span>
        <span class="chip">lifetime cost ${money(rate.components.lifetime_cost, cur)}</span>
        <span class="chip">uptime ${Math.round(rate.components.uptime_hours_per_year)} h/yr</span>
        <span class="chip">capital ${money(rate.components.capital_cost_per_hr, cur)}/hr</span>
        <span class="chip">electricity ${money(rate.components.electrical_cost_per_hr, cur)}/hr</span>
        <span class="chip">× buffer ${rate.components.buffer_factor}</span>
      </div>` : `<div class="rate-comps"><span class="chip">Fixed by print_time_rate_override</span></div>`}
    </div>

    <h3 style="margin-top:22px">Global Cost Defaults</h3>
    <table class="settings-table">
      <thead><tr><th>Key</th><th>Value</th><th style="width:45%">Description</th><th></th></tr></thead>
      <tbody>
        ${data.settings.map((s) => `
          <tr data-key="${esc(s.key)}">
            <td class="key">${esc(s.key)}</td>
            <td><input class="set-val" value="${esc(s.value)}" ${data.numeric_keys.includes(s.key) ? 'inputmode="decimal"' : ""}></td>
            <td class="desc">${esc(s.description)}</td>
            <td style="text-align:right"><button class="ghost icon del-set" title="Reset to default">↺</button></td>
          </tr>`).join("")}
      </tbody>
    </table>
  `;

  wire(app, { save: saveSettings });

  app.querySelectorAll(".del-set").forEach((btn) => btn.addEventListener("click", async (e) => {
    const key = e.target.closest("tr").dataset.key;
    if (!confirm(`Reset "${key}" to its factory default? Your current value will be replaced.`)) return;
    try {
      const res = await api.del("/api/settings/" + encodeURIComponent(key));
      toast(`"${key}" reset to ${res.reset_to}.`);
      viewSettings();
    }
    catch (err) { toast(err.message, "err"); }
  }));
}

async function saveSettings() {
  const changes = {};
  document.querySelectorAll(".settings-table tr[data-key]").forEach((tr) => {
    const key = tr.dataset.key;
    const val = tr.querySelector(".set-val").value;
    if (settingsData && settingsData.settings) {
      const orig = settingsData.settings.find((s) => s.key === key);
      if (orig && orig.value !== val) changes[key] = val;
    }
  });
  if (!Object.keys(changes).length) { toast("Nothing to save."); return; }
  try {
    await api.put("/api/settings", changes);
    toast("Settings saved — costs will recalculate.");
    viewSettings();
  } catch (err) { toast(err.message, "err"); }
}
