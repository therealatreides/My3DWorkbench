"""Global cost defaults — the fixed registry of settings the cost engine reads.

The store holds exactly ``COST_SETTING_KEYS`` + ``currency_code``; every value
feeds the cost math (see :mod:`app.cost`). New keys can no longer be added —
unknown keys are rejected on every write path and hidden from read paths.
"""
import math

from flask import Blueprint, jsonify, request

from .. import cost as cost_engine
from .. import db
from .. import api_helpers as helpers
from ..errors import APIError

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

# The known, cost-consuming settings — validated as floats on write (empty
# allowed, since e.g. the print-time-rate override is intentionally blank by
# default) — plus the display currency. This is the COMPLETE registry; the
# canonical list lives in the cost engine.
KNOWN_KEYS = frozenset(cost_engine.COST_SETTING_KEYS) | {"currency_code"}
NUMERIC_KEYS = frozenset(cost_engine.COST_SETTING_KEYS)
MAX_SETTING_VALUE = 1e12   # keeps every product the cost engine forms finite

# Seeded defaults, keyed: DELETE is a *reset to these values*, not a row
# removal (see delete_setting). Built once at import time from the seed list
# in db.py — the single source of truth for factory values.
SEED_DEFAULTS = {
    key: (value, description) for key, value, description in db.SEED_SETTINGS
}


def _all_map():
    return {r["key"]: r["value"] for r in db.query_all("SELECT key, value FROM settings")}


def _validate(key, value):
    key = (key or "").strip()
    if key not in KNOWN_KEYS:
        raise APIError(f"Unknown setting '{key}'. Only the global cost "
                       f"defaults are used: {', '.join(sorted(KNOWN_KEYS))}.")
    value = "" if value is None else str(value).strip()
    if value != "":
        try:
            numeric_value = float(value)
        except ValueError:
            numeric_value = None
        if numeric_value is not None:
            if not math.isfinite(numeric_value):
                raise APIError(f"Setting '{key}' must be a finite number (got {value!r}).")
            if key in NUMERIC_KEYS and numeric_value < 0:
                raise APIError(f"Setting '{key}' must be a non-negative number (or left blank).")
            if key in NUMERIC_KEYS and numeric_value > MAX_SETTING_VALUE:
                raise APIError(f"Setting '{key}' is too large (max {MAX_SETTING_VALUE:g}).")
            if key == "estimated_uptime_fraction" and numeric_value > 1.0:
                raise APIError("Setting 'estimated_uptime_fraction' is a 0-1 fraction of the year.")
        elif key in NUMERIC_KEYS:
            raise APIError(f"Setting '{key}' must be a number (or left blank).")
    return key, value

def _upsert(key, value, description):
    db.execute(
        """INSERT INTO settings (key, value, description, updated_at)
           VALUES (?,?,?,datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,
               description = CASE WHEN excluded.description IS '' THEN settings.description
                                  ELSE excluded.description END,
               updated_at=datetime('now')""",
        (key, value, description))


@bp.get("")
def list_settings():
    like = ",".join("?" * len(KNOWN_KEYS))
    rows = [dict(r) for r in db.query_all(
        f"SELECT key, value, description, updated_at FROM settings WHERE key IN ({like}) "
        "ORDER BY key", tuple(KNOWN_KEYS))]
    return jsonify({
        "settings": rows,
        "numeric_keys": sorted(NUMERIC_KEYS),
        "machine_rate": cost_engine.compute_machine_rate(_all_map()),
    })


@bp.get("/<key>")
def get_setting(key):
    if key not in KNOWN_KEYS:
        raise APIError("Setting not found.", 404)
    row = db.query_one("SELECT * FROM settings WHERE key = ?", (key,))
    if row is None:
        raise APIError("Setting not found.", 404)
    return jsonify(dict(row))


@bp.put("")
def bulk_update():
    """Accept a JSON object {"key": value, ...} or form fields with the same pairs."""
    if request.is_json:
        payload = request.get_json(silent=True)
        if payload is None:
            raise APIError("Bulk settings must be a JSON object of {key: value}.")
        if not isinstance(payload, dict):
            raise APIError("Bulk settings must be a JSON object of {key: value}.")
    else:
        payload = {k: v for k, v in request.form.items() if k not in ("key", "description")}
    # Validate EVERYTHING before writing anything, so a bad pair in the middle
    # of a bulk request cannot leave a partially-applied update behind.
    clean_pairs = [_validate(key, value) for key, value in payload.items()]
    for clean_key, clean_value in clean_pairs:
        _upsert(clean_key, clean_value, "")  # empty description => keep existing
    return list_settings()


@bp.put("/<key>")
def update_setting(key):
    key, value = _validate(key, helpers.field("value"))
    description = helpers.str_field("description", max_len=500)
    _upsert(key, value, description)
    return jsonify(dict(db.query_one("SELECT * FROM settings WHERE key = ?", (key,))))


@bp.delete("/<key>")
def delete_setting(key):
    """Reset a key to its seeded default ("back to factory" for that row).

    The settings registry is a fixed part of the cost engine, so a real row
    removal is off the table: it would silently change the affected cost
    component in-process (missing key -> engine fallback of 0), and the next
    restart would reseed the factory value behind the user's back. Resetting
    to the seed value keeps the in-process state and the post-restart state
    identical, and is exactly what the UI and README describe.
    """
    if key not in KNOWN_KEYS:
        raise APIError("Setting not found.", 404)
    value, description = SEED_DEFAULTS[key]
    _upsert(key, value, description)
    return {"ok": True, "reset_to": value}
