from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ia.calendar import TradeCalendar
from ia.config import PortfolioConfig, Rules, Settings, UniverseItem
from ia.features.indicators import change_over, sma
from ia.market.build import (
    build_market_snapshot,
    build_portfolio,
    last_completed_trading_day,
)
from ia.market.source import DataSource
from ia.models import (
    Advice,
    AssetType,
    Bar,
    MarketSnapshot,
    Portfolio,
    Regime,
    RegimeResult,
    ScanResult,
    TargetWeights,
)
from ia.rules import rebalance, risk_scan, target as target_rules
from ia.rules.guards import InstrumentQuote
from ia.rules.regime import classify
from ia.rules.rebalance import RebalancePlan

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisContext:
    settings: Settings
    rules: Rules
    portfolio_cfg: PortfolioConfig
    universe: dict[str, tuple[UniverseItem, ...]]
    source: DataSource
    calendar: TradeCalendar
    portfolio: Portfolio
    market: MarketSnapshot
    regime: RegimeResult
    target: TargetWeights
    notes: tuple[str, ...]

    @property
    def data_stale(self) -> bool:
        if any(p.stale for p in self.portfolio.positions):
            return True
        return self.market.data_date != self.portfolio.as_of


def build_context(
    settings: Settings,
    rules: Rules,
    portfolio_cfg: PortfolioConfig,
    universe: dict[str, tuple[UniverseItem, ...]],
    source: DataSource,
    calendar: TradeCalendar,
    now: datetime | None = None,
) -> AnalysisContext:
    portfolio, notes = build_portfolio(portfolio_cfg, source, calendar, now)
    market = build_market_snapshot(source, rules, calendar, now)
    regime = classify(
        close=market.index_close,
        ma20=market.ma20,
        ma60=market.ma60,
        ma200=market.ma200,
        vol_pct=market.vol_pct,
        pe_pct=market.pe_pct,
        rate_chg_60d_bp=market.rate_chg_60d_bp,
        chg_20d=market.index_chg_20d / 100,
        thresholds=rules.regime_thresholds,
    )
    target = target_rules.compute(rules, regime, market.pe_pct)
    extra = list(notes)
    if regime.override:
        extra.append("触发急跌保护：20 日跌幅超过阈值且跌破 MA200，直接按防守处理")
    return AnalysisContext(
        settings=settings,
        rules=rules,
        portfolio_cfg=portfolio_cfg,
        universe=universe,
        source=source,
        calendar=calendar,
        portfolio=portfolio,
        market=market,
        regime=regime,
        target=target,
        notes=tuple(extra),
    )


def _default_items(ctx: AnalysisContext) -> list[UniverseItem]:
    items: list[UniverseItem] = []
    for bucket in ("equity_core", "equity_satellite", "gold", "bond"):
        items.extend(item for item in ctx.universe.get(bucket, ()) if item.default)
    return items


def build_quotes(ctx: AnalysisContext, held_codes: set[str]) -> dict[str, InstrumentQuote]:
    quotes: dict[str, InstrumentQuote] = {}
    for item in _default_items(ctx):
        if item.code in held_codes:
            continue
        try:
            bars = _bars_for(item.type, item.code, ctx.source)
            if not bars:
                continue
            closes = [b.close for b in bars]
            quotes[item.code] = InstrumentQuote(
                code=item.code,
                name=item.name,
                type=item.type,
                bucket=_bucket_of(ctx, item.code) or "equity_core",
                price=closes[-1],
                ma20=sma(closes, 20),
                ma120=sma(closes, 120),
                chg_20d=change_over(closes, 20),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("获取 %s %s 行情失败: %s", item.code, item.name, exc)
    return quotes


def _bucket_of(ctx: AnalysisContext, code: str) -> str | None:
    for bucket, items in ctx.universe.items():
        if any(item.code == code for item in items):
            return bucket
    return None


def _bars_for(asset_type: AssetType, code: str, source: DataSource) -> tuple[Bar, ...]:
    if asset_type is AssetType.STOCK:
        return source.stock_bars(code, rows=160)
    if asset_type is AssetType.ETF:
        return source.etf_bars(code, rows=160)
    return source.fund_nav(code, rows=160)


def build_weekly_advice(ctx: AnalysisContext) -> Advice:
    held = {p.code for p in ctx.portfolio.positions}
    quotes = build_quotes(ctx, held)
    if ctx.data_stale:
        return Advice(
            as_of=ctx.portfolio.as_of,
            portfolio=ctx.portfolio,
            market=ctx.market,
            regime=ctx.regime,
            target=ctx.target,
            trades=(),
            blocked=(),
            cash_after=ctx.portfolio.cash,
            data_stale=True,
            notes=ctx.notes + ("数据不新鲜，本轮不生成操作建议",),
        )

    plan: RebalancePlan = rebalance.plan(
        portfolio=ctx.portfolio,
        target=ctx.target,
        quotes=quotes,
        universe=ctx.universe,
        rules=ctx.rules,
        pe_pct=ctx.market.pe_pct,
    )
    notes = list(ctx.notes)
    if not plan.trades:
        notes.append("本周没有需要执行的调整，保持现有仓位")
    return Advice(
        as_of=ctx.portfolio.as_of,
        portfolio=ctx.portfolio,
        market=ctx.market,
        regime=ctx.regime,
        target=ctx.target,
        trades=plan.trades,
        blocked=plan.blocked,
        cash_after=plan.cash_after,
        data_stale=False,
        notes=tuple(notes),
    )


def build_daily_scan(
    ctx: AnalysisContext,
    prev_regime: Regime | None,
    now: datetime | None = None,
) -> ScanResult:
    expected = last_completed_trading_day(ctx.calendar, now)
    nav_deadline = ctx.calendar.prev_trading_day(expected)
    return risk_scan.evaluate(
        portfolio=ctx.portfolio,
        market=None if ctx.data_stale else ctx.market,
        target=ctx.target,
        rules=ctx.rules,
        prev_regime=prev_regime,
        expected_date=expected,
        nav_deadline=nav_deadline,
    )


def projected_bucket_weights(portfolio: Portfolio, advice: Advice) -> dict[str, float]:
    values = {bucket: 0.0 for bucket in ("equity_core", "equity_satellite", "stock", "gold", "bond", "cash")}
    for pos in portfolio.positions:
        values[pos.bucket] += pos.market_value
    values["cash"] = portfolio.cash
    for trade in advice.trades:
        delta = trade.amount_est if trade.action.value == "SELL" else -trade.amount_est
        if trade.action.value == "BUY":
            values[trade.bucket] += trade.amount_est
        else:
            values[trade.bucket] -= trade.amount_est
        values["cash"] += delta
    total = sum(values.values())
    return {bucket: value / total if total else 0.0 for bucket, value in values.items()}
