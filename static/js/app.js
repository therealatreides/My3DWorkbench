/* My 3D Workbench — app.js: hash router + bootstrap (loads last). */
"use strict";

function currentRoute() {
  const h = (location.hash || "#/filaments").replace(/^#\/?/, "");
  const [view, id] = h.split("/");
  return { view, id };
}

const VIEWS = {
  filaments: viewFilaments,
  models: viewModels,
  printers: viewPrinters,
  settings: viewSettings,
};

async function renderRoute() {
  const { view, id } = currentRoute();
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === (view || "filaments")));
  $("#app").removeAttribute("aria-busy");
  // A route always owns the background: dismiss a modal left over from the
  // previous route (back/forward, cross-links), and on detail routes render
  // the matching LIST first — so that closing the modal, refreshing on the
  // detail URL, or bouncing back via the browser lands on the matching list,
  // never on a stale (or empty) background.
  closeModal();

  const go = VIEWS[view] || viewFilaments;
  try {
    if (view === "filaments" && id) {
      viewFilaments();
      filamentDetail(await api.get("/api/filaments/" + id));
      return;
    }
    if (view === "models" && id) {
      viewModels();
      modelDetail(await api.get("/api/models/" + id));
      return;
    }
    await go();
  } catch (err) {
    const back = `${esc(view || "filaments")}`;
    $("#app").innerHTML = `<div class="loading">⚠️ ${esc(err.message)}<br>
      <button class="primary" style="margin-top:12px" onclick="location.hash='#/${back}'">Retry</button></div>`;
  }
}

window.addEventListener("hashchange", renderRoute);
window.addEventListener("DOMContentLoaded", async () => {
  if (!location.hash) history.replaceState(null, "", "#/filaments");
  // Load the display-currency setting BEFORE the first paint so prices never
  // flash with the wrong symbol. Failure just keeps the USD default.
  try {
    const d = await api.get("/api/settings");
    const row = (d.settings || []).find((s) => s.key === "currency_code");
    if (row && row.value) App.state.currency = row.value;
  } catch (e) { /* keep default */ }
  renderRoute();
});
