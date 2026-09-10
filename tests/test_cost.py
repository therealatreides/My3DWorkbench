"""Unit tests for the pure cost engine (my3d_workbench/cost.py).

No Flask, no database — this module exercises the money math exactly as the
spreadsheet defined it. Run via ``python tests/run_tests.py``.
"""
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from my3d_workbench import cost as ce   # noqa: E402


# -- helpers ---------------------------------------------------------------
def close(a, b, tol=1e-9):
    assert abs(a - b) <= tol * max(1.0, abs(a), abs(b)), f"{a!r} !~ {b!r}"


# The seeded Adv. Inputs defaults, as a plain settings map.
def default_settings():
    return {
        "material_efficiency_factor": "1.1",
        "labor_hourly_rate": "20",
        "printer_cost": "499",
        "additional_upfront_cost": "100",
        "annual_maintenance_cost": "75",
        "estimated_life_years": "3",
        "estimated_uptime_fraction": "0.5",
        "power_consumption_watts": "150",
        "electricity_cost_kwh": "0.10643",
        "printer_cost_buffer_factor": "1.3",
        "print_time_rate_override": "",
        "currency_code": "USD",
    }


# -- machine rate ----------------------------------------------------------
def test_machine_rate_matches_sheet_defaults():
    """(499+100 + 75*3) / (8760*0.5*3) + 150W*0.10643, all x1.3  -> ~0.10228."""
    rate = ce.compute_machine_rate(default_settings())
    expected = ((499 + 100 + 75 * 3) / (8760 * 0.5 * 3)
                + (150 / 1000.0) * 0.10643) * 1.3
    close(rate["rate_per_hr"], expected)
    assert round(rate["rate_per_hr"], 5) == 0.10228, rate["rate_per_hr"]
    assert rate["source"] == "computed"
    c = rate["components"]
    close(c["total_investment"], 599)
    close(c["lifetime_cost"], 824)
    close(c["uptime_hours_per_year"], 4380)


def test_machine_rate_override():
    s = dict(default_settings(), print_time_rate_override="0.5")
    r = ce.compute_machine_rate(s)
    assert r["source"] == "override"
    close(r["rate_per_hr"], 0.5)


def test_machine_rate_override_zero_is_allowed():
    r = ce.compute_machine_rate(dict(default_settings(), print_time_rate_override="0"))
    assert r["source"] == "override" and r["rate_per_hr"] == 0.0


def test_machine_rate_override_blank_falls_back_to_computed():
    r = ce.compute_machine_rate(default_settings())
    assert r["source"] == "computed"


def test_machine_rate_garbage_override_falls_back_to_computed():
    r = ce.compute_machine_rate(dict(default_settings(), print_time_rate_override="abc"))
    assert r["source"] == "computed"


def test_machine_rate_non_finite_inputs_fall_back_to_zero():
    """_num() maps nan/inf/garbage to the default (0) — they never leak into
    the money math. (The API boundary rejects these earlier; this is the
    pure-engine safety net.)"""
    s = dict(default_settings())
    s.update({"printer_cost": "nan", "power_consumption_watts": "inf",
              "electricity_cost_kwh": "junk"})
    r = ce.compute_machine_rate(s)
    expected = ((0 + 100 + 75 * 3) / (8760 * 0.5 * 3)) * 1.3
    close(r["rate_per_hr"], expected)


def test_machine_rate_zero_uptime_is_safe():
    s = dict(default_settings(), estimated_uptime_fraction="0")
    r = ce.compute_machine_rate(s)
    import math
    assert math.isfinite(r["rate_per_hr"])


# -- settings merge (printer overrides) ------------------------------------
def test_merge_settings_semantics():
    base = {"a": "1", "b": "2"}
    # blank, None and whitespace-only = "inherit global"; "0" is a real value
    out = ce.merge_settings(base, {"a": "", "b": None, "c": "3", "d": "0", "e": "   "})
    assert out == {"a": "1", "b": "2", "c": "3", "d": "0"}, out
    assert "e" not in out, out
    assert base == {"a": "1", "b": "2"}  # neither input mutated


def test_merge_settings_none_inputs():
    assert ce.merge_settings(None, None) == {}


# -- filament allocation ----------------------------------------------------
def test_allocation_even_split_when_unpinned():
    rows = [{"id": 1, "grams": None, "total_g": 100},
            {"id": 2, "grams": None, "total_g": 100},
            {"id": 3, "grams": 0, "total_g": 100}]
    out = ce.allocate_filament_grams(rows)
    close(sum(g for _, g in out), 100)
    close(out[0][1], out[1][1])
    close(out[2][1], out[0][1])


