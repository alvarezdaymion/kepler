import datetime as dt
import json
from types import SimpleNamespace

from kepler import config, controller
from kepler.cli import demo_config, main
from kepler.sources.base import SourceData
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
