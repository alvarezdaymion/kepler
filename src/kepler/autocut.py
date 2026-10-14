"""Auto-cut: pause active ads whose verdict is cut. Off by default.

Modes (`auto_cut.mode`):
- "off"      nothing is read or changed; the page lists what the rules would cut.
- "dry-run"  every candidate is re-read and checked by the guard; nothing is changed.
- "live"     same checks, then each ad that passes is set to PAUSED and read back.

It only ever pauses. It never turns an ad on, never touches budgets and never edits anything else.
Holds that stop it acting on numbers that look broken:
- the snapshot is older than `max_snapshot_age_minutes` (live only),
- more than `max_pauses_per_run` ads would be paused at once (nothing is paused; check the target),
- the configured ad source can't re-read a single ad,
- the guard (guard.py) distrusts that ad's numbers.
Every live pause is appended to state/auto-cut.jsonl.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from . import guard


def candidates(ads_block: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in ads_block["rows"] if r["verdict"] == "cut" and r["status"] == "ACTIVE"]


def run(snapshot: dict[str, Any], source: Any, cfg: Any, now: dt.datetime, today: dt.date,
        log_path: Path | None = None) -> dict[str, Any]:
    ac = cfg["auto_cut"]
    mode = ac["mode"]
    cands = candidates(snapshot["ads"])
    out: dict[str, Any] = {"mode": mode, "candidates": [c["ad_id"] for c in cands], "paused": [],
                           "would_pause": [], "held": [], "holds": [], "errors": []}
    if mode == "off" or not cands:
        return out
    age_min = (now - dt.datetime.fromisoformat(snapshot["generated_at"])).total_seconds() / 60
    if mode == "live" and age_min > float(ac["max_snapshot_age_minutes"]):
        out["holds"].append("snapshot is %.0f min old (limit %s); not acting on stale numbers"
                            % (age_min, ac["max_snapshot_age_minutes"]))
        return out
    if len(cands) > int(ac["max_pauses_per_run"]):
        out["holds"].append("%d ads would be paused at once, over the %s-per-run limit. Check the target "
                            "cost per lead before pausing them by hand." % (len(cands), ac["max_pauses_per_run"]))
        return out
    if source is None or not hasattr(source, "reread_ad"):
        out["holds"].append("source %r can't re-read an ad's spend, so no cut can be checked" % ac["source"])
        return out
    since = dt.date.fromisoformat(snapshot["ads"]["since"])
    for c in cands:
        try:
            live_spend, live_imps = source.reread_ad(c["ad_id"], since, today)
        except Exception as e:  # a failed re-read is a hold, never a pause
            out["held"].append({"ad_id": c["ad_id"], "ad_name": c["ad_name"],
                                "reasons": ["fresh read failed: %s" % str(e)[:200]]})
            continue
        reasons = guard.check(c["spend"], c["impressions"], live_spend, live_imps,
                              float(ac["spend_tolerance"]), float(ac["max_cpm"]))
        entry = {"ad_id": c["ad_id"], "ad_name": c["ad_name"], "reason": c["reason"],
                 "snapshot_spend": c["spend"], "live_spend": live_spend}
        if reasons:
            out["held"].append({**entry, "reasons": reasons})
            continue
        if mode == "dry-run":
            out["would_pause"].append(entry)
            continue
        try:
            result = source.pause_ad(c["ad_id"])
        except Exception as e:
            out["errors"].append({"ad_id": c["ad_id"], "error": str(e)[:200]})
            continue
        if result.get("after") == "PAUSED" and result.get("before") == "ACTIVE":
            out["paused"].append({**entry, **result})
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a") as f:
                    f.write(json.dumps({"at": now.isoformat(timespec="seconds"), **entry, **result}) + "\n")
    return out
