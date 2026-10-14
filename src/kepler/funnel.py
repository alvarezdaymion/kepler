"""Work the funnel backwards from the goal.

With a goal of G sales a day and step rates r1..rn, the step before a sale needs G / rn a day, the
one before that G / rn / r(n-1), and so on up to the top of the funnel. Kepler shows the answer at
your benchmark rates and, where it can measure them, at your actual rates.
"""

from __future__ import annotations

from typing import Any

from .metrics import MetricStore, ratio


def metrics_in_order(steps: list[dict[str, Any]]) -> list[str]:
    return [steps[0]["from"]] + [s["to"] for s in steps]


def required_per_day(steps: list[dict[str, Any]], goal: float, rates: list[float | None]) -> list[float | None]:
    """Daily volume each funnel metric needs to hit `goal` sales a day, top of funnel first.
    A missing or zero rate makes everything above it unreachable (None)."""
    need: list[float | None] = [None] * (len(steps) + 1)
    need[-1] = goal
    for i in range(len(steps) - 1, -1, -1):
        below, r = need[i + 1], rates[i]
        need[i] = below / r if below is not None and r else None
    return need


def build(store: MetricStore, cfg: Any) -> dict[str, Any]:
    steps = cfg["funnel"]["steps"]
    w = cfg["windows"]
    goal = float(cfg["goal"]["sales_per_day"])
    order = metrics_in_order(steps)
    bench = [float(s["benchmark"]) for s in steps]
    actual = [ratio(store.total(s["to"], w["rate_days"]), store.total(s["from"], w["rate_days"])) for s in steps]
    at_bench = required_per_day(steps, goal, list(bench))
    at_actual = required_per_day(steps, goal, list(actual))
    rows = []
    for i, metric in enumerate(order):
        rows.append({
            "metric": metric,
            "label": cfg.label(metric),
            "tracked": store.tracked(metric),
            "stale": store.stale(metric),
            "actual_per_day": store.per_day(metric, w["pace_days"]),
            "needed_at_benchmark": at_bench[i],
            "needed_at_actual": at_actual[i],
            "rate_in": None if i == 0 else {"label": steps[i - 1]["label"], "benchmark": bench[i - 1],
                                            "actual": actual[i - 1]},
        })
    sales = store.per_day(order[-1], w["pace_days"])
    return {
        "goal_per_day": goal,
        "sales_metric": order[-1],
        "actual_per_day": sales,
        "progress": None if sales is None or goal <= 0 else sales / goal,
        "rows": rows,
    }
