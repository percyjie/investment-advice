from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ia.calendar import TradeCalendar
from ia.config import Secrets, Settings, load_all, load_secrets, load_settings
from ia.llm import hotspot as hotspot_llm
from ia.market.source import AkShareSource, DataSource
from ia.models import BUCKET_LABELS, Level, Regime, Report, ReportSection
from ia.notify import build_notifiers
from ia.notify.base import NotifyResult
from ia.report.builder import build_daily_report, build_weekly_report
from ia.report.render_text import render_text
from ia.rules.engine import (
    AnalysisContext,
    build_context,
    build_daily_scan,
    build_weekly_advice,
)
from ia.storage import Storage, advice_to_dict

log = logging.getLogger("ia")


def setup_logging(settings: Settings, name: str) -> None:
    log_dir = settings.path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    file_handler = RotatingFileHandler(log_dir / f"{name}.log", maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def cmd_check(args: argparse.Namespace) -> int:
    settings, rules, portfolio_cfg, universe = load_all()
    setup_logging(settings, "check")
    source = AkShareSource(settings)
    calendar = TradeCalendar(source)
    holdings = portfolio_cfg.holdings
    first_stock = next((h for h in holdings if h.type.value == "stock"), None)
    first_etf = next((h for h in holdings if h.type.value == "etf"), None)
    first_fund = next((h for h in holdings if h.type.value == "fund"), None)

    probes: list[tuple[str, bool, callable]] = [
        ("交易日历", True, lambda: f"{len(source.trade_dates())} 天"),
        ("沪深300 日线", True, lambda: f"{len(source.index_bars('000300', 500))} 根 / {source.index_bars('000300', 500)[-1].day}"),
        ("指数 PE", True, lambda: f"{len(source.pe_history('沪深300'))} 条"),
        ("10Y 国债", True, lambda: f"{source.rate_10y()[-1].close:.2f}% / {source.rate_10y()[-1].day}"),
        ("行业板块", False, lambda: f"{len(source.industry_snapshot())} 个"),
        ("板块资金流", False, lambda: f"{len(source.sector_fund_flow('5日'))} 个"),
        ("新闻", False, lambda: f"{len(source.news(7))} 条"),
    ]
    if first_stock:
        probes.append(("个股行情", True, lambda: f"{first_stock.code} / {source.stock_bars(first_stock.code, 160)[-1].day}"))
    if first_etf:
        probes.append(("ETF 行情", True, lambda: f"{first_etf.code} / {source.etf_bars(first_etf.code, 160)[-1].day}"))
    if first_fund:
        probes.append(("场外基金净值", True, lambda: f"{first_fund.code} / {source.fund_nav(first_fund.code, 160)[-1].day}"))

    failures = 0
    print(f"{'字段':<16}{'结果':<8}{'耗时':<8}详情")
    for name, critical, fn in probes:
        start = time.time()
        try:
            detail = fn()
            print(f"{name:<16}{'OK':<8}{time.time() - start:>5.1f}s  {detail}")
        except Exception as exc:  # noqa: BLE001
            failures += 1 if critical else 0
            mark = "FAIL" if critical else "WARN"
            print(f"{name:<16}{mark:<8}{time.time() - start:>5.1f}s  {type(exc).__name__}: {str(exc)[:120]}")

    print()
    print(f"组合：{len(portfolio_cfg.holdings)} 只标的，现金 {portfolio_cfg.cash:,.0f} 元"
          + ("（示例数据）" if portfolio_cfg.is_example else ""))
    print(f"风险偏好：{rules.risk_profile}｜目标合计：{sum(rules.targets.values()):.0f}%")
    print(f"推荐加仓标的：{sum(1 for items in universe.values() for i in items if i.default)} 个")
    print(f"最近交易日：{calendar.latest_trading_day()}")
    print("结论：" + ("关键数据源不可用，请先修复" if failures else "数据源正常"))
    return 1 if failures else 0


def _push(report: Report, settings: Settings, secrets: Secrets, dry_run: bool, no_push: bool) -> list[NotifyResult]:
    if no_push:
        return []
    notifiers = build_notifiers(settings, secrets, dry_run=dry_run)
    results = []
    for notifier in notifiers:
        results.append(notifier.send(report))
    return results


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    from zoneinfo import ZoneInfo

    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if parsed.tzinfo is None else parsed


def _report_outputs(report: Report, storage: Storage, out: str | None) -> Path:
    text = render_text(report)
    path = storage.save_report(report.kind, report.as_of or date.today(), text)
    if out:
        Path(out).expanduser().write_text(text, encoding="utf-8")
    return path


def cmd_weekly(args: argparse.Namespace) -> int:
    settings, rules, portfolio_cfg, universe = load_all()
    setup_logging(settings, "weekly")
    storage = Storage(settings.path(settings.data_dir))
    with storage.lock():
        source = AkShareSource(settings)
        ctx = build_context(
            settings, rules, portfolio_cfg, universe, source, TradeCalendar(source), now=_parse_now(args.now)
        )
        advice = build_weekly_advice(ctx)
        secret = load_secrets()
        result = hotspot_llm.analyze(ctx, settings, secret, enabled=not args.no_llm)
        report = build_weekly_report(ctx, advice, result)

        state = storage.load_state()
        if state.last_weekly is not None and (date.today() - state.last_weekly).days > 10:
            report = Report(
                kind=report.kind,
                title=report.title,
                level=report.level,
                headline=report.headline,
                sections=report.sections,
                footer="补发｜" + report.footer,
                as_of=report.as_of,
                generated_at=report.generated_at,
            )

        path = _report_outputs(report, storage, args.out)
        storage.save_snapshot("weekly", advice.as_of, advice_to_dict(advice))

        state.last_weekly = advice.as_of
        state.prev_regime = ctx.regime.regime.value
        storage.save_state(state)

        print(f"已保存：{path}")
        if args.no_push:
            print(render_text(report))
        else:
            for item in _push(report, settings, secret, args.dry_run, args.no_push):
                print(f"推送 {item.channel}: {'成功' if item.ok else '失败 ' + item.detail}")
        if result.available is False:
            print(f"热点模块：{result.error}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    settings, rules, portfolio_cfg, universe = load_all()
    setup_logging(settings, "daily")
    storage = Storage(settings.path(settings.data_dir))
    with storage.lock():
        source = AkShareSource(settings)
        calendar = TradeCalendar(source)
        if not args.force and not calendar.is_trading_day(calendar.today()):
            print("今天不是交易日，跳过")
            return 0

        state = storage.load_state()
        prev = Regime(state.prev_regime) if state.prev_regime else None
        now = _parse_now(args.now)
        ctx = build_context(settings, rules, portfolio_cfg, universe, source, calendar, now=now)
        scan = build_daily_scan(ctx, prev, now=now)

        new_alerts = [a for a in scan.alerts if not state.alert_seen(scan.as_of, a.rule_id)]
        if scan.alerts and not new_alerts:
            print("今日同类告警已推送过，跳过重复推送")

        report = build_daily_report(ctx, scan)
        if state.last_daily is not None and (calendar.today() - state.last_daily).days > 4:
            report = Report(
                kind=report.kind,
                title=report.title,
                level=report.level,
                headline=report.headline,
                sections=report.sections,
                footer="补发｜" + report.footer,
                as_of=report.as_of,
                generated_at=report.generated_at,
            )

        path = _report_outputs(report, storage, args.out)
        storage.save_snapshot("daily", scan.as_of, advice_to_dict(scan))

        state.last_daily = calendar.today()
        state.prev_regime = ctx.regime.regime.value
        if scan.alerts:
            state.mark_alerts(scan.as_of, [a.rule_id for a in scan.alerts])
        storage.save_state(state)

        print(f"已保存：{path}")
        if args.no_push:
            print(render_text(report))
        elif not (scan.alerts and not new_alerts):
            for item in _push(report, settings, load_secrets(), args.dry_run, args.no_push):
                print(f"推送 {item.channel}: {'成功' if item.ok else '失败 ' + item.detail}")
    return 0


def cmd_notify_test(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(settings, "notify")
    secrets = load_secrets()
    report = Report(
        kind="test",
        title="推送测试",
        level=Level.GREEN,
        headline="如果你看到这条消息，说明推送配置成功",
        sections=(
            ReportSection("说明", ("这是 investment-advice 的测试消息，不会触发任何交易。",)),
        ),
        footer="investment-advice",
    )
    notifiers = build_notifiers(settings, secrets, dry_run=False)
    if args.channel:
        notifiers = [n for n in notifiers if n.name == args.channel]
    if not notifiers:
        print("没有可用的推送渠道，请检查 webhook 配置")
        return 1
    failed = 0
    for notifier in notifiers:
        result = notifier.send(report)
        print(f"{notifier.name}: {'成功' if result.ok else '失败 ' + result.detail}")
        failed += 0 if result.ok else 1
    return 1 if failed else 0


def cmd_show(args: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.path(settings.data_dir))
    path = storage.latest_report(args.kind)
    if path is None:
        print("还没有报告，先运行 ia weekly 或 ia daily")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_portfolio(args: argparse.Namespace) -> int:
    settings, rules, portfolio_cfg, universe = load_all()
    source = AkShareSource(settings)
    calendar = TradeCalendar(source)
    from ia.market.build import build_portfolio

    portfolio, notes = build_portfolio(portfolio_cfg, source, calendar)
    print(f"总资产 {portfolio.total_assets:,.0f} 元｜现金 {portfolio.cash:,.0f} 元（{portfolio.cash_ratio * 100:.1f}%）")
    print(f"{'代码':<8}{'名称':<14}{'桶':<8}{'市值':>12}{'占比':>8}{'浮盈':>8}")
    for pos in portfolio.positions:
        print(
            f"{pos.code:<8}{pos.name:<14}{BUCKET_LABELS[pos.bucket]:<8}"
            f"{pos.market_value:>12,.0f}{pos.weight * 100:>7.1f}%{pos.pnl_pct * 100:>7.1f}%"
        )
    print()
    for bucket, label in BUCKET_LABELS.items():
        target = rules.targets[bucket]
        actual = portfolio.bucket_weights()[bucket] * 100
        print(f"{label}：实际 {actual:.1f}%｜中性目标 {target:.0f}%")
    for note in notes:
        print(f"提示：{note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ia", description="个人投资建议系统")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="数据源探活")
    check.set_defaults(func=cmd_check)

    weekly = sub.add_parser("weekly", help="生成周报")
    weekly.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    weekly.add_argument("--no-llm", action="store_true", help="跳过模型热点分析")
    weekly.add_argument("--no-push", action="store_true", help="不推送")
    weekly.add_argument("--out", help="额外输出到指定文件")
    weekly.add_argument("--now", help="调试用：指定评估时点，如 2026-10-30T08:30")
    weekly.set_defaults(func=cmd_weekly)

    daily = sub.add_parser("daily", help="每日风险扫描")
    daily.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    daily.add_argument("--no-push", action="store_true", help="不推送")
    daily.add_argument("--out", help="额外输出到指定文件")
    daily.add_argument("--force", action="store_true", help="非交易日也执行")
    daily.add_argument("--now", help="调试用：指定评估时点，如 2026-10-30T08:30")
    daily.set_defaults(func=cmd_daily)

    notify = sub.add_parser("notify-test", help="推送连通性测试")
    notify.add_argument("--channel", choices=["feishu", "dingtalk"], help="只测试指定渠道")
    notify.set_defaults(func=cmd_notify_test)

    show = sub.add_parser("show", help="查看最近一份报告")
    show.add_argument("--kind", choices=["weekly", "daily"], help="报告类型")
    show.set_defaults(func=cmd_show)

    portfolio = sub.add_parser("portfolio", help="查看持仓估值")
    portfolio.set_defaults(func=cmd_portfolio)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("ia").exception("执行失败")
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
