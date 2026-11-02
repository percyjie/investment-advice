from __future__ import annotations

from dataclasses import dataclass, replace

from ia.config import Rules, UniverseItem
from ia.models import (
    BUCKET_LABELS,
    BUCKET_ORDER,
    EQUITY_BUCKETS,
    Action,
    BlockedTrade,
    Portfolio,
    Position,
    ReasonCode,
    TargetWeights,
    Trade,
)
from ia.rules.guards import (
    InstrumentQuote,
    fund_fee_locked,
    is_chasing,
    is_stop_loss,
    round_buy,
    round_sell,
    single_cap,
)


@dataclass(frozen=True)
class RebalancePlan:
    trades: tuple[Trade, ...]
    blocked: tuple[BlockedTrade, ...]
    cash_after: float


def _bucket_value(positions: dict[str, Position], bucket: str) -> float:
    return sum(p.market_value for p in positions.values() if p.bucket == bucket)


def plan(
    portfolio: Portfolio,
    target: TargetWeights,
    quotes: dict[str, InstrumentQuote],
    universe: dict[str, tuple[UniverseItem, ...]],
    rules: Rules,
    pe_pct: float | None,
) -> RebalancePlan:
    total = portfolio.total_assets
    cash = portfolio.cash
    as_of = portfolio.as_of
    cfg = rules.rebalance
    min_amount = max(200.0, min(cfg.min_amount, total * 0.02))

    trades: list[Trade] = []
    blocked: list[BlockedTrade] = []
    blocked_seen: set[tuple[str, str]] = set()

    def block(code: str, name: str, reason: ReasonCode, note: str) -> None:
        key = (code or name, reason.value)
        if key in blocked_seen:
            return
        blocked_seen.add(key)
        blocked.append(BlockedTrade(code=code, name=name, reason=reason, note=note))

    remaining: dict[str, Position] = {p.code: p for p in portfolio.positions}
    stopped: set[str] = set()

    # 1. 止损：优先执行，不受换手上限约束
    for pos in portfolio.positions:
        if not is_stop_loss(pos, rules):
            continue
        qty, amount = round_sell(pos.type, pos.market_value, pos.price, pos.qty, rules)
        if amount < 100:
            continue
        trades.append(
            Trade(
                action=Action.SELL,
                code=pos.code,
                name=pos.name,
                type=pos.type,
                bucket=pos.bucket,
                qty=qty,
                amount_est=amount,
                price=pos.price,
                cur_w=pos.weight,
                tgt_w=target.get(pos.bucket),
                reason=ReasonCode.STOP_LOSS,
                note=f"浮亏 {pos.pnl_pct * 100:.1f}% 且跌破 MA120",
            )
        )
        cash += amount
        remaining.pop(pos.code, None)
        stopped.add(pos.code)

    # 2. 计算各桶偏离与卖出需求（先卖）
    desired_sells: list[tuple[Position, float, str]] = []
    for bucket in BUCKET_ORDER:
        if bucket == "cash":
            continue
        cur = _bucket_value(remaining, bucket)
        tgt_amt = target.get(bucket) * total
        delta = tgt_amt - cur
        dev = target.get(bucket) - (cur / total if total else 0)
        if abs(delta) < min_amount or abs(dev) < cfg.min_deviation:
            continue
        if delta >= 0:
            continue
        need = min(cur, -delta)
        positions = sorted(
            (p for p in remaining.values() if p.bucket == bucket),
            key=lambda p: p.market_value,
            reverse=True,
        )
        if not positions:
            continue
        bucket_cur = sum(p.market_value for p in positions)
        for pos in positions:
            if fund_fee_locked(pos, as_of, rules):
                block(pos.code, pos.name, ReasonCode.FUND_FEE_LOCK, "场外基金持有不足 7 天，赎回费较高")
                continue
            share = need * (pos.market_value / bucket_cur) if bucket_cur else 0
            desired_sells.append((pos, share, f"{BUCKET_LABELS[bucket]}超配 {dev * 100:.1f}pp"))

    cap_total = max(cfg.max_turnover * total, cfg.max_turnover_floor)
    sell_total_desired = sum(amount for _, amount, _ in desired_sells)
    sell_scale = 1.0
    if sell_total_desired > cap_total and sell_total_desired > 0:
        sell_scale = cap_total / sell_total_desired

    sell_total = 0.0
    for pos, amount, note in desired_sells:
        qty, actual = round_sell(pos.type, amount * sell_scale, pos.price, pos.qty, rules)
        if actual < 100:
            if amount * sell_scale >= 100:
                block(
                    pos.code,
                    pos.name,
                    ReasonCode.ROUNDING,
                    f"本次额度 {amount * sell_scale:,.0f} 元，不足最小交易单位",
                )
            continue
        trades.append(
            Trade(
                action=Action.SELL,
                code=pos.code,
                name=pos.name,
                type=pos.type,
                bucket=pos.bucket,
                qty=qty,
                amount_est=actual,
                price=pos.price,
                cur_w=pos.weight,
                tgt_w=target.get(pos.bucket),
                reason=ReasonCode.DEVIATION_DOWN,
                note=note,
            )
        )
        cash += actual
        sell_total += actual
        remaining[pos.code] = replace(
            pos,
            market_value=pos.market_value - actual,
            qty=max(0.0, pos.qty - (qty if pos.type.value != "fund" else actual / pos.price)),
        )

    # 3. 买入预算：保留现金目标（不低于现金底线）
    reserve = max(cfg.cash_floor, target.get("cash")) * total
    budget = cash - reserve
    turnover_left = max(0.0, cap_total - sell_total)
    budget = min(budget, turnover_left)

    equity_blocked = pe_pct is not None and pe_pct > rules.guards.valuation_hard_gate

    buy_requests: list[tuple[float, str]] = []
    for bucket in BUCKET_ORDER:
        if bucket == "cash":
            continue
        cur = _bucket_value(remaining, bucket)
        tgt_amt = target.get(bucket) * total
        delta = tgt_amt - cur
        dev = target.get(bucket) - (cur / total if total else 0)
        if delta <= 0 or abs(dev) < cfg.min_deviation:
            continue
        if delta < min_amount:
            block(
                "",
                BUCKET_LABELS[bucket],
                ReasonCode.MIN_AMOUNT,
                f"偏离 {dev * 100:.1f}pp，差额 {delta:,.0f} 元低于门槛 {min_amount:,.0f} 元",
            )
            continue
        buy_requests.append((delta, bucket))

    buy_requests.sort(key=lambda item: item[0], reverse=True)
    if budget <= 0:
        for _, bucket in buy_requests:
            block("", BUCKET_LABELS[bucket], ReasonCode.CASH_FLOOR, "现金已到目标/底线，本轮不买入")

    buy_total = 0.0
    for need, bucket in buy_requests:
        if budget <= 0:
            break
        if equity_blocked and bucket in EQUITY_BUCKETS:
            block("", BUCKET_LABELS[bucket], ReasonCode.VALUATION_GATE, "沪深300 PE 分位过高，暂停权益买入")
            continue

        held = [p for p in remaining.values() if p.bucket == bucket and p.market_value > 0]
        targets: list[tuple[InstrumentQuote, float]] = []
        if held:
            bucket_cur = sum(p.market_value for p in held)
            for pos in held:
                quote = InstrumentQuote(
                    code=pos.code,
                    name=pos.name,
                    type=pos.type,
                    bucket=pos.bucket,
                    price=pos.price,
                    ma20=pos.ma20,
                    ma120=pos.ma120,
                    chg_20d=pos.chg_20d,
                )
                targets.append((quote, need * (pos.market_value / bucket_cur)))
        else:
            default = next((item for item in universe.get(bucket, ()) if item.default), None)
            if default is None:
                block("", BUCKET_LABELS[bucket], ReasonCode.NO_DEFAULT, f"{BUCKET_LABELS[bucket]}桶未配置默认加仓标的")
                continue
            quote = quotes.get(default.code)
            if quote is None:
                block(default.code, default.name, ReasonCode.NOISE, "未取得行情，本轮无法给出买入价")
                continue
            if quote.code in stopped:
                block(quote.code, quote.name, ReasonCode.STOP_LOSS, "本周已触发止损，暂不回补")
                continue
            targets.append((quote, need))

        for quote, amount in targets:
            if amount < 100:
                continue
            if is_chasing(quote, rules):
                block(quote.code, quote.name, ReasonCode.NO_CHASE, "20 日涨幅或偏离 MA20 过大，禁止追涨")
                continue
            current_value = remaining.get(quote.code).market_value if quote.code in remaining else 0.0
            room = single_cap(quote.type, rules) * total - current_value
            if room <= 0:
                block(quote.code, quote.name, ReasonCode.SINGLE_CAP, "单一标的已达仓位上限")
                continue
            alloc = min(amount, budget, room)
            qty, actual = round_buy(quote.type, alloc, quote.price, rules)
            if actual < 100:
                if amount >= 100:
                    block(quote.code, quote.name, ReasonCode.ROUNDING, "取整后不足最小交易单位")
                continue
            trades.append(
                Trade(
                    action=Action.BUY,
                    code=quote.code,
                    name=quote.name,
                    type=quote.type,
                    bucket=bucket,
                    qty=qty,
                    amount_est=actual,
                    price=quote.price,
                    cur_w=current_value / total if total else 0.0,
                    tgt_w=target.get(bucket),
                    reason=ReasonCode.DEVIATION_UP,
                    note=f"{BUCKET_LABELS[bucket]}低配 {need / total * 100:.1f}pp",
                )
            )
            budget -= actual
            buy_total += actual

    cash_after = cash - buy_total
    trades.sort(key=lambda t: (0 if t.action is Action.SELL else 1, -t.amount_est))
    return RebalancePlan(trades=tuple(trades), blocked=tuple(blocked), cash_after=cash_after)
