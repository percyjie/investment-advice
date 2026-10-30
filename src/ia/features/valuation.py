from __future__ import annotations

from datetime import date, timedelta

from ia.features.indicators import percentile_rank
from ia.models import Bar


def pe_percentile(bars: tuple[Bar, ...], years: int = 10, as_of: date | None = None) -> float | None:
    if not bars:
        return None
    as_of = as_of or bars[-1].day
    cutoff = as_of - timedelta(days=365 * years + 30)
    window = [b.close for b in bars if b.day >= cutoff]
    if len(window) < 60:
        window = [b.close for b in bars]
    return percentile_rank(window)
