from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ia.models import (
    BUCKET_LABELS,
    BUCKET_ORDER,
    Advice,
    HotspotResult,
    Level,
    ReasonCode,
    Report,
    ReportSection,
    ScanResult,
    Trade,
)
from ia.rules.engine import AnalysisContext, projected_bucket_weights

SH = ZoneInfo("Asia/Shanghai")


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _trade_line(trade: Trade) -> str:
    if trade.type.value == "fund":
        size = f"{_money(trade.amount_est)} 元"
    else:
        unit = "股" if trade.type.value == "stock" else "份"
        size = f"{_money(trade.qty)} {unit}（约 {_money(trade.amount_est)} 元）"
    note = f" · {trade.note}" if trade.note else ""
    return f"{trade.action.label} {trade.name} {size}{note}"


def _footer(ctx: AnalysisContext, prefix: str = "") -> str:
    parts = [f"数据截止 {ctx.portfolio.as_of.isoformat()}"]
    if ctx.portfolio.is_example:
        parts.append("示例持仓")
    parts.append("仅供参考，不构成投资建议")
    text = "｜".join(parts)
    return f"{prefix}{text}"


def build_weekly_report(ctx: AnalysisContext, advice: Advice, hotspot: HotspotResult | None) -> Report:
    sells = [t for t in advice.trades if t.action.value == "SELL"]
    buys = [t for t in advice.trades if t.action.value == "BUY"]
    buy_amount = sum(t.amount_est for t in buys)
    sell_amount = sum(t.amount_est for t in sells)

    if advice.data_stale:
        level = Level.RED
        headline = "数据异常，本周暂停给出操作建议"
    else:
        level = Level.YELLOW if ctx.regime.regime.value == "risk_off" else Level.GREEN
        if ctx.regime.override:
            regime_text = f"防守（急跌保护，评分 {ctx.regime.score:+d}）"
        else:
            regime_text = f"{ctx.regime.regime.label}（评分 {ctx.regime.score:+d}）"
        if advice.trades:
            parts = []
            if buy_amount:
                parts.append(f"买入 {_money(buy_amount)} 元")
            if sell_amount:
                parts.append(f"卖出 {_money(sell_amount)} 元")
            headline = f"{regime_text} · 本周 {len(advice.trades)} 笔操作：{'，'.join(parts)}"
        else:
            headline = f"{regime_text} · 仓位在目标区间内，本周无需操作"

    sections: list[ReportSection] = []

    if advice.trades:
        sections.append(ReportSection("本周操作", tuple(_trade_line(t) for t in advice.trades[:5])))
    else:
        sections.append(ReportSection("本周操作", ("无操作，保持现有仓位",)))

    if advice.blocked:
        blocked_lines = tuple(f"{b.name}：{b.reason.label}（{b.note}）" for b in advice.blocked[:3])
        sections.append(ReportSection("被风控拦截", blocked_lines))

    projected = projected_bucket_weights(ctx.portfolio, advice)
    actual = ctx.portfolio.bucket_weights()
    bucket_lines = []
    for bucket in BUCKET_ORDER:
        label = BUCKET_LABELS[bucket]
        line = f"{label} {actual[bucket] * 100:.0f}% → 目标 {ctx.target.get(bucket) * 100:.0f}%"
        if bucket != "cash":
            line += f"（预计 {projected[bucket] * 100:.0f}%）"
        bucket_lines.append(line)
    sections.append(ReportSection("仓位对照", tuple(bucket_lines)))

    risk_lines: list[str] = list(advice.notes[:3])
    if advice.cash_after < ctx.rules.rebalance.cash_floor * ctx.portfolio.total_assets:
        risk_lines.insert(0, "交易后现金低于底线，已自动截断买入")
    if not risk_lines:
        risk_lines = ["无明显风险项"]
    sections.append(ReportSection("风险提示", tuple(risk_lines)))

    if hotspot is not None:
        if hotspot.available:
            lines = []
            if hotspot.market_summary:
                lines.append(hotspot.market_summary)
            for topic in hotspot.topics[:3]:
                related = "、".join(name for _, name, _ in topic.related[:2])
                tail = f"｜关联 {related}" if related else ""
                lines.append(f"{topic.name}（{topic.direction_label}）：{topic.summary}{tail}")
            sections.append(ReportSection("行业热点", tuple(lines[:4])))
        else:
            sections.append(ReportSection("行业热点", (f"本轮未生成：{hotspot.error or '模型不可用'}",)))

    sections.append(
        ReportSection(
            "市场状态",
            (
                f"沪深300 {ctx.market.index_close:,.0f}（{ctx.market.index_chg_pct:+.2f}%）"
                f"｜PE 十年分位 {ctx.market.pe_pct:.0f}%"
                f"｜20日波动率分位 {ctx.market.vol_pct:.0f}%"
                f"｜10Y 国债 {ctx.market.rate_10y:.2f}%",
            ),
        )
    )

    return Report(
        kind="weekly",
        title="投资周报",
        level=level,
        headline=headline,
        sections=tuple(sections),
        footer=_footer(ctx),
        as_of=ctx.portfolio.as_of,
        generated_at=datetime.now(SH),
    )


def build_daily_report(ctx: AnalysisContext, scan: ScanResult) -> Report:
    if scan.alerts:
        headline = f"{scan.level.badge} {scan.level.label}｜{len(scan.alerts)} 条提示"
        if scan.should_act():
            headline += "，有需要处理的风险"
        else:
            headline += "，无需紧急操作"
    else:
        headline = "🟢 一切正常，无需操作"

    sections: list[ReportSection] = []
    if scan.alerts:
        lines = []
        for alert in scan.alerts[:5]:
            hint = f"｜{alert.action_hint}" if alert.action_hint else ""
            lines.append(f"{alert.level.badge} {alert.title}：{alert.detail}{hint}")
        sections.append(ReportSection("风险提示", tuple(lines)))
    else:
        sections.append(ReportSection("风险提示", ("所有监控项正常",)))

    if scan.market is not None:
        market = scan.market
        regime_text = (
            f"防守（急跌保护，评分 {ctx.regime.score:+d}）"
            if ctx.regime.override
            else f"{ctx.regime.regime.label}（评分 {ctx.regime.score:+d}）"
        )
        sections.append(
            ReportSection(
                "市场状态",
                (
                    f"{regime_text}"
                    f"｜沪深300 {market.index_chg_pct:+.2f}%"
                    f"｜PE 分位 {market.pe_pct:.0f}%"
                    f"｜波动率分位 {market.vol_pct:.0f}%"
                    f"｜10Y {market.rate_10y:.2f}%",
                ),
            )
        )

    actual = ctx.portfolio.bucket_weights()
    equity = sum(actual[b] for b in ("equity_core", "equity_satellite", "stock"))
    worst = min(ctx.portfolio.positions, key=lambda p: p.pnl_pct, default=None)
    holding_line = f"权益 {equity * 100:.0f}%｜现金 {ctx.portfolio.cash_ratio * 100:.0f}%"
    if worst is not None:
        holding_line += f"｜最大浮亏 {worst.name} {worst.pnl_pct * 100:+.1f}%"
    sections.append(ReportSection("持仓概况", (holding_line,)))

    return Report(
        kind="daily",
        title="每日风险扫描",
        level=scan.level,
        headline=headline,
        sections=tuple(sections),
        footer=_footer(ctx, prefix="基于上一交易日收盘｜"),
        as_of=scan.as_of,
        generated_at=datetime.now(SH),
    )
