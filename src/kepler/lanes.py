"""Acquisition lanes: each KPI graded green / yellow / red against its target.

A KPI's `value` is a metric name ("optins") or a ratio of two ("spend / optins"). Totals cover the
last `windows.lane_days` complete days; `per_day = true` divides by the window.

Grades for better = "higher": at or above target is green, within the yellow band below it is
yellow, lower is red. For "lower" it mirrors. A KPI whose metric no source provides is
"not_tracked"; one with a zero denominator is "no_data". Neither is ever shown as 0.
"""

from __future__ import annotations

from typing import Any

from .metrics import MetricStore


def parse(expr: str) -> tuple[str, str | None]:
    parts = [p.strip() for p in expr.split("/")]
    if len(parts) == 1 and parts[0]:
        return parts[0], None
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    raise ValueError("KPI value must be 'metric' or 'metric / metric', got %r" % expr)


def grade(value: float, target: float, better: str, band: float) -> str:
    if better == "lower":
        if value <= target:
            return "green"
        return "yellow" if value <= target * (1 + band) else "red"
    if value >= target:
        return "green"
    return "yellow" if value >= target * (1 - band) else "red"


def kpi(store: MetricStore, k: dict[str, Any], days: int, band: float) -> dict[str, Any]:
    num, den = parse(k["value"])
    out = {"label": k["label"], "target": k.get("target"), "better": k.get("better", "higher"),
           "format": k.get("format", "number"), "value": None, "grade": "not_tracked",
           "stale": store.stale(num) or (den is not None and store.stale(den))}
    n = store.total(num, days)
    d = store.total(den, days) if den else None
    if n is None or (den and d is None):
        return out
    if den:
        if not d:
            out["grade"] = "no_data"
            return out
        value = n / d
    else:
        value = n / days if k.get("per_day") else n
    out["value"] = value
    out["grade"] = grade(value, float(k["target"]), out["better"], band) if k.get("target") is not None else "info"
    return out


ORDER = {"red": 0, "yellow": 1, "green": 2, "info": 3, "no_data": 4, "not_tracked": 5}


def build(store: MetricStore, cfg: Any) -> list[dict[str, Any]]:
    days = cfg["windows"]["lane_days"]
    band = float(cfg["lane_bands"]["yellow"])
    lanes = []
    for lane in cfg["lanes"]:
        kpis = [kpi(store, k, days, band) for k in lane.get("kpis", [])]
        graded = [k["grade"] for k in kpis if k["grade"] in ("red", "yellow", "green")]
        if graded:
            overall = min(graded, key=ORDER.__getitem__)
        else:
            overall = "no_data" if any(k["grade"] in ("no_data", "info") for k in kpis) else "not_tracked"
        lanes.append({"key": lane["key"], "name": lane["name"], "window_days": days, "yellow_band": band,
                      "grade": overall, "kpis": kpis})
    return lanes
