from __future__ import annotations

from ia.models import Report
from ia.notify.base import NotifyResult
from ia.report.render_text import render_text


class ConsoleNotifier:
    name = "console"

    def send(self, report: Report) -> NotifyResult:
        print(render_text(report))
        return NotifyResult(True, self.name)
