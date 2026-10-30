from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ia.market.cache import DiskCache, Throttle
from ia.models import NewsItem

log = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")


def _parse_dt(text: str) -> datetime | None:
    text = str(text).strip()
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=SH)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=SH)
        except ValueError:
            continue
    return None


def _fetch_rows() -> list[dict]:
    import akshare as ak

    df = None
    try:
        df = ak.stock_info_global_cls(symbol="全部")
    except Exception as exc:  # noqa: BLE001
        log.warning("财联社电报失败（%s），尝试东财全球快讯", exc)
    if df is None or df.empty:
        df = ak.stock_info_global_em()
    if df is None or df.empty:
        return []

    cols = {str(c): c for c in df.columns}

    def pick(*names: str):
        for name in names:
            if name in cols:
                return cols[name]
        for col in cols:
            for name in names:
                if name in col:
                    return cols[col]
        return None

    title_col = pick("标题")
    body_col = pick("内容", "摘要")
    date_col = pick("发布日期")
    time_col = pick("发布时间")
    dt_col = pick("发布时间") if date_col is None else None

    rows: list[dict] = []
    for _, row in df.iterrows():
        title = str(row[title_col]).strip() if title_col else ""
        body = str(row[body_col]).strip() if body_col else ""
        if not title and not body:
            continue
        raw_dt = ""
        if date_col is not None and time_col is not None:
            raw_dt = f"{row[date_col]} {row[time_col]}"
        elif time_col is not None:
            raw_dt = str(row[time_col])
        elif dt_col is not None:
            raw_dt = str(row[dt_col])
        published = _parse_dt(raw_dt) or datetime.now(SH)
        rows.append(
            {
                "title": title[:120],
                "summary": body[:200],
                "published_at": published.isoformat(),
            }
        )
    return rows


def fetch_news(days: int = 7, cache: DiskCache | None = None, throttle: Throttle | None = None) -> tuple[NewsItem, ...]:
    def guarded() -> list[dict]:
        if throttle is not None:
            throttle.wait()
        return _fetch_rows()

    if cache is None:
        rows = guarded()
    else:
        result = cache.get_or_fetch(f"news_{days}", 3600, guarded)
        rows = result.data

    cutoff = datetime.now(SH) - timedelta(days=days)
    items: list[NewsItem] = []
    for row in rows:
        published = _parse_dt(row["published_at"])
        if published is None or published < cutoff:
            continue
        items.append(NewsItem(title=row["title"], summary=row["summary"], published_at=published))
    items.sort(key=lambda i: i.published_at, reverse=True)
    return tuple(items[:120])
