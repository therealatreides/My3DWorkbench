"""End-to-end API tests against a throwaway database + uploads dir.

Exercises the full REST contract with Flask's test client: CRUD on all four
resources, search semantics (incl. LIKE-wildcard escaping), uploads, cascade
deletes, per-printer overrides, ``?printer=`` previews, settings validation,
and error envelopes. Cost figures are cross-checked against the pure engine
(my3d_workbench/cost.py) computed independently in the test.

Run via ``python tests/run_tests.py`` (env is set up there, before import).
"""
import importlib
import contextlib
import io
import json
import os
import sqlite3
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from my3d_workbench import create_app           # noqa: E402
from my3d_workbench import cost as ce           # noqa: E402

APP = create_app()
APP.config["TESTING"] = True
CLIENT = APP.test_client()
UP_ROOT = APP.config["UPLOAD_FOLDER"]
DB_PATH = APP.config["DATABASE_PATH"]

# Shared fixtures created lazily; ids are unique per run (fresh temp DB).
FID_A = FID_B = FID_C = None    # literal-wildcard search trio
MODEL_ID = None
PRINTER_ID = None


# ---------------------------------------------------------------- utils ----
def close(a, b, tol=1e-9):
    assert abs(a - b) <= tol * max(1.0, abs(a), abs(b)), f"{a!r} !~ {b!r}"


def json_body(resp):
    return json.loads(resp.get_data(as_text=True))


def get(path):
    r = CLIENT.get(path)
    assert r.status_code == 200, f"GET {path} -> {r.status_code}: {r.get_data(as_text=True)[:300]}"
    return json_body(r)


def post(path, body=None, data=None, expected=201):
    r = (CLIENT.post(path, json=body) if body is not None
         else CLIENT.post(path, data=data, content_type="multipart/form-data"))
    assert r.status_code == expected, \
        f"POST {path} ({body if data is None else sorted(data)}) -> {r.status_code}: {r.get_data(as_text=True)[:400]}"
    return json_body(r) if r.get_data(as_text=True) else None


def put(path, body=None, data=None, expected=200):
    r = (CLIENT.put(path, json=body) if body is not None
         else CLIENT.put(path, data=data, content_type="multipart/form-data"))
    assert r.status_code == expected, \
        f"PUT {path} -> {r.status_code}: {r.get_data(as_text=True)[:400]}"
    return json_body(r) if r.get_data(as_text=True) else None


def make_png(width=1, height=1):
    def chunk(tag, payload):
        c = struct.pack(">I", len(payload)) + tag + payload
        return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"\xff\xff\xff\xff" * (width * height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def settings_map():
    return {s["key"]: s["value"] for s in get("/api/settings")["settings"]}


def global_rate(overrides=None):
    merged = dict(settings_map())
    for k, v in (overrides or {}).items():
        if not (isinstance(v, str) and v.strip() == ""):
            merged[k] = v
    return ce.compute_machine_rate(merged)["rate_per_hr"]


# -------------------------------------------------- fixtures (ordered) ----
# NOTE: fixtures are set up inside named tests (fixture_* come first
# alphabetically-ish via numbering) so the suite is readable top-to-bottom.

def _mk_filament(color, brand="", weight=1000, cost=20.0, stock=1250, **extra):
    body = {"color_name": color, "type": "PLA", "brand": brand,
            "spool_weight_g": weight, "cost_per_spool": cost,
            "current_stock_g": stock}
    body.update(extra)
    return post("/api/filaments", body)


def test_00_ui_index():
    r = CLIENT.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "My 3D Workbench" in html and "/static/js/app.js" in html


def test_01_health():
    d = get("/api/health")
    assert d["status"] == "ok" and "filaments" in d and "models" in d


def test_02_error_envelopes_are_json():
    r = CLIENT.get("/api/no_such_endpoint")
    assert r.status_code == 404 and json_body(r)["error"]
    r = CLIENT.post("/api/filaments/999999")   # path exists only for GET/PUT/DELETE
    assert r.status_code == 405 and json_body(r)["error"]


def test_03_wal_and_schema():
    with db() as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal", mode
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"filaments", "models", "model_filaments",
                "settings", "printers", "printer_settings"} <= tables
        # packaging/line items were removed — the old table must be gone
        assert "model_items" not in tables


def test_10_filament_create_and_derived_fields():
    f = _mk_filament("Cobalt Blue", brand="Generic", weight=1000, cost=20.0, stock=1250)
    global FID_A
    FID_A = f["id"]
    close(f["cost_per_kg"], 20.0)          # 20 / (1000g) = $20/kg
    assert f["in_stock"] is True
    assert f["picture_url"] is None
    assert f["type"] == "PLA"
    f2 = _mk_filament("Coral 700", brand="GenericB", weight=7000, cost=14.0, stock=0)
    close(f2["cost_per_kg"], 2.0)
    assert f2["in_stock"] is False


def test_11_filament_list_and_sort():
    rows = get("/api/filaments")
    assert len(rows) >= 2
    types = [r["type"] for r in rows]
    assert types == sorted(types)


def test_12_filament_search_semantics():
    # Literal-wildcard trio: LIKE meta-characters must be literal, not wildcards.
    a = _mk_filament("50% off special", brand="Plain")
    b = _mk_filament("fifty off", brand="Plain")
    c = _mk_filament("fifty_x blend", brand="Plain")
    global FID_B, FID_C
    FID_B, FID_C = b["id"], c["id"]

    q_percent = [r["color_name"] for r in get('/api/filaments?q=%25')]
    assert q_percent == ["50% off special"], q_percent      # '%' is literal

    q_under = [r["color_name"] for r in get('/api/filaments?q=_')]
    assert q_under == ["fifty_x blend"], q_under             # '_' is literal

    q_fifty = sorted(r["color_name"] for r in get('/api/filaments?q=FIFTY'))
    assert q_fifty == ["fifty off", "fifty_x blend"], q_fifty   # case-insensitive

    q_brand = [r["color_name"] for r in get('/api/filaments?q=Plain')]
    assert len(q_brand) == 3 and "50% off special" in q_brand   # brand match

    assert get('/api/filaments?q=zzz_nothing') == []


def test_13_filament_validation():
    for bad in (
        {"type": "PLA", "spool_weight_g": 1000, "cost_per_spool": 1},            # no name
        {"color_name": "x", "spool_weight_g": 1000, "cost_per_spool": 1},        # no type
        {"color_name": "x", "type": "PLA", "spool_weight_g": 0, "cost_per_spool": 1},
        {"color_name": "x", "type": "PLA", "spool_weight_g": 1000, "cost_per_spool": -1},
        {"color_name": "x", "type": "PLA", "spool_weight_g": "abc", "cost_per_spool": 1},
        {"color_name": "x", "type": "PLA", "spool_weight_g": 1e15, "cost_per_spool": 1},  # INT64 guard
        {"color_name": "x", "type": "PLA", "spool_weight_g": "inf", "cost_per_spool": 1},
    ):
        r = CLIENT.post("/api/filaments", json=bad)
        assert r.status_code == 400, (bad, r.status_code, r.get_data(as_text=True)[:200])
    # boundary: exactly 1e12 g is still a legal SQLite INTEGER and must be accepted
    ok = _mk_filament("Huge Spool", weight=10**12, cost=1.0)
    CLIENT.delete(f"/api/filaments/{ok['id']}")


def test_14_filament_detail_and_404():
    d = get(f"/api/filaments/{FID_A}")
    assert d["color_name"] == "Cobalt Blue" and "related_models" in d
    r = CLIENT.get("/api/filaments/999999")
    assert r.status_code == 404 and json_body(r)["error"]


