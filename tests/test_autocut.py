import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from kepler import autocut, config

NOW = dt.datetime(2026, 1, 31, 12, 0, tzinfo=dt.UTC)
TODAY = NOW.date()


def row(ad_id, spend, impressions, verdict="cut", status="ACTIVE", name=None):
    return {"ad_id": ad_id, "ad_name": name or "ad " + ad_id, "status": status, "spend": spend,
            "impressions": impressions, "leads": 0, "verdict": verdict, "reason": "0 leads", "sources": ["meta"]}


def snapshot(rows, age_min=1):
    return {"generated_at": (NOW - dt.timedelta(minutes=age_min)).isoformat(),
            "ads": {"since": "2026-01-02", "until": TODAY.isoformat(), "leads_from": "ads",
                    "leads_available": True, "lead_sources": [], "rows": rows},
            "sources": [{"name": "meta", "status": "ok", "as_of": NOW.isoformat(), "max_age_minutes": 30}]}


class FakeAds:
    def __init__(self, truth, fail=()):
        self.truth, self.fail = truth, set(fail)
        self.rereads, self.paused = [], []

    def reread_ad(self, ad_id, since, until):
        self.rereads.append(ad_id)
        if ad_id in self.fail:
            raise RuntimeError("timeout")
        return self.truth[ad_id]

    def preflight_ad(self, ad_id):
        return {"status": "ACTIVE", "campaign_id": "7"}

    def pause_ad(self, ad_id, on_ack=None, before_send=None):
        if before_send:
            before_send()
        self.paused.append(ad_id)
        if on_ack:
            on_ack()
        return {"before": "ACTIVE", "after": "PAUSED"}


def cfg(tmp_path, mode, **extra):
    return config.from_dict({"auto_cut": {"mode": mode, **extra}}, tmp_path)


def test_default_mode_is_off(tmp_path):
    assert config.from_dict({}, tmp_path)["auto_cut"]["mode"] == "off"


def test_off_reads_and_changes_nothing(tmp_path):
    src = FakeAds({"1": (80.0, 4000)})
    out = autocut.run(snapshot([row("1", 80.0, 4000)]), src, cfg(tmp_path, "off"), NOW, TODAY)
    assert out["candidates"] == ["1"] and src.rereads == [] and src.paused == []


def test_dry_run_checks_but_never_pauses(tmp_path):
    src = FakeAds({"1": (80.0, 4000)})
    out = autocut.run(snapshot([row("1", 80.0, 4000)]), src, cfg(tmp_path, "dry-run"), NOW, TODAY)
    assert [p["ad_id"] for p in out["would_pause"]] == ["1"] and src.paused == []


def test_live_pauses_only_what_passes_the_guard(tmp_path):
    rows = [row("1", 80.0, 4000), row("2", 74.49, 11), row("3", 90.0, 5000, verdict="keep"),
            row("4", 70.0, 3000, status="PAUSED")]
    src = FakeAds({"1": (80.2, 4010), "2": (3.25, 11)})
    log = tmp_path / "auto-cut.jsonl"
    out = autocut.run(snapshot(rows), src, cfg(tmp_path, "live"), NOW, TODAY, log)
    assert src.paused == ["1"]
    assert [h["ad_id"] for h in out["held"]] == ["2"]
    assert src.rereads == ["1", "2"]  # keep and already-paused ads are never touched
    assert [json.loads(line)["phase"] for line in log.read_text().splitlines()] == [
        "attempted", "acknowledged", "confirmed"]


def test_the_bad_read_never_reaches_pause(tmp_path):
    src = FakeAds({"2": (3.25, 11)})
    out = autocut.run(snapshot([row("2", 74.49, 11)]), src, cfg(tmp_path, "live"), NOW, TODAY)
    assert src.paused == [] and out["held"][0]["ad_id"] == "2"


def test_too_many_cuts_at_once_holds_all(tmp_path):
    rows = [row(str(i), 80.0, 4000) for i in range(4)]
    src = FakeAds({str(i): (80.0, 4000) for i in range(4)})
    out = autocut.run(snapshot(rows), src, cfg(tmp_path, "live", max_pauses_per_run=3), NOW, TODAY)
    assert out["holds"] and src.rereads == [] and src.paused == []


def test_stale_snapshot_is_refused_in_live(tmp_path):
    src = FakeAds({"1": (80.0, 4000)})
    out = autocut.run(snapshot([row("1", 80.0, 4000)], age_min=45), src, cfg(tmp_path, "live"), NOW, TODAY)
    assert out["holds"] and src.paused == []


def test_failed_reread_is_a_hold(tmp_path):
    src = FakeAds({}, fail={"1"})
    out = autocut.run(snapshot([row("1", 80.0, 4000)]), src, cfg(tmp_path, "live"), NOW, TODAY)
    assert out["held"] and src.paused == []


def test_source_that_cannot_reread_holds(tmp_path):
    out = autocut.run(snapshot([row("1", 80.0, 4000)]), object(), cfg(tmp_path, "live"), NOW, TODAY)
    assert out["holds"] and not out["paused"]


