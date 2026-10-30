from __future__ import annotations

from datetime import date

from ia.models import Level, Report, ReportSection
from ia.report.render_dingtalk import render_dingtalk
from ia.report.render_feishu import render_feishu
from ia.report.render_text import render_text


def _report() -> Report:
    return Report(
        kind="weekly",
        title="投资周报",
        level=Level.YELLOW,
        headline="防守（评分 -3）· 本周 1 笔操作",
        sections=(
            ReportSection("本周操作", ("买入 黄金ETF 4,300 元（约 860 份）",)),
            ReportSection("风险提示", ("现金偏低",)),
        ),
        footer="仅供参考，不构成投资建议",
        as_of=date(2026, 10, 30),
    )


def test_text_render():
    text = render_text(_report())
    assert "投资周报" in text
    assert "本周操作" in text
    assert "黄金ETF" in text
    assert "仅供参考" in text


def test_feishu_card():
    card = render_feishu(_report())
    assert card["header"]["template"] == "yellow"
    assert "投资周报" in card["header"]["title"]["content"]
    content = card["elements"][0]["text"]["content"]
    assert "买入 黄金ETF" in content
    assert card["config"]["wide_screen_mode"] is True


def test_dingtalk_markdown():
    text = render_dingtalk(_report())
    assert text.startswith("### 投资周报")
    assert "买入 黄金ETF" in text
    assert "> 仅供参考" in text
