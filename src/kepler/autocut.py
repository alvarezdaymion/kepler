"""Guarded auto-cut: fresh inputs, shared preflight, and durable pause reservations.

Both dry-run and live check source health, campaign eligibility and the bad-data guard.
Live journals an attempt before sending a pause; an attempted ad is never retried automatically.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import guard
from .state import append_json, locked


def candidates(ads_block: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in ads_block["rows"] if r["verdict"] == "cut" and r["status"] == "ACTIVE"]


def _freshness_holds(snapshot: dict[str, Any], cfg: Any, now: dt.datetime) -> list[str]:
    try:
        age = (now - dt.datetime.fromisoformat(snapshot["generated_at"])).total_seconds() / 60
        if age < -1 or age > float(cfg["auto_cut"]["max_snapshot_age_minutes"]):
            return ["snapshot is outside its freshness limit"]
        block = snapshot["ads"]
        selected_crm = cfg["ads"]["leads_from"] == "ad_leads"
        if block["leads_from"] != ("crm" if selected_crm else "ads") or not block["leads_available"]:
            return ["selected lead source is unavailable"]
        source_name = cfg["auto_cut"]["source"]
        required = {source_name}
        for row in candidates(block):
            if row.get("sources") != [source_name]:
                return ["ad inputs do not come exclusively from the configured auto-cut source"]
        if selected_crm:
            if not block["lead_sources"]:
                return ["CRM lead provenance is unavailable"]
            required.update(block["lead_sources"])
            required.update(s["name"] for s in cfg["sources"] if s.get("kind") == "ad_leads")
        health = {s["name"]: s for s in snapshot["sources"]}
        holds = []
        for name in sorted(required):
            row = health[name]
            age = (now - dt.datetime.fromisoformat(row["as_of"])).total_seconds() / 60
            limit = float(row["max_age_minutes"])
            if row["status"] != "ok" or not math.isfinite(limit) or limit <= 0 or age < -1 or age > limit:
                holds.append("source %s is stale or unhealthy" % name)
        return holds
    except (KeyError, TypeError, ValueError, OverflowError):
        return ["input freshness or provenance is missing or invalid"]


def _attempted(path: Path, source: str) -> set[str]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return set()
    attempted = set()
    for line in lines:
        record = json.loads(line)
        if not isinstance(record, dict) or not isinstance(record.get("ad_id"), str):
            raise ValueError("invalid pause journal")
        if record.get("source", source) == source:
            attempted.add(record["ad_id"])
    return attempted


def run(snapshot: dict[str, Any], source: Any, cfg: Any, now: dt.datetime, today: dt.date,
        log_path: Path | None = None, decision_clock: Callable[[], dt.datetime] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    clock = decision_clock or (lambda: now + dt.timedelta(seconds=time.monotonic() - started))
    path = log_path if log_path is not None else cfg.state_dir / "auto-cut.jsonl"
    with locked(path.with_suffix(".lock")):
        return _run(snapshot, source, cfg, today, path, clock)


def _run(snapshot: dict[str, Any], source: Any, cfg: Any, today: dt.date, path: Path,
         clock: Callable[[], dt.datetime]) -> dict[str, Any]:
    ac = cfg["auto_cut"]
    mode = ac["mode"]
    cands = candidates(snapshot["ads"])
    out: dict[str, Any] = {"mode": mode, "candidates": [c["ad_id"] for c in cands], "paused": [],
                           "would_pause": [], "held": [], "holds": [], "errors": []}
    if mode == "off" or not cands:
        return out
    out["holds"] = _freshness_holds(snapshot, cfg, clock())
    if out["holds"]:
        return out
    if len(cands) > int(ac["max_pauses_per_run"]):
        out["holds"].append("%d ads would be paused at once, over the %s-per-run limit. Check the target "
                            "cost per lead before pausing them by hand." % (len(cands), ac["max_pauses_per_run"]))
        return out
    if source is None or not all(callable(getattr(source, method, None))
                                 for method in ("reread_ad", "preflight_ad", "pause_ad")):
        out["holds"].append("source %r cannot preflight, re-read and pause an ad" % ac["source"])
        return out
    try:
        attempted = _attempted(path, ac["source"])
        since = dt.date.fromisoformat(snapshot["ads"]["since"])
        until = dt.date.fromisoformat(snapshot["ads"]["until"])
        if until != today or (until - since).days + 1 != int(cfg["ads"]["judge_days"]):
            raise ValueError("invalid judgment window")
    except (OSError, ValueError, KeyError, TypeError):
        out["holds"].append("pause journal or judgment window is unusable; manual inspection required")
        return out
    for c in cands:
        entry = {"ad_id": c["ad_id"], "ad_name": c["ad_name"], "reason": c["reason"],
                 "snapshot_spend": c["spend"]}
        if c["ad_id"] in attempted:
            out["held"].append({**entry, "reasons": ["a pause was already attempted; manual inspection required"]})
            continue
        reasons = _freshness_holds(snapshot, cfg, clock())
        if reasons:
            out["holds"].extend(reasons)
            break
        try:
            preflight = source.preflight_ad(c["ad_id"])
            if preflight.get("status") != "ACTIVE":
                raise RuntimeError("ad is no longer active")
            live_spend, live_imps = source.reread_ad(c["ad_id"], since, until)
        except Exception as e:
            out["held"].append({**entry, "reasons": ["fresh read or preflight failed: %s" % str(e)[:200]]})
            continue
        reasons = guard.check(c["spend"], c["impressions"], live_spend, live_imps,
                              float(ac["spend_tolerance"]), float(ac["max_cpm"]))
        reasons.extend(_freshness_holds(snapshot, cfg, clock()))
        entry["live_spend"] = live_spend
        if reasons:
            out["held"].append({**entry, "reasons": reasons})
            continue
        if mode == "dry-run":
            out["would_pause"].append(entry)
            continue
        base = {"source": ac["source"], **entry}

        def journal(phase: str, base: dict[str, Any] = base, **extra: Any) -> None:
            append_json(path, {**base, "at": clock().isoformat(timespec="seconds"), "phase": phase, **extra})

        def before_send() -> None:
            holds = _freshness_holds(snapshot, cfg, clock())
            if holds:
                raise RuntimeError("; ".join(holds))

        try:
            journal("attempted")
        except (OSError, ValueError) as e:
            out["errors"].append({"ad_id": c["ad_id"], "error": "cannot reserve pause: %s" % str(e)[:200]})
            break
        attempted.add(c["ad_id"])
        try:
            result = source.pause_ad(c["ad_id"], on_ack=lambda: journal("acknowledged"), before_send=before_send)
            if result.get("after") != "PAUSED" or result.get("before") != "ACTIVE":
                raise RuntimeError("pause verification failed")
            journal("confirmed", **result)
            out["paused"].append({**entry, **result})
        except Exception as e:
            error = str(e)[:200]
            out["errors"].append({"ad_id": c["ad_id"], "error": error})
            try:
                journal("uncertain", error=error)
            except (OSError, ValueError):
                out["errors"].append({"ad_id": c["ad_id"], "error": "cannot record uncertain outcome"})
    return out
