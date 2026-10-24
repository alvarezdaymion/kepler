import pytest

from kepler import config


@pytest.mark.parametrize("currency", ["EUR", "invalid", "usd"])
def test_unsupported_currency_is_rejected(currency):
    with pytest.raises(ValueError, match="currency"):
        config.from_dict({"business": {"currency": currency}})


@pytest.mark.parametrize("section,key,value", [
    ("ads", "target_cpl", float("nan")), ("ads", "judge_days", 0), ("ads", "judge_days", 1.5),
    ("ads", "leads_from", "unknown"), ("ads", "warning_max_hours", -1),
    ("auto_cut", "max_snapshot_age_minutes", float("inf")), ("auto_cut", "spend_tolerance", -1),
    ("auto_cut", "max_cpm", 0), ("auto_cut", "max_pauses_per_run", 1.5),
    ("analysis", "every_minutes", 0), ("controller", "every_minutes", float("nan")),
])
def test_invalid_decision_limits_are_rejected(section, key, value):
    with pytest.raises(ValueError):
        config.from_dict({section: {key: value}})


def test_source_currency_cannot_disagree_with_display():
    with pytest.raises(ValueError, match="currency"):
        config.from_dict({"sources": [{"name": "pay", "type": "stripe", "currency": "eur"}]})
