from __future__ import annotations

from ia.llm.hotspot import HotspotPayload, validate


def _payload(topics):
    return HotspotPayload.model_validate({"market_summary": "市场震荡", "topics": topics})


def _topic(**overrides):
    base = {
        "name": "半导体回暖",
        "summary": "板块连续走强",
        "direction": "bullish",
        "confidence": 0.8,
        "related_symbols": [{"code": "512480", "name": "半导体ETF", "relation": "direct"}],
        "evidence": ["半导体板块大涨"],
        "risk": "注意估值",
    }
    base.update(overrides)
    return base


def test_valid_topic_passes():
    result = validate(
        _payload([_topic()]),
        allowed={"512480": "半导体ETF"},
        news_texts=("今日半导体板块大涨，资金流入",),
        min_confidence=0.5,
        max_topics=5,
    )
    assert result.available is True
    assert len(result.topics) == 1
    assert result.topics[0].related[0][0] == "512480"


def test_evidence_not_substring_drops_topic():
    result = validate(
        _payload([_topic(evidence=["编造的新闻标题"])]),
        allowed={"512480": "半导体ETF"},
        news_texts=("今日半导体板块大涨，资金流入",),
        min_confidence=0.5,
        max_topics=5,
    )
    assert result.topics == ()


def test_unknown_symbol_removed():
    result = validate(
        _payload([_topic(related_symbols=[{"code": "999999", "name": "野生标的"}])]),
        allowed={"512480": "半导体ETF"},
        news_texts=("半导体板块大涨",),
        min_confidence=0.5,
        max_topics=5,
    )
    assert result.topics[0].related == ()


def test_amount_stripped():
    result = validate(
        _payload([_topic(summary="资金流入3000亿元，半导体走强")]),
        allowed={"512480": "半导体ETF"},
        news_texts=("半导体板块大涨",),
        min_confidence=0.5,
        max_topics=5,
    )
    assert "3000亿" not in result.topics[0].summary
    assert "半导体走强" in result.topics[0].summary


def test_low_confidence_dropped():
    result = validate(
        _payload([_topic(confidence=0.3)]),
        allowed={"512480": "半导体ETF"},
        news_texts=("半导体板块大涨",),
        min_confidence=0.5,
        max_topics=5,
    )
    assert result.topics == ()


def test_max_topics_limit():
    result = validate(
        _payload([_topic(name=f"热点{i}") for i in range(8)]),
        allowed={"512480": "半导体ETF"},
        news_texts=("半导体板块大涨",),
        min_confidence=0.5,
        max_topics=3,
    )
    assert len(result.topics) == 3
