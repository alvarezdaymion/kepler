import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from kepler import analysis, config

NOW = dt.datetime(2026, 1, 31, 12, tzinfo=dt.UTC)
FACTS = {"goal": {}, "funnel": {}, "bottlenecks": [], "lanes": [], "ads": {"rows": []}, "sources": []}


class Model:
    def __init__(self, request):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=request))


@pytest.mark.parametrize("failure", ["refusal", "timeout"])
def test_failed_attempts_wait_the_full_cadence(tmp_path, failure):
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        if failure == "timeout":
            raise RuntimeError("request failed")
        return SimpleNamespace(stop_reason="refusal")

    cfg = config.from_dict({"analysis": {"enabled": True}}, tmp_path)
    client = Model(request)
    for minutes in (0, 61, 1439):
        result = analysis.maybe_run(cfg, FACTS, NOW + dt.timedelta(minutes=minutes), tmp_path, client)
        assert result["status"] == "error"
    assert len(calls) == 1
    analysis.maybe_run(cfg, FACTS, NOW + dt.timedelta(minutes=1440), tmp_path, client)
    assert len(calls) == 2


def test_attempt_is_durable_before_call_and_survives_interruption(tmp_path):
    def interrupted(**kwargs):
        record = json.loads((tmp_path / "analysis.json").read_text())
        assert record["attempted_at"] == NOW.isoformat(timespec="seconds")
        raise KeyboardInterrupt

    cfg = config.from_dict({"analysis": {"enabled": True}}, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        analysis.maybe_run(cfg, FACTS, NOW, tmp_path, Model(interrupted))
    result = analysis.maybe_run(cfg, FACTS, NOW + dt.timedelta(minutes=15), tmp_path, Model(interrupted))
    assert result["status"] == "error"


def test_overlapping_analysis_reserves_one_attempt(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(stop_reason="refusal")

    cfg = config.from_dict({"analysis": {"enabled": True}}, tmp_path)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(analysis.maybe_run, cfg, FACTS, NOW, tmp_path, Model(request))
        assert entered.wait(5)
        second = pool.submit(analysis.maybe_run, cfg, FACTS, NOW, tmp_path, Model(request))
        release.set()
        first.result(5)
        second.result(5)
    assert len(calls) == 1
