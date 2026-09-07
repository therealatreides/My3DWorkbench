"""
SQLite database layer.

Responsibilities
----------------
* Owns schema creation (idempotent — safe to run on every start)
* Seeds the ``settings`` table with the global defaults taken from the
  ``Adv. Inputs`` sheet of ``!Product_Pricing_Worksheet_V2.xlsx``
* Provides one ``sqlite3`` connection **per request** (stored on ``flask.g``),
  which is the safe pattern for threaded servers such as waitress

Concurrency notes
-----------------
``journal_mode=WAL`` (set once, persistently) allows concurrent readers while
a write is in progress. For a small LAN tool with a handful of users that is
more than sufficient; if the app ever needs heavy concurrency, swapping this
module for SQLAlchemy is the clean upgrade path because it is the *only*
module that touches sqlite3 directly.
"""
import os
import sqlite3

from flask import current_app, g

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# The four core tables (filaments, models, model_filaments, settings) plus the
# printer fleet (printers, printer_settings).
SCHEMA = """
PRAGMA journal_mode=WAL;

-- Central spool inventory (in-stock and out-of-stock spools alike).
CREATE TABLE IF NOT EXISTS filaments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    color_name      TEXT    NOT NULL,               -- e.g. "Cobalt Blue"
    type            TEXT    NOT NULL,               -- PLA / PETG / ASA / ABS / TPU ...
    brand           TEXT    NOT NULL DEFAULT '',
    purchase_link   TEXT    NOT NULL DEFAULT '',
    spool_weight_g  INTEGER NOT NULL DEFAULT 0,     -- nominal weight of a full spool, grams
    cost_per_spool  REAL    NOT NULL DEFAULT 0,     -- what a spool costs (currency)
    current_stock_g REAL    NOT NULL DEFAULT 0,     -- grams on hand; 0 => out of stock
    image_path      TEXT    NOT NULL DEFAULT '',    -- stored relative to /uploads
    notes           TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_filaments_type    ON filaments (type);
CREATE INDEX IF NOT EXISTS idx_filaments_color   ON filaments (color_name);

-- Individual 3D-printable models.
CREATE TABLE IF NOT EXISTS models (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    purpose           TEXT    NOT NULL DEFAULT 'Personal'
                      CHECK (purpose IN ('Personal', 'Profit')),
    description       TEXT    NOT NULL DEFAULT '',
    -- Cost-calculation variables (mirrors the blue input cells D11-D13 of the
    -- Calculation sheet):
    filament_amount_g REAL    NOT NULL DEFAULT 0,   -- total filament per print, grams
    print_time_hr     REAL    NOT NULL DEFAULT 0,   -- estimated print time, hours
    labor_min         REAL    NOT NULL DEFAULT 0,   -- post-processing labor, minutes
    image_path        TEXT    NOT NULL DEFAULT '',
    notes             TEXT    NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_models_purpose ON models (purpose);

-- Many-to-many link between models and the filaments they require.
-- ``grams`` is OPTIONAL: when present it pins exactly how many grams of this
-- filament the model uses; when NULL the model's total filament weight is
-- split evenly across its associated filaments at cost-calculation time.
CREATE TABLE IF NOT EXISTS model_filaments (
    model_id     INTEGER NOT NULL REFERENCES models(id)     ON DELETE CASCADE,
    filament_id  INTEGER NOT NULL REFERENCES filaments(id)  ON DELETE CASCADE,
    grams        REAL,                                     -- nullable allocation
    PRIMARY KEY (model_id, filament_id)
);

CREATE INDEX IF NOT EXISTS idx_model_filaments_fid ON model_filaments (filament_id);

-- Additional material line items for a model (wood dowels, magnets, shipping
-- boxes, …) that sit on top of print/labor cost. Each row is
-- ``quantity × unit_cost``; the cost engine (cost.py) sums them into
-- ``materials_cost``, which is added into the model's total cost before the
-- suggested prices are computed. Rows are shown in creation order (id).
CREATE TABLE IF NOT EXISTS model_materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id    INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    description TEXT    NOT NULL,                  -- "wood dowels" etc.
    quantity    REAL    NOT NULL DEFAULT 1,        -- how many are used (>= 0)
    unit_cost   REAL    NOT NULL DEFAULT 0,        -- cost per unit (>= 0)
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_model_materials_mid ON model_materials (model_id);

-- Global cost defaults — the fixed registry the cost engine reads
-- (see app/routes/settings.py; only these keys exist).
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 3D printers in the fleet.
CREATE TABLE IF NOT EXISTS printers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer TEXT    NOT NULL DEFAULT '',
    model_name   TEXT    NOT NULL,                 -- e.g. "P1S", "Ender-3 V3 SE"
    bed_size     TEXT    NOT NULL DEFAULT '',      -- free text, e.g. "256 x 256 x 256 mm"
    notes        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_printers_model ON printers (model_name);

-- Per-printer overrides of the cost-calculation settings (the same keys the
-- global ``settings`` table holds). An override wins when present;
-- otherwise the global setting applies (see merge_settings() in cost.py).
-- Deleting a row (or the parent printer) restores the global value.
CREATE TABLE IF NOT EXISTS printer_settings (
    printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (printer_id, key)
);
"""

