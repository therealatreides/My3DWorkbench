"""Printer fleet: CRUD for 3D printers and their per-printer cost settings.

Each printer stores basics (manufacturer, model, bed size, notes) plus
optional **overrides** of the global cost-calculation settings (the 11 keys
the cost engine consumes, listed in ``cost.COST_SETTING_KEYS``). An override
is a (printer_id, key, value) row in ``printer_settings``.

Override semantics (matching the global settings API):
  * value present & a number -> override set for that printer
  * value present & blank    -> override removed (printer inherits the global
                                 setting for that key)
  * key absent from the request -> left unchanged (update only)

At calculation time the engine merges ``global settings <- overrides``
(``cost.merge_settings``), and cost endpoints accept ``?printer=<id>``.
"""

import math

from flask import Blueprint, jsonify

from .. import cost as cost_engine
from .. import db as db_module
from .. import errors
from ..api_helpers import field, str_field

bp = Blueprint("printers", __name__, url_prefix="/api/printers")

COST_KEYS = cost_engine.COST_SETTING_KEYS
MAX_OVERRIDE_VALUE = 1e12   # keeps every product the cost engine forms finite


# -- helpers ------------------------------------------------------------------
def _get_or_404(printer_id):
    _require_int64(printer_id)
    row = db_module.query_one("SELECT * FROM printers WHERE id=?", (printer_id,))
    if not row:
        raise errors.APIError(f"Printer {printer_id} not found.", 404)
    return row


def _global_map():
    return {r["key"]: r["value"] for r in
            db_module.query_all("SELECT key, value FROM settings")}


def _full(printer_id):
    """Printer row + its overrides + the machine rate they would produce."""
    d = dict(_get_or_404(printer_id))
    d["settings"] = {r["key"]: r["value"] for r in
                     db_module.query_all(
                         "SELECT key, value FROM printer_settings "
                         "WHERE printer_id=?", (printer_id,))}
    merged = cost_engine.merge_settings(_global_map(), d["settings"])
    d["machine_rate"] = cost_engine.compute_machine_rate(merged)["rate_per_hr"]
    return d


def _parse_basics():
    return (str_field("model_name", required=True, max_len=255),
            str_field("manufacturer", max_len=255),
            str_field("bed_size", max_len=255),
            str_field("notes", max_len=2000))


def _parse_overrides():
    """Pull the cost-setting keys out of the request (JSON body or form).

    Returns a dict of the keys the request *mentioned*: a validated number to
    set, or None to clear the override (key sent blank). Keys the request did
    not mention are absent, so an update leaves them untouched.
    """
    out = {}
    for key in COST_KEYS:
        raw = field(key)          # form or JSON, whichever was used
        if raw is None:
            continue              # not provided -> leave alone
        if str(raw).strip() == "":
            out[key] = None       # provided but blank -> inherit global
            continue
        if isinstance(raw, bool):
            raise errors.APIError(f"'{key}' must be a number, not a boolean.")
        try:
            n = float(raw)
        except (TypeError, ValueError):
            raise errors.APIError(f"'{key}' must be a number (or blank to "
                                  "inherit the global setting).")
        if not math.isfinite(n):
            raise errors.APIError(f"'{key}' must be a finite number.")
        if n < 0:
            raise errors.APIError(f"'{key}' must be 0 or higher.")
        if n > MAX_OVERRIDE_VALUE:
            raise errors.APIError(f"'{key}' is too large (max {MAX_OVERRIDE_VALUE:g}).")
        if key == "estimated_uptime_fraction" and n > 1.0:
            raise errors.APIError("'estimated_uptime_fraction' is a 0-1 fraction of the year.")
        out[key] = n
    return out


def _num_text(n):
    """Store a number without float artifacts: ``str(1499.0)`` is ``'1499.0'``,
    but the settings table is human-readable text, so ``1499`` is the value a
    user typed and the value they should see back."""
    text = str(n)
    return text[:-2] if text.endswith(".0") else text


def _apply_overrides(cur, printer_id, overrides):
    if not overrides:
        return
    for key, value in overrides.items():
        if value is None:
            cur.execute("DELETE FROM printer_settings WHERE printer_id=? AND key=?",
                        (printer_id, key))
        else:
            cur.execute(
                "INSERT INTO printer_settings (printer_id, key, value) VALUES (?,?,?) "
                "ON CONFLICT (printer_id, key) DO UPDATE SET value=excluded.value",
                (printer_id, key, _num_text(value)))
    # db.execute() commits the caller's statement, but the statements above
    # run in a new implicit transaction — commit it explicitly (models
    # _write_links uses the same pattern).
    cur.connection.commit()


# -- routes ---------------------------------------------------------
@bp.get("")
def list_printers():
    ids = [r["id"] for r in
           db_module.query_all("SELECT id FROM printers ORDER BY id")]
    return jsonify([_full(pid) for pid in ids])


@bp.post("")
def create_printer():
    name, manufacturer, bed_size, notes = _parse_basics()
    overrides = _parse_overrides()
    cur = db_module.execute(
        "INSERT INTO printers (manufacturer, model_name, bed_size, notes) "
        "VALUES (?,?,?,?)", (manufacturer, name, bed_size, notes))
    printer_id = cur.lastrowid      # capture before _apply_overrides reuses the cursor
    _apply_overrides(cur, printer_id, overrides)
    return jsonify(_full(printer_id)), 201


@bp.get("/<int:printer_id>")
def get_printer(printer_id):
    return jsonify(_full(printer_id))


def _require_int64(printer_id):
    """Route-converted ids are arbitrary-size Python ints; beyond INT64 they
    would overflow SQLite at bind time with an unhandled 500 — so treat an
    out-of-range id as a plain not-found."""
    if not (1 <= printer_id <= 2**63 - 1):
        raise errors.APIError(f"Printer {printer_id} not found.", 404)


@bp.put("/<int:printer_id>")
def update_printer(printer_id):
    _require_int64(printer_id)
    name, manufacturer, bed_size, notes = _parse_basics()
    overrides = _parse_overrides()
    cur = db_module.execute(
        "UPDATE printers SET manufacturer=?, model_name=?, bed_size=?, notes=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (manufacturer, name, bed_size, notes, printer_id))
    if cur.rowcount == 0:
        raise errors.APIError(f"Printer {printer_id} not found.", 404)
    _apply_overrides(cur, printer_id, overrides)
    return jsonify(_full(printer_id))


@bp.delete("/<int:printer_id>")
def delete_printer(printer_id):
    _require_int64(printer_id)
    cur = db_module.execute("DELETE FROM printers WHERE id=?", (printer_id,))
    if cur.rowcount == 0:
        raise errors.APIError(f"Printer {printer_id} not found.", 404)
    # printer_settings rows cascade (PRAGMA foreign_keys=ON, ON DELETE CASCADE)
    return "", 204
