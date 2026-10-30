from __future__ import annotations

from datetime import date

from conftest import make_portfolio, make_position
from ia.config import PROJECT_ROOT, load_rules
from ia.models import (
    AssetType,
    BoardPerf,
    Level,
    MarketSnapshot,
    Regime,
    RegimeResult,
)
from ia.rules.risk_scan import evaluate
from ia.rules.target import compute

RULES = load_rules(PROJECT_ROOT / "config" / "rules.yaml")
AS_OF = date(2026, 10, 30)
NAV_DEADLINE = date(2026, 10, 29)


def _market(**overrides) -> MarketSnapshot:
    base = dict(
        as_of=AS_OF,
        index_close=4000.0,
        index_chg_pct=0.5,
        index_chg_20d=1.5,
        ma20=3950.0,
        ma60=3900.0,
        ma200=3800.0,
        vol_20d=0.15,
        vol_pct=40.0,
        pe_pct=50.0,
        rate_10y=2.1,
        rate_chg_60d_bp=3.0,
        rate_chg_5d_bp=4.0,
        board_top=(BoardPerf("半导体", 2.0),),
        board_bottom=(BoardPerf("地产", -2.5),),
        data_date=AS_OF,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def _target(regime: Regime = Regime.NEUTRAL, pe: float = 50.0):
    return compute(RULES, RegimeResult(regime=regime, score=0, votes=()), pe)


def _healthy_portfolio():
    return make_portfolio(
        [
            make_position(code="510300", bucket="equity_core", qty=5000, price=4.0, cost=4.0),
            make_position(
                code="512480", name="半导体ETF", bucket="equity_satellite",
                qty=5000, price=1.0, cost=1.0,
            ),
            make_position(
                code="600519", name="某股票", type=AssetType.STOCK, bucket="stock",
                qty=50, price=100.0, cost=100.0,
            ),
            make_position(code="518880", name="黄金ETF", bucket="gold", qty=4000, price=5.0, cost=5.0),
            make_position(code="511010", name="国债ETF", bucket="bond", qty=10000, price=2.0, cost=2.0),
        ],
        cash=30000,
    )


def test_green_when_all_healthy():
    scan = evaluate(_healthy_portfolio(), _market(), _target(), RULES, None, AS_OF, NAV_DEADLINE)
    assert scan.level is Level.GREEN
    assert scan.alerts == ()


def test_stale_data_is_red():
    portfolio = make_portfolio(
        [make_position(code="510300", stale=True)], cash=80000
    )
    scan = evaluate(portfolio, _market(data_date=date(2026, 10, 28)), _target(), RULES, None, AS_OF, NAV_DEADLINE)
    assert scan.level is Level.RED
    assert any(a.rule_id == "data_stale" for a in scan.alerts)


def test_low_cash_is_red():
    portfolio = make_portfolio(
        [make_position(code="510300", bucket="equity_core", qty=20000, price=4.0)],
        cash=10000,
    )
    scan = evaluate(portfolio, _market(), _target(), RULES, None, AS_OF, NAV_DEADLINE)
    assert scan.level is Level.RED
    assert any(a.rule_id == "cash_ratio" and a.level is Level.RED for a in scan.alerts)


def test_regime_switch_to_risk_off_is_red():
    scan = evaluate(
        _healthy_portfolio(), _market(), _target(Regime.RISK_OFF), RULES, Regime.RISK_ON, AS_OF, NAV_DEADLINE
    )
    alert = next(a for a in scan.alerts if a.rule_id == "regime_switch")
    assert alert.level is Level.RED
    assert scan.should_act() is True


def test_single_stock_concentration():
    positions = [
        make_position(
            code="600519", name="某股票", type=AssetType.STOCK, bucket="stock",
            qty=100, price=100.0,
        )
    ]
    portfolio = make_portfolio(positions, cash=90000)
    scan = evaluate(portfolio, _market(), _target(), RULES, None, AS_OF, NAV_DEADLINE)
    assert any(a.rule_id == "single_stock" for a in scan.alerts)


def test_fund_nav_lag():
    from dataclasses import replace

    positions = [
        make_position(
            code="110022", name="易方达消费", type=AssetType.FUND,
            bucket="equity_satellite", qty=10000, price=2.0,
        )
    ]
    positions[0] = replace(positions[0], nav_date=date(2026, 10, 27))
    portfolio = make_portfolio(positions, cash=80000)
    scan = evaluate(portfolio, _market(), _target(), RULES, None, AS_OF, NAV_DEADLINE)
    assert any(a.rule_id == "nav_lag" for a in scan.alerts)


def test_valuation_high_alert():
    scan = evaluate(_healthy_portfolio(), _market(pe_pct=90.0), _target(pe=90.0), RULES, None, AS_OF, NAV_DEADLINE)
    assert any(a.rule_id == "valuation_high" for a in scan.alerts)
