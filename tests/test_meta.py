import datetime as dt
import json
import urllib.parse

import pytest

from kepler.http import HttpError
from kepler.sources.meta import MetaSource

TOKEN = "test-token-not-real"


class FakeGraph:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, body))
        assert headers["Authorization"] == "Bearer " + TOKEN
        path = urllib.parse.urlsplit(url).path
        key = (method, path)
        if key not in self.routes:
            return 400, json.dumps({"error": {"message": "unexpected %s %s" % key}}).encode()
        resp = self.routes[key]
        if callable(resp):
            resp = resp(url, body)
        return 200, json.dumps(resp).encode()


def make(routes, **kw):
    fake = FakeGraph(routes)
    return MetaSource(token=TOKEN, account_id="42", transport=fake, **kw), fake


def insights_routes():
    page2 = {"data": [{"date_start": "2026-01-02", "ad_id": "11", "ad_name": "A", "campaign_id": "7",
                       "spend": "5.50", "impressions": "300", "inline_link_clicks": "4"}]}
    page1 = {"data": [{"date_start": "2026-01-01", "ad_id": "11", "ad_name": "A", "campaign_id": "7",
                       "spend": "10.00", "impressions": "500", "inline_link_clicks": "6",
                       "actions": [{"action_type": "lead", "value": "2"},
                                   {"action_type": "link_click", "value": "6"}]}],
             "paging": {"next": "https://graph.facebook.com/v23.0/act_42/insights?after=x"}}

    def insights(url, body):
        return page2 if "after=x" in url else page1

    return {
        ("GET", "/v23.0/act_42/insights"): insights,
        ("GET", "/v23.0/act_42/ads"): {"data": [{"id": "11", "name": "A", "status": "ACTIVE"}]},
    }


def test_fetch_reads_ad_rows_by_id_and_pages():
    src, fake = make(insights_routes())
    data = src.fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 2))
    assert [(r.date, r.ad_id, r.spend, r.leads, r.status) for r in data.ads] == [
        ("2026-01-01", "11", 10.0, 2, "ACTIVE"), ("2026-01-02", "11", 5.5, 0, "ACTIVE")]
    assert data.daily["spend"] == {"2026-01-01": 10.0, "2026-01-02": 5.5}
    assert data.daily["meta_leads"]["2026-01-01"] == 2
    assert all(m == "GET" for m, _, _ in fake.calls)


def test_reread_is_one_ad_without_breakdown():
    src, fake = make({("GET", "/v23.0/11/insights"): {"data": [{"spend": "3.25", "impressions": "11"}]}})
    assert src.reread_ad("11", dt.date(2026, 1, 1), dt.date(2026, 1, 2)) == (3.25, 11)
    assert "time_increment" not in fake.calls[0][1] and "breakdowns" not in fake.calls[0][1]


def test_pause_posts_exactly_status_paused_and_reads_back():
    states = iter([{"status": "ACTIVE", "campaign_id": "7"}, {"status": "PAUSED", "campaign_id": "7"}])
    src, fake = make({("GET", "/v23.0/11"): lambda u, b: next(states),
                      ("POST", "/v23.0/11"): {"success": True}}, campaign_ids=["7"])
    assert src.pause_ad("11") == {"before": "ACTIVE", "after": "PAUSED"}
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert len(posts) == 1 and posts[0][2] == b"status=PAUSED"


def test_pause_skips_ads_that_are_not_active():
    src, fake = make({("GET", "/v23.0/11"): {"status": "PAUSED", "campaign_id": "7"}})
    assert src.pause_ad("11")["after"] == "PAUSED"
    assert not [c for c in fake.calls if c[0] == "POST"]


def test_pause_refuses_ads_outside_the_allowlist():
    src, fake = make({("GET", "/v23.0/11"): {"status": "ACTIVE", "campaign_id": "8"}}, campaign_ids=["7"])
    with pytest.raises(RuntimeError):
        src.pause_ad("11")
    assert not [c for c in fake.calls if c[0] == "POST"]