def test_15_filament_list_filters():
    # Three rows with one unique brand + one unique color each so every
    # filter is unambiguous; mixed stock states cover in/out.
    a = _mk_filament("Filter Red", brand="Filtrate Co", stock=500)
    b = _mk_filament("Filter Blue", brand="Filtrate Co", stock=0)
    t = _mk_filament("Filter Green", brand="Filtrate Co", stock=10, type="PETG")
    try:
        # brand: exact + case-insensitive — NOT a substring match
        brand = get("/api/filaments?brand=Filtrate%20Co")
        assert {r["color_name"] for r in brand} == {"Filter Red", "Filter Blue", "Filter Green"}
        assert get("/api/filaments?brand=filtrate") == []          # substring must NOT match
        assert [r["color_name"] for r in get("/api/filaments?brand=GENERIC")] == ["Cobalt Blue"]

        # color: exact match on the color name (case-insensitive)
        assert [r["color_name"] for r in get("/api/filaments?color=FILTER%20RED")] == ["Filter Red"]
        assert get("/api/filaments?color=red") == []

        # type: case-insensitive exact match
        types = get("/api/filaments?type=peTg")
        assert {r["type"] for r in types} == {"PETG"}, types
        assert {r["color_name"] for r in types} == {"Filter Green"}

        # stock: in = grams left, out = zero
        out = get("/api/filaments?brand=Filtrate%20Co&stock=out")
        assert {r["color_name"] for r in out} == {"Filter Blue"}
        inn = get("/api/filaments?brand=Filtrate%20Co&stock=in")
        assert {r["color_name"] for r in inn} == {"Filter Red", "Filter Green"}

        # filters stack with the free-text search (AND semantics)
        combo = get("/api/filaments?q=filter&stock=out")
        assert {r["color_name"] for r in combo} == {"Filter Blue"}

        # blank params are ignored entirely; unknown stock values are 400
        total = len(get("/api/filaments"))
        assert len(get("/api/filaments?brand=&type=&stock=")) == total
        r = CLIENT.get("/api/filaments?stock=maybe")
        assert r.status_code == 400 and json_body(r)["error"]

        # facets feed the dropdowns: distinct, ci-sorted, no blanks,
        # and exactly the values the filters above accepted
        fac = get("/api/filaments/facets")
        assert "Filtrate Co" in fac["brands"] and "Generic" in fac["brands"]
        assert "Filter Red" in fac["colors"]
        assert fac["types"] == ["PETG", "PLA"], fac["types"]
        for key in ("brands", "colors", "types"):
            vals = fac[key]
            assert vals == sorted(vals, key=str.lower), (key, vals)
            assert all(v and v == v.strip() for v in vals), (key, vals)
    finally:
        for row in (a, b, t):
            CLIENT.delete(f"/api/filaments/{row['id']}")


def test_20_upload_lifecycle():
    px = make_png()
    f = post("/api/filaments", data={
        "color_name": "Uploady", "type": "PLA", "spool_weight_g": "1000",
        "cost_per_spool": "10", "picture": (io.BytesIO(px), "spool.png")})
    assert f["picture_url"], f
    rel = f["picture_url"].split("/uploads/", 1)[1]
    assert os.path.isfile(os.path.join(UP_ROOT, rel))
    r = CLIENT.get(f["picture_url"])
    assert r.status_code == 200 and r.get_data() == px
    del r  # release the response (it may still hold the image file open —
           # on Windows an open file cannot be deleted)

    # replace with a second upload -> old file disappears
    other = make_png(2, 2)
    f2 = put(f"/api/filaments/{f['id']}", data={
        "color_name": "Uploady2", "type": "PLA", "spool_weight_g": "1000",
        "cost_per_spool": "12", "picture": (io.BytesIO(other), "spool2.png")})
    assert f2["picture_url"] != f["picture_url"]
    assert not os.path.exists(os.path.join(UP_ROOT, rel)), "old upload must be deleted"

    # delete the row -> its file goes too
    rel2 = f2["picture_url"].split("/uploads/", 1)[1]
    assert CLIENT.delete(f"/api/filaments/{f['id']}").status_code == 200
    assert not os.path.exists(os.path.join(UP_ROOT, rel2))


def test_21_upload_update_keeps_image_when_none_sent():
    f = post("/api/filaments", data={
        "color_name": "Keeper", "type": "PLA", "spool_weight_g": "1000",
        "cost_per_spool": "10", "picture": (io.BytesIO(make_png()), "k.png")})
    url = f["picture_url"]
    f2 = put(f"/api/filaments/{f['id']}", body={
        "color_name": "Keeper New", "type": "PLA", "brand": "",
        "spool_weight_g": 1000, "cost_per_spool": 11, "current_stock_g": 0,
        "notes": "", "purchase_link": ""})
    assert f2["picture_url"] == url, "PUT without a file must keep the current image"
    CLIENT.delete(f"/api/filaments/{f['id']}")


def test_22_upload_rejects_bad_extension():
    px = b"not really an image"
    r = CLIENT.post("/api/filaments", data={
        "color_name": "Bad Ext", "type": "PLA", "spool_weight_g": "1000",
        "cost_per_spool": "1", "picture": (io.BytesIO(px), "evil.exe")},
        content_type="multipart/form-data")
    assert r.status_code == 400, r.get_data(as_text=True)


def test_23_upload_too_large_413_json():
    small = create_app(config_overrides={"MAX_CONTENT_LENGTH": 512})
    c = small.test_client()
    big = make_png(64, 64)
    r = c.post("/api/filaments", data={
        "color_name": "Big", "type": "PLA", "spool_weight_g": "1000",
        "cost_per_spool": "1", "picture": (io.BytesIO(big), "big.png")},
        content_type="multipart/form-data")
    assert r.status_code == 413 and json_body(r)["error"], \
        (r.status_code, r.get_data(as_text=True)[:120])


def test_24_upload_path_traversal_blocked():
    r = CLIENT.get("/uploads/../../../../My3DWorkbench-run.py")
    assert r.status_code == 404
    r2 = CLIENT.get("/uploads/..%2f..%2f..%2fmy3d_workbench%2fcost.py")
    assert r2.status_code in (400, 404)


# ------------------------------------------------------------- models ----
def _mk_model(name, fil_ids=(), grams=None, **extra):
    body = {"name": name, "purpose": "Profit", "description": "",
            "filament_amount_g": 100, "print_time_hr": 1.5, "labor_min": 15,
            "notes": "", "filament_ids": list(fil_ids),
            "filament_grams": json.dumps(grams or {})}
    body.update(extra)
    return post("/api/models", body)


def test_30_model_create_full_breakdown():
    global MODEL_ID
    m = _mk_model("Benchy", fil_ids=[FID_A])
    MODEL_ID = m["id"]
    b = m["breakdown"]
    # independent expectation via the pure engine
    s = settings_map()
    f_row = get(f"/api/filaments/{FID_A}")
    model = {"filament_amount_g": 100, "print_time_hr": 1.5, "labor_min": 15}
    fil = [{"id": f_row["id"], "grams": None, "total_g": 100,
            "cost_per_spool": f_row["cost_per_spool"],
            "spool_weight_g": f_row["spool_weight_g"]}]
    exp = ce.compute_model_cost(model, fil, s)
    for key in ("part_material_cost", "labor_cost", "machine_cost", "total_cost"):
        close(b[key], exp[key], tol=1e-6)
    close(b["machine_rate"], ce.compute_machine_rate(s)["rate_per_hr"], tol=1e-6)
    close(b["suggested"]["50"], exp["suggested"]["50"], tol=1e-6)
    assert "landed_cost" not in b and "packaging_cost" not in b
    assert "items" not in m and m["purpose"] == "Profit" and m["filaments"]


def test_31_model_purpose_normalized():
    m = _mk_model("Lowercase Lululemon", fil_ids=[])
    assert m["purpose"] in ("Personal", "Profit")
    r = CLIENT.post("/api/models", json={"name": "X", "purpose": "Wealth",
                                         "filament_ids": [],
                                         "filament_grams": "{}"})
    assert r.status_code == 400
    CLIENT.delete(f"/api/models/{m['id']}")


def test_32_model_validation_errors():
    cases = [
        {"name": "Dup", "filament_ids": [FID_A, FID_A], "filament_grams": "{}"},
        {"name": "Ghost", "filament_ids": [999999], "filament_grams": "{}"},
        {"name": "BadGrams", "filament_ids": [FID_A],
         "filament_grams": json.dumps({FID_A: "a lot"})},
        {"filament_ids": [FID_A], "filament_grams": "{}"},            # name missing
        {"name": "   ", "filament_ids": [FID_A], "filament_grams": "{}"},   # blank name
        {"name": "NoName2", "filament_ids": True, "filament_grams": "{}"},
    ]
    for body in cases:
        r = CLIENT.post("/api/models", json=body)
        assert r.status_code == 400, (body, r.status_code, r.get_data(as_text=True)[:200])


def test_33_model_list_search():
    all_names = {m["name"] for m in get("/api/models")}
    assert "Benchy" in all_names
    hit = [m for m in get('/api/models?q=BENCHY') if m["name"] == "Benchy"]
    assert hit, "case-insensitive name search failed"
    assert get('/api/models?q=definitely_not_a_model') == []


