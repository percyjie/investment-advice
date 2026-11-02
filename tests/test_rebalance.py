from __future__ import annotations

from datetime import date

import pytest

from conftest import make_portfolio, make_position
from ia.config import PROJECT_ROOT, load_rules, load_universe
from ia.models import BUCKET_ORDER, AssetType, Regime, ReasonCode, TargetWeights
from ia.rules.guards import InstrumentQuote
from ia.rules.rebalance import plan

RULES = load_rules(PROJECT_ROOT / "config" / "rules.yaml")
UNIVERSE = load_universe(PROJECT_ROOT / "config" / "universe.yaml")
QUOTES = {
    "518880": InstrumentQuote("518880", "黄金ETF", AssetType.ETF, "gold", 5.0),
    "511010": InstrumentQuote("511010", "国债ETF", AssetType.ETF, "bond", 2.0),
    "512480": InstrumentQuote("512480", "半导体ETF", AssetType.ETF, "equity_satellite", 1.0),
}


def _target(**weights: float) -> TargetWeights:
    base = {bucket: 0.0 for bucket in BUCKET_ORDER}
    base.update(weights)
    assert abs(sum(base.values()) - 1.0) < 1e-9
    return TargetWeights(regime=Regime.NEUTRAL, score=0, weights=base)


def test_no_trade_when_on_target():
    positions = [
        make_position(code="510300", bucket="equity_core", qty=5000, price=4.0),
        make_position(code="511010", name="国债ETF", bucket="bond", qty=10000, price=2.0),
    ]
    portfolio = make_portfolio(positions, cash=50000)
    target = _target(equity_core=0.2, bond=0.2, cash=0.6)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    assert result.trades == ()


def test_sell_overweight_then_buy_underweight():
    positions = [
        make_position(code="510300", bucket="equity_core", qty=6250, price=4.0),
        make_position(code="512480", name="半导体ETF", bucket="equity_satellite", qty=5000, price=1.0),
        make_position(code="518880", name="黄金ETF", bucket="gold", qty=3800, price=5.0),
        make_position(code="511010", name="国债ETF", bucket="bond", qty=7500, price=2.0),
    ]
    portfolio = make_portfolio(positions, cash=36000)
    target = _target(equity_core=0.2, equity_satellite=0.05, gold=0.2, bond=0.2, cash=0.35)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    sells = [t for t in result.trades if t.action.value == "SELL"]
    buys = [t for t in result.trades if t.action.value == "BUY"]
    assert sells, "超配的宽基应被卖出"
    assert all(t.reason is ReasonCode.DEVIATION_DOWN for t in sells)
    assert result.cash_after >= RULES.rebalance.cash_floor * portfolio.total_assets
    if buys:
        assert all(t.amount_est >= 100 for t in buys)


def test_turnover_cap_limits_trades():
    positions = [
        make_position(code="510300", bucket="equity_core", qty=10000, price=4.0),
        make_position(code="518880", name="黄金ETF", bucket="gold", qty=7000, price=5.0),
    ]
    portfolio = make_portfolio(positions, cash=35000)
    target = _target(equity_core=0.1, gold=0.1, cash=0.8)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    traded = sum(t.amount_est for t in result.trades)
    assert traded <= RULES.rebalance.max_turnover * portfolio.total_assets + 1e-6


def test_valuation_gate_blocks_equity_buy():
    positions = [make_position(code="518880", name="黄金ETF", bucket="gold", qty=8000, price=5.0)]
    portfolio = make_portfolio(positions, cash=60000)
    target = _target(equity_core=0.2, gold=0.4, cash=0.4)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=95.0)
    assert not any(t.action.value == "BUY" and t.bucket == "equity_core" for t in result.trades)
    assert any(b.reason is ReasonCode.VALUATION_GATE for b in result.blocked)


def test_no_chase_blocks_buy():
    positions = [make_position(code="511010", name="国债ETF", bucket="bond", qty=10000, price=2.0)]
    portfolio = make_portfolio(positions, cash=60000)
    target = _target(bond=0.2, gold=0.2, cash=0.6)
    quotes = dict(QUOTES)
    quotes["518880"] = InstrumentQuote("518880", "黄金ETF", AssetType.ETF, "gold", 5.0, chg_20d=0.3)
    result = plan(portfolio, target, quotes, UNIVERSE, RULES, pe_pct=50.0)
    assert not any(t.action.value == "BUY" and t.code == "518880" for t in result.trades)
    assert any(b.reason is ReasonCode.NO_CHASE for b in result.blocked)


