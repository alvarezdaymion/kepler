import pytest

from kepler.bottlenecks import Step, binom_cdf, chain, evaluate


def rate(key, bench, entered, converted, monthly_in=None, min_volume=50):
    return Step(key=key, label=key, kind="rate", benchmark=bench,
                monthly_in=entered if monthly_in is None else monthly_in,
                entered=entered, converted=converted, min_volume=min_volume)


def test_zero_bookings_is_the_bottleneck_and_blocks_everything_above_it():
    # 770 opt-ins a month, 0 booked calls; show 65%, close 25% not judged yet (no calls).
    steps = [
        rate("optin", 0.30, 2600, 770, min_volume=100),  # behind its 30% benchmark
        rate("book", 0.12, 770, 0),
        rate("show", 0.65, 0, 0, min_volume=20),
        rate("close", 0.25, 0, 0, min_volume=20),
    ]
    ranked = chain(steps, revenue_per_sale=1500)
    top = ranked[0]
    assert top["key"] == "book"
    assert top["status"] == "behind"
    assert top["at_stake"] == pytest.approx(770 * 0.12 * 0.65 * 0.25 * 1500)
    optin = next(r for r in ranked if r["key"] == "optin")
    assert optin["status"] == "behind"
    assert optin["at_stake"] == 0
    assert optin["blocked_by"] == "book"


def test_zero_flagged_before_min_volume():
    s = rate("book", 0.12, 30, 0, min_volume=50)  # 30 x 12% = 3.6 expected, got 0
    assert evaluate(s, 1000)["status"] == "zero"


def test_too_early_when_volume_is_small():
    s = rate("book", 0.12, 5, 0, min_volume=50)
    r = evaluate(s, 1000)
    assert r["status"] == "too_early" and r["at_stake"] == 0


def test_binomial_early_flag():
    # 1 in 40 against a 25% benchmark is very unlikely: flagged before min volume.
    assert binom_cdf(1, 40, 0.25) < 0.05
    assert evaluate(rate("close", 0.25, 40, 1, min_volume=100), 1000)["status"] == "flagged_early"


def test_on_track_is_worth_nothing():
    assert evaluate(rate("show", 0.65, 100, 80, min_volume=20), 1000)["at_stake"] == 0


def test_untracked_step_is_not_zero():
    s = Step(key="x", label="x", kind="rate", benchmark=0.1, monthly_in=None)
    r = evaluate(s, 1000)
    assert r["status"] == "not_tracked" and r["actual"] is None


def test_volume_step_uses_downstream_rates():
    steps = [
        Step(key="top", label="top", kind="volume", benchmark=3000, monthly_in=1000),
        rate("a", 0.5, 1000, 500),
        rate("b", 0.1, 500, 50),
    ]
    top = next(r for r in chain(steps, 100) if r["key"] == "top")
    assert top["at_stake"] == pytest.approx(2000 * 0.5 * 0.1 * 100)


def test_ranking_order_biggest_first():
    steps = [rate("a", 0.5, 1000, 400), rate("b", 0.5, 400, 100)]
    ranked = chain(steps, 100)
    assert ranked[0]["at_stake"] >= ranked[1]["at_stake"]