def test_allocation_pinned_wins_rest_share_remainder():
    rows = [{"id": 1, "grams": 30, "total_g": 100},
            {"id": 2, "grams": None, "total_g": 100},
            {"id": 3, "grams": None, "total_g": 100}]
    out = ce.allocate_filament_grams(rows)
    close(out[0][1], 30)
    close(out[1][1], 35)
    close(out[2][1], 35)


def test_allocation_all_pinned():
    rows = [{"id": 1, "grams": 40, "total_g": 100},
            {"id": 2, "grams": 60, "total_g": 100}]
    out = ce.allocate_filament_grams(rows)
    close(out[0][1], 40)
    close(out[1][1], 60)


def test_allocation_overpinned_clamps_remainder():
    rows = [{"id": 1, "grams": 150, "total_g": 100},
            {"id": 2, "grams": None, "total_g": 100}]
    out = ce.allocate_filament_grams(rows)
    close(out[0][1], 150)      # pinned value honoured as-is
    close(out[1][1], 0)        # remainder clamped to 0


def test_allocation_empty():
    assert ce.allocate_filament_grams([]) == []


# -- full model cost --------------------------------------------------------
def test_remo_100g_20perkg_readme_example():
    """README worked example: 100 g of $20/kg @1.1 eff, 15 min @ $20/hr,
    1 print-hour -> total 7.30, suggested 50% = 14.60."""
    s = default_settings()
    model = {"filament_amount_g": 100, "print_time_hr": 1, "labor_min": 15}
    fil = [{"id": 1, "grams": None, "total_g": 100, "cost_per_spool": 20,
            "spool_weight_g": 1000, "color_name": "x", "type": "PLA", "brand": "y"}]
    b = ce.compute_model_cost(model, fil, s)

    rate = ce.compute_machine_rate(s)["rate_per_hr"]
    close(b["part_material_cost"], 2.2)
    close(b["labor_cost"], 5.0)
    close(b["machine_cost"], rate)
    close(b["total_cost"], 7.2 + rate)
    assert round(b["total_cost"], 2) == 7.30
    assert round(b["suggested"]["50"], 2) == 14.60
    # price = cost / (1 - margin)
    close(b["suggested"]["60"], b["total_cost"] / 0.4)
    close(b["suggested"]["70"], b["total_cost"] / 0.3)
    assert b["currency"] == "USD"
    # removed features must be gone from the payload
    assert "landed_cost" not in b and "packaging_cost" not in b
    assert "hardware_cost" not in b and "suggested_landed" not in b


def test_labor_rate_change_recompute():
    """README: labour 20 -> 25/hr makes the total move 7.30 -> 8.55."""
    s = default_settings()
    model = {"filament_amount_g": 100, "print_time_hr": 1, "labor_min": 15}
    fil = [{"id": 1, "grams": None, "total_g": 100, "cost_per_spool": 20,
            "spool_weight_g": 1000}]
    s["labor_hourly_rate"] = "25"
    b = ce.compute_model_cost(model, fil, s)
    assert round(b["total_cost"], 2) == 8.55


def test_multi_filament_cost_split():
    s = default_settings()
    model = {"filament_amount_g": 100, "print_time_hr": 0, "labor_min": 0}
    fil = [{"id": 1, "grams": None, "total_g": 100, "cost_per_spool": 20, "spool_weight_g": 1000},
           {"id": 2, "grams": None, "total_g": 100, "cost_per_spool": 40, "spool_weight_g": 1000}]
    b = ce.compute_model_cost(model, fil, s)
    # 50 g @20/kg + 50 g @40/kg, x1.1
    close(b["part_material_cost"], (0.05 * 20 + 0.05 * 40) * 1.1)
    assert len(b["filaments"]) == 2
    close(b["filaments"][0]["grams"], 50)
    close(b["filaments"][1]["grams"], 50)


def test_pinned_grams_allocation_in_cost():
    s = default_settings()
    model = {"filament_amount_g": 100, "print_time_hr": 0, "labor_min": 0}
    fil = [{"id": 1, "grams": 25, "total_g": 100, "cost_per_spool": 20, "spool_weight_g": 1000},
           {"id": 2, "grams": None, "total_g": 100, "cost_per_spool": 40, "spool_weight_g": 1000}]
    b = ce.compute_model_cost(model, fil, s)
    close(b["part_material_cost"], (0.025 * 20 + 0.075 * 40) * 1.1)
    close(b["filaments"][0]["grams"], 25)
    close(b["filaments"][1]["grams"], 75)


