from __future__ import annotations

import logging
import socket
import time
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from ia.config import Settings
from ia.market.cache import DiskCache, Throttle
from ia.models import Bar, BoardPerf, NewsItem

log = logging.getLogger(__name__)


class DataSourceError(RuntimeError):
    pass


class DataSource(Protocol):
    def index_bars(self, code: str, rows: int) -> tuple[Bar, ...]: ...
    def stock_bars(self, code: str, rows: int) -> tuple[Bar, ...]: ...
    def etf_bars(self, code: str, rows: int) -> tuple[Bar, ...]: ...
    def fund_nav(self, code: str, rows: int) -> tuple[Bar, ...]: ...
    def pe_history(self, name: str) -> tuple[Bar, ...]: ...
    def rate_10y(self) -> tuple[Bar, ...]: ...
    def trade_dates(self) -> tuple[date, ...]: ...
    def industry_snapshot(self) -> tuple[BoardPerf, ...]: ...
    def sector_fund_flow(self, indicator: str = "5日") -> tuple[BoardPerf, ...]: ...
    def news(self, days: int) -> tuple[NewsItem, ...]: ...
    @property
    def stale_keys(self) -> set[str]: ...


def _col(df, *candidates: str) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    for col in df.columns:
        for name in candidates:
            if name in str(col):
                return str(col)
    return None


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() in ("", "-", "nan", "None"):
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _sina_symbol(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _sina_index_symbol(code: str) -> str:
    return f"sz{code}" if code.startswith("399") else f"sh{code}"


def _chain(name: str, fetchers: list[tuple[str, Any]], attempts: int = 2) -> Any:
    errors: list[str] = []
    for label, fetch in fetchers:
        for attempt in range(attempts):
            try:
                df = fetch()
            except Exception as exc:  # noqa: BLE001
                error_name = type(exc).__name__
                errors.append(f"{label}: {error_name}")
                log.warning("%s 数据源 %s 失败: %s", name, label, exc)
                # 东财限流时重复调用会延长封禁，直接换源
                if "Push2RateLimit" in error_name:
                    break
                if attempt + 1 < attempts:
                    time.sleep(1.0)
                continue
            if df is None or getattr(df, "empty", True):
                errors.append(f"{label}: 空数据")
                break
            return df
    raise DataSourceError(f"{name} 全部数据源失败（{'; '.join(errors)}）")


class AkShareSource:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache = DiskCache(settings.path(settings.data_dir) / "cache")
        self.throttle = Throttle(1.0)
        self._stale: set[str] = set()
        socket.setdefaulttimeout(25)

    @property
    def stale_keys(self) -> set[str]:
        return set(self._stale)

    def _ak(self):
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - 安装期问题
            raise DataSourceError("未安装 akshare，请先执行 pip install -e '.[dev]'") from exc
        return ak

    def _fetch(self, key: str, ttl: int, fn) -> list[dict]:
        def guarded() -> list[dict]:
            self.throttle.wait()
            rows = fn()
            if not rows:
                raise DataSourceError(f"{key} 未返回数据")
            return rows

        result = self.cache.get_or_fetch(key, ttl, guarded)
        if result.stale:
            self._stale.add(key)
        return result.data

    @staticmethod
    def _bars(rows: list[dict]) -> tuple[Bar, ...]:
        bars = [Bar(day=date.fromisoformat(r["day"]), close=float(r["close"])) for r in rows]
        bars.sort(key=lambda b: b.day)
        return tuple(bars)

    @staticmethod
    def _rows_from_df(df, date_col: str, close_col: str) -> list[dict]:
        rows: list[dict] = []
        for _, row in df.iterrows():
            day = _to_date(row[date_col])
            close = _to_float(row[close_col])
            if day is not None and close is not None and close > 0:
                rows.append({"day": day.isoformat(), "close": close})
        return rows

    def _hist(self, key: str, rows: int, fetch_df) -> tuple[Bar, ...]:
        def fn() -> list[dict]:
            df = fetch_df()
            if df is None or df.empty:
                return []
            date_col = _col(df, "日期", "date")
            close_col = _col(df, "收盘", "单位净值", "close")
            if not date_col or not close_col:
                raise DataSourceError(f"{key} 列名异常: {list(df.columns)}")
            return self._rows_from_df(df, date_col, close_col)

        ttl = self.settings.cache_ttl.bars
        data = self._fetch(f"{key}:{rows}", ttl, fn)
        return self._bars(data[-rows:])

    def index_bars(self, code: str = "000300", rows: int = 500) -> tuple[Bar, ...]:
        ak = self._ak()
        start = (date.today() - timedelta(days=int(rows * 1.8) + 60)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")
        fetchers = [
            ("eastmoney", lambda: ak.index_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end)),
            ("sina", lambda: ak.stock_zh_index_daily(symbol=_sina_index_symbol(code))),
            ("tencent", lambda: ak.stock_zh_index_daily_tx(symbol=_sina_index_symbol(code))),
        ]
        return self._hist(
            f"index_{code}",
            rows,
            lambda: _chain(f"index {code}", fetchers),
        )

    def stock_bars(self, code: str, rows: int = 160) -> tuple[Bar, ...]:
        ak = self._ak()
        start = (date.today() - timedelta(days=int(rows * 1.8) + 60)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")
        fetchers = [
            (
                "eastmoney",
                lambda: ak.stock_zh_a_hist(
                    symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq"
                ),
            ),
            (
                "sina",
                lambda: ak.stock_zh_a_daily(
                    symbol=_sina_symbol(code), start_date=start, end_date=end, adjust="qfq"
                ),
            ),
            (
                "tencent",
                lambda: ak.stock_zh_a_hist_tx(
                    symbol=_sina_symbol(code), start_date=start, end_date=end, adjust="qfq"
                ),
            ),
        ]
        return self._hist(f"stock_{code}", rows, lambda: _chain(f"stock {code}", fetchers))

    def etf_bars(self, code: str, rows: int = 160) -> tuple[Bar, ...]:
        ak = self._ak()
        start = (date.today() - timedelta(days=int(rows * 1.8) + 60)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")
        fetchers = [
            (
                "eastmoney",
                lambda: ak.fund_etf_hist_em(
                    symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq"
                ),
            ),
            ("sina", lambda: ak.fund_etf_hist_sina(symbol=_sina_symbol(code))),
        ]
        return self._hist(f"etf_{code}", rows, lambda: _chain(f"etf {code}", fetchers))

    def fund_nav(self, code: str, rows: int = 160) -> tuple[Bar, ...]:
        ak = self._ak()
        return self._hist(
            f"fund_{code}",
            rows,
            lambda: ak.fund_open_fund_info_em(
                symbol=code, indicator="单位净值走势", period="成立来"
            ),
        )

    def pe_history(self, name: str = "沪深300") -> tuple[Bar, ...]:
        ak = self._ak()

        def fn() -> list[dict]:
            df = ak.stock_index_pe_lg(symbol=name)
            if df is None or df.empty:
                return []
            date_col = _col(df, "日期")
            pe_col = _col(df, "滚动市盈率", "静态市盈率", "等权滚动市盈率", "等权静态市盈率")
            if not date_col or not pe_col:
                raise DataSourceError(f"PE 列名异常: {list(df.columns)}")
            return self._rows_from_df(df, date_col, pe_col)

        data = self._fetch(f"pe_{name}", self.settings.cache_ttl.valuation, fn)
        return self._bars(data)

    def rate_10y(self) -> tuple[Bar, ...]:
        ak = self._ak()

        def fn() -> list[dict]:
            start = (date.today() - timedelta(days=365 * 4)).strftime("%Y%m%d")
            df = ak.bond_zh_us_rate(start_date=start)
            if df is None or df.empty:
                return []
            date_col = _col(df, "日期")
            rate_col = _col(df, "中国国债收益率10年")
            if not date_col or not rate_col:
                raise DataSourceError(f"国债收益率列名异常: {list(df.columns)}")
            return self._rows_from_df(df, date_col, rate_col)

        data = self._fetch("rate_10y", self.settings.cache_ttl.bars, fn)
        return self._bars(data)

    def trade_dates(self) -> tuple[date, ...]:
        ak = self._ak()

        def fn() -> list[str]:
            df = ak.tool_trade_date_hist_sina()
            if df is None or df.empty:
                return []
            col = _col(df, "trade_date", "日期")
            if not col:
                raise DataSourceError(f"交易日历列名异常: {list(df.columns)}")
            out = []
            for value in df[col].tolist():
                day = _to_date(value)
                if day is not None:
                    out.append(day.isoformat())
            return out

        data = self._fetch("trade_calendar", self.settings.cache_ttl.calendar, fn)
        return tuple(date.fromisoformat(d) for d in data)

    def industry_snapshot(self) -> tuple[BoardPerf, ...]:
        ak = self._ak()

        def fn() -> list[dict]:
            fetchers = [
                ("eastmoney", lambda: ak.stock_board_industry_name_em()),
                ("sina", lambda: ak.stock_sector_spot(indicator="新浪行业")),
            ]
            df = _chain("industry", fetchers)
            name_col = _col(df, "板块名称", "板块", "名称")
            chg_col = _col(df, "涨跌幅")
            if not name_col or not chg_col:
                raise DataSourceError(f"行业板块列名异常: {list(df.columns)}")
            rows = []
            for _, row in df.iterrows():
                chg = _to_float(row[chg_col])
                if chg is None:
                    continue
                rows.append({"name": str(row[name_col]), "chg_pct": chg})
            return rows

        data = self._fetch("industry_snapshot", self.settings.cache_ttl.quote, fn)
        boards = [BoardPerf(name=r["name"], chg_pct=float(r["chg_pct"])) for r in data]
        boards.sort(key=lambda b: b.chg_pct, reverse=True)
        return tuple(boards)

    def sector_fund_flow(self, indicator: str = "5日") -> tuple[BoardPerf, ...]:
        ak = self._ak()

        def fn() -> list[dict]:
            df = ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type="行业资金流")
            if df is None or df.empty:
                return []
            name_col = _col(df, "名称", "板块名称")
            chg_col = _col(df, f"{indicator}涨跌幅", "涨跌幅")
            flow_col = _col(df, f"{indicator}主力净流入-净额", "主力净流入-净额")
            if not name_col:
                raise DataSourceError(f"资金流列名异常: {list(df.columns)}")
            rows = []
            for _, row in df.iterrows():
                chg = _to_float(row[chg_col]) if chg_col else None
                flow = _to_float(row[flow_col]) if flow_col else None
                rows.append(
                    {
                        "name": str(row[name_col]),
                        "chg_pct": chg if chg is not None else 0.0,
                        "net_inflow_yi": flow / 1e8 if flow is not None else None,
                    }
                )
            return rows

        try:
            data = self._fetch(f"fund_flow_{indicator}", self.settings.cache_ttl.quote, fn)
        except Exception as exc:  # noqa: BLE001
            log.warning("板块资金流失败（%s），退化为板块涨跌榜", exc)
            return self.industry_snapshot()[:10]
        boards = [
            BoardPerf(
                name=r["name"],
                chg_pct=float(r["chg_pct"]),
                net_inflow_yi=r.get("net_inflow_yi"),
            )
            for r in data
        ]
        boards.sort(key=lambda b: (b.net_inflow_yi or 0.0), reverse=True)
        return tuple(boards)

    def news(self, days: int = 7) -> tuple[NewsItem, ...]:
        from ia.market.news import fetch_news

        return fetch_news(days=days, cache=self.cache, throttle=self.throttle)


def create_source(settings: Settings) -> DataSource:
    return AkShareSource(settings)