def test_34_model_update_replaces_links():
    before = get(f"/api/models/{MODEL_ID}")
    after = put(f"/api/models/{MODEL_ID}", body={
        "name": "Benchy MkII", "purpose": "Personal", "description": "v2",
        "filament_amount_g": 200, "print_time_hr": 2, "labor_min": 10,
        "notes": "", "filament_ids": [FID_A, FID_B],
        "filament_grams": json.dumps({str(FID_A): 50})})
    assert after["purpose"] == "Personal"
    assert {f["id"] for f in after["filaments"]} == {FID_A, FID_B}
    assert "items" not in after
    # grams: FID_A pinned to 50 (of 200 total); FID_B gets the 150 remainder
    b = after["breakdown"]
    row_a = [f for f in after["filaments"] if f["id"] == FID_A][0]
    row_b = [f for f in after["filaments"] if f["id"] == FID_B][0]
    close(b["filaments"][0]["grams"], 50, tol=1e-9)
    # cross-check vs engine
    s = settings_map()
    exp = ce.compute_model_cost(
        {"filament_amount_g": 200, "print_time_hr": 2, "labor_min": 10},
        [{"id": row_a["id"], "grams": 50, "total_g": 200,
          "cost_per_spool": row_a["cost_per_spool"], "spool_weight_g": row_a["spool_weight_g"]},
         {"id": row_b["id"], "grams": 150, "total_g": 200,
          "cost_per_spool": row_b["cost_per_spool"], "spool_weight_g": row_b["spool_weight_g"]}],
        s)
    close(b["part_material_cost"], exp["part_material_cost"], tol=1e-6)
    close(b["total_cost"], exp["total_cost"], tol=1e-6)


def test_35_filament_detail_shows_related_models():
    d = get(f"/api/filaments/{FID_A}")
    names = [m["name"] for m in d["related_models"]]
    assert "Benchy MkII" in names, names


def test_36_model_delete_cascades():
    m = _mk_model("Doomed", fil_ids=[FID_A, FID_C])
    with db() as c:
        assert c.execute("SELECT COUNT(*) n FROM model_filaments WHERE model_id=?",
                         (m["id"],)).fetchone()["n"] == 2
    assert CLIENT.delete(f"/api/models/{m['id']}").status_code == 200
    with db() as c:
        assert c.execute("SELECT COUNT(*) n FROM model_filaments WHERE model_id=?",
                         (m["id"],)).fetchone()["n"] == 0
    # deleting a filament also drops just that link
    m2 = _mk_model("Twin", fil_ids=[FID_A, FID_B])
    assert CLIENT.delete(f"/api/filaments/{FID_C if False else FID_B}").status_code == 200
    d = get(f"/api/models/{m2['id']}")
    assert {f["id"] for f in d["filaments"]} == {FID_A}
    CLIENT.delete(f"/api/filaments/{FID_C}")
    CLIENT.delete(f"/api/models/{m2['id']}")
    # FID_B was deleted above; recreate for later tests
    _mk_filament("fifty off", brand="Plain")


def test_37_model_get_404():
    assert CLIENT.get("/api/models/999999").status_code == 404
    assert CLIENT.delete("/api/models/999999").status_code == 404


# ------------------------------------------------------------ printers ----
def test_40_printer_create_with_overrides():
    global PRINTER_ID
    p = post("/api/printers", body={
        "model_name": "P1S", "manufacturer": "Bambu Lab",
        "bed_size": "256 x 256 x 256 mm", "notes": "test rig",
        "printer_cost": 1499, "power_consumption_watts": 350,
        "electricity_cost_kwh": 0.12})
    PRINTER_ID = p["id"]
    assert p["settings"]["printer_cost"] == "1499"
    assert p["settings"]["power_consumption_watts"] == "350"
    assert p["settings"]["electricity_cost_kwh"] == "0.12"
    assert "material_efficiency_factor" not in p["settings"], "unmentioned keys must not gain overrides"
    expected = ce.merge_settings(settings_map(),
                                 {"printer_cost": "1499", "power_consumption_watts": "350",
                                  "electricity_cost_kwh": "0.12"})
    close(p["machine_rate"], ce.compute_machine_rate(expected)["rate_per_hr"], tol=1e-9)
    assert abs(p["machine_rate"] - ce.compute_machine_rate(settings_map())["rate_per_hr"]) > 1e-6


def test_41_printer_list_and_detail():
    rows = get("/api/printers")
    mine = [r for r in rows if r["id"] == PRINTER_ID]
    assert mine and mine[0]["settings"]["printer_cost"] == "1499"
    d = get(f"/api/printers/{PRINTER_ID}")
    assert d["model_name"] == "P1S" and d["bed_size"]
    assert CLIENT.get("/api/printers/999999").status_code == 404


def test_42_printer_update_change_and_clear_override():
    # change one override, leave another, clear a third by sending blank
    p = put(f"/api/printers/{PRINTER_ID}", body={
        "model_name": "P1S Pro", "manufacturer": "Bambu Lab", "bed_size": "", "notes": "",
        "printer_cost": 1999, "power_consumption_watts": 350,
        "electricity_cost_kwh": ""})
    assert p["settings"].get("printer_cost") == "1999"
    assert p["settings"].get("power_consumption_watts") == "350"
    assert "electricity_cost_kwh" not in p["settings"], "blank value must remove the override"
    with db() as c:
        n = c.execute("SELECT COUNT(*) n FROM printer_settings WHERE printer_id=?",
                      (PRINTER_ID,)).fetchone()["n"]
        assert n == 2, n
    expected = ce.merge_settings(settings_map(),
                                 {"printer_cost": "1999", "power_consumption_watts": "350"})
    close(p["machine_rate"], ce.compute_machine_rate(expected)["rate_per_hr"], tol=1e-9)


def test_43_printer_validation_and_404s():
    r = CLIENT.put(f"/api/printers/{PRINTER_ID}",
                   json={"model_name": "X", "printer_cost": "abc"})
    assert r.status_code == 400, r.get_data(as_text=True)
    r = CLIENT.put(f"/api/printers/{PRINTER_ID}",
                   json={"model_name": "X", "labor_hourly_rate": -5})
    assert r.status_code == 400, r.get_data(as_text=True)
    r = CLIENT.get(f"/api/printers/{PRINTER_ID}",
                   headers={"Range": "bytes=0"})  # sanity: still 200 after failed PUTs
    assert r.status_code == 200
    assert CLIENT.put("/api/printers/999999", json={"model_name": "X"}).status_code == 404
    assert CLIENT.delete("/api/printers/999999").status_code == 404


def test_44_models_list_respects_printer_param():
    d = get(f"/api/models/{MODEL_ID}")
    s = settings_map()
    base = ce.compute_model_cost(
        {"filament_amount_g": d["filament_amount_g"],
         "print_time_hr": d["print_time_hr"], "labor_min": d["labor_min"]},
        d["filaments"], s)
    over = ce.merge_settings(s, get(f"/api/printers/{PRINTER_ID}")["settings"])
    withp = ce.compute_model_cost(
        {"filament_amount_g": d["filament_amount_g"],
         "print_time_hr": d["print_time_hr"], "labor_min": d["labor_min"]},
        d["filaments"], over)

    row = [m for m in get("/api/models") if m["id"] == MODEL_ID][0]
    close(row["cost"]["total_cost"], base["total_cost"], tol=1e-6)
    row2 = [m for m in get(f"/api/models?printer={PRINTER_ID}") if m["id"] == MODEL_ID][0]
    close(row2["cost"]["total_cost"], withp["total_cost"], tol=1e-6)
    assert abs(row2["cost"]["total_cost"] - row["cost"]["total_cost"]) > 1e-9, \
        "printer param must change the money"
    for bad in ("abc", "999999"):
        r = CLIENT.get(f"/api/models?printer={bad}")
        assert r.status_code == 400 and json_body(r)["error"], (bad, r.status_code)


def test_45_model_detail_printer_preview():
    d = get(f"/api/models/{MODEL_ID}")
    assert "printer_preview" not in d
    d2 = get(f"/api/models/{MODEL_ID}?printer={PRINTER_ID}")
    pv = d2["printer_preview"]
    assert pv["printer"]["id"] == PRINTER_ID
    assert d2["breakdown"]["machine_rate"] != pv["breakdown"]["machine_rate"]
    s = settings_map()
    exp = ce.merge_settings(s, get(f"/api/printers/{PRINTER_ID}")["settings"])
    m = {"filament_amount_g": d2["filament_amount_g"],
         "print_time_hr": d2["print_time_hr"], "labor_min": d2["labor_min"]}
    close(pv["breakdown"]["total_cost"],
          ce.compute_model_cost(m, d2["filaments"], exp)["total_cost"], tol=1e-6)
    # base breakdown must be untouched by the preview
    close(d2["breakdown"]["total_cost"], d["breakdown"]["total_cost"], tol=1e-12)


