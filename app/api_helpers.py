"""Shared helpers for /api routes: field parsing, image uploads, serializers.

All mutation endpoints accept *either* JSON bodies or multipart/form-data
(this lets the browser send picture files together with the other fields via
a single FormData post).
"""
import math
import os
import re
import time
import uuid

from flask import current_app, request

from .errors import APIError

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


# ---------------------------------------------------------------------------
# Request-field accessors (form-first, JSON fallback)
# ---------------------------------------------------------------------------
def field(name, default=None):
    """Read a field from form data or the JSON body, whichever the client used."""
    if name in request.form:
        return request.form[name]
    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, dict) and name in body:
            return body[name]
    return default


def str_field(name, default="", required=False, max_len=1000):
    raw = field(name)
    if isinstance(raw, bool):
        raise APIError(f"Field '{name}' must be text, not a boolean.")
    if raw is None:
        if required:
            raise APIError(f"Field '{name}' is required.")
        return default
    text = str(raw).strip()
    if required and not text:
        raise APIError(f"Field '{name}' is required.")
    if len(text) > max_len:
        raise APIError(f"Field '{name}' is too long (max {max_len} chars).")
    return text


def float_field(name, default=0.0, required=False, min_value=None, max_value=None):
    raw = field(name)
    if isinstance(raw, bool):
        raise APIError(f"Field '{name}' must be a number, not a boolean.")
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if required:
            raise APIError(f"Field '{name}' is required.")
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise APIError(f"Field '{name}' must be a number (got {raw!r}).")
    if not math.isfinite(value):
        raise APIError(f"Field '{name}' must be a finite number (got {raw!r}).")
    if min_value is not None and value < min_value:
        raise APIError(f"Field '{name}' must be {min_value} or greater.")
    if max_value is not None and value > max_value:
        raise APIError(f"Field '{name}' is too large (max {max_value:g}).")
    return value


def int_list_field(name, default=()):
    """Parse a list of integers from a JSON array, list, or '1,2,3' string.

    Rejects rather than coerces: booleans, non-whole floats and values
    outside SQLite's INTEGER range are all 400 (int(True) would be 1!;
    int(1.9) would silently become id 1; values past INT64 would blow up
    at bind time with a 500)."""
    raw = field(name)
    if raw is None or raw == "":
        return list(default)
    parts = raw if isinstance(raw, (list, tuple)) else [p.strip() for p in str(raw).split(",")]
    out = []
    for p in parts:
        if p == "":
            continue
        if isinstance(p, bool):
            raise APIError(f"Invalid id in field '{name}': booleans are not ids.")
        if isinstance(p, float) and not p.is_integer():
            raise APIError(f"Invalid id in field '{name}': {p!r} is not a whole number.")
        try:
            value = int(p)
        except (TypeError, ValueError, OverflowError):
            raise APIError(f"Invalid id in field '{name}': {p!r}.")
        if not (1 <= value <= 2**63 - 1):
            raise APIError(f"Invalid id in field '{name}': {value} is out of range.")
        out.append(value)
    return out


# Stored web-link fields (filament purchase links) are rendered straight into
# an ``href`` in the UI — only plain http(s) links are acceptable there, so
# ``javascript:`` / ``data:`` / ``vbscript:`` co. are rejected at the boundary
# (a stored script-scheme link is a stored-XSS vector once someone clicks it).
WEB_LINK_RE = re.compile(r"^https?://", re.IGNORECASE)


def web_link_field(name, default="", required=False, max_len=500):
    """A web-link field: ``http(s)://…`` or blank — nothing else."""
    text = str_field(name, default=default, required=required, max_len=max_len)
    if text and not WEB_LINK_RE.match(text):
        raise APIError(f"Field '{name}' must be an http(s):// web link (or left blank).")
    return text


def json_field(name, default=None):
    """Parse a field that holds a JSON document (sent as string or native)."""
    raw = field(name)
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    import json
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise APIError(f"Field '{name}' must be valid JSON.")


# ---------------------------------------------------------------------------
# Free-text search (list endpoints)
# ---------------------------------------------------------------------------
def like_pattern(value):
    """Build a safe ``%...%`` LIKE pattern from a free-text query.

    Returns ``None`` for an empty query, or an escaped ``%term%`` pattern
    otherwise. LIKE metacharacters (``%``, ``_``, ``\\``) in user input are
    escaped so searches match literally — always pair with ``ESCAPE '\\'``.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ---------------------------------------------------------------------------
# Image uploads (pictures for filaments / models)
# ---------------------------------------------------------------------------
def save_image(subdir):
    """Store uploaded ``picture`` file under <uploads>/<subdir>/<uuid><ext>.

    Returns the stored relative path, or '' when no file was sent.
    """
    file = request.files.get("picture")
    if file is None or not file.filename:
        return ""
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise APIError("Unsupported image type. Use PNG, JPEG, GIF, WEBP or BMP.")
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], subdir)
    os.makedirs(folder, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{ext}"
    file.save(os.path.join(folder, safe_name))
    return f"{subdir}/{safe_name}"


def delete_upload(rel_path):
    """Best-effort removal of an uploaded file recorded on a row.

    The stored path is server-generated (``<subdir>/<uuid><ext>``), but the
    value ultimately comes from the database — so only delete files that
    resolve *inside* the uploads root, never anything outside it.

    The file may still be held open by an in-flight request that is serving
    it (on Windows an open file cannot be removed), so retry briefly before
    giving up — and leave a log line if it truly sticks around.
    """
    if not rel_path:
        return
    root = os.path.abspath(current_app.config["UPLOAD_FOLDER"])
    path = os.path.abspath(os.path.join(root, rel_path))
    if not path.startswith(root + os.sep):
        return
    attempts = 5
    for attempt in range(attempts):
        if not os.path.isfile(path):
            return
        try:
            os.remove(path)
            return
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(0.1)
    try:
        current_app.logger.warning("Could not delete stale upload file: %s", path)
    except RuntimeError:  # no app context (shouldn't happen for route calls)
        print(f"WARNING: could not delete stale upload file: {path}")


# ---------------------------------------------------------------------------
# Serializers (sqlite3.Row -> API dict)
# ---------------------------------------------------------------------------
def filament_to_dict(row):
    d = dict(row)
    d["in_stock"] = float(d.get("current_stock_g") or 0) > 0
    spool_g = float(d.get("spool_weight_g") or 0)
    cost_per_kg = float(d.get("cost_per_spool") or 0) / (spool_g / 1000.0) if spool_g > 0 else 0.0
    # A legacy row with absurdly large values could overflow to Infinity, which
    # is NOT legal JSON (breaks strict clients) — report it as 0 instead.
    d["cost_per_kg"] = round(cost_per_kg, 4) if math.isfinite(cost_per_kg) else 0.0
    d["picture_url"] = f"/uploads/{d['image_path']}" if d.get("image_path") else None
    return d


def model_to_dict(row):
    d = dict(row)
    d["picture_url"] = f"/uploads/{d['image_path']}" if d.get("image_path") else None
    return d
