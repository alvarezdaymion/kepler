import datetime as dt
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from kepler import config, controller
from kepler.cli import demo_config, main
from kepler.sources.base import AdRow, SourceData
from kepler.sources.demo import DemoWorld

NOW = dt.datetime(2026, 1, 31, 12, 0).astimezone()


class Fixed:
    def __init__(self, name, data=None, error=None):
        self.name, self.data, self.error = name, data, error

    def fetch(self, since, until):
        if self.error:
            raise RuntimeError(self.error)
        return self.data


def crm_data(as_of):
    d = SourceData(as_of=as_of)
    for i in range(1, 31):
        day = (NOW.date() - dt.timedelta(days=i)).isoformat()
        d.add("optins", day, 20)
        d.add("booked", day, 0)
    return d


def cfg(tmp_path):
    return config.from_dict({"sources": [{"name": "crm", "type": "csv", "path": "crm.csv"},
                                         {"name": "pay", "type": "stripe"}]}, tmp_path)


def test_failed_source_keeps_last_good_copy_and_is_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    c = cfg(tmp_path)
    controller.run_tick(c, NOW, built={"crm": Fixed("crm", crm_data(NOW))})
    later = NOW + dt.timedelta(minutes=15)
    snap = controller.run_tick(c, later, built={"crm": Fixed("crm", error="connection reset")})
    crm = next(s for s in snap["sources"] if s["name"] == "crm")
    assert crm["status"] == "stale" and "connection reset" in crm["error"]
    rows = {r["metric"]: r for r in snap["funnel"]["rows"]}
    assert rows["optins"]["actual_per_day"] == 20 and rows["optins"]["stale"]


def test_never_read_source_is_missing_and_unknown_not_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    snap = controller.run_tick(cfg(tmp_path), NOW, built={"crm": Fixed("crm", crm_data(NOW))})
    pay = next(s for s in snap["sources"] if s["name"] == "pay")
    assert pay["status"] == "missing" and "STRIPE_API_KEY" in pay["error"]
    assert snap["goal"]["actual_per_day"] is None  # sales unknown, not 0
    html = (tmp_path / "site" / "index.html").read_text()
    assert "Not tracked" in html


def test_old_data_is_stale_even_when_the_read_works(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    snap = controller.run_tick(cfg(tmp_path), NOW,
                               built={"crm": Fixed("crm", crm_data(NOW - dt.timedelta(hours=3)))})
    assert next(s for s in snap["sources"] if s["name"] == "crm")["status"] == "stale"


def test_demo_end_to_end_both_scenarios(tmp_path):
    for scenario in ("zero-bookings", "mixed"):
        c = demo_config(tmp_path / scenario)
        snap = controller.run_tick(c, NOW, demo_world=DemoWorld(NOW.date(), scenario, NOW))
        assert snap["bottlenecks"][0]["at_stake"] > 0
        assert (c.site_dir / "ads.html").exists() and (c.state_dir / "snapshot.json").exists()
        ac = snap["auto_cut"]
        assert ac["mode"] == "dry-run" and ac["paused"] == []
        assert [h["ad_id"] for h in ac["held"]] == ["1107"]
        assert {p["ad_id"] for p in ac["would_pause"]} == {"1102", "1104"}
        assert next(s for s in snap["sources"] if s["name"] == "organic")["status"] == "stale"
    zero = json.loads((tmp_path / "zero-bookings" / "state" / "snapshot.json").read_text())
    assert zero["bottlenecks"][0]["label"] == "Opt-in to booked call"
    assert any(b.get("blocked_by") == "Opt-in to booked call" for b in zero["bottlenecks"])


def test_demo_cli_runs(tmp_path, capsys):
    assert main(["demo", "--dir", str(tmp_path / "d")]) == 0
    out = capsys.readouterr().out
    assert "Opt-in to booked call" in out and "guard held" in out


class FakeLLM:
    def __init__(self):
        self.calls = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        assert kw["fallbacks"] == "default" and kw["output_config"] == {"effort": "low"}
        return SimpleNamespace(stop_reason="end_turn", model=kw["model"],
                               content=[SimpleNamespace(type="text", text="Fix booking first.")],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=120))


def test_analysis_off_by_default(tmp_path):
    snap = controller.run_tick(demo_config(tmp_path), NOW, demo_world=DemoWorld(NOW.date(), now=NOW))
    assert snap["analysis"]["status"] == "off"