def test_single_cap_clips_buy():
    positions = [make_position(code="511010", name="国债ETF", bucket="bond", qty=10000, price=2.0)]
    portfolio = make_portfolio(positions, cash=80000)
    target = _target(bond=0.4, cash=0.6)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    buys = [t for t in result.trades if t.code == "511010"]
    assert buys, "低配债券应买入"
    for trade in buys:
        assert trade.amount_est + 20000 <= RULES.caps["etf"] * portfolio.total_assets + 1e-6


def test_stop_loss_sells_position():
    positions = [
        make_position(
            code="600519", name="贵州茅台", type=AssetType.STOCK, bucket="stock",
            qty=100, price=1200.0, cost=1500.0, ma120=1400.0, pnl=-0.2,
        )
    ]
    portfolio = make_portfolio(positions, cash=88000)
    target = _target(stock=0.1, cash=0.9)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    assert any(t.reason is ReasonCode.STOP_LOSS for t in result.trades)


def test_fund_fee_lock_blocks_sell():
    positions = [
        make_position(
            code="110022", name="易方达消费", type=AssetType.FUND, bucket="equity_satellite",
            qty=20000, price=2.0, cost=1.8, bought_at=date(2026, 10, 29),
        )
    ]
    portfolio = make_portfolio(positions, cash=60000)
    target = _target(equity_satellite=0.0, cash=1.0)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    assert not any(t.code == "110022" for t in result.trades)
    assert any(b.reason is ReasonCode.FUND_FEE_LOCK for b in result.blocked)


def test_cash_floor_blocks_buys():
    positions = [
        make_position(code="510300", bucket="equity_core", qty=7500, price=4.0),
        make_position(code="512480", name="半导体ETF", bucket="equity_satellite", qty=30000, price=1.0),
        make_position(code="518880", name="黄金ETF", bucket="gold", qty=2000, price=5.0),
        make_position(code="511010", name="国债ETF", bucket="bond", qty=12500, price=2.0),
    ]
    portfolio = make_portfolio(positions, cash=5000)
    target = _target(equity_core=0.2, equity_satellite=0.05, gold=0.2, bond=0.2, cash=0.35)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    assert not any(t.action.value == "BUY" for t in result.trades)
    assert any(b.reason is ReasonCode.CASH_FLOOR for b in result.blocked)


def test_cash_after_matches_trades():
    positions = [
        make_position(code="510300", bucket="equity_core", qty=8000, price=4.0),
        make_position(code="518880", name="黄金ETF", bucket="gold", qty=4000, price=5.0),
    ]
    portfolio = make_portfolio(positions, cash=20000)
    target = _target(equity_core=0.2, gold=0.2, bond=0.2, cash=0.4)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    expected = portfolio.cash
    for trade in result.trades:
        expected += trade.amount_est if trade.action.value == "SELL" else -trade.amount_est
    assert result.cash_after == pytest.approx(expected)


def test_no_rebuy_after_stop_loss():
    from ia.config import UniverseItem

    positions = [
        make_position(
            code="600519", name="贵州茅台", type=AssetType.STOCK, bucket="stock",
            qty=100, price=1200.0, cost=1500.0, ma120=1400.0, pnl=-0.2,
        )
    ]
    portfolio = make_portfolio(positions, cash=200000)
    target = _target(stock=0.1, cash=0.9)
    universe = {"stock": (UniverseItem("600519", "贵州茅台", AssetType.STOCK, default=True),)}
    quotes = {
        "600519": InstrumentQuote("600519", "贵州茅台", AssetType.STOCK, "stock", 1200.0)
    }
    result = plan(portfolio, target, quotes, universe, RULES, pe_pct=50.0)
    assert any(t.reason is ReasonCode.STOP_LOSS for t in result.trades)
    assert not any(t.action.value == "BUY" and t.code == "600519" for t in result.trades)
    assert any(b.code == "600519" and b.reason is ReasonCode.STOP_LOSS for b in result.blocked)


def test_small_account_uses_adaptive_min_amount_and_turnover_floor():
    portfolio = make_portfolio([], cash=7400)
    target = _target(gold=0.25, cash=0.75)
    result = plan(portfolio, target, QUOTES, UNIVERSE, RULES, pe_pct=50.0)
    buys = [t for t in result.trades if t.code == "518880"]
    assert buys, "7,400 元的组合也应能买入黄金 ETF"
    traded = sum(t.amount_est for t in result.trades)
    assert traded <= RULES.rebalance.max_turnover_floor + 1e-6
    assert not any(b.reason is ReasonCode.MIN_AMOUNT for b in result.blocked)
