from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ia.models import Report


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    channel: str
    detail: str = ""


class Notifier(Protocol):
    name: str

    def send(self, report: Report) -> NotifyResult: ...
