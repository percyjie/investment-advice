from __future__ import annotations

import pytest

from ia.config import PROJECT_ROOT, load_rules
from ia.models import Regime, RegimeResult
from ia.rules.target import compute, equity_target

RULES = load_rules(PROJECT_ROOT / "config" / "rules.yaml")


def _regime(regime: Regime, score: int) -> RegimeResult:
    return RegimeResult(regime=regime, score=score, votes=())


def test_targets_sum_to_one():
    for regime in Regime:
        for pe in (10.0, 50.0, 95.0):
            target = compute(RULES, _regime(regime, 0), pe)
            assert sum(target.weights.values()) == pytest.approx(1.0)


def test_neutral_matches_configured_targets():
    target = compute(RULES, _regime(Regime.NEUTRAL, 0), 50.0)
    for bucket, expected in RULES.targets.items():
        assert target.get(bucket) == pytest.approx(expected / 100)


def test_risk_off_moves_to_gold_and_bond():
    neutral = compute(RULES, _regime(Regime.NEUTRAL, 0), 50.0)
    defensive = compute(RULES, _regime(Regime.RISK_OFF, -4), 50.0)
    assert equity_target(defensive) < equity_target(neutral)
    assert defensive.get("gold") > neutral.get("gold")
    assert defensive.get("bond") > neutral.get("bond")


def test_expensive_valuation_cuts_equity():
    neutral = compute(RULES, _regime(Regime.NEUTRAL, 0), 50.0)
    expensive = compute(RULES, _regime(Regime.NEUTRAL, 0), 95.0)
    assert equity_target(expensive) < equity_target(neutral)
    assert expensive.get("cash") > neutral.get("cash")


def test_bounds_never_violated():
    for regime in Regime:
        for pe in (1.0, 99.0):
            target = compute(RULES, _regime(regime, 0), pe)
            for bucket, (low, high) in RULES.bounds.items():
                value = target.get(bucket) * 100
                assert low - 1e-6 <= value <= high + 1e-6