def test_analysis_runs_at_most_once_per_cadence(tmp_path):
    c = demo_config(tmp_path)
    c.data["analysis"]["enabled"] = True
    llm = FakeLLM()
    world = DemoWorld(NOW.date(), now=NOW)
    for minutes in (0, 15, 30, 45):
        snap = controller.run_tick(c, NOW + dt.timedelta(minutes=minutes), demo_world=world, llm_client=llm)
    assert llm.calls == 1 and snap["analysis"]["text"] == "Fix booking first."
    controller.run_tick(c, NOW + dt.timedelta(days=1, minutes=1), demo_world=world, llm_client=llm)
    assert llm.calls == 2


def test_example_config_loads():
    from pathlib import Path

    import kepler
    c = config.load(Path(kepler.__file__).parent / "templates" / "kepler.example.toml")
    assert c["auto_cut"]["mode"] == "off" and c["analysis"]["enabled"] is False


class Pausable(Fixed):
    def __init__(self, data=None, error=None):
        super().__init__("meta", data, error)
        self.paused = []

    def preflight_ad(self, ad_id):
        return {"status": "ACTIVE", "campaign_id": "7"}

    def reread_ad(self, ad_id, since, until):
        return 80, 4000

    def pause_ad(self, ad_id, on_ack=None, before_send=None):
        if before_send:
            before_send()
        self.paused.append(ad_id)
        if on_ack:
            on_ack()
        return {"before": "ACTIVE", "after": "PAUSED"}


@pytest.mark.parametrize("failure", ["healthy", "cached_ads", "stale_ads", "stale_crm", "missing_crm", "future_ads"])
def test_auto_cut_requires_healthy_selected_inputs(tmp_path, failure):
    c = config.from_dict({"auto_cut": {"mode": "live"}, "ads": {"leads_from": "ad_leads"}, "sources": [
        {"name": "meta", "type": "meta", "max_age_minutes": 30},
        {"name": "crm", "type": "csv", "kind": "ad_leads", "path": "leads.csv", "max_age_minutes": 30},
    ]}, tmp_path)
    ad_time = NOW - dt.timedelta(days=2) if failure == "stale_ads" else NOW
    if failure == "future_ads":
        ad_time = NOW + dt.timedelta(days=1)
    source = Pausable(SourceData(ad_time, ads=[
        AdRow(NOW.date().isoformat(), "1", status="ACTIVE", spend=80, impressions=4000)
    ]))
    lead_time = NOW - dt.timedelta(days=2) if failure == "stale_crm" else NOW
    crm = Fixed("crm", SourceData(lead_time, ad_leads=[(NOW.date().isoformat(), "1", 0)]),
                error="unavailable" if failure == "missing_crm" else None)
    if failure == "cached_ads":
        c.data["auto_cut"]["mode"] = "off"
        controller.run_tick(c, NOW, built={"meta": source, "crm": crm})
        c.data["auto_cut"]["mode"] = "live"
        source.error = "unavailable"
    snap = controller.run_tick(c, NOW, built={"meta": source, "crm": crm})
    assert source.paused == (["1"] if failure == "healthy" else []), snap["auto_cut"]


@pytest.mark.parametrize("cached", [
    {"as_of": "invalid"}, {"as_of": NOW.isoformat(), "daily": []},
    {"as_of": NOW.isoformat(), "ads": [{"date": "2026-01-30", "ad_id": "1", "spend": "bad"}]},
    {"as_of": NOW.isoformat(), "ads": [{"date": "2026-01-30", "ad_id": "1", "ad_name": {}}]},
    {"as_of": NOW.replace(tzinfo=None).isoformat()},
])
def test_unusable_source_cache_is_missing_and_tick_continues(tmp_path, cached):
    c = config.from_dict({"sources": [{"name": "meta", "type": "meta"}]}, tmp_path)
    controller.write_json(c.state_dir / "sources" / "meta.json", {"data": cached})
    snap = controller.run_tick(c, NOW, built={"meta": Fixed("meta", error="unavailable")})
    assert snap["sources"][0]["status"] == "missing"
    assert (c.site_dir / "index.html").exists()