def test_46_printer_delete_cascades():
    p = post("/api/printers", body={"model_name": "Temp", "printer_cost": 999})
    with db() as c:
        assert c.execute("SELECT COUNT(*) n FROM printer_settings WHERE printer_id=?",
                         (p["id"],)).fetchone()["n"] == 1
    assert CLIENT.delete(f"/api/printers/{p['id']}").status_code == 204
    with db() as c:
        assert c.execute("SELECT COUNT(*) n FROM printer_settings WHERE printer_id=?",
                         (p["id"],)).fetchone()["n"] == 0
    assert CLIENT.delete(f"/api/printers/{p['id']}").status_code == 404


# ------------------------------------------------------------ settings ----
def test_50_settings_list_shape():
    d = get("/api/settings")
    assert len(d["numeric_keys"]) == len(ce.COST_SETTING_KEYS) == 11
    keys = {s["key"] for s in d["settings"]}
    # the registry is FIXED: exactly the cost keys + currency, nothing else
    assert keys == set(ce.COST_SETTING_KEYS) | {"currency_code"}
    assert d["machine_rate"]["source"] == "computed"
    close(d["machine_rate"]["rate_per_hr"],
          ce.compute_machine_rate(settings_map())["rate_per_hr"], tol=1e-12)


def test_51_settings_registry_is_fixed_no_new_keys():
    """Settings ARE the global cost defaults; arbitrary new keys can't be added."""
    # The add-key endpoint is gone.
    assert CLIENT.post("/api/settings", json={"key": "my_custom_key", "value": "hello"}).status_code == 405
    # Unknown keys are rejected on every write path.
    assert CLIENT.put("/api/settings/my_custom_key", json={"value": "x"}).status_code == 400
    assert CLIENT.put("/api/settings", json={"my_custom_key": "x"}).status_code == 400
    # A stray key from an older database stays invisible and unwritable.
    with sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('leftover_key', 'x')")
    try:
        assert "leftover_key" not in {s["key"] for s in get("/api/settings")["settings"]}
        assert CLIENT.put("/api/settings/leftover_key", json={"value": "y"}).status_code == 400
        assert CLIENT.delete("/api/settings/leftover_key").status_code == 404
    finally:
        with sqlite3.connect(DB_PATH) as c:
            c.execute("DELETE FROM settings WHERE key = 'leftover_key'")


def test_52_settings_update_and_validation():
    r = CLIENT.put("/api/settings/labor_hourly_rate", json={"value": "25"})
    assert r.status_code == 200 and json_body(r)["value"] == "25"
    for bad_value in ("abc", "-3", "1e999", "nan", "inf"):
        r = CLIENT.put("/api/settings/labor_hourly_rate", json={"value": bad_value})
        assert r.status_code == 400, (bad_value, r.status_code)
    # restore
    CLIENT.put("/api/settings/labor_hourly_rate", json={"value": "20"})


def test_53_settings_bulk_update():
    r = CLIENT.put("/api/settings", json={"labor_hourly_rate": "22",
                                          "material_efficiency_factor": "1.2"})
    assert r.status_code == 200
    s = settings_map()
    assert s["labor_hourly_rate"] == "22" and s["material_efficiency_factor"] == "1.2"
    # restore
    CLIENT.put("/api/settings", json={"labor_hourly_rate": "20",
                                      "material_efficiency_factor": "1.1"})


def test_54_settings_bulk_atomic_on_bad_pair():
    s0 = settings_map()
    r = CLIENT.put("/api/settings", json={"labor_hourly_rate": "77",
                                          "material_efficiency_factor": "not-a-number"})
    assert r.status_code == 400, r.get_data(as_text=True)
    s1 = settings_map()
    assert s0 == s1, "a bad pair in a bulk update must leave ALL values untouched"


def test_55_settings_bulk_invalid_json_body_is_400():
    r = CLIENT.put("/api/settings", data="{not json", content_type="application/json")
    assert r.status_code == 400 and json_body(r)["error"]
    r2 = CLIENT.put("/api/settings", json=[1, 2, 3])
    assert r2.status_code == 400


def test_56_settings_delete():
    """DELETE is a *reset to the seeded default*, not a row removal: unknown keys are a 404, known keys always come back as 200 with the seed value \u2014 including a second DELETE, because the row was never actually removed."""
    assert CLIENT.delete("/api/settings/never_existed").status_code == 404
    assert CLIENT.delete("/api/settings/unknown_freeform_key").status_code == 404
    CLIENT.put("/api/settings/additional_upfront_cost", json={"value": "555"})
    assert settings_map()["additional_upfront_cost"] == "555"
    r = CLIENT.delete("/api/settings/additional_upfront_cost")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert json_body(r) == {"ok": True, "reset_to": "100"}
    assert settings_map()["additional_upfront_cost"] == "100", "must reset to the seed value"
    assert CLIENT.delete("/api/settings/additional_upfront_cost").status_code == 200
    assert settings_map()["additional_upfront_cost"] == "100"


def test_57_settings_delete_resets_to_seeded_default():
    """DELETE must leave exactly the post-restart state: the key keeps the *seeded* value in-process, so the cost math never silently drops to a missing-key 0 (a deleted labor rate used to read as $0)."""
    CLIENT.put("/api/settings/material_efficiency_factor", json={"value": "2.0"})
    assert settings_map()["material_efficiency_factor"] == "2.0"
    r = CLIENT.delete("/api/settings/material_efficiency_factor")
    assert r.status_code == 200 and json_body(r)["reset_to"] == "1.1"
    assert settings_map()["material_efficiency_factor"] == "1.1"
    # key is still listed (row intact): same registry as a fresh seed
    lst = get("/api/settings")["settings"]
    assert any(s["key"] == "material_efficiency_factor" for s in lst)
    d = get("/api/settings")
    close(d["machine_rate"]["rate_per_hr"], ce.compute_machine_rate(settings_map())["rate_per_hr"])
    # and the labor math reads the seeded rate, not a missing-key 0
    fid = post("/api/filaments", body={"color_name": "Lk77", "type": "PLA",
                                      "spool_weight_g": 1000, "cost_per_spool": 10})["id"]
    m = post("/api/models", body={"name": "LaborReset", "filament_ids": [fid], "labor_min": 60})
    try:
        b = get(f"/api/models/{m['id']}")["breakdown"]
        assert b["labor_cost"] == 20.0, b          # seeded $20/hr (60 min) \u2014 not the missing-key $0
    finally:
        CLIENT.delete(f"/api/models/{m['id']}")
        CLIENT.delete(f"/api/filaments/{fid}")
    assert settings_map()["material_efficiency_factor"] == "1.1"


