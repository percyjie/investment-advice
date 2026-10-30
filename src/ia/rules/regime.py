from __future__ import annotations

from ia.config import RegimeThresholds
from ia.models import Regime, RegimeResult, RegimeVote


def classify(
    *,
    close: float,
    ma20: float | None,
    ma60: float | None,
    ma200: float | None,
    vol_pct: float | None,
    pe_pct: float | None,
    rate_chg_60d_bp: float | None,
    chg_20d: float | None,
    thresholds: RegimeThresholds,
) -> RegimeResult:
    votes: list[RegimeVote] = []

    if ma200 is None:
        votes.append(RegimeVote("长期趋势", True, "数据不足", 0))
    else:
        up = close > ma200
        votes.append(
            RegimeVote(
                "长期趋势",
                up,
                f"收盘{close:.2f} {'>' if up else '<'} MA200 {ma200:.2f}",
                1 if up else -1,
            )
        )

    if ma20 is None or ma60 is None:
        votes.append(RegimeVote("中期趋势", True, "数据不足", 0))
    else:
        up = ma20 > ma60
        votes.append(
            RegimeVote(
                "中期趋势",
                up,
                f"MA20 {ma20:.2f} {'>' if up else '<'} MA60 {ma60:.2f}",
                1 if up else -1,
            )
        )

    if vol_pct is None:
        votes.append(RegimeVote("波动率", True, "数据不足", 0))
    else:
        ok = vol_pct < thresholds.vol_pct
        votes.append(
            RegimeVote("波动率", ok, f"波动率处于近一年 {vol_pct:.0f}% 分位", 1 if ok else -1)
        )

    if pe_pct is None:
        votes.append(RegimeVote("估值", True, "数据不足", 0))
    else:
        ok = pe_pct < thresholds.pe_pct
        votes.append(RegimeVote("估值", ok, f"沪深300 PE 十年分位 {pe_pct:.0f}%", 1 if ok else -1))

    if rate_chg_60d_bp is None:
        votes.append(RegimeVote("利率", True, "数据不足", 0))
    else:
        ok = rate_chg_60d_bp < thresholds.rate_chg_bp
        votes.append(
            RegimeVote(
                "利率",
                ok,
                f"10Y 国债 60日变动 {rate_chg_60d_bp:+.0f}bp",
                1 if ok else -1,
            )
        )

    score = sum(v.score for v in votes)
    override = (
        ma200 is not None
        and chg_20d is not None
        and close < ma200
        and chg_20d * 100 <= thresholds.crash_chg_20d
    )
    if override:
        regime = Regime.RISK_OFF
    elif score >= thresholds.risk_on_score:
        regime = Regime.RISK_ON
    elif score <= thresholds.risk_off_score:
        regime = Regime.RISK_OFF
    else:
        regime = Regime.NEUTRAL

    return RegimeResult(regime=regime, score=score, votes=tuple(votes), override=override)
