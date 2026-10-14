import datetime as dt

from kepler import ads, config
from kepler.metrics import MetricStore
from kepler.sources.base import AdRow

T = 25.0


def test_rules_at_the_boundaries():
    assert ads.decide(49.99, 0, T)[0] == "testing"  # under 2T: never cut
    assert ads.decide(10, 3, T)[0] == "testing"
    assert ads.decide(50, 0, T)[0] == "cut"  # at 2T with 0 leads
    assert ads.decide(100, 4, T)[0] == "keep"  # CPL 25 = T
    assert ads.decide(100, 3, T)[0] == "warning"  # CPL 33.33
    assert ads.decide(100, 2, T)[0] == "warning"  # CPL 50 = 2T
    assert ads.decide(101, 2, T)[0] == "cut"  # CPL 50.50 > 2T


def test_duplicate_names_are_kept_apart_by_id():
    # A duplicated campaign gives a new ad the same name as an old one.
    rows = [AdRow("2026-01-%02d" % d, "111", "Offer video", "c1", "PAUSED", 3.75, 150, 2, 0) for d in range(1, 20)]
    rows.append(AdRow("2026-01-20", "222", "Offer video", "c2", "ACTIVE", 3.25, 11, 0, 0))
    by_id = ads.aggregate(rows, "2026-01-01")
    assert by_id["222"]["spend"] == 3.25
    assert ads.decide(by_id["222"]["spend"], 0, T)[0] == "testing"
    # Summing by name would have handed the new ad $74.50 and cut it.
    by_name = sum(r.spend for r in rows if r.ad_name == "Offer video")
    assert round(by_name, 2) == 74.50
    assert ads.decide(by_name, 0, T)[0] == "cut"


def test_crm_leads_replace_platform_leads():
    rows = [AdRow("2026-01-05", "1", "A", "", "ACTIVE", 100, 5000, 50, 1)]
    out = ads.aggregate(rows, "2026-01-01", [("2026-01-05", "1", 5), ("2026-01-05", "9", 3)])
    assert out["1"]["leads"] == 5 and "9" not in out


def test_warning_turns_into_cut_after_the_limit(tmp_path):
    cfg = config.from_dict({"ads": {"warning_max_hours": 72}}, tmp_path)
    store = MetricStore(dt.date(2026, 1, 31))
    store.ads = [AdRow("2026-01-20", "1", "A", "", "ACTIVE", 120, 6000, 60, 3)]  # CPL 40: warning
    state: dict = {}
    t0 = dt.datetime(2026, 1, 31, 9, tzinfo=dt.UTC)
    assert ads.build(store, cfg, state, t0)["rows"][0]["verdict"] == "warning"
    assert ads.build(store, cfg, state, t0 + dt.timedelta(hours=71))["rows"][0]["verdict"] == "warning"
    row = ads.build(store, cfg, state, t0 + dt.timedelta(hours=73))["rows"][0]
    assert row["verdict"] == "cut" and "in warning for 73 h" in row["reason"]


def test_leaving_warning_resets_the_clock(tmp_path):
    cfg = config.from_dict({}, tmp_path)
    store = MetricStore(dt.date(2026, 1, 31))
    t0 = dt.datetime(2026, 1, 31, 9, tzinfo=dt.UTC)
    state: dict = {}
    store.ads = [AdRow("2026-01-20", "1", "A", "", "ACTIVE", 120, 6000, 60, 3)]
    ads.build(store, cfg, state, t0)
    store.ads = [AdRow("2026-01-20", "1", "A", "", "ACTIVE", 120, 6000, 60, 6)]  # CPL 20: keep
    ads.build(store, cfg, state, t0 + dt.timedelta(hours=1))
    assert "1" not in state["warning_since"]