def test_overlapping_ticks_serialize_reads_and_writes(tmp_path):
    entered, release, second_read = threading.Event(), threading.Event(), threading.Event()
    calls = 0

    class Slow(Fixed):
        def fetch(self, since, until):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                assert release.wait(5)
            else:
                second_read.set()
            return SourceData(NOW)

    c = config.from_dict({"sources": [{"name": "crm", "type": "csv", "path": "unused.csv"}]}, tmp_path)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(controller.run_tick, c, NOW, {"crm": Slow("crm")})
        assert entered.wait(5)
        second = pool.submit(controller.run_tick, c, NOW, {"crm": Slow("crm")})
        try:
            assert not second_read.wait(0.1)
        finally:
            release.set()
        first.result(5)
        second.result(5)


@pytest.mark.parametrize("record", ["20260130,1,9", "2026-01-30,1,0,9"])
def test_malformed_crm_export_cannot_trigger_a_live_pause(tmp_path, record):
    path = tmp_path / "leads.csv"
    path.write_text("date,ad_id,leads\n%s\n" % record)
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    c = config.from_dict({"auto_cut": {"mode": "live"}, "ads": {"leads_from": "ad_leads"}, "sources": [
        {"name": "meta", "type": "meta"},
        {"name": "crm", "type": "csv", "kind": "ad_leads", "path": "leads.csv"},
    ]}, tmp_path)
    source = Pausable(SourceData(NOW, ads=[
        AdRow("2026-01-30", "1", status="ACTIVE", spend=80, impressions=4000, leads=9)
    ]))
    snap = controller.run_tick(c, NOW, built={"meta": source})
    assert not source.paused
    assert snap["sources"][1]["status"] == "missing"
    assert snap["ads"]["rows"][0]["leads"] is None


def test_source_health_and_cache_timestamp_use_read_completion(tmp_path, monkeypatch):
    elapsed = 0
    monkeypatch.setattr(controller.time, "monotonic", lambda: elapsed)

    class Slow:
        def fetch(self, since, until):
            nonlocal elapsed
            elapsed += 150
            return SourceData(NOW + dt.timedelta(seconds=elapsed))

    c = config.from_dict({"sources": [{"name": "crm", "type": "csv", "path": "unused.csv"}]}, tmp_path)
    snap = controller.run_tick(c, NOW, built={"crm": Slow()})
    assert snap["sources"][0]["status"] == "ok"
    assert snap["sources"][0]["age_minutes"] == 0
    completed = NOW + dt.timedelta(seconds=150)
    cached = controller.read_json(c.state_dir / "sources" / "crm.json")
    assert dt.datetime.fromisoformat(cached["fetched_at"]) == completed
    assert dt.datetime.fromisoformat(snap["generated_at"]) == completed


def test_slow_later_source_ages_earlier_inputs(tmp_path, monkeypatch):
    elapsed = 0
    monkeypatch.setattr(controller.time, "monotonic", lambda: elapsed)

    class Slow:
        def fetch(self, since, until):
            nonlocal elapsed
            elapsed += 31 * 60
            return SourceData(NOW + dt.timedelta(seconds=elapsed))

    c = config.from_dict({"sources": [
        {"name": "first", "type": "csv", "path": "unused.csv", "max_age_minutes": 30},
        {"name": "second", "type": "csv", "path": "unused.csv", "max_age_minutes": 30},
    ]}, tmp_path)
    store, health, _ = controller.read_sources(c, NOW, {"first": Fixed("first", SourceData(NOW)), "second": Slow()})
    assert [row["status"] for row in health] == ["stale", "ok"]
    assert store.stale_sources == {"first"}


def test_slow_reads_do_not_shorten_model_request_cadence(tmp_path, monkeypatch):
    elapsed = 0
    monkeypatch.setattr(controller.time, "monotonic", lambda: elapsed)

    class SlowOnce:
        def fetch(self, since, until):
            nonlocal elapsed
            elapsed = 16 * 60
            return SourceData(NOW + dt.timedelta(seconds=elapsed))

    c = config.from_dict({"analysis": {"enabled": True, "every_minutes": 15},
                          "sources": [{"name": "crm", "type": "csv", "path": "unused.csv"}]}, tmp_path)
    client, source = FakeLLM(), SlowOnce()
    controller.run_tick(c, NOW, built={"crm": source}, llm_client=client)
    call_time = NOW + dt.timedelta(seconds=elapsed)
    record = controller.read_json(c.state_dir / "analysis.json")
    assert dt.datetime.fromisoformat(record["attempted_at"]) == call_time
    controller.run_tick(c, call_time, built={"crm": source}, llm_client=client)
    assert client.calls == 1
