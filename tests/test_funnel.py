import datetime as dt

import pytest

from kepler import config, funnel
from kepler.metrics import MetricStore
from kepler.sources.base import SourceData


def test_required_per_day_works_back_from_goal():
    steps = config.DEFAULT_FUNNEL
    need = funnel.required_per_day(steps, 1.0, [s["benchmark"] for s in steps])
    # sales 1 -> held 4 -> booked 6.15 -> opt-ins 51.3 -> clicks 171 -> impressions 11,396
    assert need[-1] == 1
    assert need[-2] == pytest.approx(4.0)
    assert need[-3] == pytest.approx(4 / 0.65)
    assert need[2] == pytest.approx(4 / 0.65 / 0.12)
    assert need[0] == pytest.approx(4 / 0.65 / 0.12 / 0.30 / 0.015)


def test_zero_rate_makes_upstream_unreachable():
    steps = config.DEFAULT_FUNNEL
    need = funnel.required_per_day(steps, 1.0, [0.01, 0.3, 0.0, 0.6, 0.25])
    assert need[3] is not None and need[2] is None and need[0] is None


def test_untracked_metric_is_none_not_zero(tmp_path):
    cfg = config.from_dict({}, tmp_path)
    today = dt.date(2026, 1, 31)
    store = MetricStore(today)
    data = SourceData(as_of=dt.datetime(2026, 1, 31, tzinfo=dt.UTC))
    for i in range(1, 31):
        data.add("optins", (today - dt.timedelta(days=i)).isoformat(), 10)
    store.add_source("crm", data, stale=False)
    f = funnel.build(store, cfg)
    rows = {r["metric"]: r for r in f["rows"]}
    assert rows["optins"]["actual_per_day"] == 10
    assert rows["booked"]["actual_per_day"] is None
    assert rows["booked"]["tracked"] is False
    assert f["actual_per_day"] is None and f["progress"] is None
