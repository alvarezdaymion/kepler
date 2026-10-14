"""Load kepler.toml and fill in defaults. Every number Kepler judges against lives here."""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LABELS = {
    "impressions": "Impressions",
    "clicks": "Link clicks",
    "spend": "Ad spend",
    "optins": "Opt-ins",
    "booked": "Booked calls",
    "showed": "Calls held",
    "sales": "Sales",
    "cash": "Cash collected",
    "refunds": "Refunds",
    "email_sent": "Emails sent",
    "email_opens": "Email opens",
    "email_clicks": "Email clicks",
    "search_clicks": "Search clicks",
    "search_impressions": "Search impressions",
    "video_views": "Video views",
    "dials": "Dials",
    "conversations": "Conversations",
    "referrals": "Referrals",
}

DEFAULT_FUNNEL = [
    {"from": "impressions", "to": "clicks", "label": "Ad click-through", "benchmark": 0.015,
     "min_volume": 2000, "action": "Test new hooks and creatives."},
    {"from": "clicks", "to": "optins", "label": "Click to opt-in", "benchmark": 0.30,
     "min_volume": 100, "action": "Tighten the landing page: one promise, one field, fast load."},
    {"from": "optins", "to": "booked", "label": "Opt-in to booked call", "benchmark": 0.12,
     "min_volume": 50, "action": "Put the booking link in front of every new lead within minutes."},
    {"from": "booked", "to": "showed", "label": "Booked to held call", "benchmark": 0.65,
     "min_volume": 20, "action": "Add reminders by text and email; confirm the day before."},
    {"from": "showed", "to": "sales", "label": "Held call to sale", "benchmark": 0.25,
     "min_volume": 20, "action": "Review call recordings against your offer and objections."},
]

# 15 stages in three groups. A stage without a metric shows as "Not tracked", never 0.
DEFAULT_JOURNEY = [
    {"stage": "Reach", "group": "attraction", "metric": "impressions"},
    {"stage": "Click", "group": "attraction", "metric": "clicks"},
    {"stage": "Opt in", "group": "attraction", "metric": "optins"},
    {"stage": "Nurture", "group": "attraction", "metric": ""},
    {"stage": "Book", "group": "attraction", "metric": "booked"},
    {"stage": "Show", "group": "conversion", "metric": "showed"},
    {"stage": "Decide", "group": "conversion", "metric": ""},
    {"stage": "Buy", "group": "conversion", "metric": "sales"},
    {"stage": "Pay", "group": "conversion", "metric": ""},
    {"stage": "Onboard", "group": "conversion", "metric": ""},
    {"stage": "Deliver", "group": "fulfillment", "metric": ""},
    {"stage": "First result", "group": "fulfillment", "metric": ""},
    {"stage": "Stay", "group": "fulfillment", "metric": ""},
    {"stage": "Renew", "group": "fulfillment", "metric": ""},
    {"stage": "Refer", "group": "fulfillment", "metric": "referrals"},
]

DEFAULT_LANES = [
    {"key": "paid", "name": "Paid ads", "kpis": [
        {"label": "Opt-ins per day", "value": "optins", "per_day": True, "target": 20, "better": "higher"},
        {"label": "Cost per lead", "value": "spend / optins", "target": 25, "better": "lower", "format": "money"},
        {"label": "Click-through", "value": "clicks / impressions", "target": 0.015, "better": "higher",
         "format": "percent"},
    ]},
    {"key": "email", "name": "Email", "kpis": [
        {"label": "Click rate per send", "value": "email_clicks / email_sent", "target": 0.05,
         "better": "higher", "format": "percent"},
        {"label": "Open rate per send", "value": "email_opens / email_sent", "target": 0.40,
         "better": "higher", "format": "percent"},
    ]},
    {"key": "organic", "name": "Organic and search", "kpis": [
        {"label": "Search clicks per day", "value": "search_clicks", "per_day": True, "target": 30,
         "better": "higher"},
        {"label": "Video views per day", "value": "video_views", "per_day": True, "target": 500,
         "better": "higher"},
    ]},
    {"key": "phone", "name": "Phone sales", "kpis": [
        {"label": "Show rate", "value": "showed / booked", "target": 0.65, "better": "higher",
         "format": "percent"},
        {"label": "Close rate", "value": "sales / showed", "target": 0.25, "better": "higher",
         "format": "percent"},
    ]},
    {"key": "partners", "name": "Partners", "kpis": [
        {"label": "Referrals, last 7 days", "value": "referrals", "target": 3, "better": "higher"},
    ]},
]

DEFAULTS: dict[str, Any] = {
    "business": {"name": "Your business", "currency": "USD"},
    "goal": {"sales_per_day": 1.0, "revenue_per_sale": 1500.0},
    "windows": {"pace_days": 7, "rate_days": 30, "lane_days": 7},
    "funnel": {"steps": DEFAULT_FUNNEL},
    "labels": {},
    "journey": DEFAULT_JOURNEY,
    "lanes": DEFAULT_LANES,
    "lane_bands": {"yellow": 0.2},
    "ads": {
        "target_cpl": 25.0,
        "floor_multiple": 2.0,
        "cut_multiple": 2.0,
        "warning_max_hours": 72.0,
        "judge_days": 30,
        "leads_from": "ads",
    },
    "auto_cut": {
        "mode": "off",
        "source": "meta",
        "max_pauses_per_run": 3,
        "max_snapshot_age_minutes": 20,
        "spend_tolerance": 1.0,
        "max_cpm": 500.0,
    },
    "analysis": {
        "enabled": False,
        "every_minutes": 1440,
        "model": "claude-opus-5-5",
        "effort": "low",
        "fallbacks": True,
        "max_tokens": 4000,
    },
    "controller": {"every_minutes": 15},
    "paths": {"state": "state", "site": "site"},
    "sources": [],
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class Config:
    data: dict[str, Any]
    root: Path

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def state_dir(self) -> Path:
        return (self.root / self.data["paths"]["state"]).resolve()

    @property
    def site_dir(self) -> Path:
        return (self.root / self.data["paths"]["site"]).resolve()

    def label(self, metric: str) -> str:
        return self.data["labels"].get(metric) or DEFAULT_LABELS.get(metric) or metric.replace("_", " ")


def validate(data: dict[str, Any]) -> None:
    steps = data["funnel"]["steps"]
    if not steps:
        raise ValueError("funnel.steps is empty")
    for prev, step in zip(steps, steps[1:], strict=False):
        if prev["to"] != step["from"]:
            raise ValueError("funnel steps must chain: %r ends at %r but the next starts at %r"
                             % (prev["label"], prev["to"], step["from"]))
    for step in steps:
        if not 0 < float(step["benchmark"]) <= 1:
            raise ValueError("benchmark for %r must be a rate between 0 and 1" % step["label"])
    if data["auto_cut"]["mode"] not in ("off", "dry-run", "live"):
        raise ValueError("auto_cut.mode must be off, dry-run or live")
    if float(data["ads"]["target_cpl"]) <= 0:
        raise ValueError("ads.target_cpl must be above 0")
    names = [s["name"] for s in data["sources"]]
    if len(names) != len(set(names)):
        raise ValueError("source names must be unique")


def from_dict(data: dict[str, Any], root: Path | str = ".") -> Config:
    merged = _merge(DEFAULTS, data)
    validate(merged)
    return Config(merged, Path(root).resolve())


def load(path: Path | str) -> Config:
    p = Path(path)
    with p.open("rb") as f:
        data = tomllib.load(f)
    return from_dict(data, p.parent)
