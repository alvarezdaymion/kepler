"""One controller tick. Schedule `kepler run` every 15 minutes.

1. Read every configured source. A source that fails keeps its last good copy and is labelled
   stale; one that has never succeeded is "missing" and its metrics are unknown, never 0.
2. Compute everything in plain Python: goal and funnel, bottlenecks, journey, lanes, ad verdicts.
3. Auto-cut (off by default), then the optional analysis (at most once per its cadence).
4. Write state/snapshot.json and the HTML pages.

Each step after the reads runs on its own, so a failing auto-cut or analysis never stops the page.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from . import ads, analysis, autocut, bottlenecks, funnel, journey, lanes, web
from .metrics import MetricStore
from .sources import apply_rename, build_one
from .sources.base import SourceData
from .sources.demo import DemoWorld
from .state import locked, write_json


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def lookback_days(cfg: Any) -> int:
    w = cfg["windows"]
    return max(w["pace_days"], w["rate_days"], w["lane_days"], int(cfg["ads"]["judge_days"])) + 2


def read_sources(cfg: Any, now: dt.datetime, built: dict[str, Any] | None = None,
                 demo_world: DemoWorld | None = None) -> tuple[MetricStore, list[dict[str, Any]], dict[str, Any]]:
    today = now.date()
    since = today - dt.timedelta(days=lookback_days(cfg))
    store = MetricStore(today)
    health, objs = [], {}
    cache_dir = cfg.state_dir / "sources"
    for spec in cfg["sources"]:
        name = spec["name"]
        cache = cache_dir / ("%s.json" % name)
        data, error = None, None
        try:
            obj = (built or {}).get(name) or build_one(spec, cfg.root, demo_world)
            objs[name] = obj
            data = apply_rename(obj.fetch(since, today), spec.get("rename", {}))
            data.validate()
            write_json(cache, {"fetched_at": now.isoformat(timespec="seconds"), "data": data.to_json()})
        except Exception as e:
            data = None
            error = "%s: %s" % (type(e).__name__, str(e)[:240])
            cached = read_json(cache)
            if cached:
                try:
                    data = SourceData.from_json(cached["data"])
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    error += "; cached data is unusable"
        row: dict[str, Any] = {"name": name, "type": spec.get("type"), "error": error,
                               "max_age_minutes": spec.get("max_age_minutes", 60)}
        if data is None:
            row.update(status="missing", as_of=None, age_minutes=None, metrics=[])
            health.append(row)
            continue
        age = (now - data.as_of).total_seconds() / 60
        stale = error is not None or age < -1 or age > float(row["max_age_minutes"])
        row.update(status="stale" if stale else "ok", as_of=data.as_of.isoformat(timespec="seconds"),
                   age_minutes=round(age, 1), metrics=sorted(data.daily), ad_rows=len(data.ads))
        store.add_source(name, data, stale)
        health.append(row)
    return store, health, objs


def build_snapshot(cfg: Any, store: MetricStore, health: list[dict[str, Any]], now: dt.datetime,
                   ad_state: dict[str, Any]) -> dict[str, Any]:
    f = funnel.build(store, cfg)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "business": cfg["business"],
        "goal": {**cfg["goal"], "actual_per_day": f["actual_per_day"], "progress": f["progress"]},
        "windows": cfg["windows"],
        "funnel": f,
        "bottlenecks": bottlenecks.from_store(store, cfg, f["rows"][0]["needed_at_benchmark"]),
        "journey": journey.build(store, cfg),
        "lanes": lanes.build(store, cfg),
        "ads": {**ads.build(store, cfg, ad_state, now), "tracked": store.tracked("spend") or bool(store.ads)},
        "sources": health,
        "warnings": list(store.conflicts),
    }


def run_tick(cfg: Any, now: dt.datetime | None = None, built: dict[str, Any] | None = None,
             demo_world: DemoWorld | None = None, llm_client: Any = None) -> dict[str, Any]:
    with locked(cfg.state_dir / "tick.lock"):
        return _run_tick(cfg, now, built, demo_world, llm_client)


def _run_tick(cfg: Any, now: dt.datetime | None, built: dict[str, Any] | None,
              demo_world: DemoWorld | None, llm_client: Any) -> dict[str, Any]:
    now = now or dt.datetime.now().astimezone()
    started = time.monotonic()

    def decision_clock() -> dt.datetime:
        return now + dt.timedelta(seconds=time.monotonic() - started)

    state = cfg.state_dir
    state.mkdir(parents=True, exist_ok=True)
    store, health, objs = read_sources(cfg, now, built, demo_world)
    ad_state = read_json(state / "ad-state.json")
    if not isinstance(ad_state, dict) or any(not isinstance(v, dict) for v in ad_state.values()):
        ad_state = {}
    snap = build_snapshot(cfg, store, health, now, ad_state)
    write_json(state / "ad-state.json", ad_state)
    try:
        snap["auto_cut"] = autocut.run(snap, objs.get(cfg["auto_cut"]["source"]), cfg, now, store.today,
                                       state / "auto-cut.jsonl", decision_clock=decision_clock)
    except Exception as e:
        snap["auto_cut"] = {"mode": cfg["auto_cut"]["mode"], "errors": [{"error": str(e)[:300]}],
                            "paused": [], "would_pause": [], "held": [], "holds": [], "candidates": []}
    try:
        snap["analysis"] = analysis.maybe_run(cfg, snap, now, state, llm_client)
    except Exception as e:
        snap["analysis"] = {"status": "error", "error": str(e)[:300]}
    write_json(state / "snapshot.json", snap)
    web.render_site(snap, cfg.site_dir)
    return snap
