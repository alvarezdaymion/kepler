"""Server-rendered HTML from the snapshot. No JavaScript, no build step.

Pages: index.html (overview), lane-<key>.html (one per acquisition lane), ads.html (ad health and
auto-cut). `kepler serve` serves the site folder on localhost.
"""

from __future__ import annotations

import datetime as dt
import functools
import http.server
import os
from html import escape
from pathlib import Path
from typing import Any

CSS = """
:root{--bg:#000;--panel:#0b0b0b;--line:#1f1f1f;--text:#ededed;--muted:#8a8a8a;--accent:#4F070D;
--red:#E5484D;--yellow:#E5B248;--green:#3FB950;--grey:#6e6e6e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
nav{display:flex;flex-wrap:wrap;gap:4px;border-bottom:2px solid var(--accent);padding:10px 16px;background:#050505}
nav a{color:var(--muted);text-decoration:none;padding:6px 10px;border-radius:6px}
nav a.on{background:var(--accent);color:#fff}
h1{font-size:22px;margin:16px 0 4px}h2{font-size:16px;margin:28px 0 8px;text-transform:uppercase;
letter-spacing:.06em;color:#cfcfcf;border-left:4px solid var(--accent);padding-left:8px}
.sub{color:var(--muted);font-size:13px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin:8px 0}
.big{font-size:34px;font-weight:700}
.bar{height:10px;background:#1a1a1a;border-radius:5px;overflow:hidden;margin-top:8px}
.bar>div{height:100%;background:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.wrap{overflow-x:auto}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.green{color:var(--green)}.yellow{color:var(--yellow)}.red{color:var(--red)}.grey{color:var(--grey)}
.bg-green{background:var(--green)}.bg-yellow{background:var(--yellow)}.bg-red{background:var(--red)}
.bg-grey{background:var(--grey)}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line);
color:var(--muted);margin-left:4px}
.tag.stale{border-color:var(--yellow);color:var(--yellow)}.tag.cut{border-color:var(--red);color:var(--red)}
.groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.stage{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed var(--line)}
.lanes{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.lanes a{color:inherit;text-decoration:none}
details{margin:24px 0;color:var(--muted)}summary{cursor:pointer}
pre{white-space:pre-wrap;font:inherit;margin:0}
"""

GRADE_CLASS = {"green": "green", "yellow": "yellow", "red": "red", "info": "grey", "no_data": "grey",
               "not_tracked": "grey"}
STATUS_TEXT = {"zero": "zero", "flagged_early": "far below", "behind": "behind", "too_early": "too early",
               "on_track": "on track", "not_tracked": "not tracked"}


def num(v: float | None, kind: str = "number") -> str:
    if v is None:
        return '<span class="grey">Not tracked</span>'
    if kind == "money":
        return "$" + format(v, ",.0f") if abs(v) >= 100 else "$" + format(v, ",.2f")
    if kind == "percent":
        return format(v * 100, ".1f") + "%"
    if abs(v) >= 100 or float(v).is_integer():
        return format(v, ",.0f")
    return format(v, ",.1f")


def rate_txt(v: float | None, tracked: bool = True) -> str:
    """A rate with no denominator yet is "no data yet"; one whose metrics nobody reads is "Not tracked"."""
    if v is None and tracked:
        return '<span class="grey">no data yet</span>'
    return num(v, "percent")


def stale_tag(flag: bool) -> str:
    return '<span class="tag stale">stale</span>' if flag else ""


def page(title: str, active: str, snap: dict[str, Any], body: str) -> str:
    links = [("index.html", "Overview")] + [("lane-%s.html" % la["key"], la["name"]) for la in snap["lanes"]]
    links.append(("ads.html", "Ad health"))
    nav = "".join('<a href="%s" class="%s">%s</a>' % (h, "on" if h == active else "", escape(t)) for h, t in links)
    when = dt.datetime.fromisoformat(snap["generated_at"]).strftime("%b %d, %H:%M")
    stale = [s["name"] for s in snap["sources"] if s["status"] != "ok"]
    plural = "" if len(stale) == 1 else "s"
    note = ' <span class="tag stale">%d source%s stale or missing</span>' % (len(stale), plural) if stale else ""
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            '<meta http-equiv=refresh content=300><title>%s · Kepler</title><style>%s</style></head><body>'
            "<nav>%s</nav><main><h1>%s</h1><div class=sub>%s · updated %s%s</div>%s</main></body></html>"
            % (escape(title), CSS, nav, escape(title), escape(snap["business"]["name"]), when, note, body))


def goal_card(snap: dict[str, Any]) -> str:
    g = snap["goal"]
    prog = g.get("progress")
    width = 0 if prog is None else min(100, prog * 100)
    pct = "Not tracked" if prog is None else "%.0f%% of goal" % (prog * 100)
    return ('<div class=card><div class=sub>Goal</div><div class=big>%s / day</div>'
            '<div>Last %d days: <b>%s sales a day</b> · %s · %s per sale</div>'
            '<div class=bar><div style="width:%.1f%%"></div></div></div>'
            % (num(g["sales_per_day"]), snap["windows"]["pace_days"], num(g.get("actual_per_day")), pct,
               num(float(g["revenue_per_sale"]), "money"), width))


def funnel_table(snap: dict[str, Any]) -> str:
    rows = []
    prev_tracked = True
    for r in snap["funnel"]["rows"]:
        rate = r["rate_in"]
        conv = "" if rate is None else "%s: %s vs %s" % (
            escape(rate["label"]), rate_txt(rate["actual"], prev_tracked and r["tracked"]),
            num(rate["benchmark"], "percent"))
        prev_tracked = r["tracked"]
        rows.append("<tr><td>%s%s</td><td class=n>%s</td><td class=n>%s</td><td class=n>%s</td><td>%s</td></tr>"
                    % (escape(r["label"]), stale_tag(r["stale"]), num(r["needed_at_benchmark"]),
                       num(r["needed_at_actual"]) if r["needed_at_actual"] is not None else "–",
                       num(r["actual_per_day"]), conv))
    return ('<div class="card wrap"><table><tr><th>Step</th><th>Needed / day at benchmark</th>'
            "<th>Needed / day at your rates</th><th>Actual / day</th><th>Conversion into this step</th></tr>%s"
            "</table></div>" % "".join(rows))


def bottleneck_table(snap: dict[str, Any]) -> str:
    rows = []
    for i, b in enumerate(snap["bottlenecks"], 1):
        if b["status"] == "not_tracked":
            continue
        kind = "percent" if b["kind"] == "rate" else "number"
        if b.get("blocked_by") and b["status"] in ("behind", "zero", "flagged_early"):
            stake = '<span class=grey>$0 until "%s" is fixed</span>' % escape(b["blocked_by"])
        elif b["at_stake"]:
            stake = "<b class=red>%s</b> / mo" % num(b["at_stake"], "money")
        else:
            stake = '<span class=grey>$0</span>'
        if b["kind"] == "rate":
            actual = rate_txt(b["actual"], b["status"] != "not_tracked")
        else:
            actual = num(b["actual"]) + " / mo"
        bench = num(b["benchmark"], kind) if b["kind"] == "rate" else num(b["benchmark"]) + " / mo needed"
        rows.append("<tr><td class=n>%d</td><td>%s%s</td><td class=n>%s</td><td class=n>%s</td><td>%s</td>"
                    "<td class=n>%s</td><td>%s</td></tr>"
                    % (i, escape(b["label"]), stale_tag(b.get("stale", False)), actual, bench,
                       STATUS_TEXT[b["status"]], stake, escape(b["action"])))
    return ('<div class="card wrap"><table><tr><th>#</th><th>Step</th><th>Actual</th><th>Benchmark</th>'
            "<th>Status</th><th>At stake</th><th>Next action</th></tr>%s</table></div>" % "".join(rows))


def blind_spots(snap: dict[str, Any]) -> str:
    steps = [b["label"] for b in snap["bottlenecks"] if b["status"] == "not_tracked"]
    stages = [s["stage"] for g in snap["journey"]["groups"] for s in g["stages"] if s["status"] == "not_tracked"]
    if not steps and not stages:
        return ""
    items = "".join("<li>%s (funnel step)</li>" % escape(s) for s in steps)
    items += "".join("<li>%s (journey stage)</li>" % escape(s) for s in stages)
    return ("<h2>Blind spots</h2><div class=card><div class=sub>No source reads these, so they can't be "
            "ranked in dollars. Start tracking them.</div><ul>%s</ul></div>" % items)


def journey_html(snap: dict[str, Any]) -> str:
    j = snap["journey"]
    cols = []
    for g in j["groups"]:
        lines = []
        for s in g["stages"]:
            conv = ""
            if s["from_previous"] is not None and s["n"] > 1:
                conv = ' <span class=sub>(%s)</span>' % num(s["from_previous"], "percent")
            lines.append('<div class=stage><span>%d. %s%s</span><span>%s%s</span></div>'
                         % (s["n"], escape(s["stage"]), stale_tag(s["status"] == "stale"), num(s["value"]), conv))
        cols.append('<div class=card><div class=sub>%s</div>%s</div>' % (escape(g["group"].title()), "".join(lines)))
    return ('<h2>Customer journey</h2><div class=sub>Last %d days · %d of %d stages tracked</div>'
            '<div class=groups>%s</div>' % (j["window_days"], j["tracked"], j["total"], "".join(cols)))


