from __future__ import annotations

import math
from collections.abc import Sequence


def _clean(values: Sequence[float | None]) -> list[float]:
    return [float(v) for v in values if v is not None and math.isfinite(float(v))]


def sma(values: Sequence[float | None], n: int) -> float | None:
    data = _clean(values)
    if n <= 0 or len(data) < n:
        return None
    window = data[-n:]
    return sum(window) / n


def percentile_rank(history: Sequence[float | None], value: float | None = None) -> float | None:
    data = _clean(history)
    if len(data) < 20:
        return None
    target = data[-1] if value is None else float(value)
    below = sum(1 for v in data if v <= target)
    return below / len(data) * 100


def change_over(values: Sequence[float | None], n: int) -> float | None:
    data = _clean(values)
    if n <= 0 or len(data) <= n:
        return None
    base = data[-1 - n]
    if base == 0:
        return None
    return data[-1] / base - 1


def annualized_volatility(values: Sequence[float | None], n: int = 20) -> float | None:
    data = _clean(values)
    if len(data) < n + 1:
        return None
    window = data[-(n + 1):]
    returns = [window[i] / window[i - 1] - 1 for i in range(1, len(window)) if window[i - 1] > 0]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252)


def deviation_from_ma(values: Sequence[float | None], n: int) -> float | None:
    data = _clean(values)
    avg = sma(data, n)
    if avg is None or avg == 0:
        return None
    return data[-1] / avg - 1


def max_drawdown(values: Sequence[float | None]) -> float | None:
    data = _clean(values)
    if not data:
        return None
    peak = data[0]
    worst = 0.0
    for value in data:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst
