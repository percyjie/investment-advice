from __future__ import annotations

from dataclasses import replace
from datetime import date

from ia.models import AssetType, Portfolio, Position


def make_position(
    code: str = "510300",
    name: str = "沪深300ETF",
    type: AssetType = AssetType.ETF,
    bucket: str = "equity_core",
    qty: float = 1000,
    cost: float = 3.5,
    price: float = 4.0,
    ma20: float | None = None,
    ma120: float | None = None,
    chg_20d: float | None = None,
    pnl: float | None = None,
    bought_at: date | None = None,
    stale: bool = False,
) -> Position:
    market_value = qty * price
    pnl_pct = pnl if pnl is not None else (price / cost - 1 if cost else 0.0)
    return Position(
        code=code,
        name=name,
        type=type,
        bucket=bucket,
        qty=qty,
        cost=cost,
        price=price,
        price_date=date(2026, 10, 30),
        market_value=market_value,
        weight=0.0,
        pnl_pct=pnl_pct,
        ma20=ma20,
        ma120=ma120,
        chg_20d=chg_20d,
        bought_at=bought_at,
        stale=stale,
    )


def make_portfolio(positions: list[Position], cash: float, as_of: date = date(2026, 10, 30)) -> Portfolio:
    total = sum(p.market_value for p in positions) + cash
    weighted = tuple(replace(p, weight=p.market_value / total if total else 0.0) for p in positions)
    return Portfolio(as_of=as_of, cash=cash, positions=weighted, total_assets=total)