def lanes_summary(snap: dict[str, Any]) -> str:
    cards = []
    for la in snap["lanes"]:
        g = la["grade"]
        cards.append('<a href="lane-%s.html"><div class=card><span class="dot bg-%s"></span><b>%s</b>'
                     '<div class=sub>%s</div></div></a>'
                     % (escape(la["key"]), GRADE_CLASS.get(g, "grey"), escape(la["name"]),
                        {"not_tracked": "Not tracked", "no_data": "No data yet"}.get(g, g)))
    return '<h2>Lanes</h2><div class=lanes>%s</div>' % "".join(cards)


def analysis_html(snap: dict[str, Any]) -> str:
    a = snap.get("analysis") or {}
    if a.get("status") == "off":
        body = '<span class=grey>%s</span>' % escape(a.get("reason", "Analysis is off."))
    elif a.get("text"):
        meta = "written %s by %s from the numbers on this page" % (a.get("generated_at", "?"), a.get("model", "?"))
        if a.get("status") == "error":
            meta += " · last refresh failed: %s" % a.get("error", "")
        body = "<pre>%s</pre><div class=sub style='margin-top:8px'>%s</div>" % (escape(a["text"]), escape(meta))
    else:
        body = '<span class=red>Analysis failed: %s</span>' % escape(a.get("error", "unknown error"))
    return "<h2>Analysis</h2><div class=card>%s</div>" % body


def sources_html(snap: dict[str, Any]) -> str:
    rows = []
    for s in snap["sources"]:
        cls = {"ok": "green", "stale": "yellow", "missing": "red"}[s["status"]]
        m = None if s["age_minutes"] is None else max(0.0, s["age_minutes"])
        age = "–" if m is None else ("%.0f min" % m if m < 120 else "%.1f h" % (m / 60))
        rows.append("<tr><td>%s</td><td class=%s>%s</td><td class=n>%s</td><td>%s</td><td class=sub>%s</td></tr>"
                    % (escape(s["name"]), cls, s["status"], age, escape(", ".join(s.get("metrics", [])) or "–"),
                       escape(s.get("error") or "")))
    warn = "".join("<div class=yellow>%s</div>" % escape(w) for w in snap.get("warnings", []))
    return ('<h2>Data health</h2><div class="card wrap"><table><tr><th>Source</th><th>Status</th>'
            "<th>Data age</th><th>Metrics</th><th>Last error</th></tr>%s</table>%s</div>" % ("".join(rows), warn))


HOW = """<details><summary>How it's counted</summary>
<p>Every number is computed in code from the sources in Data health. Nothing on this page is written by a model
except the Analysis panel, which only reads these numbers.</p>
<p>Pace is the average over the last {pace} complete days. Conversion rates use the last {rate} days.
Monthly figures are pace x 30.</p>
<p>Needed per day works backwards from the goal: sales needed / last step's rate = volume needed at the
step before, and so on up the funnel.</p>
<p>At stake for a rate step = monthly volume into the step x (benchmark - actual) x the rates of every later
step x revenue per sale. A later step stuck at zero makes every earlier step worth $0 until it is fixed.
A step is judged once it has its minimum volume; zero conversions after twice the volume that should
produce one is flagged at once.</p>
<p>A metric no source provides shows as Not tracked, never 0. A source that failed its last read, or whose
data is older than its limit, is labelled stale and its last good numbers are used.</p></details>"""


def overview(snap: dict[str, Any]) -> str:
    w = snap["windows"]
    body = (goal_card(snap) + "<h2>Funnel, worked back from the goal</h2>" + funnel_table(snap)
            + "<h2>Bottlenecks by revenue at stake</h2>" + bottleneck_table(snap) + blind_spots(snap)
            + journey_html(snap) + lanes_summary(snap) + analysis_html(snap) + sources_html(snap)
            + HOW.format(pace=w["pace_days"], rate=w["rate_days"]))
    return page("Overview", "index.html", snap, body)


def lane_page(snap: dict[str, Any], lane: dict[str, Any]) -> str:
    rows = []
    for k in lane["kpis"]:
        g = k["grade"]
        target = "–" if k["target"] is None else "%s %s" % ("≤" if k["better"] == "lower" else "≥",
                                                             num(float(k["target"]), k["format"]))
        val = '<span class=grey>No data yet</span>' if g == "no_data" else num(k["value"], k["format"])
        rows.append('<tr><td><span class="dot bg-%s"></span>%s%s</td><td class=n>%s</td><td class=n>%s</td>'
                    "<td class=%s>%s</td></tr>"
                    % (GRADE_CLASS.get(g, "grey"), escape(k["label"]), stale_tag(k["stale"]), val, target,
                       GRADE_CLASS.get(g, "grey"), "Not tracked" if g == "not_tracked" else g.replace("_", " ")))
    body = ('<div class=sub>Last %d complete days. Green meets the target, yellow is within %s of it, red is '
            'further off.</div><div class="card wrap"><table><tr><th>KPI</th><th>Value</th><th>Target</th>'
            "<th>Grade</th></tr>%s</table></div>" % (lane["window_days"], "%.0f%%" % (lane["yellow_band"] * 100),
                                                     "".join(rows)))
    return page(lane["name"], "lane-%s.html" % lane["key"], snap, body)


