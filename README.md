# My 3D Workbench — 3D Prints Management

A small, local, full-stack web app for running a 3D-printing workflow:
filament spool (in stock **and** out of stock, bulk-importable
from a **CSV** file, and **bulk-deletable via a select mode** — pick any
handful and delete them in one go), a catalog
of models with the filaments they use and any **extra material line items**
(wood dowels, magnets, shipping boxes, each valued quantity × unit price and
added into the model's cost), a fleet of **printers** (each with its
own overrides of the cost settings), and a cost engine ported 1:1 from
a spreadsheet I have used for years. Filaments are searchable by
color or brand, **filterable by brand, color, type, or stock status**,
and **sortable by color, brand, or type** (A→Z / Z→A, your
choice is remembered); models are searchable by name; and any model's costs can be **previewed on
any printer** using that printer's setting overrides.

Runs on Windows, macOS, and a Raspberry Pi (Pi OS / any Linux) alike — no
internet access required at runtime, no external CDNs, no frontend
framework: plain Flask + SQLite + vanilla JS.

---

## Install & run

The only runtime dependency is **Flask** (+ `waitress`, the production WSGI
server — the server falls back to Flask's dev server if it is missing).
Requires **Python 3.10+**. The app lives **at the root of this repository**
— `run.py`, `my3d_workbench/` (the package), and `tests/` sit next to this
file — so work from whichever folder the repo cloned into (the one
containing `run.py`).

### Windows

PowerShell or Command Prompt, in the project folder (the one containing `run.py`):

```powershell
py -3 -m venv .venv                     # "py -3" = newest installed Python 3; or `python` if that is 3.10+
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

* No `py` launcher? Install Python 3.10+ from python.org (tick *Add python.exe to PATH*).
* Optional demo data (1 spool + 1 model + 1 printer): `$env:SEED_DEMO = "1"; python run.py`.
* Stop the server with `Ctrl+C`.

### macOS / Linux desktop

Terminal, in the project folder:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python run.py
```

* Homebrew Python is fine too: `brew install python@3.12`, then `python3.12 -m venv .venv`.
* Optional demo data: `SEED_DEMO=1 python run.py`.

### Raspberry Pi (4 or 5, Raspberry Pi OS 64-bit)

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip     # the venv module; no-op if already present
cd ~/My3DWorkbench    # the folder that contains run.py (here the repo clone)
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python run.py
```

* Raspberry Pi OS (bookworm and newer) ships Python 3.11, so there is nothing
  to install — the `apt` line above only guarantees `python3-venv` exists.
* Optional demo data: `SEED_DEMO=1 python run.py`.

### All platforms

Open `http://localhost:8080` — or, from another machine on the same LAN,
`http://<machine-ip>:8080` (the app binds `0.0.0.0` by default).

### Or install it as a command (wheel)

The same app can be packaged and installed once, then started by name — no
repo checkout needed at runtime:

```bash
pip wheel . -w dist                          # build once (needs hatchling, fetched by pip)
pip install dist/my3dworkbench-*.whl
my3dworkbench                                # starts the server on :8080
```

(`pipx install dist/my3dworkbench-*.whl` works too. Same env vars, same
data dir — the CLI and the repo checkout share one profile per machine.)

### Releases (prebundled, no Python needed)

Each `vX.Y.Z` tag pushes a **GitHub Actions** build that bundles the app
(PyInstaller) per platform and posts these to **GitHub → Releases**:

| File | For |
|------|-----|
| `My3DWorkbench-X.Y.Z-windows-x86_64.exe` | Windows — run it; stop with `Ctrl+C` in the console window |
| `My3DWorkbench-X.Y.Z-macos-arm64.tar.gz` | macOS / Apple Silicon (Intel Macs: use the wheel above) |
| `My3DWorkbench-X.Y.Z-linux-x86_64.tar.gz` | Linux (incl. x86-64 Pis) |
| `my3dworkbench-X.Y.Z-py3-none-any.whl` | `pip`/`pipx` on any platform |

Same env vars on every flavor (`MY3DWORKBENCH_*`, see below), same data dir —
an exe and a pip install on one machine read the same data. macOS binaries
are not code-signed: Gatekeeper may block a first double-click (right-click →
Open, or run it in a terminal — it's a local server, not a GUI app).

**Cutting a release:** bump `version` in `pyproject.toml`, commit, then
`git tag vX.Y.Z && git push --tags` — the workflow gates on the full test
suite, then builds, smoke-tests, and publishes the assets.

### Security posture (read before exposing beyond a trusted LAN)

* **There is no authentication or per-user accounts** — by design, it is a tool
  for a trusted local network. Anyone who can reach the port can read and write
  data. Do not port-forward it to the internet as-is.
* The SQLite file and `uploads/` contain everything — treat them as sensitive.
* All SQL is parameterized, uploads are renamed to random UUIDs and served from
  a directory-scoped endpoint, and all user text is escaped in the UI. Stored
  web links (purchase links) are restricted to `http(s)` at the API boundary,
  so a saved `javascript:`/`data:` value cannot become a stored-XSS vector in
  the clickable link.
* Validation **rejects, it never coerces**: a boolean where a number is
  expected, a non-whole float or out-of-range value in an id, or a value past
  the explicit caps is a `400` with the field named — never a silent fix.
* Every API failure, including unexpected 500s, uses the same
  `{"error": "..."}` JSON envelope, and money values are capped so responses
  stay valid JSON (no raw `Infinity` tokens).
* The API additionally accepts JSON as well as form data, so simple scripts can
  drive it (e.g. `curl -X POST -H 'Content-Type: application/json' …`).

### Environment variables

| Variable              | Default                              | Purpose                          |
|-----------------------|--------------------------------------|----------------------------------|
| `MY3DWORKBENCH_HOST`  | `0.0.0.0`                            | bind address                     |
| `MY3DWORKBENCH_PORT`  | `8080`                               | port                             |
| `MY3DWORKBENCH_HOME`  | OS app-data dir + `My3DWorkbench`    | data folder (DB + uploads)       |
| `MY3DWORKBENCH_DB`    | `<data dir>/My3DWorkbench.db`        | SQLite file location             |
| `MY3DWORKBENCH_UPLOADS` | `<data dir>/uploads`              | uploaded pictures                |
| `MY3DWORKBENCH_LOG`     | unset                              | append all server output to this file — **required when running without a console** (scheduled tasks, launchd, systemd)    |
| `SEED_DEMO`           | unset                                | `1` = also load sample data      |

The data folder default is per-OS (all overridable — any folder works):

* **Windows:** `%LOCALAPPDATA%\My3DWorkbench`
* **macOS:** `~/Library/Application Support/My3DWorkbench`
* **Linux / Pi:** `$XDG_DATA_HOME/My3DWorkbench` (default `~/.local/share/My3DWorkbench`)

So a dev checkout, a wheel install, and a bundled binary on the same machine
all share one profile. (Before the rename, data lived in the project folder
itself — `spool.db` + `uploads/`; copy those into the data dir to migrate, or
point `MY3DWORKBENCH_DB` / `MY3DWORKBENCH_UPLOADS` at the old folder to use
it in place.)

### Run on startup / as a service (no person in front of a console)

The launcher is console-safe either way: set `MY3DWORKBENCH_LOG` and *all*
server output goes to that file (line-buffered, appended, UTF-8) instead of
crashing on the missing console. Every recipe below is just "start the same
process + log file + a service manager that restarts it".

**Windows — Task Scheduler** (either works; pick by what you want to see):

* *Run whether user is logged on or not* (true background service):
  ```
  program:  C:\...\my3dworkbench.exe          (or C:\...\python run.py in your venv)
  env vars: MY3DWORKBENCH_LOG=C:\Logs\My3DWorkbench\server.log   ← required here
  trigger : At startup  (check “Run as soon as possible after a scheduled start is missed”)
  ```
* *Run only when user is logged on*: a normal console window opens at login —
  then `MY3DWORKBENCH_LOG` is optional and `Ctrl+C` in the window stops it.

**macOS — launchd (LaunchAgent, per user):**
`~/Library/LaunchAgents/io.my3dworkbench.server.plist`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.my3dworkbench.server</string>
  <key>ProgramArguments</key>
  <array><string>/path/to/my3dworkbench</string></array>  <!-- or: python + path/to/run.py -->
  <key>EnvironmentVariables</key><dict>
    <key>MY3DWORKBENCH_LOG</key><string>~/Library/Logs/My3DWorkbench/server.log</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict></plist>
```
```bash
mkdir -p ~/Library/Logs/My3DWorkbench
launchctl load ~/Library/LaunchAgents/io.my3dworkbench.server.plist   # `launchctl unload` to stop
```
(`launchd` resolves `~` in paths; a system-wide daemon wants `LaunchDaemons` + a
real user for the data dir — for a personal LAN server, the LaunchAgent above
is the right size.)

**Linux / Pi — systemd user service (recommended)**:
`~/.config/systemd/user/my3dworkbench.service`
```ini
[Unit]
Description=My 3D Workbench (user service)

[Service]
ExecStart=/home/pi/My3DWorkbench/.venv/bin/python /home/pi/My3DWorkbench/run.py
Environment=MY3DWORKBENCH_LOG=/home/pi/My3DWorkbench/logs/server.log
Restart=on-failure

[Install]
WantedBy=default.target
```
```bash
mkdir -p ~/My3DWorkbench/logs
systemctl --user enable --now my3dworkbench
journalctl --user -u my3dworkbench     # or: cat ~/My3DWorkbench/logs/server.log
```
A *system* unit also works (see next section, Pi setup) but user units are
simpler and keep data owned by the user.

---

## Project layout

```
.                          # project root (the folder that contains run.py)
├── run.py                     # dev entrypoint (waitress → falls back to Flask dev server)
├── requirements.txt
├── pyproject.toml             # wheel build + `my3dworkbench` CLI entry point
├── my3d_workbench/            # the package (what a wheel installs)
│   ├── __init__.py            # Flask application factory + error handlers + data-dir defaults
│   ├── __main__.py            # `my3dworkbench` / `python -m my3d_workbench` launcher
│   ├── db.py                  # schema DDL, settings seed, per-request connections
│   ├── cost.py                # COST ENGINE — pure functions, no Flask/DB imports
│   ├── api_helpers.py         # request parsing, uploads, serializers
│   ├── errors.py              # APIError → {"error": "..."} JSON
│   ├── routes/
│   │   ├── core.py            # / (UI), /uploads/*, /api/health
│   │   ├── filaments.py       # /api/filaments CRUD + related models + bulk delete (+ CSV import)
│   │   ├── models.py          # /api/models CRUD + associations + material line items + cost breakdown (+ ?printer=)
│   │   ├── printers.py        # /api/printers CRUD + per-printer cost overrides
│   │   └── settings.py        # global cost defaults (fixed registry) + derived machine rate
│   ├── seed_demo.py           # optional sample data
│   ├── templates/index.html   # single-page shell
│   └── static/
│       ├── css/app.css            # all styling (no dependencies)
│       └── js/
│           ├── core.js            # API client, toasts, modals, formatters
│           ├── views-filaments.js # filament list / detail / form
│           ├── views-models.js    # model list / cost-breakdown detail / printer preview
│           ├── views-printers.js  # printer fleet list / form (per-printer overrides)
│           ├── forms-model.js     # model form (multi-filament selection)
│           ├── views-settings.js  # global cost defaults table + machine-rate panel
│           └── app.js             # hash router + bootstrap
```

**Extensibility:** `my3d_workbench/cost.py` is the *only* place that knows
the pricing math, and it only takes/returns plain dicts — unit-test it without
any app context. Any new cost rule goes there. New API resources are a new
file in `my3d_workbench/routes/` registered in `my3d_workbench/__init__.py`.

---

## Database schema (SQLite)

| Table             | Purpose |
|-------------------|---------|
| `filaments`       | color, type, brand, purchase link, spool weight (g), cost per spool, **`current_stock_g`** (0 = out of stock), picture path, notes |
| `models`          | name, purpose (`Personal`/`Profit`), **`filament_amount_g`**, **`print_time_hr`**, **`labor_min`**, picture, description, notes |
| `model_filaments` | many-to-many model ↔ filament. Optional **`grams`** column pins exactly how much of that filament the model uses; when `NULL` the model's total grams are split *evenly* across its filaments at costing time |
| `settings`        | the fixed **global cost defaults** — the 11 cost keys from `Adv. Inputs` + `currency_code`. This is the complete registry the cost engine reads; no other keys are accepted |
| `printers`        | the machine fleet: `manufacturer`, `model_name`, `bed_size` (free text), `notes` |
| `printer_settings`| per-printer **overrides** of the cost settings (same keys as `settings`); a row present means "use this value for this printer", and deleting a row puts the printer back on the global value |

Foreign keys use `ON DELETE CASCADE`; `PRAGMA journal_mode=WAL` enables
concurrent reads while a write happens.

---

## Cost engine

All logic lives in `my3d_workbench/cost.py`.

**Machine rate** (computed live from `settings`):

```
total_investment    = printer_cost + additional_upfront_cost
lifetime_cost       = total_investment + annual_maintenance × life_years
uptime_hours        = 8760 × uptime_fraction
capital_per_hr      = lifetime_cost / (uptime_hours × life_years)
electrical_per_hr   = (watts / 1000) × electricity_rate
print_time_rate     = (capital_per_hr + electrical_per_hr) × buffer_factor
```

Defaults (499 + 100, 75/yr, 3 yr, 50% uptime, 150 W, $0.10643/kWh, ×1.3)
→ **≈ $0.10 per print-hour**. A `print_time_rate_override` setting can pin the
rate manually (sheet cell C5).

**Per-printer settings:** each printer may override any of these keys
(`printer_settings`; blank in the form = use the global value). Cost
calculation merges `global ← printer overrides` (`cost.merge_settings()`), so
the same engine serves both the base price and any "what would this cost on
that printer?" preview. The list of overridable keys is `COST_SETTING_KEYS` in
`cost.py` — the single source of truth shared by Settings validation and the
Printers routes.

**Per-model cost** (from the `Calculation Sheet`):

```
part material   = Σ_filament (grams_f / 1000) × cost_per_kg × efficiency_factor
labor cost      = (labor_min / 60) × labor_hourly_rate
machine cost    = print_time_hr × print_time_rate
extra materials = Σ_line item (quantity × unit_cost)   ← dowels, magnets, boxes
TOTAL cost      = part material + labor cost + machine cost + extra materials

Suggested price (50/60/70%) — computed on the TOTAL, so material line items
inflate the suggested price as well.
  price = TOTAL / (1 − margin)      (sheet G52–G56)
```

(Packaging/shipping are handled as ordinary material line items — e.g. a
"shipping box" row with its quantity and unit price — rather than a separate
"landed price" concept.)
`cost_per_kg` is derived, not stored: `cost_per_spool ÷ (spool_weight_g/1000)`.

Everything recalculates instantly when you edit a filament, a model, or a
setting — nothing in the pricing math is denormalized.

---

## REST API

All mutations accept **JSON or `multipart/form-data`** (the latter lets the
UI send a picture file plus fields in one request).

### Filaments
| Method | Path | Notes |
|---|---|---|
| GET | `/api/filaments` | all spools + derived `cost_per_kg`, `in_stock`; combinable filters (all case-insensitive): `q=` free text on **color name OR brand**, `color=` / `brand=` / `type=` exact match, `stock=` `in` \| `out` |
| GET | `/api/filaments/facets` | distinct brand / color / type values present in the data (feeds the UI filter dropdowns) |
| POST | `/api/filaments` | create (fields: `color_name`, `type`, `brand`, `purchase_link` (an `http(s)://` link or blank — anything else is a 400), `spool_weight_g` (1 … 1 trillion g), `cost_per_spool` (≤ 1 trillion), `current_stock_g`, `notes`, optional file `picture`) |
| GET | `/api/filaments/<id>` | detail **including `related_models`** (cross-reference view) |
| PUT | `/api/filaments/<id>` | update |
| DELETE | `/api/filaments/<id>` | deletes; model links cascade away |
| DELETE | `/api/filaments/bulk` | **bulk delete** (the UI's select mode) — JSON `{"ids": [1, 2, …]}` or a bare list; unknown / junk / boolean values are skipped without failing the request, at most 1000 ids per request; model links cascade away and uploaded images of the deleted rows are removed |
| GET | `/api/filaments/import/template` | downloadable CSV with the expected headers + example rows |
| POST | `/api/filaments/import` | **bulk import** — multipart file field `file` (CSV, ≤2 MB). Additive: never touches existing rows; per-row problems are reported (`skipped: [{row, reason}]`) instead of failing the whole file. Flexible headers (case/punctuation/unit-agnostic), accepts comma/semicolon/tab/pipe, UTF-8 (BOM ok) or Latin-1. A purchase link, if present, must be an `http(s)://` link. |

### Models
| Method | Path | Notes |
|---|---|---|
| GET | `/api/models` | list with filaments + cost summary; optional `?q=` — case-insensitive match on **name only**; optional `?printer=<id>` — cost computed with that printer's setting overrides |
| POST | `/api/models` | create — `filament_amount_g` / `print_time_hr` / `labor_min` each ≤ 1,000,000; extra fields: `filament_ids` (id list — booleans, non-whole floats and out-of-range ids are 400), `filament_grams` (`{"<id>": 120}` optional grams per filament, each ≤ 1,000,000 g) + `materials` (optional list of line items as {description, quantity, unit_cost} objects — each row is valued quantity × unit cost; max 50 rows, qty and cost each capped at 1,000,000) |
| GET | `/api/models/<id>` | detail + `breakdown` (full cost table incl. `materials` rows, `materials_cost` and suggested prices); with `?printer=<id>` also returns **`printer_preview`** (the same breakdown recomputed with that printer's overrides) |
| PUT | `/api/models/<id>` | update (replaces filament associations and the material line items) |
| DELETE | `/api/models/<id>` | cascade |

### Printers
| Method | Path | Notes |
|---|---|---|
| GET | `/api/printers` | list with each printer's `settings` overrides **and its resulting `machine_rate`** |
| POST | `/api/printers` | create (fields: `model_name`*, `manufacturer`, `bed_size`, `notes` + any of the 11 cost-setting keys, each ≤ 1 trillion and `estimated_uptime_fraction` ≤ 1; blank = inherit global) |
| GET | `/api/printers/<id>` | detail |
| PUT | `/api/printers/<id>` | update — a cost-setting key sent **blank** removes that override; a key not sent is left alone |
| DELETE | `/api/printers/<id>` | deletes; its `printer_settings` cascade away |

### Settings (global cost defaults)
| Method | Path | Notes |
|---|---|---|
| GET | `/api/settings` | all 12 cost defaults + `machine_rate` derivation details |
| PUT | `/api/settings/<key>` | change one key's value/description |
| DELETE | `/api/settings/<key>` | **reset the key to its seeded default** (response: `{"ok": true, "reset_to": <value>}`) — the registry is fixed, so this is "back to factory", not a row removal |
| PUT | `/api/settings` | bulk JSON object `{"key": "value", …}` — atomic, all-or-nothing |

The key set is **fixed** (the 11 numeric cost keys + `currency_code`); new keys
are rejected on every write path. Numeric values are capped at 1 trillion
(that keeps every product the cost engine forms finite — so no response can
contain a raw `Infinity`), and `estimated_uptime_fraction` must be a 0–1
fraction of the year.

### Misc
`GET /api/health` · `GET /uploads/<path>` (pictures) · `GET /` (UI)

---

## Production on the Raspberry Pi

`waitress` (already in `requirements.txt`) is a fine production server; if it
isn't installed, `run.py` falls back to Flask's dev server.

**systemd unit** (`/etc/systemd/system/my3dworkbench.service`; paths assume the clone
lives in `~/My3DWorkbench` — adjust if it is elsewhere):

```ini
[Unit]
Description=My 3D Workbench - 3D Prints Management
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/My3DWorkbench
ExecStart=/home/pi/My3DWorkbench/.venv/bin/python run.py
Restart=on-failure
Environment=MY3DWORKBENCH_HOST=0.0.0.0
Environment=MY3DWORKBENCH_PORT=8080

[Install]
WantedBy=multi-user.service
```

```bash
sudo systemctl enable --now my3dworkbench
# LAN access: http://<machine-ip>:8080
# firewall (optional):  sudo ufw allow 8080
```

**Backups:** everything lives in the data dir — `My3DWorkbench.db` and `uploads/`
(default: the OS app-data folder for *My3DWorkbench* — see Environment
variables above). Copy those two and you're backed up.

**Back to defaults:** delete `My3DWorkbench.db` (settings reseed on next start) —
filament/model data is user data and will not come back.

---

## Tests performed

* Machine-rate derivation matches the spreadsheet at its defaults
  (computed **$0.10228/hr** = $(499+100)+75×3 / (8760×0.5×3) + 150W×$0.10643, ×1.3).
* Sample model: 100 g of $20/kg PLA → material $2.20 (×1.1 efficiency),
  15 min labor @ $20/hr → $5.00, 1 print-hour → $0.10 →
  **total $7.30, suggested price at 50% margin $14.60** — all verified
  through the REST API, and recalculation verified after changing
  `labor_hourly_rate` to 25 (total → $8.55).
* Cross-reference: filament detail lists models using it; model form lists
  every filament with live stock badges, but still allows out-of-stock
  selection.
* Cascade deletes, upload save/serve, and bulk settings updates exercised — and
  settings DELETE verified as a **reset to the seeded default**: the row keeps
  its factory value in-process (response `reset_to` matches), the live machine
  rate and a model's labor math read the seeded number, and the state equals a
  fresh/seeded registry (the old remove-the-row behavior silently made a
  deleted rate $0 until the next restart reseeded it).
* CSV bulk import: header aliasing, comma/semicolon/tab, BOM + Unicode, currency
  and thousands-separator cleaning, per-row skip reporting with row numbers,
  all-invalid → 400 with the row list, row/size limits, additive semantics
  (duplicates allowed, existing rows untouched), and a downloadable template
  that round-trips through the importer.
* Bulk filament delete (UI select mode → Delete N): a single request with a
  mixed selection deletes exactly the known rows while a bystander survives;
  junk/boolean ids, non-whole floats, and overlong id lists (→ 400) are
  skipped or rejected with no side effects, and model links to deleted
  filaments cascade away.
* Model material line items (dowels, magnets, boxes): rows valued quantity × unit
  cost fold into total cost and the suggested prices; create carries them, update
  replaces the list, `[]` clears it, bad rows (missing or boolean description,
  negative or boolean values, values over the 1,000,000 cap, over 50 rows,
  non-list) all get a clean 400 leaving the saved data untouched, and deleting
  the model cascades the rows away.
* Per-printer overrides & preview: a printer overriding `printer_cost`/watts/
  electricity-rate produces a blended machine rate verified by hand calculation
  (e.g. $1,499 + 350 W @ $0.12 → **$0.2351/hr**; clearing the watts override
  and doubling the buffer → **$0.3136/hr**); model detail `?printer=` and model
  list `?printer=` return the recomputed breakdown; sending an override
  **blank** restores the global value.
* Validation hardening (each got a regression test):
  - **reject, don't coerce** — `cost_per_spool: true`, `notes: false`, a
    `filament_grams` value of `true`, a `printer_cost: true` override → all
    400 (Python's `float(True) == 1.0` used to let them through as 1);
  - **id parsing** — `filament_ids: [1.9]` and `[true]` are 400 (previously
    silently linked filament 1), out-of-INT64 ids are a clean 400/404 JSON
    envelope instead of an HTML 500, and a whole-number float (`2.0`) is
    still accepted as 2;
  - **valid JSON everywhere** — money inputs are capped (`cost_per_spool`,
    `filament_amount_g`, `print_time_hr`, `labor_min`, settings values,
    overrides) and the engine guards a near-zero uptime denominator, so no
    finite request can produce an `Infinity` token in a response (a 1 g spool
    at $10⁹⁹⁹ used to); responses are checked with a strict parser, and even
    unexpected 500s now use the `{"error": ...}` envelope;
  - **no script-scheme links** — `javascript:`, `data:`, or `vbscript:` values
    for `purchase_link` are 400 on create (row not created) and on update
    (stored link untouched), and a CSV import row carrying one is skipped with
    a reason while the good rows still import — those values used to be stored
    and later rendered into a clickable `<a href>` (stored XSS); an
    `https://example.com` link passes on every path.

---

## Running the test suite

A zero-dependency suite lives in `tests/` — plain asserts plus a small runner,
so it needs **nothing extra** (no pytest, no network):

```bash
.venv/Scripts/python tests/run_tests.py     # Windows
.venv/bin/python tests/run_tests.py         # Linux / Pi
```

It uses a temporary database and uploads directory (never your real data dir —
`My3DWorkbench.db` lives under the OS app-data folder since the rename), covers the cost engine unit tests, the whole REST API
(validation, cascades, search semantics, uploads, JSON error envelopes, the
413 handler, path-traversal guard, bulk-settings atomicity, demo-seed
idempotency), the exact multipart field shapes the browser sends, and a
live `run.py`/waitress subprocess hammered by 8 concurrent threads. Exit
code 0 + `ALL GREEN` means pass.

> The live-server test binds a **random localhost** port (no internet needed)
> and always kills its subprocess in a `finally`. If you Ctrl-C mid-test, one
> stray `run.py` may linger on its port — harmless; end the task if you care.
