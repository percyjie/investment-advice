from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from ia.config import PortfolioConfig
from ia.market.build import build_portfolio
from ia.models import AssetType, Bar, Holding


class FakeCalendar:
    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5

    def prev_trading_day(self, day: date) -> date:
        cursor = day - timedelta(days=1)
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        return cursor


class FakeSource:
    def _bars(self) -> tuple[Bar, ...]:
        start = date(2026, 10, 29)
        return tuple(Bar(start - timedelta(days=i), 1.2) for i in range(30))[::-1]

    def fund_nav(self, code: str, rows: int = 160):
        return self._bars()

    def stock_bars(self, code: str, rows: int = 160):
        return self._bars()

    def etf_bars(self, code: str, rows: int = 160):
        return self._bars()


def test_amount_based_fund_holding():
    holding = Holding(
        code="019305",
        name="标普500",
        type=AssetType.FUND,
        bucket="equity_core",
        qty=0,
        cost=0,
        amount=1200,
    )
    config = PortfolioConfig(cash=2000, holdings=(holding,), is_example=False)
    portfolio, notes = build_portfolio(
        config, FakeSource(), FakeCalendar(), now=datetime(2026, 10, 30, 8, 30)
    )
    pos = portfolio.positions[0]
    assert pos.qty == pytest.approx(1000)
    assert pos.cost == pytest.approx(1.2)
    assert pos.market_value == pytest.approx(1200)
    assert pos.pnl_pct == 0
    assert portfolio.total_assets == pytest.approx(3200)
    assert any("未提供成本价" in note for note in notes)
