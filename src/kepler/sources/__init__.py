"""Build the configured sources. Each [[sources]] entry in kepler.toml names a `type`:

  meta      Meta Marketing API ad insights (real connector; sources/meta.py)
  stripe    Stripe charges (real connector; sources/stripe.py)
  csv       a CSV export from any other system (sources/csv_file.py)
  demo      synthetic data (sources/demo.py), used by `kepler demo`

Common options: `name`, `max_age_minutes` (older data is labelled stale) and `rename`
(publish a metric under another name, e.g. rename = { meta_leads = "optins" }).
To add a system, write a class with `name` and `fetch(since, until) -> SourceData` and register it here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AdRow, AdSource, Source, SourceData, apply_rename
from .csv_file import CsvSource
from .demo import DemoSource, DemoWorld

__all__ = ["AdRow", "AdSource", "Source", "SourceData", "apply_rename", "build_one", "DemoWorld"]


def build_one(spec: dict[str, Any], root: Path, demo_world: DemoWorld | None = None) -> Any:
    kind = spec.get("type")
    name = spec["name"]
    if kind == "meta":
        from .meta import MetaSource
        return MetaSource(name=name, api_version=spec.get("api_version", "v23.0"),
                          lead_action_types=spec.get("lead_action_types"),
                          campaign_ids=[str(c) for c in spec.get("campaign_ids", [])])
    if kind == "stripe":
        from .stripe import StripeSource
        return StripeSource(name=name, currency=spec.get("currency", "usd"),
                            min_sale_amount=float(spec.get("min_sale_amount", 0)))
    if kind == "csv":
        return CsvSource(name, root / spec["path"], spec.get("kind", "daily"))
    if kind == "demo":
        if demo_world is None:
            raise RuntimeError("demo sources need a demo world; run `kepler demo`")
        return DemoSource(name, spec["kind"], demo_world, float(spec.get("lag_hours", 0)))
    raise ValueError("unknown source type %r for %r" % (kind, name))

