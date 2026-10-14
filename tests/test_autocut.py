import datetime as dt

from kepler import autocut, config

NOW = dt.datetime(2026, 1, 31, 12, 0, tzinfo=dt.UTC)
TODAY = NOW.date()


def row(ad_id, spend, impressions, verdict="cut", status="ACTIVE", name=None):
    return {"ad_id": ad_id, "ad_name": name or "ad " + ad_id, "status": status, "spend": spend,
            "impressions": impressions, "leads": 0, "verdict": verdict, "reason": "0 leads"}


def snapshot(rows, age_min=1):
    return {"generated_at": (NOW - dt.timedelta(minutes=age_min)).isoformat(),
            "ads": {"since": "2026-01-01", "rows": rows}}


class FakeAds:
    def __init__(self, truth, fail=()):
        self.truth, self.fail = truth, set(fail)
        self.rereads, self.paused = [], []

    def reread_ad(self, ad_id, since, until):
        self.rereads.append(ad_id)
        if ad_id in self.fail:
            raise RuntimeError("timeout")
        return self.truth[ad_id]

    def pause_ad(self, ad_id):
        self.paused.append(ad_id)
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
    assert log.read_text().count("\n") == 1


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
