import pytest

from kepler import guard


def test_the_74_dollars_on_11_impressions_read_is_held():
    # One bulk pull said a brand-new ad spent $74.49 on 11 impressions; its real spend was $3.25.
    reasons = guard.check(74.49, 11, 3.25, 11, spend_tolerance=1.0, max_cpm=500)
    assert len(reasons) == 2
    assert "$6772 CPM" in reasons[0]
    assert "fresh read says $3.25" in reasons[1]


def test_spend_disagreement_alone_holds():
    assert guard.check(60.0, 3000, 58.5, 2950)  # $1.50 apart, CPM fine


def test_agreeing_reads_pass():
    assert guard.check(60.0, 3000, 60.4, 3010) == []


def test_fresh_read_slightly_higher_passes():
    # The ad kept spending between the snapshot and the re-read.
    assert guard.check(60.0, 3000, 63.0, 3150) == []


def test_spend_without_impressions_is_held():
    assert guard.check(40.0, 0, 40.0, 0)


def test_impossible_fresh_read_is_held():
    assert guard.check(3.0, 100, 3.0, 2)


@pytest.mark.parametrize("values", [
    (60, 3000, 60, 0), (0.01, 0, 0.01, 0),
    (float("nan"), 3000, 60, 3000), (60, 3000, float("inf"), 3000),
    (60, -1, 60, 3000), (60, 3000, -1, 3000), (60, 3000, 60, None),
    (60, 3000, 60, 3.5),
])
def test_invalid_numbers_hold_both_reads(values):
    assert guard.check(*values)