def test_60_demo_seed_idempotent():
    """SEED_DEMO=1 on a fresh DB creates a coherent demo; re-runs must not
    duplicate. (Uses its own temp database.)"""
    import tempfile
    d = tempfile.mkdtemp(prefix="m3wb-demo-")
    env = dict(os.environ, MY3DWORKBENCH_DB=os.path.join(d, "demo.db"),
               MY3DWORKBENCH_UPLOADS=os.path.join(d, "up"), SEED_DEMO="1")
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from my3d_workbench import create_app\n"
        "app = create_app()\n"
        "with app.app_context():\n"
        "    from my3d_workbench import db\n"
        "    print('fil', db.query_one('SELECT COUNT(*) n FROM filaments')['n'])\n"
        "    print('mod', db.query_one('SELECT COUNT(*) n FROM models')['n'])\n"
        "    print('prn', db.query_one('SELECT COUNT(*) n FROM printers')['n'])\n"
        "    print('ovr', db.query_one('SELECT COUNT(*) n FROM printer_settings')['n'])\n"
        "import os; os.environ['SEED_DEMO']='1'\n"
        "app2 = create_app()\n"
        "with app2.app_context():\n"
        "    from my3d_workbench import db\n"
        "    print('fil2', db.query_one('SELECT COUNT(*) n FROM filaments')['n'])\n"
        "    print('prn2', db.query_one('SELECT COUNT(*) n FROM printers')['n'])\n"
        % src)
    import subprocess
    exe = sys.executable
    out = subprocess.run([exe, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr[-800:]
    lines = dict(l.split(" ", 1) for l in out.stdout.splitlines())
    assert lines["fil"] == "1" and lines["mod"] == "1" and lines["prn"] == "1"
    assert lines["ovr"] == "3"
    assert lines["fil2"] == "1" and lines["prn2"] == "1", "second seed must be idempotent"


# ------------------------------------ browser-fidelity (multipart) contracts ----
# The actual UI submits these as multipart/form-data — replicate the exact
# field shapes from static/js/forms-model.js and views-printers.js.

def test_38_model_form_multipart_like_browser():
    f_new = _mk_filament("Formflow Blend", brand="FF")
    ids = f"{FID_A},{f_new['id']}"
    m = post("/api/models", data={
        "name": "Formflow", "purpose": "profit",          # lowercase, like a user typing
        "filament_amount_g": "120", "print_time_hr": "0.75", "labor_min": "25",
        "description": "from the real form", "notes": "",
        "filament_ids": ids,                                # comma string (JS: pickedIds.join(","))
        "filament_grams": json.dumps({str(FID_A): 80})})    # JS: JSON.stringify(gramsMap)
    assert m["purpose"] == "Profit"
    assert {f["id"] for f in m["filaments"]} == {FID_A, f_new["id"]}
    assert "items" not in m
    close(m["breakdown"]["part_material_cost"],
          (0.08 * (20.0 / 1.0) + 0.04 * (20.0 / 1.0)) * 1.1, tol=1e-6)   # 80 g + 40 g of $20/kg @1.1
    # and a multipart PUT round-trip, again exactly as forms-model.js sends it
    m2 = put(f"/api/models/{m['id']}", data={
        "name": "Formflow v2", "purpose": "Personal",
        "filament_amount_g": "120", "print_time_hr": "0.75", "labor_min": "25",
        "description": "", "notes": "updated",
        "filament_ids": str(f_new["id"]),
        "filament_grams": "{}"})
    assert {f["id"] for f in m2["filaments"]} == {f_new["id"]}
    assert m2["notes"] == "updated"
    CLIENT.delete(f"/api/models/{m['id']}")
    CLIENT.delete(f"/api/filaments/{f_new['id']}")


def test_39_printer_form_multipart_like_browser():
    """views-printers.js submits ALL 11 cost keys (blank = inherit)."""
    body = {
        "model_name": "Bench Form", "manufacturer": "Maker",
        "bed_size": "220 x 220 x 250 mm", "notes": "from the printer form",
        "material_efficiency_factor": "",
        "labor_hourly_rate": "30",                  # set
        "printer_cost": "",
        "additional_upfront_cost": "0",             # explicit zero = real override
        "annual_maintenance_cost": "",
        "estimated_life_years": "",
        "estimated_uptime_fraction": "",
        "power_consumption_watts": "250",           # set
        "electricity_cost_kwh": "",
        "printer_cost_buffer_factor": "",
        "print_time_rate_override": "",
    }
    p = post("/api/printers", data=body)
    assert p["settings"].get("labor_hourly_rate") == "30"
    assert p["settings"].get("power_consumption_watts") == "250"
    assert p["settings"].get("additional_upfront_cost") == "0"
    assert "print_time_rate_override" not in p["settings"]
    assert "material_efficiency_factor" not in p["settings"]
    # now blank out labor_hourly_rate like a user clearing the field
    body2 = dict(body, model_name="Bench Form Pro", labor_hourly_rate="",
                 additional_upfront_cost="")
    p2 = put(f"/api/printers/{p['id']}", data=body2)
    assert "labor_hourly_rate" not in p2["settings"]
    assert "additional_upfront_cost" not in p2["settings"]
    assert p2["settings"].get("power_consumption_watts") == "250"
    CLIENT.delete(f"/api/printers/{p['id']}")


def test_70_live_server_concurrency():
    """Start the REAL production entrypoint (run.py -> waitress) in a
    subprocess and hammer it with mixed concurrent reads/writes. Catches
    cross-thread DB/connection issues that the test client can't (it shares
    one process/interleaving pattern)."""
    import socket, subprocess, time, urllib.request, urllib.error, threading

    def free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    d = tempfile.mkdtemp(prefix="m3wb-live-")
    port = free_port()
    env = dict(os.environ,
               MY3DWORKBENCH_DB=os.path.join(d, "live.db"),
               MY3DWORKBENCH_UPLOADS=os.path.join(d, "up"),
               MY3DWORKBENCH_HOST="127.0.0.1", MY3DWORKBENCH_PORT=str(port), SEED_DEMO="")
    exe = sys.executable
    proc = subprocess.Popen([exe, "run.py"], cwd=os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir)),
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 15
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                    if r.status == 200:
                        ready = True
                        break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.2)
        assert ready, "live server did not become ready"

        def jreq(method, path, payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data else {})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read().decode() or "null")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode() or "{}")

        # create a pool of rows first (sequential, for ids we can then update)
        ids = []
        for i in range(4):
            st, row = jreq("POST", "/api/filaments",
                           {"name": f"conc {i}", "color_name": f"Conc {i}",
                            "type": "PLA", "spool_weight_g": 1000, "cost_per_spool": 10})
            assert st == 201, (st, row)
            ids.append(row["id"])

        errors = []
        stop = time.time() + 12  # ~12 s of load
        def worker(n):
            i = 0
            while time.time() < stop and not errors:
                i += 1
                if i % 4 == 1:
                    st, _ = jreq("GET", "/api/filaments")
                elif i % 4 == 2:
                    st, _ = jreq("PUT", f"/api/filaments/{ids[n % len(ids)]}",
                                 {"color_name": f"Conc {n}-{i}", "type": "PLA",
                                  "spool_weight_g": 1000, "cost_per_spool": 10})
                elif i % 4 == 3:
                    st, _ = jreq("GET", "/api/health")
                else:
                    st, row = jreq("POST", "/api/filaments",
                                   {"color_name": f"Burst {n}.{i}", "type": "PETG",
                                    "spool_weight_g": 750, "cost_per_spool": 15})
                if st not in (200, 201):
                    errors.append((n, i, st))
        threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert not errors, f"unexpected status codes under load: {errors[:5]}"

        st, lst = jreq("GET", "/api/filaments")
        assert st == 200 and isinstance(lst, list)
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass



# ------------------------------- CSV import -------------------------------

IMPORT_HEADER = "Color,Type,Brand,Spool weight (g),Cost per spool (USD),Stock (g),Notes"


def _upload_csv(text, name="spools.csv", expected=201):
    return post("/api/filaments/import",
                data={"file": (io.BytesIO(text.encode("utf-8")), name)},
                expected=expected)


def test_80_csv_import_basic_round_trip():
    before = len(get("/api/filaments"))
    text = (IMPORT_HEADER + "\n"
            "Cobalt Blue,PLA,Bob's Spools,1000,20.00,875,Primes well\n"
            "Sail,ASA,,750,$15.50,0,\n"
            "Midnight Black,PETG,NightCo,1000, 18 ,500,\n"
            "Expo,PLA,Sci,1e3,20,0,\n")
    r = _upload_csv(text)
    assert r["imported"] == 4 and r["skipped"] == [] and r["total_rows"] == 4
    lst = get("/api/filaments")
    assert len(lst) == before + 4
    ex = next(x for x in lst if x["color_name"] == "Expo")
    assert ex["spool_weight_g"] == 1000          # '1e3' = 1000, not 13
    CLIENT.delete(f"/api/filaments/{ex['id']}")
    c = next(x for x in lst if x["color_name"] == "Cobalt Blue")
    assert c["type"] == "PLA" and c["brand"] == "Bob's Spools"
    assert c["spool_weight_g"] == 1000 and c["cost_per_spool"] == 20.0
    close(c["cost_per_kg"], 20.0)                     # $20.00 per 1000 g
    assert c["in_stock"] is True and c["current_stock_g"] == 875
    s = next(x for x in lst if x["color_name"] == "Sail")
    assert s["cost_per_spool"] == 15.5 and s["in_stock"] is False   # "$15.50" cleaned
    m = next(x for x in lst if x["color_name"] == "Midnight Black")
    assert m["cost_per_spool"] == 18.0                 # " 18 " cleaned
    for one in (c, s, m):
        CLIENT.delete(f"/api/filaments/{one['id']}")

def test_81_csv_import_partial_reports_row_numbers():
    before = len(get("/api/filaments"))
    text = (IMPORT_HEADER + "\n"
            "Good One,PLA,A,1000,20,50,ok\n"
            "Bad Cost,PLA,A,1000,abc,50,\n"
            "Good Two,PETG,B,500,10,0,\n"
            "Bad Weight,PLA,C,,20,0,\n"
            "Good Three,PLA,D,1000,5,25,\n")
    r = _upload_csv(text)
    assert r["imported"] == 3 and r["total_rows"] == 5
    rows = sorted(s["row"] for s in r["skipped"])
    assert rows == [3, 5], r["skipped"]
    reasons = {s["row"]: s["reason"] for s in r["skipped"]}
    assert "cost per spool" in reasons[3]
    assert "spool weight" in reasons[5]
    assert len(get("/api/filaments")) == before + 3