# ---------------------------------------------------------------------------
# Seed data — global defaults transcribed from the "Adv. Inputs" sheet.
# (INSERT OR IGNORE => seeding only happens the very first time the app runs;
# user edits are preserved across restarts.)
# ---------------------------------------------------------------------------
SEED_SETTINGS = [
    ("material_efficiency_factor", "1.1",
     "Multiplier applied to filament usage to cover waste & support material (sheet: Adv. Inputs C4)."),
    ("labor_hourly_rate", "20",
     "Labor cost for post-processing / fulfillment, currency per hour (sheet: Adv. Inputs C6)."),
    ("printer_cost", "499",
     "Purchase price of the 3D printer (sheet: Adv. Inputs C12)."),
    ("additional_upfront_cost", "100",
     "Cost of upgrades made when the printer was brought up to speed (sheet: Adv. Inputs C14)."),
    ("annual_maintenance_cost", "75",
     "Estimated repair & maintenance cost per year (sheet: Adv. Inputs C18)."),
    ("estimated_life_years", "3",
     "Expected useful life of the printer, in years (sheet: Adv. Inputs C23)."),
    ("estimated_uptime_fraction", "0.5",
     "Fraction of the year the printer is actually printing, 0-1. 0.5 = 50% (sheet: Adv. Inputs C25)."),
    ("power_consumption_watts", "150",
     "Average power draw while printing, in watts (sheet: Adv. Inputs C28)."),
    ("electricity_cost_kwh", "0.10643",
     "Electricity rate, currency per KWh (sheet: Adv. Inputs C30)."),
    ("printer_cost_buffer_factor", "1.3",
     "Safety buffer multiplied onto the machine rate to cover unforeseen costs (sheet: Adv. Inputs C31)."),
    ("print_time_rate_override", "",
     "Optional: force the machine rate (currency per print-hour) to a fixed value. Leave blank to compute it automatically."),
    ("currency_code", "USD",
     "Code used to display prices in the UI, e.g. USD, EUR, GBP."),
]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def _connect(db_path):
    """Open a database connection with sensible pragmas for our workload."""
    conn = sqlite3.connect(db_path)          # one connection per request
    conn.row_factory = sqlite3.Row          # dict-like row access
    conn.execute("PRAGMA foreign_keys = ON")  # enforce ON DELETE CASCADE etc.
    return conn


def init_app(app):
    """Create the schema (if needed) and seed default settings. Idempotent."""
    db_path = app.config["DATABASE_PATH"]
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
            SEED_SETTINGS,
        )
        conn.commit()
    finally:
        conn.close()


def close_db(_exc=None):
    """Flask teardown hook: close this request's connection, if any."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ---------------------------------------------------------------------------
# Per-request accessors used by the routes (thin wrappers keep route code
# readable — no commit / fetchall boilerplate in every function).
# ---------------------------------------------------------------------------
def get_db():
    """Return this request's database connection, creating it on first use."""
    if "db" not in g:
        g.db = _connect(current_app.config["DATABASE_PATH"])
    return g.db


def query_all(sql, params=()):
    """Run a query and return all rows (sqlite3.Row objects)."""
    return get_db().execute(sql, params).fetchall()


def query_one(sql, params=()):
    """Run a query and return the first row, or None."""
    return get_db().execute(sql, params).fetchone()


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE, commit, and return the cursor."""
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur
