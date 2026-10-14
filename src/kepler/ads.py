"""Ad health: one verdict per ad, keyed by ad ID.

T is the target cost per lead (`ads.target_cpl`). Cumulative numbers over `ads.judge_days`, never
one day's:

- spend < 2T                    -> testing (never cut)
- spend >= 2T and 0 leads       -> cut
- cost per lead <= T            -> keep
- T < cost per lead <= 2T       -> warning; cut once it has stayed in warning longer than
                                   `ads.warning_max_hours`
- cost per lead > 2T            -> cut

The multiples (2 and 2) are `ads.floor_multiple` and `ads.cut_multiple`.

Everything is keyed by ad ID. Two ads can share a name (a duplicated campaign copies the name);
summing by name would hand one ad the other's spend.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from .sources.base import AdRow


def money(x: float) -> str:
    return "$%s" % format(round(x, 2), ",.2f")


def decide(spend: float, leads: int, target: float, floor_multiple: float = 2.0,
           cut_multiple: float = 2.0) -> tuple[str, str]:
    """One ad's verdict: (testing | keep | warning | cut, reason)."""
    floor = floor_multiple * target
    if spend < floor:
        got = "0 leads" if not leads else "%d lead%s" % (leads, "" if leads == 1 else "s")
        return "testing", "%s on %s; judged from %s (%gx target)" % (got, money(spend), money(floor),
                                                                    floor_multiple)
    if not leads:
        return "cut", "0 leads on %s (floor %s)" % (money(spend), money(floor))
    cpl = spend / leads
    each = "%s per lead (%d on %s)" % (money(cpl), leads, money(spend))
    if cpl > cut_multiple * target:
        return "cut", "%s, over %s (%gx target)" % (each, money(cut_multiple * target), cut_multiple)
    if cpl > target:
        return "warning", "%s, over the %s target" % (each, money(target))
    return "keep", "%s, at or under the %s target" % (each, money(target))


def aggregate(rows: list[AdRow], since: str, ad_leads: list[tuple[str, str, int]] | None = None
              ) -> dict[str, dict[str, Any]]:
    """Totals per ad ID from `since` on. The latest row's name and status win.
    With `ad_leads` (from a CRM), leads come from there instead of the ad rows."""
    acc: dict[str, dict[str, Any]] = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0,
                                                          "leads": 0, "name": "", "status": "",
                                                          "campaign_id": "", "last": ""})
    for r in rows:
        if r.date < since:
            continue
        a = acc[r.ad_id]
        a["spend"] += r.spend
        a["impressions"] += r.impressions
        a["clicks"] += r.clicks
        if ad_leads is None:
            a["leads"] += r.leads
        if r.date >= a["last"]:
            a["last"] = r.date
            a["name"], a["status"], a["campaign_id"] = r.ad_name, r.status, r.campaign_id
    if ad_leads is not None:
        for day, ad_id, n in ad_leads:
            if day >= since and ad_id in acc:
                acc[ad_id]["leads"] += n
    for a in acc.values():
        a["spend"] = round(a["spend"], 2)
    return dict(acc)


def build(store: Any, cfg: Any, state: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    """Verdicts for every ad. `state` holds when each ad entered warning; it is updated in place."""
    a = cfg["ads"]
    target = float(a["target_cpl"])
    since = (store.today - dt.timedelta(days=int(a["judge_days"]))).isoformat()
    use_crm = a.get("leads_from") == "ad_leads" and store.has_ad_leads
    totals = aggregate(store.ads, since, store.ad_leads if use_crm else None)
    warn_since: dict[str, str] = state.setdefault("warning_since", {})
    limit_h = float(a["warning_max_hours"])
    rows = []
    for ad_id, t in totals.items():
        level, reason = decide(t["spend"], t["leads"], target, float(a["floor_multiple"]),
                               float(a["cut_multiple"]))
        hours_in_warning = None
        if level == "warning":
            started = dt.datetime.fromisoformat(warn_since.setdefault(ad_id, now.isoformat()))
            hours_in_warning = (now - started).total_seconds() / 3600
            if hours_in_warning > limit_h:
                level = "cut"
                reason += "; in warning for %.0f h (limit %.0f h)" % (hours_in_warning, limit_h)
        else:
            warn_since.pop(ad_id, None)
        rows.append({"ad_id": ad_id, "ad_name": t["name"], "campaign_id": t["campaign_id"],
                     "status": t["status"], "spend": t["spend"], "impressions": t["impressions"],
                     "clicks": t["clicks"], "leads": t["leads"],
                     "cpl": round(t["spend"] / t["leads"], 2) if t["leads"] else None,
                     "verdict": level, "reason": reason, "hours_in_warning": hours_in_warning})
    order = {"cut": 0, "warning": 1, "testing": 2, "keep": 3}
    rows.sort(key=lambda r: (r["status"] != "ACTIVE", order[r["verdict"]], -r["spend"]))
    names: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        names[r["ad_name"]].append(r["ad_id"])
    shared = {n: ids for n, ids in names.items() if n and len(ids) > 1}
    return {"target_cpl": target, "floor": float(a["floor_multiple"]) * target, "since": since,
            "leads_from": "crm" if use_crm else "ads", "warning_max_hours": limit_h,
            "rows": rows, "shared_names": shared}
