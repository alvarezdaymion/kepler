"""Command line: kepler demo | init | check | run | serve."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import controller, web
from .sources.demo import SCENARIOS, DemoWorld

TEMPLATE = Path(__file__).parent / "templates" / "kepler.example.toml"
ENV_VARS = {"meta": ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"), "stripe": ("STRIPE_API_KEY",)}


def demo_config(root: Path) -> config_mod.Config:
    return config_mod.from_dict({
        "business": {"name": "Demo business (synthetic data)"},
        "auto_cut": {"mode": "dry-run", "source": "ads"},
        "sources": [
            {"name": "ads", "type": "demo", "kind": "ads", "max_age_minutes": 30},
            {"name": "crm", "type": "demo", "kind": "crm", "max_age_minutes": 30},
            {"name": "payments", "type": "demo", "kind": "payments", "max_age_minutes": 30},
            {"name": "email", "type": "demo", "kind": "email", "max_age_minutes": 1500},
            {"name": "organic", "type": "demo", "kind": "organic", "max_age_minutes": 1500, "lag_hours": 50},
        ],
    }, root)


def summary(snap: dict[str, Any]) -> str:
    out = []
    g = snap["goal"]
    actual = g.get("actual_per_day")
    out.append("Goal: %s sale(s)/day. Last %d days: %s."
               % (g["sales_per_day"], snap["windows"]["pace_days"],
                  "sales not tracked" if actual is None else "%.2f/day" % actual))
    need = snap["funnel"]["rows"]
    out.append("Needed per day at benchmark: " + " -> ".join(
        "%s %s" % ("?" if r["needed_at_benchmark"] is None else format(r["needed_at_benchmark"], ",.0f"),
                   r["label"].lower()) for r in need))
    out.append("Top bottlenecks (monthly revenue at stake):")
    shown = 0
    for b in snap["bottlenecks"]:
        if b["status"] in ("not_tracked",) or shown >= 5:
            continue
        shown += 1
        if b.get("blocked_by") and b["status"] in ("behind", "zero", "flagged_early"):
            stake = '$0 until "%s" is fixed' % b["blocked_by"]
        else:
            stake = "$%s" % format(b["at_stake"], ",.0f")
        if b["kind"] == "rate":
            act = "–" if b["actual"] is None else "%.1f%%" % (b["actual"] * 100)
            detail = "%s vs %.1f%% benchmark" % (act, b["benchmark"] * 100)
        else:
            detail = "%s/mo vs %s/mo needed" % (format(b["actual"] or 0, ",.0f"), format(b["benchmark"], ",.0f"))
        out.append("  %d. %-24s %-28s %-10s %s" % (shown, b["label"], detail, b["status"], stake))
    if not shown:
        out.append("  none yet: no funnel step has data (see Sources)")
    out.append("Sources:" if snap["sources"] else "Sources: none configured")
    for s in snap["sources"]:
        out.append("  %-10s %-8s %s" % (s["name"], s["status"], s.get("error") or ""))
    ac = snap.get("auto_cut") or {}
    out.append("Auto-cut (%s):" % ac.get("mode", "off"))
    for p in ac.get("would_pause", []):
        out.append("  would pause %s (ID %s): %s" % (p["ad_name"], p["ad_id"], p["reason"]))
    for p in ac.get("paused", []):
        out.append("  PAUSED %s (ID %s): %s" % (p["ad_name"], p["ad_id"], p["reason"]))
    for h in ac.get("held", []):
        out.append("  guard held %s (ID %s): %s" % (h["ad_name"], h["ad_id"], "; ".join(h["reasons"])))
    for h in ac.get("holds", []):
        out.append("  held: %s" % h)
    if ac.get("mode") != "off" and not (ac.get("would_pause") or ac.get("paused") or ac.get("held")
                                        or ac.get("holds")):
        out.append("  nothing to cut")
    a = snap.get("analysis") or {}
    out.append("Analysis: %s" % (a.get("reason") or a.get("status")))
    return "\n".join(out)


def cmd_demo(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    cfg = demo_config(root)
    now = dt.datetime.now().astimezone()
    world = DemoWorld(now.date(), args.scenario, now)
    snap = controller.run_tick(cfg, now, demo_world=world)
    print("Demo (%s scenario), synthetic data only. Nothing was changed anywhere.\n" % args.scenario)
    print(summary(snap))
    print("\nPages written to %s" % cfg.site_dir)
    if args.serve:
        web.serve(cfg.site_dir, port=args.port)
    else:
        print("Open %s/index.html, or run: kepler demo --serve" % cfg.site_dir)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.config)
    if dest.exists() and not args.force:
        print("%s already exists (use --force to overwrite)" % dest)
        return 1
    shutil.copyfile(TEMPLATE, dest)
    print("Wrote %s. Edit the goal, funnel benchmarks and sources, then run: kepler check" % dest)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    print("Config OK: %s" % Path(args.config).resolve())
    print("Goal: %s sale(s)/day at $%s per sale" % (cfg["goal"]["sales_per_day"], cfg["goal"]["revenue_per_sale"]))
    if not cfg["sources"]:
        print("No sources configured: every metric will show as Not tracked.")
    for s in cfg["sources"]:
        need = ENV_VARS.get(s.get("type", ""), ())
        missing = [v for v in need if not os.environ.get(v)]
        extra = ""
        if s.get("type") == "csv":
            p = cfg.root / s["path"]
            extra = "file found" if p.exists() else "file missing: %s" % p
        state = "missing env: " + ", ".join(missing) if missing else (extra or "env set")
        print("  source %-14s type=%-7s %s" % (s["name"], s.get("type"), state))
    print("Auto-cut mode: %s" % cfg["auto_cut"]["mode"])
    print("Analysis: %s" % ("on, every %s min" % cfg["analysis"]["every_minutes"]
                            if cfg["analysis"]["enabled"] else "off"))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    while True:
        snap = controller.run_tick(cfg)
        if not args.quiet:
            print(summary(snap))
        if not args.loop:
            return 0
        time.sleep(60 * float(cfg["controller"]["every_minutes"]))


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    if not (cfg.site_dir / "index.html").exists():
        controller.run_tick(cfg)
    web.serve(cfg.site_dir, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kepler", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="run once on synthetic data, no keys needed")
    d.add_argument("--scenario", choices=SCENARIOS, default="zero-bookings")
    d.add_argument("--dir", default="kepler-demo", help="where the demo writes state and pages")
    d.add_argument("--serve", action="store_true")
    d.add_argument("--port", type=int, default=8765)
    d.set_defaults(fn=cmd_demo)
    i = sub.add_parser("init", help="write kepler.toml from the example")
    i.add_argument("--config", default="kepler.toml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)
    c = sub.add_parser("check", help="validate the config and show which credentials are set")
    c.add_argument("--config", default="kepler.toml")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("run", help="one controller tick: read every source, rebuild everything")
    r.add_argument("--config", default="kepler.toml")
    r.add_argument("--loop", action="store_true", help="keep running every controller.every_minutes")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("serve", help="serve the pages on localhost")
    s.add_argument("--config", default="kepler.toml")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
