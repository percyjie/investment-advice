from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from ia.config import Rules
from ia.models import AssetType, Position


@dataclass(frozen=True)
class InstrumentQuote:
    code: str
    name: str
    type: AssetType
    bucket: str
    price: float
    ma20: float | None = None
    ma120: float | None = None
    chg_20d: float | None = None


def fund_fee_locked(position: Position, as_of: date, rules: Rules) -> bool:
    if position.type is not AssetType.FUND or position.bought_at is None:
        return False
    return (as_of - position.bought_at).days < rules.guards.fund_fee_lock_days


def is_chasing(quote: InstrumentQuote | Position, rules: Rules) -> bool:
    chg_20d = getattr(quote, "chg_20d", None)
    ma20 = getattr(quote, "ma20", None)
    if chg_20d is not None and chg_20d > rules.guards.no_chase_20d:
        return True
    if ma20 and ma20 > 0 and quote.price / ma20 - 1 > rules.guards.no_chase_ma20:
        return True
    return False


def is_stop_loss(position: Position, rules: Rules) -> bool:
    guards = rules.guards
    if position.bucket in set(guards.stop_loss_exempt_buckets):
        return False
    if position.pnl_pct > guards.stop_loss_loss:
        return False
    if position.ma120 is None:
        return False
    return position.price < position.ma120


def single_cap(asset_type: AssetType, rules: Rules) -> float:
    return rules.caps[asset_type.value]


def round_buy(asset_type: AssetType, desired_amount: float, price: float, rules: Rules) -> tuple[float, float]:
    """返回 (qty, amount)。场外基金按元计价，股票/ETF 按份数计价。"""
    if desired_amount <= 0 or price <= 0:
        return 0.0, 0.0
    unit = rules.rounding[asset_type.value]
    if asset_type is AssetType.FUND:
        amount = math.floor(desired_amount / unit) * unit
        return amount, amount
    shares = math.floor(desired_amount / price / unit) * unit
    return shares, shares * price


def round_sell(
    asset_type: AssetType,
    desired_amount: float,
    price: float,
    held_qty: float,
    rules: Rules,
) -> tuple[float, float]:
    if desired_amount <= 0 or price <= 0:
        return 0.0, 0.0
    unit = rules.rounding[asset_type.value]
    if asset_type is AssetType.FUND:
        amount = min(math.floor(desired_amount / unit) * unit, math.floor(held_qty * price))
        return amount, amount
    shares = math.floor(min(desired_amount, held_qty * price) / price / unit) * unit
    shares = min(shares, held_qty)
    return shares, shares * price
