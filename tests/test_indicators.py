from __future__ import annotations

import pytest

from ia.features.indicators import (
    annualized_volatility,
    change_over,
    deviation_from_ma,
    percentile_rank,
    sma,
)


def test_sma_insufficient_samples():
    assert sma([1, 2, 3], 5) is None


def test_sma_uses_last_window():
    assert sma(list(range(1, 21)), 20) == pytest.approx(10.5)
    assert sma([1, 2, 3, 4], 2) == pytest.approx(3.5)


def test_change_over():
    values = [100, 110, 121]
    assert change_over(values, 2) == pytest.approx(0.21)
    assert change_over(values, 5) is None


def test_percentile_rank():
    history = list(range(1, 101))
    assert percentile_rank(history, 50) == pytest.approx(50.0)
    assert percentile_rank(history, 100) == pytest.approx(100.0)
    assert percentile_rank([1, 2], 2) is None


def test_annualized_volatility_flat():
    assert annualized_volatility([10.0] * 30) == pytest.approx(0.0)
    assert annualized_volatility([10.0] * 5) is None


def test_deviation_from_ma():
    closes = [10.0] * 20 + [11.0]
    value = deviation_from_ma(closes, 20)
    assert value is not None and value > 0
