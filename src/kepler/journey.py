"""The customer journey: configurable stages grouped attraction -> conversion -> fulfillment."""

from __future__ import annotations

from typing import Any

from .metrics import MetricStore, ratio

GROUPS = ("attraction", "conversion", "fulfillment")


def build(store: MetricStore, cfg: Any) -> dict[str, Any]:
    days = cfg["windows"]["rate_days"]
    stages, prev = [], None
    for n, st in enumerate(cfg["journey"], start=1):
        metric = st.get("metric") or ""
        value = store.total(metric, days) if metric else None
        if value is None:
            status = "not_tracked"
        elif store.stale(metric):
            status = "stale"
        else:
            status = "tracked"
        stages.append({"n": n, "stage": st["stage"], "group": st.get("group", "attraction"),
                       "metric": metric, "value": value, "status": status,
                       "from_previous": ratio(value, prev) if value is not None else None})
        if value is not None:
            prev = value
    groups = [{"group": g, "stages": [s for s in stages if s["group"] == g]} for g in GROUPS]
    extra = sorted({s["group"] for s in stages} - set(GROUPS))
    groups += [{"group": g, "stages": [s for s in stages if s["group"] == g]} for g in extra]
    tracked = sum(1 for s in stages if s["status"] != "not_tracked")
    return {"window_days": days, "groups": groups, "tracked": tracked, "total": len(stages)}
