from __future__ import annotations

import pytest

from ia.config import PROJECT_ROOT, load_rules
from ia.models import Regime
from ia.rules.regime import classify

RULES = load_rules(PROJECT_ROOT / "config" / "rules.yaml")
T = RULES.regime_thresholds


def _classify(**overrides):
    base = dict(
        close=4000.0,
        ma20=3900.0,
        ma60=3800.0,
        ma200=3700.0,
        vol_pct=30.0,
        pe_pct=40.0,
        rate_chg_60d_bp=5.0,
        chg_20d=2.0,
        thresholds=T,
    )
    base.update(overrides)
    return classify(**base)


def test_risk_on_when_all_votes_positive():
    result = _classify()
    assert result.score == 5
    assert result.regime is Regime.RISK_ON


def test_risk_off_when_all_votes_negative():
    result = _classify(
        close=3500.0, ma20=3800.0, ma60=3900.0, ma200=3700.0,
        vol_pct=90.0, pe_pct=95.0, rate_chg_60d_bp=40.0, chg_20d=-3.0,
    )
    assert result.score == -5
    assert result.regime is Regime.RISK_OFF


def test_neutral_between_thresholds():
    result = _classify(close=3600.0, vol_pct=90.0)
    assert result.score == 1
    assert result.regime is Regime.NEUTRAL


def test_crash_override_forces_risk_off():
    result = _classify(
        close=3600.0,
        ma200=3700.0,
        chg_20d=-9.0,
        ma20=3900.0,
        ma60=3800.0,
        vol_pct=10.0,
        pe_pct=10.0,
        rate_chg_60d_bp=1.0,
    )
    assert result.override is True
    assert result.regime is Regime.RISK_OFF


def test_missing_data_abstains():
    result = _classify(ma200=None, pe_pct=None)
    assert len(result.votes) == 5
    assert any(v.detail == "数据不足" for v in result.votes)


def test_boundary_scores():
    # 恰好 3 票为正即进攻
    result = _classify(vol_pct=30.0, pe_pct=95.0)
    assert result.score == 3
    assert result.regime is Regime.RISK_ON
    # 恰好 -3 票即防守
    result = _classify(
        close=3600.0, ma20=3800.0, ma60=3900.0, vol_pct=90.0, pe_pct=95.0, chg_20d=-3.0
    )
    assert result.score == -3
    assert result.regime is Regime.RISK_OFF
