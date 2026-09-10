"""Model CRUD, its filament associations, extra material line items, and cost breakdown."""
import math

from flask import Blueprint, jsonify, request

from .. import cost as cost_engine
from .. import db
from .. import api_helpers as helpers
from ..errors import APIError

bp = Blueprint("models", __name__, url_prefix="/api/models")

PURPOSES = ("Personal", "Profit")

MAX_MATERIAL_ROWS = 50   # ceiling of line-item rows per model
MAX_MATERIAL_QTY = 1_000_000
MAX_MATERIAL_UNIT_COST = 1_000_000   # qty × unit-cost product stays finite (≤ 1e12)

FILAMENTS_FOR_MODEL_SQL = """
    SELECT f.*, mf.grams AS grams, m.filament_amount_g AS total_g
    FROM filaments f
    JOIN model_filaments mf ON mf.filament_id = f.id
    JOIN models m ON m.id = mf.model_id
    WHERE m.id = ?
    ORDER BY f.type, f.color_name, f.brand
"""


def _get_or_404(mid):
    if not (1 <= mid <= 2**63 - 1):          # beyond INT64 — would overflow at bind time
        raise APIError("Model not found.", 404)
    row = db.query_one("SELECT * FROM models WHERE id = ?", (mid,))
    if row is None:
        raise APIError("Model not found.", 404)
    return row


def _settings_map():
    return {r["key"]: r["value"] for r in db.query_all("SELECT key, value FROM settings")}


