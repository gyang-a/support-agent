"""无需额外模型调用的客服质量规则评测器。

线上监控优先使用确定性规则，避免评测器本身增加延迟和不可复现性。后续可
在离线报告中加入 DeepSeek LLM-as-a-judge，但不能替代权限与工具规则检查。
"""

from __future__ import annotations

import re
from typing import Any

from core.routing import keyword_fallback


_USER_ID_PATTERN = re.compile(r"\buser[_-]\d+\b", flags=re.IGNORECASE)


def _expected_tools(query: str, expected_agent: str) -> set[str]:
    """根据问题与期望路由推导至少应调用的事实工具。"""
    normalized = query.lower()
    if expected_agent == "recommendation_agent":
        return {"recommend_products"}
    if expected_agent == "product_agent" and any(word in normalized for word in ("对比", "区别", "差异")):
        return {"compare_product_skus"}
    if expected_agent == "product_agent" and any(
        word in normalized
        for word in (
            "接口",
            "端口",
            "怎么用",
            "如何使用",
            "说明书",
            "支持什么",
            "充电",
            "功率",
            "外接",
        )
    ):
        return {"search_technical_documents"}
    if expected_agent == "order_agent":
        if any(word in normalized for word in ("物流", "快递", "到哪里", "送达")):
            return {"query_shipment_timeline"}
        return {"query_user_orders", "query_order_detail"}
    if expected_agent == "after_sales_agent":
        if any(word in normalized for word in ("创建", "提交")) and "工单" in normalized:
            return {"create_after_sales_ticket"}
        if any(word in normalized for word in ("查询工单", "我的工单", "工单进度")):
            return {"query_user_tickets", "query_ticket_detail"}
        if any(word in normalized for word in ("退货", "换货", "保修", "政策", "七天")):
            return {"search_after_sales_policies"}
        return {"diagnose_device_issue"}
    return set()


def evaluate_interaction(
    query: str,
    response: str,
    actual_agent: str,
    tool_names: list[str] | None = None,
    tool_errors: list[str] | None = None,
    expected_agent: str | None = None,
    expected_tools: list[str] | None = None,
    forbidden_phrases: list[str] | None = None,
) -> dict[str, Any]:
    """评估单轮路由、工具、事实约束、安全性和回答完整度。"""
    actual_tools = set(tool_names or [])
    expected_route = expected_agent or keyword_fallback(query)
    required_tools = set(expected_tools if expected_tools is not None else _expected_tools(query, expected_route))
    forbidden = forbidden_phrases or []

    route_score = 1.0 if actual_agent == expected_route else 0.0
    tool_score = 1.0 if not required_tools or bool(actual_tools & required_tools) else 0.0
    tool_health_score = 1.0 if not tool_errors else 0.0
    groundedness_score = 1.0 if not required_tools or tool_score == 1.0 else 0.0
    leaked_user_id = bool(_USER_ID_PATTERN.search(response))
    forbidden_hits = [phrase for phrase in forbidden if phrase and phrase in response]
    security_score = 0.0 if leaked_user_id or forbidden_hits else 1.0
    response_score = 1.0 if len(response.strip()) >= 8 else 0.0

    overall = round(
        route_score * 0.2
        + tool_score * 0.2
        + tool_health_score * 0.1
        + groundedness_score * 0.2
        + security_score * 0.2
        + response_score * 0.1,
        4,
    )
    reasons: list[str] = []
    if route_score == 0:
        reasons.append(f"路由不一致：期望 {expected_route}，实际 {actual_agent}")
    if tool_score == 0:
        reasons.append(f"缺少必要工具：{sorted(required_tools)}")
    if tool_health_score == 0:
        reasons.append(f"工具调用失败：{tool_errors}")
    if leaked_user_id:
        reasons.append("回答泄露了内部 user_id")
    if forbidden_hits:
        reasons.append(f"命中禁止表述：{forbidden_hits}")
    if response_score == 0:
        reasons.append("回答过短或为空")

    return {
        "expected_agent": expected_route,
        "actual_agent": actual_agent,
        "expected_tools": sorted(required_tools),
        "actual_tools": sorted(actual_tools),
        "scores": {
            "route": route_score,
            "tool": tool_score,
            "tool_health": tool_health_score,
            "groundedness": groundedness_score,
            "security": security_score,
            "response": response_score,
            "overall": overall,
        },
        "passed": overall >= 0.8 and security_score == 1.0,
        "reasons": reasons,
    }
