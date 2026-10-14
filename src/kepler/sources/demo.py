"""Synthetic data for `kepler demo`. Deterministic for a given date and scenario; no network, no keys.

Scenarios:
- "zero-bookings": leads come in but nobody books a call, so the booking step is the bottleneck and
  everything above it is worth $0 until it is fixed.
- "mixed": every step converts, some below benchmark, so the ranking spreads out.

The demo ad account also contains:
- two ads with the same name but different IDs (a duplicated campaign), to show why everything is
  keyed by ad ID;
- one brand-new ad whose bulk report is wrong ($74.49 on 11 impressions; the per-ad re-read says
  $3.25), to show the guard holding a cut.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from .base import AdRow, SourceData

SCENARIOS = ("zero-bookings", "mixed")
DAYS = 35


@dataclass
class _Ad:
    ad_id: str
    name: str
    status: str
    first: int  # days ago the ad started (inclusive)
    last: int  # days ago it stopped (0 = still running)
    spend: float  # per day
    cpm: float
    ctr: float
    cpl: float | None  # None = no leads


ADS = [
    _Ad("1101", "Free guide - hook A", "ACTIVE", 34, 0, 300.0, 22.0, 0.012, 14.0),
    _Ad("1102", "Free guide - hook B", "ACTIVE", 30, 0, 6.0, 25.0, 0.008, 90.0),
    _Ad("1103", "Free guide - hook C", "ACTIVE", 30, 0, 35.0, 24.0, 0.010, 35.0),
    _Ad("1104", "Checklist - carousel", "ACTIVE", 10, 0, 7.0, 26.0, 0.007, None),
    _Ad("1105", "Webinar - video v2", "PAUSED", 30, 12, 3.75, 24.0, 0.009, None),
    _Ad("1106", "Webinar - video v2", "ACTIVE", 1, 0, 3.25, 295.0, 0.0, None),
    _Ad("1107", "Quiz - static", "ACTIVE", 1, 0, 3.25, 295.0, 0.0, None),
]
BAD_REPORT_AD = "1107"
BAD_REPORT = (74.49, 11)


@dataclass
class DemoWorld:
    today: dt.date
    scenario: str = "zero-bookings"
    now: dt.datetime | None = None  # the demo's clock; defaults to the real time
    daily: dict[str, dict[str, float]] = field(default_factory=dict)
    ads: list[AdRow] = field(default_factory=list)
    truth: dict[str, tuple[float, int]] = field(default_factory=dict)
    paused: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.scenario not in SCENARIOS:
            raise ValueError("scenario must be one of %s" % ", ".join(SCENARIOS))
        for ago in range(DAYS, -1, -1):
            day = self.today - dt.timedelta(days=ago)
            rng = random.Random("%s|%s" % (self.scenario, day.isoformat()))
            part = 0.5 if ago == 0 else 1.0  # today is half over
            self._day(day.isoformat(), ago, rng, part)
        bad = [r for r in self.ads if r.ad_id == BAD_REPORT_AD]
        self.truth[BAD_REPORT_AD] = (round(sum(r.spend for r in bad), 2), sum(r.impressions for r in bad))
        bad[-1].spend, bad[-1].impressions = BAD_REPORT  # the wrong bulk report the snapshot sees

    def _put(self, metric: str, day: str, value: float) -> None:
        self.daily.setdefault(metric, {})[day] = self.daily.get(metric, {}).get(day, 0.0) + value

    def _day(self, day: str, ago: int, rng: random.Random, part: float) -> None:
        ad_leads = 0
        for a in ADS:
            if not (a.last <= ago <= a.first) or (a.first <= 1 and ago != 1):
                continue
            spend = round(a.spend * part * (rng.uniform(0.85, 1.15) if a.spend > 5 else 1.0), 2)
            imps = int(spend * 1000 / a.cpm)
            clicks = int(imps * a.ctr * rng.uniform(0.85, 1.15))
            leads = 0
            if a.cpl:
                expected = spend / a.cpl
                leads = int(expected) + (1 if rng.random() < expected - int(expected) else 0)
            if a.ad_id == "1102":
                leads = 1 if ago in (25, 9) else 0
            ad_leads += leads
            self.ads.append(AdRow(day, a.ad_id, a.name, "c-200" if a.ad_id < "1105" else "c-300", a.status,
                                  spend, imps, clicks, leads))
            self._put("spend", day, spend)
            self._put("impressions", day, imps)
            self._put("clicks", day, clicks)
        optins = ad_leads + rng.randint(2, 6) * part
        self._put("optins", day, optins)
        if self.scenario == "zero-bookings":
            booked = showed = sales = 0
        else:
            booked = sum(rng.random() < 0.05 for _ in range(int(optins)))
            showed = sum(rng.random() < 0.6 for _ in range(booked))
            sales = sum(rng.random() < 0.32 for _ in range(showed))
        self._put("booked", day, booked)
        self._put("showed", day, showed)
        self._put("sales", day, sales)
        self._put("cash", day, sales * 1500.0)
        sent = int(420 * part)
        self._put("email_sent", day, sent)
        self._put("email_opens", day, int(sent * rng.uniform(0.34, 0.40)))
        self._put("email_clicks", day, int(sent * rng.uniform(0.03, 0.04)))
        self._put("search_clicks", day, int(rng.uniform(18, 26) * part))
        self._put("video_views", day, int(rng.uniform(550, 750) * part))


GROUPS = {
    "ads": ("spend", "impressions", "clicks"),
    "crm": ("optins", "booked", "showed"),
    "payments": ("sales", "cash"),
    "email": ("email_sent", "email_opens", "email_clicks"),
    "organic": ("search_clicks", "video_views"),
}


class DemoSource:
    """One slice of the demo world. `lag_hours` makes a source look stale on purpose."""

    def __init__(self, name: str, kind: str, world: DemoWorld, lag_hours: float = 0.0):
        if kind not in GROUPS:
            raise ValueError("demo kind must be one of %s" % ", ".join(GROUPS))
        self.name, self.kind, self.world, self.lag_hours = name, kind, world, lag_hours

    def fetch(self, since: dt.date, until: dt.date) -> SourceData:
        now = self.world.now or dt.datetime.now().astimezone()
        as_of = now - dt.timedelta(hours=self.lag_hours)
        lo, hi = since.isoformat(), until.isoformat()
        cutoff = as_of.date().isoformat()
        data = SourceData(as_of=as_of)
        for m in GROUPS[self.kind]:
            data.daily[m] = {d: v for d, v in self.world.daily.get(m, {}).items() if lo <= d <= hi and d <= cutoff}
        if self.kind == "ads":
            data.ads = [r for r in self.world.ads if lo <= r.date <= hi]
        return data

    def reread_ad(self, ad_id: str, since: dt.date, until: dt.date) -> tuple[float, int]:
        if self.kind != "ads":
            raise RuntimeError("only the demo ad source can re-read ads")
        if ad_id in self.world.truth:
            return self.world.truth[ad_id]
        rows = [r for r in self.world.ads if r.ad_id == ad_id and since.isoformat() <= r.date <= until.isoformat()]
        return round(sum(r.spend for r in rows), 2), sum(r.impressions for r in rows)

    def pause_ad(self, ad_id: str) -> dict[str, str]:
        self.world.paused.append(ad_id)
        return {"before": "ACTIVE", "after": "PAUSED"}
