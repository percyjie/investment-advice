from __future__ import annotations

from ia.models import Report


def render_text(report: Report) -> str:
    lines = [f"【{report.title}】{report.as_of.isoformat() if report.as_of else ''}", report.headline, ""]
    for section in report.sections:
        lines.append(f"■ {section.title}")
        lines.extend(f"- {line}" for line in section.lines)
        lines.append("")
    lines.append(report.footer)
    return "\n".join(lines).strip() + "\n"
