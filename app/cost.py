"""
Cost-calculation engine — a faithful port of the logic in
``!Product_Pricing_Worksheet_V2.xlsx``.

Sheet mapping
=============
* ``Adv. Inputs``  -> :func:`compute_machine_rate`
    C13 total_investment      = printer_cost + additional_upfront_cost
    C16 lifetime_cost         = total_investment + annual_maintenance * life_years
    C20 uptime_hours_per_year = 8760 * uptime_fraction
    C26 capital_cost_per_hr   = lifetime_cost / (uptime_hours * life_years)
    C27 electrical_per_hr     = (watts / 1000) * electricity_rate
    C29 print_time_rate       = (capital + electrical) * buffer_factor
* ``Calculation Sheet`` -> :func:`compute_model_cost`
    D10 cost_per_kg           = filament cost_per_spool / (spool_weight_g / 1000)
    F17 part material cost    = (filament_g / 1000) * cost_per_kg * efficiency
    G28 labor cost            = (labor_min / 60) * labor_hourly_rate
    G43 machine cost          = print_time_hr * print_time_rate
    G45 total cost            = material + labor + machine
    G52/54/56 suggested price = total / (1 - margin)     @ 50 / 60 / 70 %

    (The sheet's Hardware, Packaging and "landed" blocks are intentionally
    NOT implemented — packaging/shipping was removed from the Models feature.)

Design
======
Everything here is *pure*: the functions take plain dicts and return plain
dicts, with no Flask or SQLite imports. That keeps the money math trivially
unit-testable and reusable (CLI scripts, future REST endpoints, CSV export…).
"""

import math

HOURS_PER_YEAR = 8760          # constant used by the sheet (cell C19)
SUGGESTED_MARGINS = (50, 60, 70)

# The global settings the cost engine consumes — the "cost calculation settings"
# that each printer may override individually (see :func:`merge_settings`).
COST_SETTING_KEYS = (
    "material_efficiency_factor",      # waste & support multiplier
    "labor_hourly_rate",               # currency / labor-hour
    "printer_cost",                    # machine purchase price
    "additional_upfront_cost",         # one-time upgrades
    "annual_maintenance_cost",         # currency / year
    "estimated_life_years",            # useful life
    "estimated_uptime_fraction",       # 0-1 fraction of the year printing
    "power_consumption_watts",         # average draw while printing
    "electricity_cost_kwh",            # currency / kWh
    "printer_cost_buffer_factor",      # safety multiplier on the rate
    "print_time_rate_override",        # force a fixed currency/print-hour
)


def merge_settings(base, overrides):
    """Overlay per-printer settings on top of the global ones.

    Only non-blank override values win; blank/missing values leave the global
    setting in effect. Neither input is mutated.
    """
    merged = dict(base or {})
    for key, value in (overrides or {}).items():
        if value is not None and str(value).strip() != "":
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Small, defensive numeric parsing (settings are stored as TEXT)
# ---------------------------------------------------------------------------
def _num(value, default=0.0):
    """Parse a raw setting (str/float/None) to a finite float, else default.

    ``nan``/``inf`` never enter the money math — they either fall back to the
    default here or were already rejected at the API boundary.
    """
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def setting(settings, key, default=0.0):
    """Typed getter over a ``{key: value}`` settings mapping."""
    return _num(settings.get(key), default)


# ---------------------------------------------------------------------------
# Machine rate (sheet: Adv. Inputs)
# ---------------------------------------------------------------------------
def compute_machine_rate(settings):
    """
    Derive the machine cost in currency per print-hour, exactly as the
    ``Adv. Inputs`` sheet does (C11-C29). Returns a dict::

        {"rate_per_hr": 0.1023, "source": "computed"|"override", "components": {...}}
    """
    # Override escape hatch: if a fixed rate is set, use it verbatim (sheet C5).
    override_raw = settings.get("print_time_rate_override")
    if override_raw not in (None, ""):
        override = _num(override_raw, -1.0)
        if override >= 0:
            return {"rate_per_hr": override, "source": "override", "components": {}}

    printer_cost        = _num(settings.get("printer_cost"))
    upfront_cost        = _num(settings.get("additional_upfront_cost"))
    annual_maintenance  = _num(settings.get("annual_maintenance_cost"))
    life_years          = max(_num(settings.get("estimated_life_years"), 1.0), 0.0001)
    uptime_fraction     = _num(settings.get("estimated_uptime_fraction"))
    watts               = _num(settings.get("power_consumption_watts"))
    electricity_rate    = _num(settings.get("electricity_cost_kwh"))
    buffer_factor       = _num(settings.get("printer_cost_buffer_factor"), 1.0)

    total_investment  = printer_cost + upfront_cost                      # C13
    lifetime_cost     = total_investment + annual_maintenance * life_years  # C16
    uptime_hours      = HOURS_PER_YEAR * uptime_fraction                 # C20
    denom             = uptime_hours * life_years
    # Guard the near-zero denominator, not just the exact zero: with an
    # almost-zero uptime fraction the capital cost would blow past float64 and
    # serialize as Infinity (not legal JSON). Less than an hour of print
    # capacity per lifetime hour is outside the sheet's model, so capital
    # cost simply does not apply — mirroring the exact-zero branch.
    capital_per_hr    = (lifetime_cost / denom) if denom >= 1.0 else 0.0     # C26
    electrical_per_hr = (watts / 1000.0) * electricity_rate              # C27
    rate_per_hr       = (capital_per_hr + electrical_per_hr) * buffer_factor  # C29

    return {
        "rate_per_hr": rate_per_hr,
        "source": "computed",
        "components": {
            "total_investment": total_investment,
            "lifetime_cost": lifetime_cost,
            "uptime_hours_per_year": uptime_hours,
            "capital_cost_per_hr": capital_per_hr,
            "electrical_cost_per_hr": electrical_per_hr,
            "buffer_factor": buffer_factor,
        },
    }


