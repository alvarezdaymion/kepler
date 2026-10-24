"""The interface every data source implements.

A source turns one system (an ad account, a payment processor, a CSV export) into:

- `daily`: {metric: {"YYYY-MM-DD": number}} counts per day,
- `ads`: one row per ad per day, keyed by the ad's ID (never its name),
- `ad_leads`: optional lead counts per ad per day from a CRM, also keyed by ad ID.

Sources only read. The one exception is `pause_ad` on an ad source, which the auto-cut calls
behind its guard and only when `auto_cut.mode = "live"`.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class AdRow:
    date: str
    ad_id: str
    ad_name: str = ""
    campaign_id: str = ""
    status: str = ""
    spend: float = 0.0
    impressions: int = 0
    clicks: int = 0
    leads: int = 0


@dataclass
class SourceData:
    as_of: dt.datetime
    daily: dict[str, dict[str, float]] = field(default_factory=dict)
    ads: list[AdRow] = field(default_factory=list)
    ad_leads: list[tuple[str, str, int]] | None = None

    def to_json(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "daily": self.daily,
                "ads": [asdict(a) for a in self.ads],
                "ad_leads": None if self.ad_leads is None else [list(r) for r in self.ad_leads]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> SourceData:
        data = cls(as_of=dt.datetime.fromisoformat(d["as_of"]), daily=d.get("daily", {}),
                   ads=[AdRow(**a) for a in d.get("ads", [])], ad_leads=d.get("ad_leads"))
        data.validate()
        if data.ad_leads is not None:
            data.ad_leads = [tuple(r) for r in data.ad_leads]
        return data

    def validate(self) -> None:
        if not isinstance(self.as_of, dt.datetime) or self.as_of.utcoffset() is None:
            raise ValueError("source as_of must include a timezone")
        if not isinstance(self.daily, dict) or not isinstance(self.ads, list):
            raise ValueError("invalid source series or ad rows")
        for metric, series in self.daily.items():
            if not isinstance(metric, str) or not isinstance(series, dict):
                raise ValueError("invalid metric series")
            for day, value in series.items():
                dt.date.fromisoformat(day)
                _number(value)
        for row in self.ads:
            if not isinstance(row, AdRow) or not isinstance(row.ad_id, str) or not row.ad_id:
                raise ValueError("invalid ad row")
            if not all(isinstance(value, str) for value in (row.ad_name, row.campaign_id, row.status)):
                raise ValueError("invalid ad metadata")
            dt.date.fromisoformat(row.date)
            _number(row.spend, nonnegative=True)
            for value in (row.impressions, row.clicks, row.leads):
                _number(value, nonnegative=True, whole=True)
        if self.ad_leads is not None:
            if not isinstance(self.ad_leads, list):
                raise ValueError("invalid ad lead rows")
            for day, ad_id, count in self.ad_leads:
                dt.date.fromisoformat(day)
                if not isinstance(ad_id, str) or not ad_id:
                    raise ValueError("ad leads require an ad ID")
                _number(count, nonnegative=True, whole=True)

    def add(self, metric: str, day: str, value: float) -> None:
        series = self.daily.setdefault(metric, {})
        series[day] = series.get(day, 0.0) + value


def _number(value: Any, nonnegative: bool = False, whole: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("source values must be finite numbers")
    if (nonnegative and value < 0) or (whole and int(value) != value):
        raise ValueError("source counts must be nonnegative whole numbers")


@runtime_checkable
class Source(Protocol):
    name: str

    def fetch(self, since: dt.date, until: dt.date) -> SourceData: ...


@runtime_checkable
class AdSource(Source, Protocol):
    def preflight_ad(self, ad_id: str) -> dict[str, str]:
        """Read current status and check campaign eligibility without writing."""
        ...

    def reread_ad(self, ad_id: str, since: dt.date, until: dt.date) -> tuple[float, int]:
        """Spend and impressions for one ad, read again straight from the provider."""
        ...

    def pause_ad(self, ad_id: str, on_ack: Callable[[], None] | None = None,
                 before_send: Callable[[], None] | None = None) -> dict[str, str]:
        """Check before_send before the write, report acknowledgment, then verify paused status."""
        ...


def apply_rename(data: SourceData, rename: dict[str, str]) -> SourceData:
    """Publish a source's metric under another name, e.g. Meta's lead count as `optins`."""
    if not rename:
        return data
    daily: dict[str, dict[str, float]] = {}
    for metric, series in data.daily.items():
        daily[rename.get(metric, metric)] = series
    data.daily = daily
    return data
