import datetime as dt
import json

import pytest

from kepler.sources.stripe import StripeSource


def ts(day, hour=12):
    return int(dt.datetime.combine(day, dt.time(hour)).astimezone().timestamp())


def test_counts_sales_cash_and_refunds_with_get_only():
    d = dt.date(2026, 1, 10)
    charges = [
        {"id": "ch_1", "created": ts(d), "status": "succeeded", "paid": True, "captured": True,
         "currency": "usd", "amount": 150000, "amount_refunded": 0, "refunded": False},
        {"id": "ch_2", "created": ts(d), "status": "succeeded", "paid": True, "captured": True,
         "currency": "usd", "amount": 150000, "amount_refunded": 150000, "refunded": True},
        {"id": "ch_3", "created": ts(d), "status": "failed", "paid": False, "captured": False,
         "currency": "usd", "amount": 150000, "amount_refunded": 0, "refunded": False},
        {"id": "ch_4", "created": ts(d), "status": "succeeded", "paid": True, "captured": True,
         "currency": "usd", "amount": 2000, "amount_refunded": 0, "refunded": False},
    ]
    pages = [{"data": charges[:2], "has_more": True}, {"data": charges[2:], "has_more": False}]
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url))
        return 200, json.dumps(pages[len(calls) - 1]).encode()

    src = StripeSource(api_key="rk_test_x", min_sale_amount=100, transport=transport)
    data = src.fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    day = d.isoformat()
    assert data.daily["sales"][day] == 1  # refunded and under-minimum charges are not sales
    assert data.daily["cash"][day] == 1520.0
    assert data.daily["refunds"][day] == 1500.0
    assert all(m == "GET" for m, _ in calls)
    assert "starting_after=ch_2" in calls[1][1]


def test_no_charges_is_zero_not_unknown():
    def transport(method, url, headers, body, timeout):
        return 200, b'{"data": [], "has_more": false}'

    data = StripeSource(api_key="rk_test_x", transport=transport).fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 2))
    assert data.daily["sales"] == {}  # read and empty: the metric is tracked, its total is 0


@pytest.mark.parametrize("body", [{}, {"data": [], "has_more": "false"},
                                       {"data": {}, "has_more": False}, {"data": [None], "has_more": False}])
def test_invalid_page_schema_fails(body):
    src = StripeSource(api_key="synthetic-key", transport=lambda *args: (200, json.dumps(body).encode()))
    with pytest.raises(RuntimeError):
        src.fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))


def test_remaining_page_at_limit_is_incomplete(monkeypatch):
    from kepler.sources import stripe

    monkeypatch.setattr(stripe, "MAX_PAGES", 1)
    body = {"data": [{"id": "ch_1", "status": "failed"}], "has_more": True}
    src = StripeSource(api_key="synthetic-key", transport=lambda *args: (200, json.dumps(body).encode()))
    with pytest.raises(RuntimeError, match="incomplete"):
        src.fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))


def test_malformed_response_preserves_cache(tmp_path):
    from kepler import config, controller

    now = dt.datetime(2026, 1, 31, 12).astimezone()
    bodies = iter([json.dumps({"data": [{
        "id": "ch_1", "status": "succeeded", "paid": True, "captured": True,
        "created": ts(dt.date(2026, 1, 30)), "currency": "usd", "amount": 9000,
        "amount_refunded": 0, "refunded": False,
    }], "has_more": False}).encode(), b"invalid json"])
    class FixedClockStripe(StripeSource):
        def fetch(self, since, until):
            data = super().fetch(since, until)
            data.as_of = now
            return data

    src = FixedClockStripe(api_key="synthetic-key", transport=lambda *args: (200, next(bodies)))
    cfg = config.from_dict({"sources": [{"name": "pay", "type": "stripe"}]}, tmp_path)
    _, health, _ = controller.read_sources(cfg, now, {"pay": src})
    assert health[0]["status"] == "ok"
    store, health, _ = controller.read_sources(cfg, now + dt.timedelta(minutes=15), {"pay": src})
    assert health[0]["status"] == "stale"
    assert store.total("cash", 7) == 90


@pytest.mark.parametrize("refund,flag", [
    (-1, False), (0.5, False), (True, False), (None, False), ("0", False), (4001, False),
    (0, "false"), (0, 0), (0, None), (4000, False), (500, True), (0, True),
])
def test_invalid_refund_values_fail_the_read(refund, flag):
    day = dt.date(2026, 1, 30)
    charge = {"id": "ch_1", "status": "succeeded", "paid": True, "captured": True,
              "created": ts(day), "currency": "usd", "amount": 4000, "amount_refunded": refund, "refunded": flag}
    payload = json.dumps({"data": [charge], "has_more": False}).encode()
    source = StripeSource(api_key="synthetic-key", transport=lambda *args: (200, payload))
    with pytest.raises(RuntimeError, match="refund"):
        source.fetch(day, day)


@pytest.mark.parametrize("missing", ["amount_refunded", "refunded"])
def test_missing_refund_fields_fail_the_read(missing):
    day = dt.date(2026, 1, 30)
    charge = {"id": "ch_1", "status": "succeeded", "paid": True, "captured": True,
              "created": ts(day), "currency": "usd", "amount": 4000, "amount_refunded": 0, "refunded": False}
    charge.pop(missing)
    payload = json.dumps({"data": [charge], "has_more": False}).encode()
    source = StripeSource(api_key="synthetic-key", transport=lambda *args: (200, payload))
    with pytest.raises(RuntimeError, match="refund"):
        source.fetch(day, day)


@pytest.mark.parametrize("refund,flag,cash,sales", [(0, False, 40, 1), (1200, False, 28, 1), (4000, True, 0, 0)])
def test_valid_refunds_preserve_cash_and_sale_counts(refund, flag, cash, sales):
    day = dt.date(2026, 1, 30)
    charge = {"id": "ch_1", "status": "succeeded", "paid": True, "captured": True,
              "created": ts(day), "currency": "usd", "amount": 4000, "amount_refunded": refund, "refunded": flag}
    payload = json.dumps({"data": [charge], "has_more": False}).encode()
    source = StripeSource(api_key="synthetic-key", transport=lambda *args: (200, payload))
    data = source.fetch(day, day)
    assert data.daily["cash"][day.isoformat()] == cash
    assert data.daily["refunds"][day.isoformat()] == refund / 100
    assert data.daily["sales"].get(day.isoformat(), 0) == sales