# ---------------------------------------------------------------------------
# Filament allocation across a multi-filament model
# ---------------------------------------------------------------------------
def allocate_filament_grams(model_rows):
    """
    Split a model's total filament weight across its associated filaments.

    ``model_rows``: list of filament rows (dicts) with keys
    ``id``, ``grams`` (nullable, the pinned allocation from join table),
    ``total_g`` (the model's total filament grams per print).

    Rules
    -----
    * Explicit positive allocations are honoured as-is.
    * Filaments with no allocation share the *remainder* evenly.
    * If explicit allocations exceed the total, the remainder is clamped to 0
      (the frontend can surface a soft warning).

    Returns a list of ``(filament_id, grams)`` in input order.
    """
    total_g = max(_num(model_rows[0].get("total_g")) if model_rows else 0.0, 0.0)
    explicit = [r for r in model_rows if _num(r.get("grams")) > 0]
    remaining = max(0.0, total_g - sum(_num(r["grams"]) for r in explicit))
    unassigned = [r for r in model_rows if _num(r.get("grams")) <= 0]
    even_share = (remaining / len(unassigned)) if unassigned else 0.0

    out = []
    for r in model_rows:
        grams = _num(r.get("grams"))
        out.append((r["id"], grams if grams > 0 else even_share))
    return out


# ---------------------------------------------------------------------------
# Per-model cost breakdown (sheet: Calculation Sheet)
# ---------------------------------------------------------------------------
def compute_model_cost(model, filament_rows, settings, materials=None):
    """
    Compute the full cost breakdown for one model.

    Parameters
    ----------
    model:         dict with ``filament_amount_g``, ``print_time_hr``, ``labor_min``
    filament_rows: list of dicts, one per associated filament, each providing
                   ``id``, optional ``grams``, ``cost_per_kg`` and display fields
    settings:      ``{key: value}`` mapping as loaded from the settings table
    materials:     optional list of extra material line items, each a dict with
                   ``description``, ``quantity`` (defaults to 1) and
                   ``unit_cost`` (defaults to 0); each row is valued at
                   quantity × unit_cost and summed into ``materials_cost``

    Returns a plain dict (all money values are unrounded floats).
    """
    currency    = (settings.get("currency_code") or "USD").strip()
    efficiency  = setting(settings, "material_efficiency_factor", 1.0)
    labor_rate  = setting(settings, "labor_hourly_rate")
    rate_info   = compute_machine_rate(settings)
    machine_rate = rate_info["rate_per_hr"]

    # -- materials: printed part ------------------------------------------------
    # Derive cost-per-kg at FULL precision from the raw spool fields when they
    # are available (the display serializer rounds cost_per_kg for UI purposes,
    # and consuming that would drift the money math from the spreadsheet).
    def _cost_per_kg(row):
        spool_g = _num(row.get("spool_weight_g"))
        cost_pool = _num(row.get("cost_per_spool"))
        if spool_g > 0:
            return cost_pool / (spool_g / 1000.0)
        return _num(row.get("cost_per_kg"))

    allocation = allocate_filament_grams(filament_rows)
    filament_cost_rows = []
    part_material_cost = 0.0
    for (fid, grams), row in zip(allocation, filament_rows):
        cost_per_kg = _cost_per_kg(row)
        cost = (grams / 1000.0) * cost_per_kg * efficiency      # sheet F17
        part_material_cost += cost
        filament_cost_rows.append({
            "id": fid,
            "color_name": row.get("color_name"),
            "type": row.get("type"),
            "brand": row.get("brand"),
            "grams": grams,
            "cost_per_kg": cost_per_kg,
            "cost": cost,
        })

    # -- labor -------------------------------------------------------------------
    labor_cost = (_num(model.get("labor_min")) / 60.0) * labor_rate    # sheet G28

    # -- machine -------------------------------------------------------------------
    machine_cost = _num(model.get("print_time_hr")) * machine_rate     # sheet G43

    # -- extra materials (wood, magnets, shipping boxes, …) ----------------------
    # Plain line items valued at quantity × unit_cost. They are not subject to
    # the waste/efficiency factor — a box costs what you paid for the box —
    # but they ARE part of the total, so suggested prices carry them too.
    materials_cost = 0.0
    material_cost_rows = []
    for item in (materials or []):
        qty  = _num(item.get("quantity"), 1.0)
        unit = _num(item.get("unit_cost"))
        cost = qty * unit
        materials_cost += cost
        material_cost_rows.append({
            "description": str(item.get("description") or "").strip(),
            "quantity":    qty,
            "unit_cost":   unit,
            "cost":        cost,
        })

    # -- totals ---------------------------------------------------------------------
    total_cost = part_material_cost + labor_cost + machine_cost + materials_cost        # sheet G45

    def _at(base, margin_pct):
        if base <= 0:
            return 0.0
        return base / (1.0 - margin_pct / 100.0)

    return {
        "currency": currency,
        "efficiency_factor": efficiency,
        "labor_rate": labor_rate,
        "machine_rate": machine_rate,
        "machine_rate_source": rate_info["source"],
        "machine_rate_components": rate_info["components"],
        "filaments": filament_cost_rows,
        "part_material_cost": part_material_cost,
        "labor_cost": labor_cost,
        "machine_cost": machine_cost,
        "materials": material_cost_rows,
        "materials_cost": materials_cost,
        "total_cost": total_cost,
        "suggested": {str(m): _at(total_cost, m) for m in SUGGESTED_MARGINS},
    }
