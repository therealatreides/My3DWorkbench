"""Filament CRUD + cross-reference (related models) + bulk CSV import."""
import csv
import io
import math
import re

from flask import Blueprint, Response, jsonify, request

from .. import db
from .. import api_helpers as helpers
from ..errors import APIError

bp = Blueprint("filaments", __name__, url_prefix="/api/filaments")

MAX_BULK_DELETE_IDS = 1000   # bounds the request (see endpoint)


RELATED_MODELS_SQL = """
    SELECT m.id, m.name, m.purpose, m.filament_amount_g, m.print_time_hr,
           mf.grams AS allocated_grams
    FROM models m
    JOIN model_filaments mf ON mf.model_id = m.id
    WHERE mf.filament_id = ?
    ORDER BY m.name
"""


def _get_or_404(fid):
    if not (1 <= fid <= 2**63 - 1):          # beyond INT64 — would overflow at bind time
        raise APIError("Filament not found.", 404)
    row = db.query_one("SELECT * FROM filaments WHERE id = ?", (fid,))
    if row is None:
        raise APIError("Filament not found.", 404)
    return row


def _parse_payload(existing=None):
    """Validate request fields (NO file I/O yet); returns a dict of column -> value."""
    # max_value keeps this inside SQLite's INTEGER (INT64) bind range after
    # the int(round()) below — 1e12 g (a thousand thousand tonnes) is far
    # beyond any real spool, but 1e300 would blow past the range and raise a
    # 500 at bind time.
    spool_weight_g = int(round(
        helpers.float_field("spool_weight_g", required=True, min_value=0, max_value=1e12)))
    if spool_weight_g <= 0:
        raise APIError("spool_weight_g must be greater than 0.")
    data = {
        "color_name": helpers.str_field("color_name", required=True),
        "type": (helpers.str_field("type", required=True, max_len=50).upper()),
        "brand": helpers.str_field("brand", max_len=200),
        "purchase_link": helpers.web_link_field("purchase_link", max_len=500),
        "spool_weight_g": spool_weight_g,
        # capped like spool_weight_g: with the 1 g minimum spool, cost_per_kg is
        # cost × 1000 / spool — uncapped, a finite input could still serialize
        # as Infinity (not legal JSON) in list/detail responses.
        "cost_per_spool": helpers.float_field(
            "cost_per_spool", required=True, min_value=0, max_value=1e12),
        "current_stock_g": helpers.float_field("current_stock_g", min_value=0),
        "notes": helpers.str_field("notes", max_len=2000),
        "image_path": dict(existing)["image_path"] if existing else "",
    }
    img = helpers.save_image("filaments")   # only reached when every field is valid
    if img:
        data["image_path"] = img
    return data


# Exact-match list filters: request param name -> column value. Values
# already exist in the data (the UI serves them from /facets), so a trimmed
# case-insensitive equality test is the right semantic — and it stays literal
# (a brand filter of "Pet" must not match "PetG").
EXACT_FILTERS = (("color", "color_name"), ("brand", "brand"), ("type", "type"))
STOCK_FILTERS = {"in": "current_stock_g > 0", "out": "current_stock_g <= 0"}


def _list_query():
    """Build (sql, params) for GET /api/filaments from the request args.

    ``q`` is a free-text search on color name OR brand; the brand/color/type
    params are exact (case-insensitive) matches; ``stock`` is 'in' or 'out'.
    All present filters combine with AND; blank params are ignored.
    """
    clauses, params = [], []
    like = helpers.like_pattern(request.args.get("q"))
    if like is not None:
        clauses.append("(color_name LIKE ? ESCAPE '\\' COLLATE NOCASE "
                       "OR brand LIKE ? ESCAPE '\\' COLLATE NOCASE)")
        params.extend((like, like))
    for param, column in EXACT_FILTERS:
        text = (request.args.get(param) or "").strip()
        if text:
            clauses.append(f"{column} = ? COLLATE NOCASE")
            params.append(text)
    stock = (request.args.get("stock") or "").strip().lower()
    if stock in STOCK_FILTERS:
        clauses.append(STOCK_FILTERS[stock])
    elif stock:
        raise APIError("Unknown stock filter — use 'in' or 'out'.", 400)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return ("SELECT * FROM filaments" + where
            + " ORDER BY type, color_name, brand, id"), params


