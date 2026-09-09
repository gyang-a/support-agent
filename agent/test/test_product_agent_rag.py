"""ProductAgent 强制技术知识预检索的回归测试。"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

from core.evaluation import evaluate_interaction
from core.knowledge.prefetch import (
    compact_technical_evidence,
    latest_user_query,
    should_prefetch_policy,
    should_prefetch_technical,
)


def test_latest_natural_language_is_used_for_product_rag() -> None:
    state = {
        "messages": [HumanMessage(content="星云 AirBook 14 Pro 支持什么接口？")]
    }

    assert latest_user_query(state) == "星云 AirBook 14 Pro 支持什么接口？"


def test_prefetch_context_keeps_evidence_and_reranker_metadata() -> None:
    raw = json.dumps(
        {
            "status": "success",
            "data": {
                "query": "AirBook 14 Pro 接口",
                "resolved_product_models": ["airbook_14_pro"],
                "results": [
                    {
                        "document_id": "nebula_airbook_14_pro_manual",
                        "product_model": "airbook_14_pro",
                        "title": "使用说明",
                        "section_path": "接口说明",
                        "version": "2026.08",
                        "citation": "[doc] 使用说明 / 接口说明",
                        "excerpt": "USB-C 1、USB-C 2、USB-A、HDMI。",
                        "rerank_logit": 5.7,
                        "reranker_mode": "bge_cross_encoder",
                    }
                ],
            },
        },
        ensure_ascii=False,
    )

    context, hit = compact_technical_evidence(raw)

    assert hit is True
    assert "nebula_airbook_14_pro_manual" in context
    assert "USB-C 1" in context
    assert "bge_cross_encoder" in context


def test_interface_answer_fails_quality_check_without_rag_tool() -> None:
    missing = evaluate_interaction(
        query="星云 AirBook 14 Pro 支持什么接口？",
        response="目录里没有这个商品。",
        actual_agent="product_agent",
        tool_names=["search_product_catalog"],
    )
    grounded = evaluate_interaction(
        query="星云 AirBook 14 Pro 支持什么接口？",
        response="根据说明书，它提供 USB-C、USB-A 和 HDMI。",
        actual_agent="product_agent",
        tool_names=["search_technical_documents"],
    )

    assert missing["passed"] is False
    assert missing["scores"]["groundedness"] == 0
    assert grounded["passed"] is True


def test_only_two_agents_have_rule_based_prefetch() -> None:
    assert should_prefetch_technical("product_agent", "支持什么接口") is True
    assert should_prefetch_technical("product_agent", "你好") is False
    assert should_prefetch_technical("after_sales_agent", "黄灯一直亮") is True
    assert should_prefetch_policy("after_sales_agent", "保修期多久") is True
    assert should_prefetch_technical("recommendation_agent", "支持什么接口") is False
    assert should_prefetch_policy("order_agent", "保修期多久") is False
