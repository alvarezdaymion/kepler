"""CSV import: for any system without a connector yet (CRM, email, Search Console, YouTube...).

Two shapes, chosen with `kind` in kepler.toml:

kind = "daily" (default): a `date` column (YYYY-MM-DD) and one column per metric.
    date,optins,booked,showed
    2026-01-05,24,3,2

kind = "ad_leads": leads per ad from your CRM, keyed by the ad's ID.
    date,ad_id,leads
    2026-01-05,1203,4

The data is as fresh as the file: `as_of` is the file's modification time, so a forgotten export
shows up as stale instead of silently looking current. Empty cells are skipped, not read as 0.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from .base import SourceData


class CsvSource:
    def __init__(self, name: str, path: Path | str, kind: str = "daily"):
        if kind not in ("daily", "ad_leads"):
            raise ValueError("csv kind must be daily or ad_leads")
        self.name = name
        self.path = Path(path)
        self.kind = kind

    def fetch(self, since: dt.date, until: dt.date) -> SourceData:
        if not self.path.exists():
            raise FileNotFoundError("CSV not found: %s" % self.path.name)
        mtime = dt.datetime.fromtimestamp(self.path.stat().st_mtime).astimezone()
        data = SourceData(as_of=mtime, ad_leads=[] if self.kind == "ad_leads" else None)
        lo, hi = since.isoformat(), until.isoformat()
        with self.path.open(newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "date" not in reader.fieldnames:
                raise ValueError("%s needs a 'date' column" % self.path.name)
            if self.kind == "ad_leads" and not {"ad_id", "leads"} <= set(reader.fieldnames):
                raise ValueError("ad leads CSV needs date, ad_id and leads columns")
            metrics = [c for c in reader.fieldnames if c != "date"]
            for m in metrics if self.kind == "daily" else []:
                data.daily.setdefault(m.strip(), {})
            for row in reader:
                day = (row.get("date") or "").strip()
                dt.date.fromisoformat(day)  # rejects malformed dates loudly
                if not lo <= day <= hi:
                    continue
                if self.kind == "ad_leads":
                    count = float(row["leads"])
                    if count < 0 or not count.is_integer():
                        raise ValueError("ad leads must be nonnegative whole counts")
                    data.ad_leads.append((day, row["ad_id"].strip(), int(count)))
                    continue
                for m in metrics:
                    cell = (row.get(m) or "").strip()
                    if cell:
                        data.add(m.strip(), day, float(cell))
        data.validate()
        return data