@bp.get("")
def list_filaments():
    """List all filaments.

    Optional, combinable filters (all case-insensitive):
      q=       free-text search on color name or brand
      color=   exact color name
      brand=   exact brand
      type=    exact type (PLA / PETG / …)
      stock=   'in' (grams left) or 'out' (no stock)
    """
    sql, params = _list_query()
    return jsonify([helpers.filament_to_dict(r) for r in db.query_all(sql, params)])


@bp.get("/facets")
def list_facets():
    """Distinct values for the filaments page's filter dropdowns.

    Values keep their stored spelling and are sorted case-insensitively;
    blanks are dropped — a spool without a brand isn't a filter target.
    (Feeds exactly what /api/filaments' brand/color/type filters accept.)
    """
    rows = db.query_all("SELECT color_name, brand, type FROM filaments")

    def distinct(column):
        vals = {str(r[column]).strip() for r in rows if str(r[column] or "").strip()}
        return sorted(vals, key=str.lower)

    return jsonify({
        "colors": distinct("color_name"),
        "brands": distinct("brand"),
        "types": distinct("type"),
    })


@bp.post("")
def create_filament():
    data = _parse_payload()
    cur = db.execute(
        """INSERT INTO filaments (color_name, type, brand, purchase_link,
                                  spool_weight_g, cost_per_spool, current_stock_g,
                                  image_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (data["color_name"], data["type"], data["brand"], data["purchase_link"],
         data["spool_weight_g"], data["cost_per_spool"], data["current_stock_g"],
         data["image_path"], data["notes"]))
    return jsonify(helpers.filament_to_dict(
        db.query_one("SELECT * FROM filaments WHERE id = ?", (cur.lastrowid,)))), 201


@bp.get("/<int:fid>")
def get_filament(fid):
    d = helpers.filament_to_dict(_get_or_404(fid))
    d["related_models"] = [dict(r) for r in db.query_all(RELATED_MODELS_SQL, (fid,))]
    return jsonify(d)


@bp.put("/<int:fid>")
def update_filament(fid):
    existing = _get_or_404(fid)
    data = _parse_payload(existing=existing)
    if data["image_path"] != existing["image_path"] and existing["image_path"]:
        helpers.delete_upload(existing["image_path"])
    db.execute(
        """UPDATE filaments SET color_name=?, type=?, brand=?, purchase_link=?,
             spool_weight_g=?, cost_per_spool=?, current_stock_g=?, image_path=?, notes=?,
             updated_at = datetime('now')
           WHERE id = ?""",
        (data["color_name"], data["type"], data["brand"], data["purchase_link"],
         data["spool_weight_g"], data["cost_per_spool"], data["current_stock_g"],
         data["image_path"], data["notes"], fid))
    return jsonify(helpers.filament_to_dict(
        db.query_one("SELECT * FROM filaments WHERE id = ?", (fid,))))


@bp.delete("/<int:fid>")
def delete_filament(fid):
    row = _get_or_404(fid)
    db.execute("DELETE FROM filaments WHERE id = ?", (fid,))
    helpers.delete_upload(dict(row)["image_path"])
    return {"ok": True}


@bp.delete("/bulk")
def bulk_delete_filaments():
    """Delete several filaments in one request (the filaments page's select mode).

    Accepts ``{"ids": [1, 2, 3]}`` — a bare list works too. Unknown, junk and
    boolean values are skipped rather than failing the request (mirroring the
    CSV importer's "skip the bad rows" philosophy); a float is only a valid id
    when it is a whole number. At most ``MAX_BULK_DELETE_IDS`` ids per request
    — beyond that it's API misuse, and it would also exceed SQLite's
    per-statement parameter limit with a 500 instead of a clean 400.
    Uploaded images of the deleted rows are cleaned up, and models using
    these filaments simply lose the link (``model_filaments`` cascades).
    """
    payload = request.get_json(silent=True)
    ids = payload.get("ids") if isinstance(payload, dict) else payload
    if not isinstance(ids, (list, tuple)):
        raise APIError("Expected {\"ids\": [1, 2, …]}.", 400)

    cleaned, seen = [], set()
    for raw in ids:
        if isinstance(raw, bool):
            continue                    # JSON true/false are not ids (int(True) would be 1!)
        if isinstance(raw, float) and not raw.is_integer():
            continue                    # 1.5 is not id 1
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue                    # skip junk, keep the rest
        if value > 2**63 - 1:
            continue                    # beyond INT64 — would 500 at bind time
        if value > 0 and value not in seen:
            seen.add(value)
            cleaned.append(value)
    if not cleaned:
        raise APIError("Give me at least one filament id to delete.", 400)
    if len(cleaned) > MAX_BULK_DELETE_IDS:
        raise APIError(
            f"Too many ids — at most {MAX_BULK_DELETE_IDS} filaments per request.", 400)

    placeholders = ",".join("?" for _ in cleaned)
    rows = db.query_all(
        f"SELECT * FROM filaments WHERE id IN ({placeholders})", cleaned)
    for row in rows:
        db.execute("DELETE FROM filaments WHERE id = ?", (row["id"],))
        helpers.delete_upload(dict(row)["image_path"])
    return {"ok": True, "deleted": len(rows)}


# ---------------------------------------------------------------------------
# Bulk CSV import
# ---------------------------------------------------------------------------

# Column headings the importer understands. Comparison is case-insensitive
# and happens after stripping parenthesized units and punctuation, so
# "Spool Weight (g)", "spool_weight_g" and "SPOOL-WEIGHT" all match.
HEADER_ALIASES = {
    "color": "color_name", "color name": "color_name", "name": "color_name",
    "filament": "color_name", "filament name": "color_name",
    "type": "type", "material": "type", "material type": "type",
    "brand": "brand", "manufacturer": "brand", "maker": "brand",
    "purchase link": "purchase_link", "link": "purchase_link", "url": "purchase_link",
    "web link": "purchase_link", "shop link": "purchase_link", "buy link": "purchase_link",
    "shop": "purchase_link", "buy": "purchase_link",
    "spool weight": "spool_weight_g", "spool weight g": "spool_weight_g",
    "weight": "spool_weight_g", "weight g": "spool_weight_g",
    "grams": "spool_weight_g", "spool grams": "spool_weight_g",
    "cost per spool": "cost_per_spool", "cost": "cost_per_spool",
    "price": "cost_per_spool", "price per spool": "cost_per_spool",
    "stock": "current_stock_g", "current stock": "current_stock_g",
    "current stock g": "current_stock_g", "grams left": "current_stock_g",
    "remaining": "current_stock_g", "remaining g": "current_stock_g",
    "left": "current_stock_g",
    "notes": "notes", "note": "notes", "description": "notes",
    "comment": "notes", "comments": "notes",
}
REQUIRED_COLUMNS = ("color_name", "type", "spool_weight_g", "cost_per_spool")
FIELD_LABELS = {
    "color_name": "color name", "type": "type", "brand": "brand",
    "purchase_link": "purchase link", "spool_weight_g": "spool weight (g)",
    "cost_per_spool": "cost per spool", "current_stock_g": "stock (g)", "notes": "notes",
}
MAX_LEN = {"color_name": 100, "type": 50, "brand": 200,
           "purchase_link": 500, "notes": 2000}
MAX_IMPORT_ROWS = 5000
MAX_CSV_BYTES = 2 * 1024 * 1024          # 2 MB is plenty for a spool list


# ----------------------- parsing / validation helpers -----------------------

def _norm_header(h):
    h = re.sub(r"\(.*?\)", " ", h.lower())
    return re.sub(r"[^a-z0-9]+", " ", h).strip()


def _detect_delimiter(first_line):
    try:
        return csv.Sniffer().sniff(first_line, delimiters=",;\t|").delimiter
    except csv.Error:
        best, best_n = ",", 0
        for cand in (",", ";", "\t", "|"):
            n = first_line.count(cand)
            if n > best_n:
                best, best_n = cand, n
        return best


def _clean_number(text):
    """'$20.00 ' / '1,234' / '15' / '1e5' -> float, or None when not a usable
    number. Currency symbols, unit suffixes and (US-style) thousands
    separators are ignored; the decimal point is significant."""
    t = str(text).strip()
    if not t:
        return None
    # First pass: accept values that are already (near-)valid numbers after
    # stripping $ / thousands-commas / spaces — this preserves exotic-but-
    # correct forms like '1e5', which the fallback letter-stripping below
    # would mangle into 15.0.
    for cand in (t, t.replace("$", "").replace(",", "").replace(" ", "")):
        try:
            v = float(cand)
        except ValueError:
            continue
        return v if math.isfinite(v) else None
    # Fallback: drop currency symbols, letters/units and anything else
    # non-numeric, then try once more.
    t2 = re.sub(r"[^\d.\-+]", "", t)
    if t2 in ("", ".", "+", "-", "+.", "-.", "-+"):
        return None
    try:
        v = float(t2)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _parse_csv(text):
    """Validate the whole file BEFORE importing anything. Returns
    (data_rows, colmap, unknown_headers) or raises APIError(400)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise APIError("The CSV file is empty.", 400)
    delimiter = _detect_delimiter(lines[0])
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)
            if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise APIError("The CSV has no data rows (a header row plus at least "
                       "one filament row is expected).", 400)
    raw_header = [str(h).strip() for h in rows[0]]
    colmap, unknown = {}, []
    for idx, h in enumerate(raw_header):
        norm = _norm_header(h)
        if not norm:
            continue
        if norm in HEADER_ALIASES:
            colmap.setdefault(HEADER_ALIASES[norm], (idx, h or FIELD_LABELS[HEADER_ALIASES[norm]]))
        else:
            unknown.append(h)
    missing = [c for c in REQUIRED_COLUMNS if c not in colmap]
    if missing:
        pretty = "; ".join(
            "a {0} column (e.g. “{1}”)".format(
                FIELD_LABELS[c],
                next(i for i, v in HEADER_ALIASES.items() if v == c))
            for c in missing)
        raise APIError("Unrecognized CSV header — missing: " + pretty + ". "
                       "Download the template to see the expected columns.", 400)
    data_rows = rows[1:]
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise APIError(f"Too many rows ({len(data_rows)}). "
                       f"Maximum is {MAX_IMPORT_ROWS}.", 400)
    return data_rows, colmap, unknown


def _parse_row(row, colmap):
    """Validate one data row. Returns (values_dict, None) or (None, reason)."""
    def get(field):
        cell = colmap.get(field)
        if cell is None:
            return None
        idx, _label = cell
        return str(row[idx]).strip() if idx < len(row) else ""

    color = get("color_name")
    if not color:
        return None, f"missing required value “{FIELD_LABELS['color_name']}”"
    if len(color) > MAX_LEN["color_name"]:
        return None, f"{FIELD_LABELS['color_name']} exceeds {MAX_LEN['color_name']} characters"

    ftype = get("type")
    if not ftype:
        return None, f"missing required value “{FIELD_LABELS['type']}”"
    if len(ftype) > MAX_LEN["type"]:
        return None, f"{FIELD_LABELS['type']} exceeds {MAX_LEN['type']} characters"

    def num(field, required, minimum):
        raw = get(field)
        if raw is None or raw == "":
            if required:
                return None, f"missing required number “{FIELD_LABELS[field]}”"
            return 0.0, None
        v = _clean_number(raw)
        if v is None:
            return None, f"“{FIELD_LABELS[field]}” must be a number (got “{raw[:40]}”)"
        if v < minimum:
            return None, f"“{FIELD_LABELS[field]}” must be at least {minimum:g}"
        if v > 1e12:
            return None, f"“{FIELD_LABELS[field]}” is too large"
        return v, None

    weight, err = num("spool_weight_g", True, 1)
    if err:
        return None, err
    cost, err = num("cost_per_spool", True, 0)
    if err:
        return None, err
    stock, err = num("current_stock_g", False, 0)
    if err:
        return None, err

    brand = get("brand") or ""
    if len(brand) > MAX_LEN["brand"]:
        return None, f"{FIELD_LABELS['brand']} exceeds {MAX_LEN['brand']} characters"
    link = get("purchase_link") or ""
    if len(link) > MAX_LEN["purchase_link"]:
        return None, f"{FIELD_LABELS['purchase_link']} exceeds {MAX_LEN['purchase_link']} characters"
    if link and not helpers.WEB_LINK_RE.match(link):
        return None, f"{FIELD_LABELS['purchase_link']} must be an http(s):// link (or blank)"
    notes = get("notes") or ""
    if len(notes) > MAX_LEN["notes"]:
        return None, f"{FIELD_LABELS['notes']} exceeds {MAX_LEN['notes']} characters"

    return {"color_name": color, "type": ftype.upper(), "brand": brand,
            "purchase_link": link, "spool_weight_g": int(round(weight)),
            "cost_per_spool": cost, "current_stock_g": stock, "notes": notes}, None


# ---------------------------- endpoints ----------------------------

TEMPLATE_CSV = (
    "Color,Type,Brand,Spool weight (g),Cost per spool (USD),Stock (g),Purchase link,Notes\n"
    "Cobalt Blue,PLA,Sample Spools,1000,20.00,875,https://example.com/spool,Primes well\n"
    "Sail,ASA,Sample Spools,750,15.50,0,,\n"
)


@bp.get("/import/template")
def import_template():
    """Downloadable CSV with the expected headers plus two example rows."""
    return Response(
        TEMPLATE_CSV,
        mimetype="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="filament-import-template.csv"'})


@bp.post("/import")
def import_filaments():
    """Bulk-create filaments from an uploaded CSV (multipart field ``file``).

    Import is ADDITIVE — it never modifies or deletes existing filaments;
    duplicate color names are allowed. Rows that don't validate are skipped
    with a per-row reason, so a mostly-good file still imports its good rows.
    """
    f = request.files.get("file")
    if f is None or not f.filename:
        raise APIError("Send the CSV as a multipart file field named 'file'.", 400)
    raw = f.stream.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise APIError("CSV file is too large (2 MB max).", 400)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")    # Excel's other favorite
    data_rows, colmap, unknown = _parse_csv(text)

    imported = 0
    skipped = []
    for n, row in enumerate(data_rows, start=2):     # 1-based, past the header
        vals, err = _parse_row(row, colmap)
        if err:
            skipped.append({"row": n, "reason": err})
            continue
        db.execute(
            """INSERT INTO filaments (color_name, type, brand, purchase_link,
                                      spool_weight_g, cost_per_spool, current_stock_g,
                                      image_path, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (vals["color_name"], vals["type"], vals["brand"], vals["purchase_link"],
             vals["spool_weight_g"], vals["cost_per_spool"], vals["current_stock_g"],
             "", vals["notes"]))
        imported += 1

    if imported == 0:
        raise APIError("No filaments could be imported — see the per-row "
                       "problems below.", 400,
                       details={"skipped": skipped, "unknown_headers": unknown})
    return jsonify({"imported": imported, "skipped": skipped,
                    "total_rows": len(data_rows), "unknown_headers": unknown}), 201
