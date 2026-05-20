"""Unit test for the resolver's lagged self-reference support.

Exercises shift_months + eval_arithmetic + eval_sum with mock get/put. No DB.

Pass criteria (all asserted):
  1. shift_months(date, -1) gives the previous month, month-anchored on day=1
  2. x(t) = x(t-1)               -> all zeros (pre-scenario seed = 0)
  3. x(t) = x(t-1) + y(t), y = 1 -> arithmetic progression 1, 2, 3, ...
  4. x(t) = 2*y(t-1) via sum     -> y values shifted forward by one month
"""
from __future__ import annotations
from datetime import date

from resolve_scenario import eval_arithmetic, eval_sum, shift_months


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


def main():
    print("Lagged self-reference tests:")
    test_shift_months()
    test_self_only()
    test_progression()
    test_sum_with_offset()
    print("\nAll tests pass.")


if __name__ == "__main__":
    main()
