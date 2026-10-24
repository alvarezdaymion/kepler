import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
        moment = NOW + dt.timedelta(minutes=minutes)
        result = analysis.maybe_run(cfg, FACTS, moment, tmp_path, client, decision_clock=lambda moment=moment: moment)
        assert result["status"] == "error"
    assert len(calls) == 1
    moment = NOW + dt.timedelta(minutes=1440)
    analysis.maybe_run(cfg, FACTS, moment, tmp_path, client, decision_clock=lambda: moment)
    assert len(calls) == 2


def test_attempt_is_durable_before_call_and_survives_interruption(tmp_path):
    def interrupted(**kwargs):
        record = json.loads((tmp_path / "analysis.json").read_text())
        assert record["attempted_at"] == NOW.isoformat(timespec="seconds")
        raise KeyboardInterrupt

    cfg = config.from_dict({"analysis": {"enabled": True}}, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        analysis.maybe_run(cfg, FACTS, NOW, tmp_path, Model(interrupted), decision_clock=lambda: NOW)
    later = NOW + dt.timedelta(minutes=15)
    result = analysis.maybe_run(cfg, FACTS, later, tmp_path, Model(interrupted), decision_clock=lambda: later)
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


def test_waiting_for_analysis_lock_does_not_backdate_attempt(tmp_path, monkeypatch):
    elapsed = 0
    original_lock = analysis.locked
    monkeypatch.setattr(time, "monotonic", lambda: elapsed)

    @contextmanager
    def delayed_lock(path):
        nonlocal elapsed
        with original_lock(path):
            elapsed = 16 * 60
            yield

    monkeypatch.setattr(analysis, "locked", delayed_lock)
    calls = []

    def request(**kwargs):
        calls.append(NOW + dt.timedelta(seconds=elapsed))
        return SimpleNamespace(stop_reason="refusal")

    cfg = config.from_dict({"analysis": {"enabled": True, "every_minutes": 15}}, tmp_path)
    client = Model(request)
    analysis.maybe_run(cfg, FACTS, NOW, tmp_path, client)
    record = json.loads((tmp_path / "analysis.json").read_text())
    assert dt.datetime.fromisoformat(record["attempted_at"]) == calls[0]
    analysis.maybe_run(cfg, FACTS, calls[0], tmp_path, client)
    assert len(calls) == 1


def test_analysis_cadence_preserves_fractional_seconds(tmp_path):
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stop_reason="refusal")

    cfg = config.from_dict({"analysis": {"enabled": True, "every_minutes": 15}}, tmp_path)
    client = Model(request)
    first = NOW + dt.timedelta(microseconds=900000)
    analysis.maybe_run(cfg, FACTS, first, tmp_path, client, decision_clock=lambda: first)
    later = NOW + dt.timedelta(minutes=15, microseconds=100000)
    analysis.maybe_run(cfg, FACTS, later, tmp_path, client, decision_clock=lambda: later)
    assert len(calls) == 1
