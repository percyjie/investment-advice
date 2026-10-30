from __future__ import annotations

from datetime import date

import pytest

from ia.config import PROJECT_ROOT, load_rules
from ia.models import AssetType
from ia.rules.guards import fund_fee_locked, is_chasing, is_stop_loss, round_buy, round_sell

RULES = load_rules(PROJECT_ROOT / "config" / "rules.yaml")


def test_round_buy_etf_to_100_shares():
    qty, amount = round_buy(AssetType.ETF, 10000, 3.85, RULES)
    assert qty == 2500
    assert amount == pytest.approx(9625)


def test_round_buy_fund_to_100_yuan():
    qty, amount = round_buy(AssetType.FUND, 4350, 1.234, RULES)
    assert qty == 4300
    assert amount == 4300


def test_round_buy_too_small():
    qty, amount = round_buy(AssetType.STOCK, 5000, 1500, RULES)
    assert qty == 0
    assert amount == 0


def test_round_sell_capped_by_holding():
    qty, amount = round_sell(AssetType.ETF, 99999, 4.0, 1000, RULES)
    assert qty == 1000
    assert amount == pytest.approx(4000)


def test_round_sell_fund_is_amount_based():
    qty, amount = round_sell(AssetType.FUND, 4350, 1.234, 10000, RULES)
    assert qty == 4300
    assert amount == 4300


def test_fund_fee_lock():
    from conftest import make_position

    pos = make_position(type=AssetType.FUND, bucket="bond", bought_at=date(2026, 10, 28))
    assert fund_fee_locked(pos, date(2026, 10, 30), RULES) is True
    assert fund_fee_locked(pos, date(2026, 11, 10), RULES) is False
    etf = make_position(bought_at=date(2026, 10, 28))
    assert fund_fee_locked(etf, date(2026, 10, 30), RULES) is False


def test_stop_loss_requires_loss_and_ma():
    from conftest import make_position

    losing = make_position(bucket="stock", price=8.0, cost=10.0, ma120=9.0, pnl=-0.2)
    assert is_stop_loss(losing, RULES) is True
    above_ma = make_position(bucket="stock", price=9.5, cost=10.0, ma120=9.0, pnl=-0.05)
    assert is_stop_loss(above_ma, RULES) is False
    gold = make_position(bucket="gold", price=8.0, cost=10.0, ma120=9.0, pnl=-0.2)
    assert is_stop_loss(gold, RULES) is False


def test_no_chase():
    from conftest import make_position

    hot = make_position(chg_20d=0.2)
    assert is_chasing(hot, RULES) is True
    far_above_ma = make_position(price=11.2, ma20=10.0, chg_20d=0.05)
    assert is_chasing(far_above_ma, RULES) is True
    calm = make_position(price=10.0, ma20=10.0, chg_20d=0.02)
    assert is_chasing(calm, RULES) is False
