"""Optional demo data (SEED_DEMO=1 python run.py). Idempotent."""
from . import db


def seed_demo(app):
    with app.app_context():
        if db.query_one("SELECT 1 FROM filaments LIMIT 1"):
            return  # already seeded
        db.execute(
            """INSERT INTO filaments (color_name, type, brand, purchase_link,
                                      spool_weight_g, cost_per_spool, current_stock_g, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("Cobalt Blue", "PLA", "Generic", "https://example.com/spool",
             1000, 20.0, 1250, "Demo spool"),
        )
        fid = db.query_one("SELECT MAX(id) v FROM filaments")["v"]
        db.execute(
            """INSERT INTO models (name, purpose, description, filament_amount_g,
                                   print_time_hr, labor_min, notes)
               VALUES (?,?,?,?,?,?,?)""",
            ("Benchy Demo", "Profit", "Sample boat model.", 100, 1.2, 15, "Demo model"),
        )
        mid = db.query_one("SELECT MAX(id) v FROM models")["v"]
        db.execute("INSERT INTO model_filaments (model_id, filament_id) VALUES (?,?)", (mid, fid))
        # A sample printer with a few cost-setting overrides, so the Models
        # tab's per-printer preview has something to demonstrate.
        db.execute(
            "INSERT INTO printers (manufacturer, model_name, bed_size, notes) VALUES (?,?,?,?)",
            ("Bambu Lab", "P1S", "256 x 256 x 256 mm", "Demo printer"))
        pid = db.query_one("SELECT MAX(id) v FROM printers")["v"]
        for key, value in (("printer_cost", "1499"),
                           ("power_consumption_watts", "350"),
                           ("electricity_cost_kwh", "0.12")):
            db.execute("INSERT INTO printer_settings (printer_id, key, value) VALUES (?,?,?)",
                       (pid, key, value))
