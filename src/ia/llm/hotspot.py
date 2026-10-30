from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from ia.config import Secrets, Settings
from ia.llm.client import LLMClient
from ia.models import BUCKET_LABELS, HotspotResult, HotspotTopic, NewsItem
from ia.rules.engine import AnalysisContext

log = logging.getLogger(__name__)

AMOUNT_RE = re.compile(r"\d[\d.,]*\s*(?:万元|亿元|元|万|亿|股|份|手|%|％)")
FIELD_LIMITS = {"name": 40, "summary": 80, "risk": 50, "market_summary": 100}


class RelatedModel(BaseModel):
    code: str = ""
    name: str = ""
    relation: Literal["direct", "indirect"] = "direct"


class TopicModel(BaseModel):
    name: str = ""
    summary: str = ""
    direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    confidence: float = 0.5
    related_symbols: list[RelatedModel] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    risk: str = ""


class HotspotPayload(BaseModel):
    market_summary: str = ""
    topics: list[TopicModel] = Field(default_factory=list)
    disclaimer: str = ""


def _clean(text: str, limit: int) -> str:
    text = AMOUNT_RE.sub("", text or "")
    text = re.sub(r"\s{2,}", " ", text).strip(" ，,、；;")
    return text[:limit]


def validate(
    payload: HotspotPayload,
    allowed: dict[str, str],
    news_texts: tuple[str, ...],
    min_confidence: float,
    max_topics: int,
) -> HotspotResult:
    summary = _clean(payload.market_summary, FIELD_LIMITS["market_summary"])
    topics: list[HotspotTopic] = []
    for item in payload.topics:
        name = _clean(item.name, FIELD_LIMITS["name"])
        body = _clean(item.summary, FIELD_LIMITS["summary"])
        if not name or not body:
            continue
        evidence = tuple(e for e in item.evidence if e and any(e in text for text in news_texts))
        if not evidence:
            log.info("丢弃热点「%s」：evidence 不是输入新闻的子串", name)
            continue
        related: list[tuple[str, str, str]] = []
        for symbol in item.related_symbols:
            code = symbol.code.strip()
            if code not in allowed:
                continue
            related.append((code, allowed[code], symbol.relation))
        if item.confidence < min_confidence:
            continue
        topics.append(
            HotspotTopic(
                name=name,
                summary=body,
                direction=item.direction,
                confidence=round(item.confidence, 2),
                related=tuple(related[:3]),
                evidence=evidence[:2],
                risk=_clean(item.risk, FIELD_LIMITS["risk"]),
            )
        )
        if len(topics) >= max_topics:
            break
    return HotspotResult(
        available=bool(summary or topics),
        market_summary=summary,
        topics=tuple(topics),
    )


def _universe_map(ctx: AnalysisContext) -> dict[str, str]:
    allowed = {p.code: p.name for p in ctx.portfolio.positions}
    for items in ctx.universe.values():
        for item in items:
            allowed[item.code] = item.name
    return allowed


def _universe_text(ctx: AnalysisContext) -> str:
    lines: list[str] = []
    for bucket, items in ctx.universe.items():
        for item in items:
            lines.append(f"{item.code} {item.name}（{BUCKET_LABELS.get(bucket, bucket)}）")
    return "\n".join(lines) or "（未配置）"


def _render_news(news: tuple[NewsItem, ...]) -> tuple[str, tuple[str, ...]]:
    lines: list[str] = []
    texts: list[str] = []
    for item in news[:60]:
        combined = f"{item.title}｜{item.summary}"[:140] if item.summary else item.title[:120]
        texts.append(combined)
        lines.append(f"{item.published_at.strftime('%m-%d')} {combined}")
    return "\n".join(lines), tuple(texts)


def _render_boards(ctx: AnalysisContext) -> tuple[str, str]:
    boards = "、".join(f"{b.name} {b.chg_pct:+.1f}%" for b in ctx.market.board_top[:8]) or "（无数据）"
    flows = "、".join(
        f"{b.name} {b.net_inflow_yi:+.1f}亿" for b in ctx.market.board_flow_top[:8] if b.net_inflow_yi is not None
    ) or "（无数据）"
    return boards, flows


def analyze(ctx: AnalysisContext, settings: Settings, secrets: Secrets, enabled: bool = True) -> HotspotResult:
    if not enabled or not settings.llm.enabled:
        return HotspotResult(available=False, error="未启用模型解读")
    if not secrets.deepseek_api_key:
        return HotspotResult(available=False, error="未配置 DEEPSEEK_API_KEY")

    try:
        news = ctx.source.news(7)
    except Exception as exc:  # noqa: BLE001
        log.warning("新闻获取失败: %s", exc)
        news = ()

    news_text, news_texts = _render_news(news)
    boards, flows = _render_boards(ctx)
    holdings = "、".join(p.name for p in ctx.portfolio.positions) or "（无持仓）"
    prompts_dir = settings.path(settings.prompts_dir)
    system = (prompts_dir / "hotspot.system.md").read_text(encoding="utf-8")
    template = (prompts_dir / "hotspot.user.md").read_text(encoding="utf-8")
    user = (
        template.replace("{{regime}}", f"{ctx.regime.regime.label}（评分 {ctx.regime.score:+d}）")
        .replace("{{holdings}}", holdings)
        .replace("{{news}}", news_text or "（无新闻）")
        .replace("{{boards}}", boards)
        .replace("{{flows}}", flows)
        .replace("{{universe}}", _universe_text(ctx))
    )

    try:
        client = LLMClient(settings.llm, secrets.deepseek_api_key)
        raw = client.chat_json(system, user)
        payload = HotspotPayload.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - 模型不可用不能阻塞报告
        log.warning("热点分析失败: %s", exc)
        return HotspotResult(available=False, error=str(exc)[:120])

    return validate(
        payload,
        allowed=_universe_map(ctx),
        news_texts=news_texts,
        min_confidence=settings.llm.min_confidence,
        max_topics=settings.llm.max_topics,
    )
