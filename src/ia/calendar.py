from __future__ import annotations

import logging
from datetime import date, datetime

from ia.market.source import DataSource

log = logging.getLogger(__name__)


class TradeCalendar:
    def __init__(self, source: DataSource):
        self._source = source
        self._dates: set[date] | None = None
        self._sorted: list[date] = []

    def _load(self) -> None:
        if self._dates is not None:
            return
        try:
            dates = self._source.trade_dates()
        except Exception as exc:  # noqa: BLE001
            log.warning("交易日历获取失败（%s），退化为工作日判断", exc)
            dates = ()
        self._dates = set(dates)
        self._sorted = sorted(dates)

    @property
    def available(self) -> bool:
        self._load()
        return bool(self._dates)

    def is_trading_day(self, day: date) -> bool:
        self._load()
        if not self._dates:
            return day.weekday() < 5
        return day in self._dates

    def prev_trading_day(self, day: date) -> date:
        self._load()
        if not self._sorted:
            cursor = day
            while True:
                cursor -= date.resolution
                if cursor.weekday() < 5:
                    return cursor
        for candidate in reversed(self._sorted):
            if candidate < day:
                return candidate
        return day

    def latest_trading_day(self, day: date | None = None) -> date:
        self._load()
        day = day or date.today()
        if not self._sorted:
            cursor = day
            while cursor.weekday() >= 5:
                cursor -= date.resolution
            return cursor
        for candidate in reversed(self._sorted):
            if candidate <= day:
                return candidate
        return self._sorted[0]

    def today(self) -> date:
        return datetime.now().date()