@pytest.mark.parametrize("url,form", [
    ("https://graph.facebook.com/v23.0/11", {"status": "ACTIVE"}),
    ("https://graph.facebook.com/v23.0/11", {"status": "PAUSED", "daily_budget": "1000"}),
    ("https://graph.facebook.com/v23.0/act_42/ads", {"status": "PAUSED"}),
    ("https://graph.facebook.com/v23.0/11/copies", {"status": "PAUSED"}),
])
def test_any_other_write_is_refused_before_sending(url, form):
    src, fake = make({})
    with pytest.raises(PermissionError):
        src._post(url, form)
    assert fake.calls == []


def test_errors_never_include_the_token():
    src, _ = make({})
    with pytest.raises(HttpError) as e:
        src.fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 2))
    assert TOKEN not in str(e.value)


def test_non_numeric_ids_are_rejected():
    src, _ = make({})
    with pytest.raises(ValueError):
        src.pause_ad("11/../act_42")


def test_missing_credentials_fail_clearly(monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    with pytest.raises(RuntimeError, match="META_ACCESS_TOKEN"):
        MetaSource()


def test_preflight_checks_status_and_campaign_without_writing():
    src, fake = make({("GET", "/v23.0/11"): {"status": "ACTIVE", "campaign_id": "8"}}, campaign_ids=["7"])
    with pytest.raises(RuntimeError, match="outside"):
        src.preflight_ad("11")
    assert all(call[0] == "GET" for call in fake.calls)


def test_pause_reports_acknowledgment_before_failed_verification():
    acknowledged = []
    states = iter([{"status": "ACTIVE", "campaign_id": "7"}, {"status": "ACTIVE", "campaign_id": "7"}])
    src, _ = make({("GET", "/v23.0/11"): lambda u, b: next(states),
                   ("POST", "/v23.0/11"): {"success": True}})
    with pytest.raises(RuntimeError, match="verif"):
        src.pause_ad("11", on_ack=lambda: acknowledged.append(True))
    assert acknowledged == [True]


def test_empty_success_tracks_all_provided_metrics():
    from kepler.metrics import MetricStore

    src, _ = make({("GET", "/v23.0/act_42/insights"): {"data": []},
                   ("GET", "/v23.0/act_42/ads"): {"data": []}})
    store = MetricStore(dt.date(2026, 1, 31))
    store.add_source("meta", src.fetch(dt.date(2026, 1, 1), store.today), False)
    for metric in ("spend", "impressions", "clicks", "meta_leads"):
        assert store.total(metric, 30) == 0


@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": {}}, {"data": [None]},
                                       {"data": [], "paging": "invalid"}])
def test_invalid_page_schema_fails(body):
    src, _ = make({("GET", "/v23.0/act_42/insights"): body,
                   ("GET", "/v23.0/act_42/ads"): {"data": []}})
    with pytest.raises(RuntimeError):
        src.fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))


def test_remaining_page_at_limit_fails_without_extra_request(monkeypatch):
    from kepler.sources import meta

    monkeypatch.setattr(meta, "MAX_PAGES", 1)
    src, fake = make(insights_routes())
    with pytest.raises(RuntimeError, match="incomplete"):
        src.fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert len(fake.calls) == 1


@pytest.mark.parametrize("values", [{"spend": "80"}, {"spend": "80", "impressions": None},
                                         {"spend": "NaN", "impressions": "4000"}])
def test_reread_rejects_missing_or_invalid_numbers(values):
    src, _ = make({("GET", "/v23.0/11/insights"): {"data": [values]}})
    with pytest.raises((RuntimeError, ValueError)):
        src.reread_ad("11", dt.date(2026, 1, 1), dt.date(2026, 1, 31))


def test_dry_run_holds_excluded_campaign(tmp_path):
    from test_autocut import NOW, TODAY, row, snapshot

    from kepler import autocut, config

    src, fake = make({("GET", "/v23.0/11"): {"status": "ACTIVE", "campaign_id": "8"}}, campaign_ids=["7"])
    cfg = config.from_dict({"auto_cut": {"mode": "dry-run"}}, tmp_path)
    result = autocut.run(snapshot([row("11", 80, 4000)]), src, cfg, NOW, TODAY)
    assert result["held"] and not result["would_pause"]
    assert all(call[0] == "GET" for call in fake.calls)
