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
    ad_leads: list[tuple[str, str, int]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "daily": self.daily,
                "ads": [asdict(a) for a in self.ads], "ad_leads": [list(r) for r in self.ad_leads]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> SourceData:
        return cls(as_of=dt.datetime.fromisoformat(d["as_of"]), daily=d.get("daily", {}),
                   ads=[AdRow(**a) for a in d.get("ads", [])],
                   ad_leads=[(r[0], str(r[1]), int(r[2])) for r in d.get("ad_leads", [])])

    def add(self, metric: str, day: str, value: float) -> None:
        series = self.daily.setdefault(metric, {})
        series[day] = series.get(day, 0.0) + value


@runtime_checkable
class Source(Protocol):
    name: str

    def fetch(self, since: dt.date, until: dt.date) -> SourceData: ...


@runtime_checkable
class AdSource(Source, Protocol):
    def reread_ad(self, ad_id: str, since: dt.date, until: dt.date) -> tuple[float, int]:
        """Spend and impressions for one ad, read again straight from the provider."""
        ...

    def pause_ad(self, ad_id: str) -> dict[str, str]:
        """Set one ad to paused and read it back. Returns {"before": status, "after": status}."""
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
