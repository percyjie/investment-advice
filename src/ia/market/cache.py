from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheResult:
    data: Any
    stale: bool
    saved_at: datetime | None


class DiskCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _file(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.root / f"{safe}.json"

    def read(self, key: str) -> tuple[Any, datetime] | None:
        path = self._file(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            saved_at = datetime.fromisoformat(payload["saved_at"])
            return payload["data"], saved_at
        except (OSError, ValueError, KeyError):
            log.warning("缓存损坏，忽略: %s", path)
            return None

    def write(self, key: str, data: Any) -> None:
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        tmp = self._file(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self._file(key))

    def get_or_fetch(
        self,
        key: str,
        ttl: int,
        fetcher: Callable[[], Any],
    ) -> CacheResult:
        cached = self.read(key)
        now = datetime.now(timezone.utc)
        if cached is not None:
            data, saved_at = cached
            age = (now - saved_at).total_seconds()
            if age <= ttl:
                return CacheResult(data, stale=False, saved_at=saved_at)
        try:
            data = fetcher()
        except Exception as exc:  # noqa: BLE001 - 数据源失败要降级而不是崩溃
            if cached is not None:
                data, saved_at = cached
                log.warning("拉取 %s 失败（%s），使用过期缓存", key, exc)
                return CacheResult(data, stale=True, saved_at=saved_at)
            raise
        if data is None:
            if cached is not None:
                data, saved_at = cached
                log.warning("拉取 %s 返回空，使用过期缓存", key)
                return CacheResult(data, stale=True, saved_at=saved_at)
            raise RuntimeError(f"数据源返回空: {key}")
        self.write(key, data)
        return CacheResult(data, stale=False, saved_at=now)


class Throttle:
    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()
