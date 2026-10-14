"""Merge every source's daily counts into one store and answer window questions.

A metric no source provides is unknown and comes back as None, never 0. A metric whose source is
stale still returns its last good numbers, flagged stale.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .sources.base import AdRow, SourceData


@dataclass
class MetricStore:
    today: dt.date
    series: dict[str, dict[str, float]] = field(default_factory=dict)
    provider: dict[str, str] = field(default_factory=dict)
    stale_sources: set[str] = field(default_factory=set)
    ads: list[AdRow] = field(default_factory=list)
    ad_leads: list[tuple[str, str, int]] = field(default_factory=list)
    has_ad_leads: bool = False
    conflicts: list[str] = field(default_factory=list)

    def add_source(self, name: str, data: SourceData, stale: bool) -> None:
        if stale:
            self.stale_sources.add(name)
        for metric, s in data.daily.items():
            if metric in self.provider:
                self.conflicts.append("%s is provided by %s and %s; using %s"
                                      % (metric, self.provider[metric], name, self.provider[metric]))
                continue
            self.provider[metric] = name
            self.series[metric] = dict(s)
        self.ads.extend(data.ads)
        if data.ad_leads:
            self.has_ad_leads = True
            self.ad_leads.extend(data.ad_leads)

    def tracked(self, metric: str) -> bool:
        return metric in self.series

    def stale(self, metric: str) -> bool:
        return self.provider.get(metric) in self.stale_sources

    def total(self, metric: str, days: int, include_today: bool = False) -> float | None:
        """Sum over the last `days` complete days (yesterday backwards), or None when untracked."""
        if metric not in self.series:
            return None
        end = self.today if include_today else self.today - dt.timedelta(days=1)
        start = end - dt.timedelta(days=days - 1)
        s = self.series[metric]
        return float(sum(v for d, v in s.items() if start.isoformat() <= d <= end.isoformat()))

    def per_day(self, metric: str, days: int) -> float | None:
        t = self.total(metric, days)
        return None if t is None else t / days


def ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den
