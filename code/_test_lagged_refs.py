"""Unit tests for the resolver's lagged self-reference + scalar arithmetic.

Exercises shift_months, the AST-based eval_arithmetic, and eval_sum with mock
get/put. No DB.

Pass criteria (all asserted):
  1. shift_months(date, -1) gives the previous month, month-anchored
  2. x(t) = x(t-1)               -> all zeros (pre-scenario seed = 0)
  3. x(t) = x(t-1) + y(t), y=1   -> arithmetic progression 1, 2, 3, ...
  4. sum with offset             -> y values shifted forward by one month
  5. scalar multiplication       -> 0.3 * x = 30 when x = 100
  6. mixed scalar + variables    -> 0.7 * x + 0.3 * y
  7. division                    -> x / y normal case, y=0 short-circuits to 0
  8. unary minus on scalar       -> -0.5 * x + y
  9. classify rejects unknown vars and unsafe formulas
"""
from __future__ import annotations
from datetime import date

from resolve_scenario import (eval_arithmetic, eval_sum, shift_months,
                              classify, _parse_arithmetic, _arithmetic_names)


def test_shift_months():
    assert shift_months(date(2015, 1, 1), -1)  == date(2014, 12, 1)
    assert shift_months(date(2015, 1, 1), -12) == date(2014, 1, 1)
    assert shift_months(date(2015, 2, 1), 1)   == date(2015, 3, 1)
    assert shift_months(date(2015, 12, 1), 1)  == date(2016, 1, 1)
    assert shift_months(date(2015, 6, 1), 0)   == date(2015, 6, 1)
    print("  [OK] shift_months")


def harness(dates):
    """Build (resolved, get, make_put) for the synthetic scenario."""
    resolved = {}
    scenario_start = dates[0]

    def get(v, d):
        if d < scenario_start: return 0.0
        cell = resolved.get(v, {}).get(d)
        return cell[0] if cell else None

    def make_put(vid):
        def put(d, cell): resolved.setdefault(vid, {})[d] = cell
        return put

    return resolved, get, make_put


def test_self_only():
    dates = [date(2015, m, 1) for m in range(1, 13)]
    resolved, get, make_put = harness(dates)
    a = {"variable_id": "x", "formula": "x", "formula_inputs": ["x"], "formula_input_offsets": [-1]}
    out = eval_arithmetic(a, dates, get, make_put("x"))
    vals = [out[d][0] for d in dates]
    assert vals == [0.0] * 12, f"expected all zeros, got {vals}"
    print("  [OK] x(t) = x(t-1)   -> all zeros")


def test_progression():
    dates = [date(2015, m, 1) for m in range(1, 13)]
    resolved, get, make_put = harness(dates)
    # Pre-seed y = 1.0 at every date (mock TS-observed)
    resolved["y"] = {d: (1.0, "observed", None, "ts_y") for d in dates}
    a = {"variable_id": "x", "formula": "x + y", "formula_inputs": ["x", "y"], "formula_input_offsets": [-1, 0]}
    out = eval_arithmetic(a, dates, get, make_put("x"))
    vals = [out[d][0] for d in dates]
    expected = list(range(1, 13))  # 1, 2, 3, ..., 12
    assert vals == [float(v) for v in expected], f"expected {expected}, got {vals}"
    print(f"  [OK] x(t) = x(t-1) + 1 -> {vals[:5]}...{vals[-1]}")


def test_sum_with_offset():
    dates = [date(2015, m, 1) for m in range(1, 7)]
    resolved, get, make_put = harness(dates)
    # y(Jan)=10, y(Feb)=20, ..., y(Jun)=60
    resolved["y"] = {d: (10.0 * i, "observed", None, "ts_y") for i, d in enumerate(dates, 1)}
    a = {"variable_id": "x", "formula": "sum", "formula_inputs": ["y"], "formula_input_offsets": [-1]}
    out = eval_sum(a, dates, get, make_put("x"))
    vals = [out[d][0] for d in dates]
    # x(Jan) = y(Dec 2014) = 0 (seed). x(Feb) = y(Jan) = 10. x(Mar) = y(Feb) = 20. etc.
    expected = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    assert vals == expected, f"expected {expected}, got {vals}"
    print(f"  [OK] sum lagged -> {vals}")


def test_scalar_mult():
    dates = [date(2015, 1, 1)]
    resolved, get, make_put = harness(dates)
    resolved["x"] = {dates[0]: (100.0, "observed", None, "ts")}
    a = {"variable_id": "y", "formula": "0.3 * x", "formula_inputs": ["x"], "formula_input_offsets": [0]}
    out = eval_arithmetic(a, dates, get, make_put("y"))
    assert out[dates[0]][0] == 30.0, f"expected 30.0, got {out[dates[0]][0]}"
    print("  [OK] 0.3 * x -> 30 when x=100")