def ads_page(snap: dict[str, Any]) -> str:
    a = snap["ads"]
    if not a.get("tracked"):
        return page("Ad health", "ads.html", snap, '<div class="card grey">No ad source is connected. '
                    "Add a [[sources]] entry with type = \"meta\".</div>")
    cls = {"cut": "red", "warning": "yellow", "keep": "green", "testing": "grey", "not_tracked": "grey"}
    rows = []
    for r in a["rows"]:
        rows.append("<tr><td>%s<div class=sub>ID %s · %s</div></td><td class=n>%s</td><td class=n>%s</td>"
                    "<td class=n>%s</td><td class=n>%s</td><td class=%s><b>%s</b><div class=sub>%s</div></td></tr>"
                    % (escape(r["ad_name"] or "(no name)"), escape(r["ad_id"]), escape(r["status"] or "?"),
                       num(r["spend"], "money"), num(r["impressions"]), num(r["leads"]),
                       num(r["cpl"], "money") if r["cpl"] is not None else "–", cls[r["verdict"]],
                       r["verdict"], escape(r["reason"])))
    shared = ""
    if a.get("shared_names"):
        shared = ("<div class='card yellow'>These names are used by more than one ad, so they are tracked "
                  "separately by ID: %s</div>" % escape("; ".join("%s (%s)" % (n, ", ".join(ids))
                                                                  for n, ids in a["shared_names"].items())))
    body = ('<div class=card>Target cost per lead (T): <b>%s</b> · judged from %s spend (2T) · last %s days · '
            "leads from %s</div>%s"
            '<div class="card wrap"><table><tr><th>Ad</th><th>Spend</th><th>Impr.</th><th>Leads</th><th>CPL</th>'
            "<th>Verdict</th></tr>%s</table></div>%s"
            % (num(a["target_cpl"], "money"), num(a["floor"], "money"),
               (dt.date.fromisoformat(a["until"]) - dt.date.fromisoformat(a["since"])).days + 1,
               a["leads_from"], shared, "".join(rows), autocut_html(snap)))
    return page("Ad health", "ads.html", snap, body)


def autocut_html(snap: dict[str, Any]) -> str:
    ac = snap.get("auto_cut") or {}
    mode = ac.get("mode", "off")
    lines = ["<div>Mode: <b>%s</b>%s</div>" % (escape(mode), " · only pauses; never turns ads on or changes budgets")]
    if mode == "off":
        n = len(ac.get("candidates", []))
        lines.append("<div class=sub>%d active ad%s would be checked for a cut. Set [auto_cut] mode = \"dry-run\" "
                     "to see what the guard says.</div>" % (n, "" if n == 1 else "s"))
    for h in ac.get("holds", []):
        lines.append("<div class=yellow>Held: %s</div>" % escape(h))
    for p in ac.get("would_pause", []):
        lines.append("<div>Would pause <b>%s</b> (ID %s): %s</div>" % (escape(p["ad_name"]), escape(p["ad_id"]),
                                                                       escape(p["reason"])))
    for p in ac.get("paused", []):
        lines.append("<div class=red>Paused <b>%s</b> (ID %s): %s</div>" % (escape(p["ad_name"]), escape(p["ad_id"]),
                                                                            escape(p["reason"])))
    for h in ac.get("held", []):
        lines.append("<div class=yellow>Guard held <b>%s</b> (ID %s): %s</div>"
                     % (escape(h["ad_name"]), escape(h["ad_id"]), escape("; ".join(h["reasons"]))))
    for e in ac.get("errors", []):
        lines.append("<div class=red>Error: %s</div>" % escape(str(e)))
    return "<h2>Auto-cut</h2><div class=card>%s</div>" % "".join(lines)


def render_site(snap: dict[str, Any], site_dir: Path) -> list[Path]:
    site_dir.mkdir(parents=True, exist_ok=True)
    pages = {"index.html": overview(snap), "ads.html": ads_page(snap)}
    for la in snap["lanes"]:
        pages["lane-%s.html" % la["key"]] = lane_page(snap, la)
    out = []
    for name, html in pages.items():
        p = site_dir / name
        tmp = p.with_suffix(".tmp")
        tmp.write_text(html)
        os.replace(tmp, p)
        out.append(p)
    return out


def serve(site_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site_dir))
    with http.server.ThreadingHTTPServer((host, port), handler) as httpd:
        print("Serving %s at http://%s:%d (Ctrl+C to stop)" % (site_dir, host, port))
        httpd.serve_forever()