def test_missing_provenance_holds_cuts(tmp_path):
    snap = snapshot([row("1", 80, 4000)])
    snap.pop("sources")
    source = FakeAds({"1": (80, 4000)})
    result = autocut.run(snap, source, cfg(tmp_path, "live"), NOW, TODAY)
    assert result["holds"] and not source.paused


def test_freshness_is_checked_again_after_reread(tmp_path):
    source = FakeAds({"1": (80, 4000)})
    moments = iter([NOW, NOW, NOW + dt.timedelta(hours=1)])
    result = autocut.run(snapshot([row("1", 80, 4000)]), source, cfg(tmp_path, "live"), NOW, TODAY,
                         decision_clock=lambda: next(moments))
    assert source.rereads == ["1"] and not source.paused
    assert result["holds"] or result["held"]


@pytest.mark.parametrize("failure", ["readback", "active", "post"])
def test_uncertain_pause_is_durable_and_never_retried(tmp_path, failure):
    class Uncertain(FakeAds):
        def pause_ad(self, ad_id, on_ack=None, before_send=None):
            before_send()
            self.paused.append(ad_id)
            if failure != "post":
                on_ack()
            if failure != "active":
                raise RuntimeError("response unavailable")
            return {"before": "ACTIVE", "after": "ACTIVE"}

    source = Uncertain({"1": (80, 4000)})
    c = cfg(tmp_path, "live")
    log = tmp_path / "auto-cut.jsonl"
    snap = snapshot([row("1", 80, 4000)])
    result = autocut.run(snap, source, c, NOW, TODAY, log)
    assert result["errors"] and not result["paused"]
    phases = [json.loads(line)["phase"] for line in log.read_text().splitlines()]
    assert phases[0] == "attempted" and phases[-1] == "uncertain"
    assert ("acknowledged" in phases) == (failure != "post")
    autocut.run(snap, source, c, NOW, TODAY, log)
    assert source.paused == ["1"]


def test_overlapping_auto_cuts_reserve_one_pause(tmp_path):
    source = FakeAds({"1": (80, 4000)})
    c = cfg(tmp_path, "live")
    snap = snapshot([row("1", 80, 4000)])
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: autocut.run(snap, source, c, NOW, TODAY), range(2)))
    assert source.paused == ["1"]
    assert sum(len(result["paused"]) for result in results) == 1


def test_broken_journal_prevents_writes(tmp_path):
    source = FakeAds({"1": (80, 4000)})
    path = tmp_path / "auto-cut.jsonl"
    path.write_text('{"phase":')
    result = autocut.run(snapshot([row("1", 80, 4000)]), source, cfg(tmp_path, "live"), NOW, TODAY, path)
    assert result["holds"] and not source.paused


def test_failed_reservation_prevents_pause(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(autocut, "append_json", unavailable)
    source = FakeAds({"1": (80, 4000)})
    result = autocut.run(snapshot([row("1", 80, 4000)]), source, cfg(tmp_path, "live"), NOW, TODAY)
    assert result["errors"] and not source.paused


def test_delayed_final_preflight_cannot_send_a_stale_pause(tmp_path):
    from kepler.sources.meta import MetaSource

    current, status_reads, posts = NOW, 0, []

    def transport(method, url, headers, body, timeout):
        nonlocal current, status_reads
        if method == "POST":
            posts.append(body)
            response = {"success": True}
        elif "/insights" in url:
            response = {"data": [{"spend": "80", "impressions": "4000"}]}
        else:
            status_reads += 1
            if status_reads == 2:
                current += dt.timedelta(hours=1)
            response = {"status": "PAUSED" if posts else "ACTIVE", "campaign_id": "7"}
        return 200, json.dumps(response).encode()

    source = MetaSource(token="synthetic-token", account_id="42", transport=transport)
    result = autocut.run(snapshot([row("1", 80, 4000)]), source, cfg(tmp_path, "live"), NOW, TODAY,
                         decision_clock=lambda: current)
    assert not posts
    assert result["errors"]


@pytest.mark.parametrize("readback", ["active", "unavailable"])
def test_meta_acknowledgment_is_audited_when_verification_fails(tmp_path, readback):
    from kepler.sources.meta import MetaSource

    posts = []

    def transport(method, url, headers, body, timeout):
        if method == "POST":
            posts.append(body)
            response = {"success": True}
        elif "/insights" in url:
            response = {"data": [{"spend": "80", "impressions": "4000"}]}
        else:
            if posts and readback == "unavailable":
                raise OSError("readback unavailable")
            response = {"status": "ACTIVE", "campaign_id": "7"}
        return 200, json.dumps(response).encode()

    source = MetaSource(token="synthetic-token", account_id="42", transport=transport)
    c = cfg(tmp_path, "live")
    log = tmp_path / "auto-cut.jsonl"
    first = autocut.run(snapshot([row("1", 80, 4000)]), source, c, NOW, TODAY, log)
    assert first["errors"] and not first["paused"]
    assert [json.loads(line)["phase"] for line in log.read_text().splitlines()] == [
        "attempted", "acknowledged", "uncertain"]
    second = autocut.run(snapshot([row("1", 80, 4000)]), source, c, NOW, TODAY, log)
    assert second["held"] and posts == [b"status=PAUSED"]