def test_mixed_scalar_vars():
    dates = [date(2015, 1, 1)]
    resolved, get, make_put = harness(dates)
    resolved["x"] = {dates[0]: (10.0, "observed", None, "ts_x")}
    resolved["y"] = {dates[0]: (20.0, "observed", None, "ts_y")}
    a = {"variable_id": "z", "formula": "0.7 * x + 0.3 * y", "formula_inputs": ["x", "y"], "formula_input_offsets": [0, 0]}
    out = eval_arithmetic(a, dates, get, make_put("z"))
    # 0.7 * 10 + 0.3 * 20 = 7 + 6 = 13
    assert abs(out[dates[0]][0] - 13.0) < 1e-9, f"expected 13.0, got {out[dates[0]][0]}"
    print("  [OK] 0.7 * x + 0.3 * y -> 13 when x=10, y=20")


def test_division():
    dates = [date(2015, 1, 1), date(2015, 2, 1)]
    resolved, get, make_put = harness(dates)
    resolved["x"] = {d: (10.0, "observed", None, "tsx") for d in dates}
    resolved["y"] = {dates[0]: (5.0, "observed", None, "tsy"), dates[1]: (0.0, "observed", None, "tsy")}
    a = {"variable_id": "z", "formula": "x / y", "formula_inputs": ["x", "y"], "formula_input_offsets": [0, 0]}
    out = eval_arithmetic(a, dates, get, make_put("z"))
    assert out[dates[0]][0] == 2.0, f"expected 2.0, got {out[dates[0]][0]}"
    assert out[dates[1]][0] == 0.0, f"div-by-zero should short-circuit to 0.0, got {out[dates[1]][0]}"
    print("  [OK] x / y -> 2.0 normal, 0.0 when y=0")


def test_unary_minus():
    dates = [date(2015, 1, 1)]
    resolved, get, make_put = harness(dates)
    resolved["x"] = {dates[0]: (10.0, "observed", None, "ts_x")}
    resolved["y"] = {dates[0]: (20.0, "observed", None, "ts_y")}
    a = {"variable_id": "z", "formula": "-0.5 * x + y", "formula_inputs": ["x", "y"], "formula_input_offsets": [0, 0]}
    out = eval_arithmetic(a, dates, get, make_put("z"))
    # -0.5 * 10 + 20 = 15
    assert out[dates[0]][0] == 15.0, f"expected 15.0, got {out[dates[0]][0]}"
    print("  [OK] -0.5 * x + y -> 15 when x=10, y=20")


def test_classify_safety():
    by_id = {"x": {}, "y": {}}
    # accept scalar + var
    a = {"timeseries_id": None, "formula": "0.3 * x", "formula_inputs": ["x"]}
    assert classify(a, by_id) == "arithmetic", "scalar*var should classify as arithmetic"
    # reject formula referencing unknown var
    a = {"timeseries_id": None, "formula": "0.3 * z", "formula_inputs": ["x"]}
    assert classify(a, by_id) == "unknown", "unknown var should classify as unknown"
    # reject function calls (not in whitelist)
    a = {"timeseries_id": None, "formula": "abs(x)", "formula_inputs": ["x"]}
    assert classify(a, by_id) == "unknown", "function call should be rejected"
    # reject power operator (not in whitelist)
    a = {"timeseries_id": None, "formula": "x ** 2", "formula_inputs": ["x"]}
    assert classify(a, by_id) == "unknown", "power op should be rejected"
    # accept bare variable
    a = {"timeseries_id": None, "formula": "x", "formula_inputs": ["x"]}
    assert classify(a, by_id) == "arithmetic", "bare var should classify as arithmetic"
    # accept multi-term old-style
    a = {"timeseries_id": None, "formula": "x + y - x", "formula_inputs": ["x", "y"]}
    assert classify(a, by_id) == "arithmetic", "old-style multi-term should still work"
    print("  [OK] classify accepts/rejects per whitelist")


def main():
    print("Resolver lagged-ref + scalar arithmetic tests:")
    test_shift_months()
    test_self_only()
    test_progression()
    test_sum_with_offset()
    test_scalar_mult()
    test_mixed_scalar_vars()
    test_division()
    test_unary_minus()
    test_classify_safety()
    print("\nAll tests pass.")


if __name__ == "__main__":
    main()