def test_82_csv_import_semicolon_bom_unicode():
    text = ("\ufeff" + "Color;Type;Brand;Weight (g);Price;Stock;Notes\n"
            "\u00c9cru;PLA;Bob\u2019s;1000;12.5;50;Caf\u00e9 notes\n")
    r = _upload_csv(text, name="spool_eu.csv")
    assert r["imported"] == 1 and r["unknown_headers"] == []
    e = next(x for x in get("/api/filaments") if x["color_name"] == "\u00c9cru")
    assert e["brand"] == "Bob\u2019s" and e["notes"] == "Caf\u00e9 notes"
    assert e["cost_per_spool"] == 12.5
    CLIENT.delete(f"/api/filaments/{e['id']}")


def test_83_csv_import_rejects_header_only_bad_headers_garbage():
    r = CLIENT.post("/api/filaments/import",
                    data={"file": (io.BytesIO(IMPORT_HEADER.encode()), "h.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 400, r.status_code
    r = CLIENT.post("/api/filaments/import",
                    data={"file": (io.BytesIO(b"Name,Weight,Price\nA,1,2\n"), "x.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    body = json_body(r)
    # Name->color_name, Weight->spool_weight_g, Price->cost_per_spool all match;
    # only the type column is absent
    assert "type" in body["error"], body
    assert "spool weight" not in body["error"], body
    r = CLIENT.post("/api/filaments/import",
                    data={"file": (io.BytesIO(b"\x13\x12\x11\x01 garbage\nn"), "n.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 400                       # never a 500
    r = CLIENT.post("/api/filaments/import", data={},
                    content_type="multipart/form-data")
    assert r.status_code == 400                       # no file at all


def test_84_csv_import_row_and_size_limits():
    big = IMPORT_HEADER + "\n" + "Spool X,PLA,B,1000,20,50,\n" * 5001
    r = CLIENT.post("/api/filaments/import",
                    data={"file": (io.BytesIO(big.encode()), "big.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "Too many rows" in json_body(r)["error"]
    # a genuinely >2 MB file
    row = "S,PLA,B,1000,20,50," + ("x" * 680) + "\n"      # ~700 B/row
    bigfile = (IMPORT_HEADER + "\n") + row * 3000            # ~2.1 MB
    assert len(bigfile.encode()) > 2 * 1024 * 1024
    r = CLIENT.post("/api/filaments/import",
                    data={"file": (io.BytesIO(bigfile.encode()), "huge.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "too large" in json_body(r)["error"].lower()


def test_85_csv_import_additive_duplicates_allowed():
    before = {f["id"] for f in get("/api/filaments")}
    text = IMPORT_HEADER + "\n" \
           "Twins,PLA,A,1000,20,50,x\n" \
           "Twins,PLA,A,1000,20,50,y\n"
    r = _upload_csv(text)
    assert r["imported"] == 2
    twins = [x for x in get("/api/filaments") if x["color_name"] == "Twins"]
    assert len(twins) == 2
    assert set(t["id"] for t in twins).isdisjoint(before)
    assert {t["notes"] for t in twins} == {"x", "y"}
    for t in twins:
        CLIENT.delete(f"/api/filaments/{t['id']}")


def test_86_csv_import_all_invalid_400_with_rows():
    before = len(get("/api/filaments"))
    text = IMPORT_HEADER + "\n" \
           ",,A,1000,20,50,\n" \
           "Bad,PLA,B,,oops,0,\n"
    r = CLIENT.post("/api/filaments/import",
                    data={"file": (io.BytesIO(text.encode()), "bad.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    body = json_body(r)
    assert body["error"].startswith("No filaments")
    assert [s["row"] for s in body["skipped"]] == [2, 3]
    assert len(get("/api/filaments")) == before      # nothing created


def test_87_csv_template_matches_importer():
    import csv as _csv_mod
    from my3d_workbench.routes.filaments import HEADER_ALIASES, REQUIRED_COLUMNS
    r = CLIENT.get("/api/filaments/import/template")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "attachment" in r.headers.get("Content-Disposition", "")
    raw = r.get_data(as_text=True)
    rows = list(_csv_mod.reader(io.StringIO(raw)))
    assert len(rows) == 3 and all(len(row) == len(rows[0]) for row in rows)

    def norm(h):
        import re as _re
        h = _re.sub(r"\(.*?\)", " ", h.lower())
        return _re.sub(r"[^a-z0-9]+", " ", h).strip()

    mapped = {HEADER_ALIASES.get(norm(h)) for h in rows[0]}
    assert set(REQUIRED_COLUMNS) <= mapped, mapped
    assert "notes" in mapped and "brand" in mapped


def test_88_upload_delete_retries_when_file_is_busy():
    """A stale upload file that is briefly "busy" (locked on Windows by an
    open browser tab) must be removed on retry — not blow up the request.
    Regression: the retry loop calls time.sleep; the module once used it
    without importing it (NameError -> 500 on the live path)."""
    import errno
    import my3d_workbench.api_helpers as ah

    fid = post("/api/filaments", data={
        "color_name": "Retry Red", "type": "PLA", "brand": "T",
        "spool_weight_g": "1000", "cost_per_spool": "10", "current_stock_g": "0",
        "picture": (io.BytesIO(make_png()), "old.png"),
    })["id"]
    old_rel = get(f"/api/filaments/{fid}")["image_path"]
    assert old_rel, "fixture should have an image on file"

    real_remove = ah.os.remove
    attempts = {"n": 0}

    def flaky_remove(path, *a, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError(errno.EBUSY, "file in use")
        return real_remove(path, *a, **k)

    ah.os.remove = flaky_remove
    try:
        r = CLIENT.put(f"/api/filaments/{fid}", data={
            "color_name": "Retry Red", "type": "PLA", "brand": "T",
            "spool_weight_g": "1000", "cost_per_spool": "10", "current_stock_g": "0",
            "picture": (io.BytesIO(make_png()), "new.png"),
        }, content_type="multipart/form-data")
    finally:
        ah.os.remove = real_remove
    assert r.status_code == 200, \
        f"PUT with a busy stale upload -> {r.status_code}: {r.get_data(as_text=True)[:300]}"
    assert attempts["n"] >= 2, "expected the first delete attempt to fail"
    assert not os.path.exists(os.path.join(UP_ROOT, old_rel)), "stale upload should be removed"
    assert get(f"/api/filaments/{fid}")["image_path"] != old_rel


def test_90_filaments_bulk_delete():
    """DELETE /api/filaments/bulk — the select-mode bulk delete.

    Good ids are deleted (with upload cleanup by the shared helper), junk
    entries are skipped without failing the request, bare lists work, and
    a filament not in the set survives.
    """
    def mk(name):
        return post("/api/filaments", body={
            "color_name": name, "type": "PLA", "brand": "Bulk",
            "spool_weight_g": "1000", "cost_per_spool": "10", "current_stock_g": "0",
        })["id"]

    a, b, c, d = mk("Bulk A"), mk("Bulk B"), mk("Bulk C"), mk("Bystander")

    # malformed payloads -> 400 with the usual error envelope
    for bad in (None, {}, {"ids": "nope"}, {"ids": []}, {"ids": None}, {"ids": [None, "x", -5]}):
        r = CLIENT.delete("/api/filaments/bulk", json={} if bad is None else bad)
        assert r.status_code == 400, f"bad payload {bad!r} -> {r.status_code}"
        assert json_body(r).get("error"), "error envelope expected"
    # booleans are NOT ids (int(True)==1 must not quietly delete row 1),
    # non-whole floats skip, and an overlong id list gets a clean 400
    before = len(get("/api/filaments"))
    for bad in ({"ids": [True, False, "true"]},
                {"ids": [1.5, 2.9, "1.5", ("1",)], "x": 1}):
        r = CLIENT.delete("/api/filaments/bulk", json=bad)
        assert r.status_code == 400, f"bad payload {bad!r} -> {r.status_code}"
    assert len(get("/api/filaments")) == before, "invalid ids must not delete anything"
    r = CLIENT.delete("/api/filaments/bulk", json={"ids": [10001 + i for i in range(1001)]})
    assert r.status_code == 400 and "Too many ids" in json_body(r)["error"], json_body(r)
    # nothing was deleted by the failing attempts
    for fid in (a, b, c, d):
        assert get(f"/api/filaments/{fid}"), f"filament {fid} must survive bad payloads"

    # mixed known/unknown/junk ids -> only the known ones go
    r = CLIENT.delete("/api/filaments/bulk", json={"ids": [a, b, 999999, "junk", None, a]})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert json_body(r) == {"ok": True, "deleted": 2}, json_body(r)
    assert CLIENT.get(f"/api/filaments/{a}").status_code == 404
    assert CLIENT.get(f"/api/filaments/{b}").status_code == 404
    assert get(f"/api/filaments/{d}"), "bystander must survive a bulk delete"

    # bare-list body works too
    r = CLIENT.delete("/api/filaments/bulk", json=[c])
    assert r.status_code == 200 and json_body(r)["deleted"] == 1
    assert CLIENT.get(f"/api/filaments/{c}").status_code == 404

    # model links to a bulk-deleted filament cascade away
    _mk_model("Bulk cascade check", fil_ids=[d], grams={str(d): 50})
    with db() as conn:
        assert conn.execute(
            "SELECT 1 FROM model_filaments WHERE filament_id = ?", (d,)
        ).fetchone(), "fixture must link a model to filament d"
    r = CLIENT.delete("/api/filaments/bulk", json={"ids": [d]})
    assert r.status_code == 200 and json_body(r)["deleted"] == 1
    with db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) n FROM model_filaments WHERE filament_id = ?", (d,)
        ).fetchone()
        assert n["n"] == 0, "model_filaments rows must cascade away"


def test_94_model_materials_line_items():
    """Models can carry extra material line items (wood dowels, magnets,
    shipping boxes, …) valued quantity × unit_cost, folded into total cost
    and suggested prices; create/update replace the list, invalid rows get
    a clean 400 with no side effects, and deleting the model cascades
    its rows away."""
    base = {"name": "Clamp kit", "purpose": "Profit",
            "filament_amount_g": 0, "print_time_hr": 1.5, "labor_min": 15}
    good_materials = [
        {"description": "wood dowels", "quantity": 3, "unit_cost": 0.5},
        {"description": "shipping box"},   # defaults: qty 1, unit 0
    ]

    # --- create with materials -------------------------------------------------
    m = post("/api/models", {**base, "materials": good_materials})
    mids = {r["description"]: r for r in m["materials"]}
    assert set(mids) == {"wood dowels", "shipping box"}
    close(mids["wood dowels"]["cost"], 1.5)
    close(mids["shipping box"]["quantity"], 1.0)
    close(mids["shipping box"]["unit_cost"], 0.0)
    b = m["breakdown"]
    close(b["materials_cost"], 1.5)
    close(b["total_cost"], b["part_material_cost"] + b["labor_cost"]
          + b["machine_cost"] + b["materials_cost"], tol=1e-9)
    close(b["suggested"]["50"], b["total_cost"] / 0.5, tol=1e-9)
    close(b["suggested"]["70"], b["total_cost"] / 0.3, tol=1e-9)

    # list view carries the same total
    row = next(r for r in get("/api/models") if r["id"] == m["id"])
    close(row["cost"]["total_cost"], b["total_cost"], tol=1e-9)
    assert row["materials"] == m["materials"], "list rows should include the materials"

    # --- update replaces the whole list -----------------------------------------
    m2 = put(f"/api/models/{m['id']}", {**base,
        "materials": [{"description": "magnets", "quantity": 2, "unit_cost": 1.25}]})
    assert [r["description"] for r in m2["materials"]] == ["magnets"]
    close(m2["breakdown"]["materials_cost"], 2.5)
    m3 = put(f"/api/models/{m['id']}", {**base, "materials": []})
    assert m3["materials"] == [] and m3["breakdown"]["materials_cost"] == 0.0

    # --- invalid rows -> clean 400, existing data untouched ----------------------
    put(f"/api/models/{m['id']}", {**base, "materials": good_materials})
    before = get(f"/api/models/{m['id']}")["materials"]
    bad_materials = [
        [{"quantity": 1}],                                   # no description
        [{"description": "x", "quantity": -1}],              # negative qty
        [{"description": "x", "unit_cost": "abc"}],          # not a number
        [{"description": "x", "quantity": True}],            # boolean qty
        [{"description": True, "quantity": 1, "unit_cost": 1}],   # boolean desc (str(True)=="True" is junk)
        [{"description": "x", "quantity": 1e300, "unit_cost": 1e300}],   # product overflows to Infinity
        [{"description": "x", "quantity": 10_000_001}],      # qty over the 1M cap
        [{"description": "x", "unit_cost": 1_000_001}],      # unit cost over the 1M cap
        [{"description": "x"} for _ in range(51)],           # over the 50-row cap
        {"description": "x"},                                 # not a list
        [5],                                                 # list of non-objects
        "nope",                                              # not a list
    ]
    for bad in bad_materials:
        r = CLIENT.post("/api/models", json={**base, "materials": bad})
        assert r.status_code == 400, f"{bad!r} -> {r.status_code}: {r.get_data(as_text=True)[:200]}"
        assert json_body(r).get("error"), "error envelope expected"
    after = get(f"/api/models/{m['id']}")["materials"]
    assert after == before, "rejected payloads must not modify saved materials"

    # --- deleting the model cascades its material rows away ----------------------
    r = CLIENT.delete(f"/api/models/{m['id']}")
    assert r.status_code == 200
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM model_materials WHERE model_id = ?", (m["id"],)).fetchone()
        assert n["n"] == 0, "model_materials rows must cascade away"

# ---------------------------------------------------------------------------
# Validation hardening regressions (booleans, id coercion, 500s, Infinity)
# ---------------------------------------------------------------------------

def _strict_json(resp):
    """Parse a response as STRICT JSON — Infinity/NaN tokens must not appear
    (they are valid Python-but-invalid JSON and break spec-compliant clients)."""
    def _no_const(token):
        raise ValueError(f"non-finite JSON constant in response: {token}")
    return json.loads(resp.get_data(as_text=True), parse_constant=_no_const)


def test_91_boolean_values_are_rejected_not_coerced():
    """Python's float(True)==1.0 / int(True)==1 must not leak through the API
    boundary: a boolean where a number or id is expected is a 400, never a
    silently coerced value."""
    # filament: cost field + text field
    r = CLIENT.post("/api/filaments", json={"color_name": "Bool", "type": "PLA",
                                            "spool_weight_g": 1000, "cost_per_spool": True})
    assert r.status_code == 400 and "boolean" in json_body(r)["error"], r.get_data(as_text=True)[:300]
    r = CLIENT.post("/api/filaments", json={"color_name": "Bool", "type": "PLA",
                                            "spool_weight_g": 1000, "cost_per_spool": 10,
                                            "notes": False})
    assert r.status_code == 400 and "boolean" in json_body(r)["error"], r.get_data(as_text=True)[:300]

    # model: filament_grams values
    fid = post("/api/filaments", body={"color_name": "Grams", "type": "PLA",
                                       "spool_weight_g": 1000, "cost_per_spool": 10})["id"]
    r = CLIENT.post("/api/models", json={"name": "M", "filament_ids": [fid],
                                         "filament_grams": {str(fid): True}})
    assert r.status_code == 400 and "boolean" in json_body(r)["error"], r.get_data(as_text=True)[:300]
    # (and the model must not have been created)
    assert all(m["name"] != "M" for m in get("/api/models")), "rejected model must not be created"

    # printer override
    r = CLIENT.post("/api/printers", json={"model_name": "BoolP", "printer_cost": True})
    assert r.status_code == 400 and "boolean" in json_body(r)["error"], r.get_data(as_text=True)[:300]
    assert all(p["model_name"] != "BoolP" for p in get("/api/printers"))

    # setting value (JSON true -> str(True)=="True" must not pass as "a number")
    r = CLIENT.put("/api/settings/labor_hourly_rate", json={"value": True})
    assert r.status_code == 400 and "number" in json_body(r)["error"], r.get_data(as_text=True)[:300]


def test_92_id_parsing_rejects_coercion_and_out_of_range():
    """Non-whole floats and out-of-INT64 ids are rejected with a clean 400/404
    JSON envelope — never a coerced id and never an HTML 500 page."""
    fid = post("/api/filaments", body={"color_name": "IdCo", "type": "PLA",
                                       "spool_weight_g": 1000, "cost_per_spool": 10})["id"]
    for bad, why in (([1.9], "whole number"), ([True], "booleans"),
                     ([10**20], "range"), (["9999999999999999999999999999"], "range")):
        r = CLIENT.post("/api/models", json={"name": "BadIds", "filament_ids": bad})
        assert r.status_code == 400, f"{bad} -> {r.status_code}: {r.get_data(as_text=True)[:200]}"
        assert json_body(r).get("error"), "error envelope expected"
        assert why in json_body(r)["error"].lower(), json_body(r)
    assert all(m["name"] != "BadIds" for m in get("/api/models"))
    # a legitimate whole-number float id still works (2.0 is the number 2)
    m = post("/api/models", body={"name": "WholeFloat", "filament_ids": [float(fid)]})
    assert [f["id"] for f in m["filaments"]] == [fid]
    CLIENT.delete(f"/api/models/{m['id']}")

    # out-of-range id in a URL -> 404, not a 500/HTML crash
    huge = "9" * 30
    r = CLIENT.get(f"/api/filaments/{huge}")
    assert r.status_code == 404 and json_body(r).get("error"), r.get_data(as_text=True)[:200]
    r = CLIENT.get(f"/api/models/{huge}")
    assert r.status_code == 404 and json_body(r).get("error"), r.get_data(as_text=True)[:200]

    # bulk delete: out-of-range ids are skipped, mixed payloads still work
    other = post("/api/filaments", body={"color_name": "BulkBig", "type": "PLA",
                                         "spool_weight_g": 1000, "cost_per_spool": 10})["id"]
    r = CLIENT.delete("/api/filaments/bulk", json={"ids": [10**20, 10**300]})
    assert r.status_code == 400 and json_body(r).get("error"), r.get_data(as_text=True)[:200]
    r = CLIENT.delete("/api/filaments/bulk", json={"ids": [other, 10**20]})
    assert r.status_code == 200 and json_body(r)["deleted"] == 1, r.get_data(as_text=True)[:200]
    assert CLIENT.get(f"/api/filaments/{other}").status_code == 404

    # ?printer=<huge> -> clean 400, and huge printer URLs -> 404 envelope
    mp = post("/api/models", body={"name": "Pv", "filament_ids": [fid]})
    r = CLIENT.get(f"/api/models/{mp['id']}", query_string={"printer": "10" * 30})
    assert r.status_code == 400 and json_body(r).get("error"), r.get_data(as_text=True)[:200]
    r = CLIENT.put(f"/api/printers/{huge}", json={"model_name": "x"})
    assert r.status_code == 404 and json_body(r).get("error"), r.get_data(as_text=True)[:200]
    r = CLIENT.delete(f"/api/printers/{huge}")
    assert r.status_code == 404 and json_body(r).get("error"), r.get_data(as_text=True)[:200]
    CLIENT.delete(f"/api/models/{mp['id']}")


def test_93_money_inputs_capped_so_responses_stay_valid_json():
    """Every money input that feeds the cost engine is capped so no finite
    request can produce Infinity in a response (raw `Infinity` in JSON is
    invalid for spec-compliant clients)."""
    # filament: 1 g spool x 1000 -> cost_per_kg must stay finite
    for cost in (1e300, 1e13):
        r = CLIENT.post("/api/filaments", json={"color_name": "Huge", "type": "PLA",
                                                "spool_weight_g": 1, "cost_per_spool": cost})
        assert r.status_code == 400 and "too large" in json_body(r)["error"], \
            f"{cost} -> {r.status_code}: {r.get_data(as_text=True)[:200]}"
    big = post("/api/filaments", body={"color_name": "Max", "type": "PLA",
                                       "spool_weight_g": 1, "cost_per_spool": 1e12})
    for path in ("/api/filaments", f"/api/filaments/{big['id']}"):
        r = CLIENT.get(path)
        assert r.status_code == 200
        _strict_json(r)   # must parse as strict JSON — no Infinity tokens
    CLIENT.delete(f"/api/filaments/{big['id']}")

    # model: the three unbounded cost inputs now have caps
    base = {"name": "CapM", "purpose": "Personal", "filament_amount_g": 0,
            "print_time_hr": 0, "labor_min": 0}
    for key in ("filament_amount_g", "print_time_hr", "labor_min"):
        r = CLIENT.post("/api/models", json={**base, key: 1e7})
        assert r.status_code == 400 and "too large" in json_body(r)["error"], \
            f"{key} -> {r.status_code}: {r.get_data(as_text=True)[:200]}"
    assert all(m["name"] != "CapM" for m in get("/api/models"))

    # settings: numeric caps + the 0-1 uptime fraction
    r = CLIENT.put("/api/settings/printer_cost", json={"value": 1e13})
    assert r.status_code == 400 and "too large" in json_body(r)["error"], r.get_data(as_text=True)[:200]
    r = CLIENT.put("/api/settings/estimated_uptime_fraction", json={"value": 1.5})
    assert r.status_code == 400 and "fraction" in json_body(r)["error"], r.get_data(as_text=True)[:200]
    r = CLIENT.put("/api/settings", json={"power_consumption_watts": 1e300,
                                          "labor_hourly_rate": "7"})
    assert r.status_code == 400 and "too large" in json_body(r)["error"], r.get_data(as_text=True)[:200]

    # printer overrides: same caps as global settings
    r = CLIENT.post("/api/printers", json={"model_name": "CapP", "printer_cost": 1e13})
    assert r.status_code == 400 and "too large" in json_body(r)["error"], r.get_data(as_text=True)[:200]
    r = CLIENT.post("/api/printers", json={"model_name": "CapP",
                                           "estimated_uptime_fraction": 2})
    assert r.status_code == 400 and "fraction" in json_body(r)["error"], r.get_data(as_text=True)[:200]
    assert all(p["model_name"] != "CapP" for p in get("/api/printers"))


def test_95_purchase_link_must_be_an_http_s_link():
    """purchase_link is rendered into an <a href> in the UI, so script-scheme
    URLs (javascript:, data:, vbscript:) must be rejected at the API boundary
    \u2014 on create, on update (with the row untouched), and in CSV import
    (per-row skip, not a file-level failure)."""
    for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>",
                "vbscript:msgbox(1)", "not a web link at all"):
        r = CLIENT.post("/api/filaments", json={"color_name": "Lk", "type": "PLA",
                                                 "spool_weight_g": 1000, "cost_per_spool": 10,
                                                 "purchase_link": bad})
        assert r.status_code == 400 and "http(s)" in json_body(r)["error"], \
            f"{bad!r} -> {r.status_code}: {r.get_data(as_text=True)[:200]}"
    assert all(f["color_name"] != "Lk" for f in get("/api/filaments")), \
        "rejected filament must not be created"

    ok = post("/api/filaments", body={"color_name": "Lk", "type": "PLA",
                                      "spool_weight_g": 1000, "cost_per_spool": 10,
                                      "purchase_link": "https://example.com/spool"})
    assert ok["purchase_link"] == "https://example.com/spool"
    # the update path enforces it too
    r = CLIENT.put(f"/api/filaments/{ok['id']}", json={"color_name": "Lk", "type": "PLA",
                                                        "spool_weight_g": 1000,
                                                        "cost_per_spool": 10,
                                                        "purchase_link": "javascript:alert(1)"})
    assert r.status_code == 400 and "http(s)" in json_body(r)["error"], r.get_data(as_text=True)[:200]
    assert json_body(CLIENT.get(f"/api/filaments/{ok['id']}"))["purchase_link"] \
        == "https://example.com/spool", "a rejected update must not touch the row"

    # CSV import: a script-scheme link is a per-row problem, good rows import
    text = ("Color,Type,Brand,Spool weight (g),Cost per spool (USD),Stock (g),Purchase link,Notes\n"
            "Evil Row,PLA,A,1000,20,0,javascript:alert(1),bad link\n"
            "Fine Row,PLA,B,1000,20,0,https://example.com,ok\n")
    r = _upload_csv(text)
    assert r["imported"] == 1 and len(r["skipped"]) == 1, r
    assert "purchase link" in r["skipped"][0]["reason"], r["skipped"]
    lst = get("/api/filaments")
    assert all(x["color_name"] != "Evil Row" for x in lst)
    fine = next(x for x in lst if x["color_name"] == "Fine Row")
    assert fine["purchase_link"] == "https://example.com"
    CLIENT.delete(f"/api/filaments/{ok['id']}")
    CLIENT.delete(f"/api/filaments/{fine['id']}")



def test_96_daemon_mode_logs_to_file():
    """MY3DWORKBENCH_LOG makes the server runnable with NO console attached
    (Task Scheduler 'whether logged on or not', launchd, systemd): the
    startup lines must land in the log file, and the server must serve."""
    import socket, subprocess, time, urllib.request

    d = tempfile.mkdtemp(prefix="m3wb-daemon-")
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    logfile = os.path.join(d, "server.log")
    env = dict(os.environ,
               MY3DWORKBENCH_DB=os.path.join(d, "daemon.db"),
               MY3DWORKBENCH_UPLOADS=os.path.join(d, "up"),
               MY3DWORKBENCH_HOST="127.0.0.1", MY3DWORKBENCH_PORT=str(port),
               MY3DWORKBENCH_LOG=logfile)
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    proc = subprocess.Popen([sys.executable, "run.py"], cwd=src,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    up = False
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1).read()
                up = True
                break
            except Exception:
                time.sleep(0.25)
    finally:
        if proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
    log = open(logfile, encoding="utf-8", errors="replace").read()
    assert up, f"daemon-mode server never came up; log: {log[-400:]}"
    assert "My 3D Workbench is up" in log, f"startup line missing from log: {log[-400:]}"
