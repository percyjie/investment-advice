from __future__ import annotations

from datetime import date

from ia.config import Rules
from ia.models import (
    BUCKET_LABELS,
    Alert,
    Level,
    MarketSnapshot,
    Portfolio,
    Regime,
    RegimeResult,
    ScanResult,
    TargetWeights,
)


def _level_rank(level: Level) -> int:
    return {Level.GREEN: 0, Level.YELLOW: 1, Level.RED: 2}[level]


def evaluate(
    portfolio: Portfolio,
    market: MarketSnapshot | None,
    target: TargetWeights,
    rules: Rules,
    prev_regime: Regime | None,
    expected_date: date,
    nav_deadline: date,
) -> ScanResult:
    cfg = rules.risk_scan
    alerts: list[Alert] = []

    actual = portfolio.bucket_weights()
    equity_actual = sum(actual[b] for b in ("equity_core", "equity_satellite", "stock"))
    equity_target = sum(target.get(b) for b in ("equity_core", "equity_satellite", "stock"))
    dev = equity_actual - equity_target
    if abs(dev) >= cfg.equity_deviation.critical:
        alerts.append(
            Alert(
                "equity_deviation",
                Level.RED,
                "权益仓位大幅偏离",
                f"实际 {equity_actual * 100:.0f}%，目标 {equity_target * 100:.0f}%（偏离 {dev * 100:+.0f}pp）",
                "本周优先调仓，先减超配",
            )
        )
    elif abs(dev) >= cfg.equity_deviation.warn:
        alerts.append(
            Alert(
                "equity_deviation",
                Level.YELLOW,
                "权益仓位偏离",
                f"实际 {equity_actual * 100:.0f}%，目标 {equity_target * 100:.0f}%（偏离 {dev * 100:+.0f}pp）",
                "周报会给出调仓清单",
            )
        )

    if portfolio.cash_ratio < cfg.cash_ratio.critical:
        alerts.append(
            Alert(
                "cash_ratio",
                Level.RED,
                "现金不足",
                f"现金占比 {portfolio.cash_ratio * 100:.1f}%，低于底线 {cfg.cash_ratio.critical * 100:.0f}%",
                "暂停买入，优先补充现金",
            )
        )
    elif portfolio.cash_ratio < cfg.cash_ratio.warn:
        alerts.append(
            Alert(
                "cash_ratio",
                Level.YELLOW,
                "现金偏低",
                f"现金占比 {portfolio.cash_ratio * 100:.1f}%，低于警戒线 {cfg.cash_ratio.warn * 100:.0f}%",
                "暂缓加仓",
            )
        )

    current_regime = target.regime
    if prev_regime is not None and current_regime is not prev_regime:
        if current_regime is Regime.RISK_OFF:
            alerts.append(
                Alert(
                    "regime_switch",
                    Level.RED,
                    "市场状态转为防守",
                    f"{prev_regime.label} → {current_regime.label}（评分 {target.score:+d}）",
                    "降低行业/个股暴露，周报给出减仓方案",
                )
            )
        elif prev_regime is Regime.RISK_OFF:
            alerts.append(
                Alert(
                    "regime_switch",
                    Level.YELLOW,
                    "市场状态脱离防守",
                    f"{prev_regime.label} → {current_regime.label}（评分 {target.score:+d}）",
                    "可逐步恢复权益仓位",
                )
            )

    for pos in portfolio.positions:
        if pos.bucket == "stock" and pos.weight > cfg.single_stock:
            alerts.append(
                Alert(
                    "single_stock",
                    Level.YELLOW,
                    f"个股集中度偏高：{pos.name}",
                    f"占比 {pos.weight * 100:.1f}%，上限 {cfg.single_stock * 100:.0f}%",
                    "减至上限以内",
                )
            )

        if pos.pnl_pct <= cfg.loss_warn:
            red = pos.ma120 is not None and pos.price < pos.ma120 and pos.bucket not in set(
                rules.guards.stop_loss_exempt_buckets
            )
            alerts.append(
                Alert(
                    "position_loss",
                    Level.RED if red else Level.YELLOW,
                    f"{pos.name} 浮亏 {pos.pnl_pct * 100:.1f}%",
                    "跌破 MA120，触发止损条件" if red else "接近止损观察区",
                    "周报确认是否止损" if red else "关注，不急于操作",
                )
            )

        if pos.chg_20d is not None and pos.chg_20d > cfg.overheat_20d and pos.weight > cfg.single_stock:
            alerts.append(
                Alert(
                    "overheat",
                    Level.YELLOW,
                    f"{pos.name} 短期过热",
                    f"20 日涨幅 {pos.chg_20d * 100:.1f}%，占比 {pos.weight * 100:.1f}%",
                    "禁止追涨，可部分止盈",
                )
            )

    if market is not None:
        if market.index_chg_pct <= cfg.index_day_drop.critical:
            alerts.append(
                Alert(
                    "index_drop",
                    Level.RED,
                    "指数单日大跌",
                    f"沪深300 {market.index_chg_pct:+.2f}%",
                    "不自动操作，等待周报",
                )
            )
        elif market.index_chg_pct <= cfg.index_day_drop.warn:
            alerts.append(
                Alert(
                    "index_drop",
                    Level.YELLOW,
                    "指数明显下跌",
                    f"沪深300 {market.index_chg_pct:+.2f}%",
                    "按纪律执行，不追跌",
                )
            )
        if market.pe_pct > cfg.pe_pct:
            alerts.append(
                Alert(
                    "valuation_high",
                    Level.YELLOW,
                    "估值处于高位",
                    f"沪深300 PE 十年分位 {market.pe_pct:.0f}%",
                    "暂停权益加仓",
                )
            )
        if market.rate_chg_5d_bp > cfg.rate_chg_5d_bp:
            alerts.append(
                Alert(
                    "rate_move",
                    Level.YELLOW,
                    "利率快速上行",
                    f"10Y 国债 5 日上行 {market.rate_chg_5d_bp:.0f}bp",
                    "关注债基久期风险",
                )
            )
        if market.board_bottom:
            worst = market.board_bottom[0]
            for pos in portfolio.positions:
                if worst.name in pos.name and pos.weight > cfg.board_exposure:
                    alerts.append(
                        Alert(
                            "board_selloff",
                            Level.YELLOW,
                            f"持仓板块领跌：{worst.name}",
                            f"板块跌幅 {worst.chg_pct:+.2f}%，持仓暴露 {pos.weight * 100:.1f}%",
                            "复盘，不自动卖出",
                        )
                    )
                    break

    stale = [p for p in portfolio.positions if p.stale]
    if market is None or market.data_date != expected_date or stale:
        detail = []
        if market is None:
            detail.append("指数数据缺失")
        elif market.data_date != expected_date:
            detail.append(f"指数停留在 {market.data_date}")
        if stale:
            detail.append("持仓：" + "、".join(p.name for p in stale))
        alerts.insert(
            0,
            Alert(
                "data_stale",
                Level.RED,
                "数据不新鲜",
                "；".join(detail),
                "本轮建议不可信，先修数据",
            ),
        )

    for pos in portfolio.positions:
        if pos.nav_date is not None and pos.nav_date < nav_deadline:
            alerts.append(
                Alert(
                    "nav_lag",
                    Level.YELLOW,
                    f"{pos.name} 净值滞后",
                    f"最新净值日期 {pos.nav_date}，应为 {nav_deadline} 之后",
                    "按最新可得净值计算，注意偏差",
                )
            )

    alerts.sort(key=lambda a: _level_rank(a.level), reverse=True)
    level = Level.GREEN
    for alert in alerts:
        if _level_rank(alert.level) > _level_rank(level):
            level = alert.level
    return ScanResult(
        as_of=expected_date,
        level=level,
        alerts=tuple(alerts),
        regime=RegimeResult(regime=target.regime, score=target.score, votes=()),
        market=market,
    )


def summarize_buckets(portfolio: Portfolio, target: TargetWeights) -> list[str]:
    actual = portfolio.bucket_weights()
    lines = []
    for bucket, label in BUCKET_LABELS.items():
        lines.append(f"{label} {actual[bucket] * 100:.0f}% → {target.get(bucket) * 100:.0f}%")
    return lines
