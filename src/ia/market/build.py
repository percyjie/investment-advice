from __future__ import annotations

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ia.calendar import TradeCalendar
from ia.config import PortfolioConfig, Rules, Settings
from ia.features.indicators import annualized_volatility, change_over, percentile_rank, sma
from ia.features.valuation import pe_percentile
from ia.market.source import DataSource
from ia.models import (
    AssetType,
    Bar,
    MarketSnapshot,
    Portfolio,
    Position,
)

log = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")


def last_completed_trading_day(calendar: TradeCalendar, now: datetime | None = None) -> date:
    now = now or datetime.now(SH)
    today = now.date()
    if calendar.is_trading_day(today) and now.time() >= time(15, 30):
        return today
    return calendar.prev_trading_day(today)


def _closes(bars: tuple[Bar, ...]) -> list[float]:
    return [b.close for b in bars]


def _bars_for(pos_type: AssetType, code: str, source: DataSource) -> tuple[Bar, ...]:
    if pos_type is AssetType.STOCK:
        return source.stock_bars(code, rows=160)
    if pos_type is AssetType.ETF:
        return source.etf_bars(code, rows=160)
    return source.fund_nav(code, rows=160)


def build_portfolio(
    config: PortfolioConfig,
    source: DataSource,
    calendar: TradeCalendar,
    now: datetime | None = None,
) -> tuple[Portfolio, list[str]]:
    expected = last_completed_trading_day(calendar, now)
    notes: list[str] = []
    raw: list[dict] = []

    for holding in config.holdings:
        bars = _bars_for(holding.type, holding.code, source)
        if not bars:
            raise RuntimeError(f"{holding.code} {holding.name} 没有行情数据")
        closes = _closes(bars)
        price = closes[-1]
        price_date = bars[-1].day
        qty = holding.qty
        if qty <= 0 and holding.amount:
            qty = holding.amount / price
        cost = holding.cost if holding.cost > 0 else price
        cost_estimated = holding.cost <= 0
        market_value = qty * price
        if cost_estimated:
            notes.append(f"{holding.name} 未提供成本价，浮盈按 0 计")
        if holding.amount and holding.qty <= 0:
            notes.append(f"{holding.name} 按市值 {holding.amount:,.0f} 元折算 {qty:,.2f} 份")

        if holding.type is AssetType.FUND:
            nav_date = price_date
            stale = nav_date < calendar.prev_trading_day(expected)
            if stale:
                notes.append(f"{holding.name} 净值停留在 {nav_date}，数据偏旧")
        else:
            nav_date = None
            stale = price_date < expected
            if stale:
                notes.append(f"{holding.name} 行情停留在 {price_date}，数据偏旧")

        raw.append(
            {
                "holding": holding,
                "qty": qty,
                "cost": cost,
                "price": price,
                "price_date": price_date,
                "market_value": market_value,
                "closes": closes,
                "nav_date": nav_date,
                "stale": stale,
            }
        )

    total = sum(r["market_value"] for r in raw) + config.cash
    if total <= 0:
        raise RuntimeError("总资产为 0，无法计算")

    positions: list[Position] = []
    for item in raw:
        holding = item["holding"]
        closes = item["closes"]
        price = item["price"]
        qty = item["qty"]
        cost = item["cost"]
        positions.append(
            Position(
                code=holding.code,
                name=holding.name,
                type=holding.type,
                bucket=holding.bucket,
                qty=qty,
                cost=cost,
                price=price,
                price_date=item["price_date"],
                market_value=item["market_value"],
                weight=item["market_value"] / total,
                pnl_pct=(price / cost - 1) if cost > 0 else 0.0,
                ma20=sma(closes, 20),
                ma120=sma(closes, 120),
                chg_20d=change_over(closes, 20),
                bought_at=holding.bought_at,
                nav_date=item["nav_date"],
                stale=item["stale"],
            )
        )

    portfolio = Portfolio(
        as_of=expected,
        cash=config.cash,
        positions=tuple(positions),
        total_assets=total,
        is_example=config.is_example,
    )
    if config.is_example:
        notes.append("当前是示例持仓，请替换 config/portfolio.yaml")
    return portfolio, notes


def build_market_snapshot(
    source: DataSource,
    rules: Rules,
    calendar: TradeCalendar,
    now: datetime | None = None,
) -> MarketSnapshot:
    expected = last_completed_trading_day(calendar, now)

    index_bars = source.index_bars("000300", rows=500)
    if len(index_bars) < 250:
        raise RuntimeError("沪深300 历史数据不足 250 个交易日")
    closes = _closes(index_bars)
    latest = index_bars[-1]

    vol_20d = annualized_volatility(closes, 20) or 0.0
    vol_history = []
    for end in range(20, len(closes) + 1):
        value = annualized_volatility(closes[:end], 20)
        if value is not None:
            vol_history.append(value)
    vol_pct = percentile_rank(vol_history[-252:], vol_20d) or 50.0

    pe_bars = source.pe_history("沪深300")
    pe_pct = pe_percentile(pe_bars, years=10) or 50.0

    rate_bars = source.rate_10y()
    rate_10y = rate_bars[-1].close if rate_bars else 0.0
    rate_chg_60d = (rate_bars[-1].close - rate_bars[-61].close) if len(rate_bars) > 61 else 0.0
    rate_chg_5d = (rate_bars[-1].close - rate_bars[-6].close) if len(rate_bars) > 6 else 0.0

    board_top: tuple = ()
    board_bottom: tuple = ()
    board_flow: tuple = ()
    try:
        boards = source.industry_snapshot()
        board_top = boards[:10]
        board_bottom = boards[-5:][::-1]
    except Exception as exc:  # noqa: BLE001
        log.warning("行业板块数据失败: %s", exc)
    try:
        board_flow = source.sector_fund_flow("5日")[:10]
    except Exception as exc:  # noqa: BLE001
        log.warning("板块资金流失败: %s", exc)
    if not board_top and board_flow:
        ordered = sorted(board_flow, key=lambda b: b.chg_pct, reverse=True)
        board_top = tuple(ordered[:10])
        board_bottom = tuple(ordered[-5:][::-1])

    prev_close = closes[-2] if len(closes) >= 2 else latest.close
    chg_pct = (latest.close / prev_close - 1) * 100 if prev_close else 0.0

    snapshot = MarketSnapshot(
        as_of=expected,
        index_close=latest.close,
        index_chg_pct=chg_pct,
        index_chg_20d=(change_over(closes, 20) or 0.0) * 100,
        ma20=sma(closes, 20) or latest.close,
        ma60=sma(closes, 60) or latest.close,
        ma200=sma(closes, 200) or latest.close,
        vol_20d=vol_20d,
        vol_pct=vol_pct,
        pe_pct=pe_pct,
        rate_10y=rate_10y,
        rate_chg_60d_bp=rate_chg_60d * 100,
        rate_chg_5d_bp=rate_chg_5d * 100,
        board_top=board_top,
        board_bottom=board_bottom,
        board_flow_top=board_flow,
        data_date=latest.day,
    )
    if latest.day < expected:
        log.warning("指数行情停留在 %s，预期 %s", latest.day, expected)
    return snapshot
