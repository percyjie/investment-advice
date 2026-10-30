from __future__ import annotations

from ia.models import Level, Report

TEMPLATES = {
    Level.GREEN: "green",
    Level.YELLOW: "yellow",
    Level.RED: "red",
}


def _markdown(report: Report) -> str:
    lines = [f"**{report.headline}**", ""]
    for section in report.sections:
        lines.append(f"**{section.title}**")
        lines.extend(f"- {line}" for line in section.lines)
        lines.append("")
    lines.append(report.footer)
    return "\n".join(lines).strip()


def render_feishu(report: Report) -> dict:
    title = f"{report.title} · {report.as_of.isoformat()}" if report.as_of else report.title
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": TEMPLATES[report.level],
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": _markdown(report)}},
        ],
    }
