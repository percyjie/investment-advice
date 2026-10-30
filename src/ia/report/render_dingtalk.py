from __future__ import annotations

from ia.models import Report


def render_dingtalk(report: Report) -> str:
    title = f"{report.title} · {report.as_of.isoformat()}" if report.as_of else report.title
    lines = [f"### {title}", "", f"**{report.headline}**", ""]
    for section in report.sections:
        lines.append(f"**{section.title}**")
        lines.extend(f"- {line}" for line in section.lines)
        lines.append("")
    lines.append(f"> {report.footer}")
    text = "\n".join(lines).strip()
    return text[:19000]
