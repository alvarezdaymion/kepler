import datetime as dt
import json

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
