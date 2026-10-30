from __future__ import annotations

from ia.config import Rules
from ia.models import BUCKET_ORDER, EQUITY_BUCKETS, Regime, RegimeResult, TargetWeights


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _balance(weights: dict[str, float], rules: Rules) -> dict[str, float]:
    non_cash = [b for b in BUCKET_ORDER if b != "cash"]
    low, high = rules.bounds["cash"]
    for _ in range(12):
        for bucket in non_cash:
            lo, hi = rules.bounds[bucket]
            weights[bucket] = _clamp(weights[bucket], lo, hi)
        others = sum(weights[b] for b in non_cash)
        cash = 100 - others
        if cash < low:
            scale = (100 - low) / others if others > 0 else 0
            for bucket in non_cash:
                weights[bucket] *= scale
            weights["cash"] = low
            continue
        if cash > high:
            excess = cash - high
            if others > 0:
                for bucket in non_cash:
                    weights[bucket] += excess * (weights[bucket] / others)
            else:
                weights["equity_core"] += excess
            weights["cash"] = high
            continue
        weights["cash"] = cash
        break

    total = sum(weights.values())
    if abs(total - 100) > 1e-9:
        weights["cash"] += 100 - total
    return weights


def compute(rules: Rules, regime: RegimeResult, pe_pct: float | None) -> TargetWeights:
    base = {bucket: float(rules.targets[bucket]) for bucket in BUCKET_ORDER}
    shift = rules.regime_shift.get(regime.regime.value, {})
    weights = {bucket: base[bucket] + float(shift.get(bucket, 0)) for bucket in BUCKET_ORDER}

    gate = rules.valuation_gate
    if pe_pct is not None:
        factor = 1.0
        if pe_pct > gate.expensive:
            factor = gate.expensive_factor
        elif pe_pct < gate.cheap:
            factor = gate.cheap_factor
        if factor != 1.0:
            moved = 0.0
            for bucket in EQUITY_BUCKETS:
                before = weights[bucket]
                weights[bucket] = before * factor
                moved += before - weights[bucket]
            weights["cash"] += moved

    weights = _balance(weights, rules)
    fractions = {bucket: weights[bucket] / 100 for bucket in BUCKET_ORDER}
    total = sum(fractions.values())
    if abs(total - 1.0) > 1e-9:
        fractions["cash"] += 1.0 - total
    return TargetWeights(regime=regime.regime, score=regime.score, weights=fractions)


def equity_target(target: TargetWeights) -> float:
    return sum(target.get(b) for b in EQUITY_BUCKETS)