def test_cost_per_kg_fallback_when_spool_weight_missing():
    s = default_settings()
    model = {"filament_amount_g": 10, "print_time_hr": 0, "labor_min": 0}
    fil = [{"id": 9, "grams": 10, "total_g": 10, "spool_weight_g": 0, "cost_per_kg": 25}]
    b = ce.compute_model_cost(model, fil, s)
    close(b["part_material_cost"], 0.01 * 25 * 1.1)


def test_zero_costs_zero_prices():
    s = default_settings()
    b = ce.compute_model_cost({"filament_amount_g": 0, "print_time_hr": 0, "labor_min": 0},
                              [], s)
    close(b["total_cost"], 0)
    for k in b["suggested"].values():
        close(k, 0)


def test_currency_from_settings():
    s = dict(default_settings(), currency_code="EUR")
    b = ce.compute_model_cost({"filament_amount_g": 0, "print_time_hr": 0, "labor_min": 0},
                              [], s)
    assert b["currency"] == "EUR"


def test_printer_override_flows_through_model_cost():
    """A printer's printer_cost override must change the machine rate inside
    the model breakdown (this is the preview feature)."""
    base = ce.compute_machine_rate(default_settings())["rate_per_hr"]
    over = ce.merge_settings(default_settings(), {"printer_cost": "2000"})
    merged = ce.compute_machine_rate(over)["rate_per_hr"]
    assert abs(merged - base) > 1e-6, "override should change the rate"
    model = {"filament_amount_g": 100, "print_time_hr": 5, "labor_min": 0}
    b_global = ce.compute_model_cost(model, [{"id": 1, "grams": None, "total_g": 100,
                                              "cost_per_spool": 20, "spool_weight_g": 1000}],
                                     default_settings())
    b_printer = ce.compute_model_cost(model, [{"id": 1, "grams": None, "total_g": 100,
                                               "cost_per_spool": 20, "spool_weight_g": 1000}],
                                      over)
    close(b_printer["machine_cost"], 5 * merged)
    assert b_global["machine_cost"] != b_printer["machine_cost"]


def test_machine_rate_near_zero_uptime_is_safe():
    """An almost-zero uptime fraction used to divide by ~0 and emit Infinity
    (invalid JSON) in the rate. That is outside the sheet's model, so capital
    cost simply does not apply — mirroring the exact-zero-uptime branch —
    while the electrical component still counts."""
    s = dict(default_settings(), estimated_uptime_fraction=1e-300)
    r = ce.compute_machine_rate(s)
    rate = r["rate_per_hr"]
    assert math.isfinite(rate), f"rate must be finite (got {rate!r})"
    # capital cost does not apply below the guard, so only the electrical part
    # counts: (150/1000)*0.10643*1.3
    close(rate, (150 / 1000.0) * 0.10643 * 1.3, tol=1e-12)
    # exact zero is still safe (existing behavior, now covered together):
    # capital cost off, electrical part still counts
    zero = ce.compute_machine_rate(dict(default_settings(),
                                        estimated_uptime_fraction=0))
    assert zero["components"]["capital_cost_per_hr"] == 0.0 and math.isfinite(
        zero["rate_per_hr"])


def test_capped_inputs_stay_finite_through_the_engine():
    """With the API caps applied (cost/spool ≤ 1e12, grams ≤ 1e6, qty/unit ≤ 1e6,
    factors ≤ 1e12), every product the engine forms must stay finite — this is
    what guarantees no Infinity token can reach a JSON response."""
    s = dict(default_settings(),
             material_efficiency_factor=1e12,   # the settings cap
             labor_hourly_rate=1e12,
             print_time_rate_override=0)
    model = {"filament_amount_g": 1e6, "print_time_hr": 1e6, "labor_min": 1e6}
    row = {"id": 1, "grams": 1e6, "total_g": 1e6,
           "cost_per_spool": 1e12, "spool_weight_g": 1}      # worst case: 1 g spool
    b = ce.compute_model_cost(model, [row], s,
                              [{"description": "x", "quantity": 1e6, "unit_cost": 1e6}])
    for key in ("part_material_cost", "labor_cost", "machine_cost",
                "materials_cost", "total_cost"):
        assert math.isfinite(b[key]), f"{key} overflowed: {b[key]!r}"
    for m, v in b["suggested"].items():
        assert math.isfinite(v), f"suggested[{m}] overflowed: {v!r}"
