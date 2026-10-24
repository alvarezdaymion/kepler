import datetime as dt
import os

import pytest

from kepler import config, lanes
from kepler.metrics import MetricStore
from kepler.sources.base import SourceData
from kepler.sources.csv_file import CsvSource


def test_grades():
    assert lanes.grade(30, 25, "lower", 0.2) == "yellow"
    assert lanes.grade(20, 25, "lower", 0.2) == "green"
    assert lanes.grade(40, 25, "lower", 0.2) == "red"
    assert lanes.grade(0.045, 0.05, "higher", 0.2) == "yellow"
    assert lanes.grade(0.03, 0.05, "higher", 0.2) == "red"


def test_parse_rejects_bad_expressions():
    with pytest.raises(ValueError):
        lanes.parse("a / b / c")


def test_csv_daily_and_lanes(tmp_path):
    today = dt.date(2026, 1, 31)
    lines = ["date,spend,optins,booked"]
    for i in range(1, 8):
        lines.append("%s,100,4," % (today - dt.timedelta(days=i)).isoformat())
    (tmp_path / "crm.csv").write_text("\n".join(lines) + "\n")
    data = CsvSource("crm", tmp_path / "crm.csv").fetch(today - dt.timedelta(days=30), today)
    store = MetricStore(today)
    store.add_source("crm", data, stale=False)
    assert store.total("booked", 7) == 0  # column present, cells empty: tracked, nothing counted
    cfg = config.from_dict({}, tmp_path)
    paid = next(la for la in lanes.build(store, cfg) if la["key"] == "paid")
    cpl = next(k for k in paid["kpis"] if k["label"] == "Cost per lead")
    assert cpl["value"] == 25 and cpl["grade"] == "green"
    clicks = next(k for k in paid["kpis"] if k["label"] == "Click-through")
    assert clicks["grade"] == "not_tracked" and clicks["value"] is None
    partners = next(la for la in lanes.build(store, cfg) if la["key"] == "partners")
    assert partners["grade"] == "not_tracked"


def test_csv_freshness_is_the_file_time(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("date,optins\n2026-01-30,3\n")
    old = dt.datetime(2026, 1, 1, 12).timestamp()
    os.utime(p, (old, old))
    data = CsvSource("x", p).fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert data.as_of.date() == dt.date(2026, 1, 1)


def test_csv_ad_leads(tmp_path):
    p = tmp_path / "leads.csv"
    p.write_text("date,ad_id,leads\n2026-01-30,1203,4\n")
    data = CsvSource("x", p, kind="ad_leads").fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert data.ad_leads == [("2026-01-30", "1203", 4)]


def test_empty_crm_export_declares_lead_availability(tmp_path):
    p = tmp_path / "leads.csv"
    p.write_text("date,ad_id,leads\n")
    data = CsvSource("crm", p, kind="ad_leads").fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    store = MetricStore(dt.date(2026, 1, 31))
    store.add_source("crm", data, False)
    assert store.has_ad_leads
    assert SourceData.from_json(data.to_json()).ad_leads == []


@pytest.mark.parametrize("content", ["date\n", "date,ad_id,leads\n2026-01-30,1,\n",
                                         "date,ad_id,leads\n2026-01-30,1,-1\n"])
def test_crm_export_rejects_missing_or_invalid_counts(tmp_path, content):
    p = tmp_path / "leads.csv"
    p.write_text(content)
    with pytest.raises(ValueError):
        CsvSource("crm", p, kind="ad_leads").fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))


@pytest.mark.parametrize("kind", ["daily", "ad_leads"])
@pytest.mark.parametrize("day", ["20260130", "2026-W05-5"])
def test_csv_rejects_noncanonical_dates_before_window_filtering(tmp_path, kind, day):
    path = tmp_path / "export.csv"
    header, values = ("date,optins", "9") if kind == "daily" else ("date,ad_id,leads", "1,9")
    path.write_text("%s\n%s,%s\n" % (header, day, values))
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        CsvSource("crm", path, kind).fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))


@pytest.mark.parametrize("kind,content", [
    ("daily", "date,optins,optins\n2026-01-30,9,0\n"),
    ("daily", "date,optins, optins\n2026-01-30,9,0\n"),
    ("daily", "date,optins,\n2026-01-30,9,0\n"),
    ("daily", "date,optins\n2026-01-30,0,9\n"),
    ("daily", "date,optins\n2026-01-30\n"),
    ("ad_leads", "date,ad_id,leads,leads\n2026-01-30,1,9,0\n"),
    ("ad_leads", "date,ad_id,leads\n2026-01-30,1,0,9\n"),
    ("ad_leads", "date,ad_id,leads\n2025-12-01,1,0,9\n"),
])
def test_csv_rejects_ambiguous_headers_and_row_widths(tmp_path, kind, content):
    path = tmp_path / "export.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        CsvSource("crm", path, kind).fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