def _printer_overrides(raw):
    """Resolve a ``?printer=<id>`` request parameter.

    Returns ``(printer_meta, overrides)`` when the param is present, or None
    when it isn't. Raises APIError on an invalid or unknown id.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        pid = int(text)
    except ValueError:
        raise APIError("'printer' must be a printer id.")
    if not (1 <= pid <= 2**63 - 1):          # beyond INT64 — would overflow at bind time
        raise APIError(f"Unknown printer id: {text}.")
    meta = db.query_one(
        "SELECT id, manufacturer, model_name, bed_size FROM printers WHERE id=?",
        (pid,))
    if meta is None:
        raise APIError(f"Unknown printer id: {pid}.")
    overrides = {r["key"]: r["value"] for r in
                 db.query_all("SELECT key, value FROM printer_settings WHERE printer_id=?", (pid,))}
    return dict(meta), overrides


def _breakdown(row, filaments, materials=None, overrides=None):
    """Cost breakdown for a model; when *overrides* is given, the printer's
    settings are merged over the globals for the calculation."""
    settings_map = _settings_map()
    if overrides is not None:
        settings_map = cost_engine.merge_settings(settings_map, overrides)
    return cost_engine.compute_model_cost(dict(row), filaments, settings_map, materials)


def _filament_rows(mid):
    out = []
    for r in db.query_all(FILAMENTS_FOR_MODEL_SQL, (mid,)):
        d = helpers.filament_to_dict(r)
        d["grams"] = r["grams"]
        d["total_g"] = r["total_g"]
        out.append(d)
    return out


def _material_rows(mid):
    """A model's extra material line items, in creation order, each with a
    computed ``cost`` (quantity × unit_cost)."""
    out = []
    for r in db.query_all(
            "SELECT id, description, quantity, unit_cost FROM model_materials "
            "WHERE model_id = ? ORDER BY id", (mid,)):
        d = dict(r)
        d["cost"] = d["quantity"] * d["unit_cost"]
        out.append(d)
    return out


def _detail(mid, printer=None):
    """Full model dict with base breakdown; when *printer* is a
    ``(meta, overrides)`` pair, a ``printer_preview`` block is added."""
    row = _get_or_404(mid)
    filaments = _filament_rows(mid)
    materials = _material_rows(mid)
    d = helpers.model_to_dict(row)
    d["filaments"] = filaments
    d["materials"] = materials
    d["breakdown"] = _breakdown(row, filaments, materials)
    if printer is not None:
        meta, overrides = printer
        d["printer_preview"] = {
            "printer": meta,
            "breakdown": _breakdown(row, filaments, materials, overrides),
        }
    return d


def _validate_filament_ids(ids):
    if not ids:
        return ids
    if len(set(ids)) != len(ids):
        raise APIError("The same filament was selected more than once.")
    have = {r["id"] for r in db.query_all("SELECT id FROM filaments WHERE id IN (%s)"
                                          % ",".join("?" * len(ids)), tuple(ids))}
    missing = [i for i in ids if i not in have]
    if missing:
        raise APIError(f"Unknown filament id(s) in filament_ids: {missing}.")
    return ids


def _normalize_purpose(raw):
    """Accept any casing ("profit", "PROFIT", …) and return the canonical value."""
    text = (raw or "").strip()
    for p in PURPOSES:
        if p.lower() == text.lower():
            return p
    raise APIError("purpose must be one of: Personal, Profit.")


def _parse_payload():
    filament_ids = helpers.int_list_field("filament_ids")
    _validate_filament_ids(filament_ids)

    grams_raw = helpers.json_field("filament_grams", default={}) or {}
    if not isinstance(grams_raw, dict):
        raise APIError("'filament_grams' must be a JSON object {filament_id: grams}.")
    filament_grams = {}
    try:
        for k, v in grams_raw.items():
            if isinstance(v, bool):
                raise APIError("'filament_grams' values must be numbers, not booleans.")
            fv = float(v)
            if not math.isfinite(fv) or fv < 0:
                raise APIError("'filament_grams' values must be finite numbers >= 0.")
            if fv > 1e6:
                raise APIError(f"'filament_grams' values are capped at 1,000,000 g (got {fv:g}).")
            filament_grams[int(k)] = fv
    except OverflowError:
        raise APIError("'filament_grams' keys must be filament ids.")
    except (TypeError, ValueError):
        raise APIError("'filament_grams' keys must be filament ids and values finite numbers.")

    data = {
        "name": helpers.str_field("name", required=True),
        "purpose": _normalize_purpose(helpers.str_field("purpose", default="Personal")),
        "description": helpers.str_field("description", max_len=2000),
        # capped like the material line items: the products the cost engine
        # forms with these (× cost_per_kg, × rate, × 1.3…) must stay finite, or
        # the JSON responses would contain raw Infinity tokens (invalid JSON).
        "filament_amount_g": helpers.float_field("filament_amount_g", min_value=0, max_value=1e6),
        "print_time_hr": helpers.float_field("print_time_hr", min_value=0, max_value=1e6),
        "labor_min": helpers.float_field("labor_min", min_value=0, max_value=1e6),
        "notes": helpers.str_field("notes", max_len=2000),
        "filament_ids": filament_ids,
        "filament_grams": filament_grams,
        "materials": _parse_materials(),
    }
    img = helpers.save_image("models")
    data["image_path"] = img or ""
    return data


def _parse_materials():
    """Parse the optional ``materials`` JSON list into clean line items.

    Each item: {"description": str (required, ≤ 200), "quantity": number ≥ 0
    (default 1, max 1,000,000), "unit_cost": number ≥ 0 (default 0, max 1,000,000).
    Order is preserved; at most MAX_MATERIAL_ROWS rows. The per-row product is
    therefore always finite (≤ 1e12), so it can never produce an Infinity in
    the JSON responses (a raw JSON token that breaks spec-compliant clients).
    """
    raw = helpers.json_field("materials", default=None)
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise APIError("'materials' must be a list of {description, quantity?, unit_cost?} objects.")
    if len(raw) > MAX_MATERIAL_ROWS:
        raise APIError(f"At most {MAX_MATERIAL_ROWS} material rows are allowed per model "
                       f"(got {len(raw)}).")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise APIError(f"materials[{i}] must be an object "
                           f"{{description, quantity?, unit_cost?}} (got {type(item).__name__}).")
        desc_raw = item.get("description")
        if isinstance(desc_raw, bool):
            raise APIError(f"materials[{i}].description must be text (got a boolean).")
        desc = str(desc_raw or "").strip()
        if not desc:
            raise APIError(f"materials[{i}].description is required (e.g. 'wood dowels').")
        if len(desc) > 200:
            raise APIError(f"materials[{i}].description is too long (max 200 chars).")
        qty = item.get("quantity", 1)
        unit = item.get("unit_cost", 0)
        if isinstance(qty, bool) or isinstance(unit, bool):
            raise APIError(f"materials[{i}].quantity and .unit_cost must be numbers, not booleans.")
        try:
            qty = float(qty)
            unit = float(unit)
        except (TypeError, ValueError):
            raise APIError(f"materials[{i}].quantity and .unit_cost must be numbers.")
        if not math.isfinite(qty) or qty < 0:
            raise APIError(f"materials[{i}].quantity must be a finite number >= 0.")
        if qty > MAX_MATERIAL_QTY:
            raise APIError(f"materials[{i}].quantity is too large (max {MAX_MATERIAL_QTY:,}).")
        if not math.isfinite(unit) or unit < 0:
            raise APIError(f"materials[{i}].unit_cost must be a finite number >= 0.")
        if unit > MAX_MATERIAL_UNIT_COST:
            raise APIError(f"materials[{i}].unit_cost is too large (max {MAX_MATERIAL_UNIT_COST:,}).")
        if not math.isfinite(qty * unit):   # belt and braces: the product must be storable in REAL
            raise APIError(f"materials[{i}] overflows — quantity and unit_cost are too large together.")
        out.append({"description": desc, "quantity": qty, "unit_cost": unit})
    return out


def _write_links(mid, filament_ids, filament_grams):
    conn = db.get_db()
    conn.execute("DELETE FROM model_filaments WHERE model_id = ?", (mid,))
    for fid in filament_ids:
        conn.execute(
            "INSERT OR REPLACE INTO model_filaments (model_id, filament_id, grams) VALUES (?,?,?)",
            (mid, fid, filament_grams.get(fid))
        )
    conn.commit()


def _write_materials(mid, materials):
    """Replace the model's material line items with the parsed list."""
    conn = db.get_db()
    conn.execute("DELETE FROM model_materials WHERE model_id = ?", (mid,))
    for m in materials:
        conn.execute(
            "INSERT INTO model_materials (model_id, description, quantity, unit_cost) "
            "VALUES (?,?,?,?)", (mid, m["description"], m["quantity"], m["unit_cost"]))
    conn.commit()


@bp.get("")
def list_models():
    """List all models with computed cost. ``?q=``: case-insensitive match on
    name only; ``?printer=<id>``: cost computed with that printer's
    setting overrides merged over the global settings."""
    like = helpers.like_pattern(request.args.get("q"))
    where = " WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE" if like is not None else ""
    rows = db.query_all(
        "SELECT * FROM models" + where + " ORDER BY updated_at DESC, id DESC",
        (like,) if like is not None else ())
    resolved = _printer_overrides(request.args.get("printer"))
    overrides = resolved[1] if resolved is not None else None
    out = []
    for row in rows:
        d = helpers.model_to_dict(row)
        filaments = _filament_rows(row["id"])
        d["filaments"] = filaments
        materials = _material_rows(row["id"])
        d["materials"] = materials
        breakdown = _breakdown(row, filaments, materials, overrides)
        d["cost"] = {"total_cost": breakdown["total_cost"],
                     "currency": breakdown["currency"]}
        out.append(d)
    return jsonify(out)


@bp.post("")
def create_model():
    data = _parse_payload()
    cur = db.execute(
        """INSERT INTO models (name, purpose, description, filament_amount_g,
                               print_time_hr, labor_min, image_path, notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (data["name"], data["purpose"], data["description"], data["filament_amount_g"],
         data["print_time_hr"], data["labor_min"], data["image_path"], data["notes"]))
    new_id = cur.lastrowid
    _write_links(new_id, data["filament_ids"], data["filament_grams"])
    _write_materials(new_id, data["materials"])
    return jsonify(_detail(new_id)), 201


@bp.get("/<int:mid>")
def get_model(mid):
    """Full detail: base breakdown (global settings), plus ``printer_preview``
    when ``?printer=<id>`` is given."""
    return jsonify(_detail(mid, _printer_overrides(request.args.get("printer"))))


@bp.put("/<int:mid>")
def update_model(mid):
    existing = _get_or_404(mid)
    data = _parse_payload()
    if not data["image_path"] and existing["image_path"]:
        data["image_path"] = existing["image_path"]
    if data["image_path"] != existing["image_path"] and existing["image_path"]:
        helpers.delete_upload(existing["image_path"])
    db.execute(
        """UPDATE models SET name=?, purpose=?, description=?, filament_amount_g=?,
             print_time_hr=?, labor_min=?, image_path=?, notes=?, updated_at = datetime('now')
           WHERE id = ?""",
        (data["name"], data["purpose"], data["description"], data["filament_amount_g"],
         data["print_time_hr"], data["labor_min"], data["image_path"], data["notes"], mid))
    _write_links(mid, data["filament_ids"], data["filament_grams"])
    _write_materials(mid, data["materials"])
    return jsonify(_detail(mid))


@bp.delete("/<int:mid>")
def delete_model(mid):
    row = _get_or_404(mid)
    db.execute("DELETE FROM models WHERE id = ?", (mid,))
    helpers.delete_upload(dict(row)["image_path"])
    return {"ok": True}
