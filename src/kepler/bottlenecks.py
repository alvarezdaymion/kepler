"""Rank funnel steps by the monthly revenue their shortfall costs you.

Rate step:   monthly volume into the step x (benchmark - actual) x rates of every later step x revenue per sale
Volume step: (monthly volume needed - actual) x rates of every later step x revenue per sale

"Rates of every later step" uses each later step's actual rate once it has enough volume to judge
(capped at its benchmark), and its benchmark otherwise. So when a later step sits at zero, every step
before it is worth $0 until that step is fixed: more traffic into a broken step earns nothing.

A rate is judged once the step has `min_volume` people in it. Before that it is "too early", except:
- zero conversions after twice the volume that should have produced one is flagged at once, and
- a result so unlikely at the benchmark rate (one-sided binomial p < 0.05) is flagged early.

Pure functions: no I/O, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

EARLY_FLAG_P = 0.05
DAYS_PER_MONTH = 30


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    kind: str  # "rate" or "volume"
    benchmark: float  # a rate, or the monthly volume needed for a volume step
    monthly_in: float | None  # monthly pace into the step (rate) or actual monthly volume (volume)
    entered: float | None = None  # people who entered the step in the rate window
    converted: float | None = None  # people who got through it in the rate window
    min_volume: float = 0.0
    action: str = ""
    downstream: float = 1.0  # set by chain()


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if n <= 0:
        return 1.0
    if p >= 1:
        return 0.0 if k < n else 1.0
    term = (1 - p) ** n
    total = term
    for i in range(1, k + 1):
        term *= (n - i + 1) / i * p / (1 - p)
        total += term
    return min(1.0, total)


def evaluate(step: Step, revenue_per_sale: float) -> dict[str, Any]:
    out: dict[str, Any] = {"key": step.key, "label": step.label, "kind": step.kind,
                           "benchmark": step.benchmark, "action": step.action, "at_stake": 0.0,
                           "actual": None, "status": "not_tracked"}
    if step.kind == "volume":
        if step.monthly_in is None:
            return out
        gap = max(0.0, step.benchmark - step.monthly_in)
        out.update(actual=step.monthly_in, status="behind" if gap > 0 else "on_track",
                   at_stake=round(gap * step.downstream * revenue_per_sale, 2))
        return out
    if step.entered is None or step.converted is None or step.monthly_in is None:
        return out
    actual = step.converted / step.entered if step.entered else None
    out.update(actual=actual, entered=step.entered, converted=step.converted)
    if step.entered and step.entered >= step.min_volume:
        gap = max(0.0, step.benchmark - (actual or 0.0))
        out["status"] = "behind" if gap > 0 else "on_track"
    elif step.converted == 0 and step.entered * step.benchmark >= 2:
        gap = step.benchmark
        out["status"] = "zero"
    elif step.entered and binom_cdf(int(step.converted), int(step.entered), step.benchmark) < EARLY_FLAG_P:
        gap = max(0.0, step.benchmark - (actual or 0.0))
        out["status"] = "flagged_early"
    else:
        out["status"] = "too_early"
        return out
    out["at_stake"] = round(step.monthly_in * gap * step.downstream * revenue_per_sale, 2)
    return out


SORT = {"zero": 0, "flagged_early": 0, "behind": 0, "too_early": 1, "on_track": 2, "not_tracked": 3}


def chain(steps: list[Step], revenue_per_sale: float) -> list[dict[str, Any]]:
    """Evaluate steps given top of funnel first; return them biggest revenue at stake first."""
    results: list[dict[str, Any]] = [{} for _ in steps]
    running, blocker = 1.0, None
    for i in range(len(steps) - 1, -1, -1):
        s = steps[i]
        r = evaluate(replace(s, downstream=running), revenue_per_sale)
        r["position"] = i
        if running == 0 and blocker:
            r["blocked_by"] = blocker
        results[i] = r
        if s.kind != "rate":
            continue
        if r["status"] == "zero":
            eff = 0.0
        elif r["status"] in ("behind", "flagged_early", "on_track") and r["actual"] is not None:
            eff = min(float(r["actual"]), s.benchmark)
        else:
            eff = s.benchmark  # too early or not tracked: assume benchmark
        if eff == 0 and blocker is None:
            blocker = s.label
        running *= eff
    return sorted(results, key=lambda r: (SORT[r["status"]], -r["at_stake"], r["position"]))


def from_store(store: Any, cfg: Any, needed_top_per_day: float | None) -> list[dict[str, Any]]:
    """Build the steps from the metric store and config, then rank them."""
    w = cfg["windows"]
    steps_cfg = cfg["funnel"]["steps"]
    top = steps_cfg[0]["from"]
    pace = store.per_day(top, w["pace_days"])
    steps = [Step(key=top, label=cfg.label(top) + " volume", kind="volume",
                  benchmark=(needed_top_per_day or 0.0) * DAYS_PER_MONTH,
                  monthly_in=None if pace is None else pace * DAYS_PER_MONTH,
                  action="Raise reach: more budget on ads that keep, or more posting.")]
    for s in steps_cfg:
        pace_in = store.per_day(s["from"], w["pace_days"])
        steps.append(Step(
            key="%s_to_%s" % (s["from"], s["to"]), label=s["label"], kind="rate",
            benchmark=float(s["benchmark"]),
            monthly_in=None if pace_in is None else pace_in * DAYS_PER_MONTH,
            entered=store.total(s["from"], w["rate_days"]),
            converted=store.total(s["to"], w["rate_days"]),
            min_volume=float(s.get("min_volume", 0)), action=s.get("action", "")))
    uses = {top: [top]} | {"%s_to_%s" % (s["from"], s["to"]): [s["from"], s["to"]] for s in steps_cfg}
    ranked = chain(steps, float(cfg["goal"]["revenue_per_sale"]))
    for r in ranked:
        r["stale"] = any(store.stale(m) for m in uses[r["key"]])
    return ranked
