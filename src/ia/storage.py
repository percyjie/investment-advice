from __future__ import annotations

import fcntl
import json
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)


@dataclass
class State:
    last_daily: date | None = None
    last_weekly: date | None = None
    prev_regime: str | None = None
    seen_alerts: dict[str, list[str]] = field(default_factory=dict)

    def alert_seen(self, day: date, rule_id: str) -> bool:
        return rule_id in self.seen_alerts.get(day.isoformat(), [])

    def mark_alerts(self, day: date, rule_ids: list[str]) -> None:
        key = day.isoformat()
        existing = set(self.seen_alerts.get(key, []))
        existing.update(rule_ids)
        self.seen_alerts = {key: sorted(existing)}


class Storage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "reports").mkdir(exist_ok=True)
        (self.root / "snapshots").mkdir(exist_ok=True)

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    def load_state(self) -> State:
        if not self.state_file.exists():
            return State()
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("state.json 损坏，重置")
            return State()
        return State(
            last_daily=_parse_date(raw.get("last_daily")),
            last_weekly=_parse_date(raw.get("last_weekly")),
            prev_regime=raw.get("prev_regime"),
            seen_alerts=raw.get("seen_alerts") or {},
        )

    def save_state(self, state: State) -> None:
        payload = {
            "last_daily": state.last_daily.isoformat() if state.last_daily else None,
            "last_weekly": state.last_weekly.isoformat() if state.last_weekly else None,
            "prev_regime": state.prev_regime,
            "seen_alerts": state.seen_alerts,
        }
        self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_report(self, kind: str, as_of: date, text: str) -> Path:
        path = self.root / "reports" / f"{kind}-{as_of.isoformat()}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def latest_report(self, kind: str | None = None) -> Path | None:
        pattern = f"{kind}-*.md" if kind else "*.md"
        files = sorted((self.root / "reports").glob(pattern))
        return files[-1] if files else None

    def save_snapshot(self, kind: str, as_of: date, payload: Any) -> Path:
        path = self.root / "snapshots" / f"{kind}-{as_of.isoformat()}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    @contextmanager
    def lock(self) -> Iterator[None]:
        lock_path = self.root / ".run.lock"
        with lock_path.open("w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("已有实例在运行") from exc
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def advice_to_dict(advice: Any) -> dict:
    from dataclasses import is_dataclass

    def convert(obj: Any) -> Any:
        if is_dataclass(obj):
            return {k: convert(v) for k, v in asdict(obj).items()}
        if isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [convert(v) for v in obj]
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return obj

    return convert(advice)
