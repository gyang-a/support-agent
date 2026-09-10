"""两个知识型 Agent 的轻量预检索规则与证据裁剪。"""

from __future__ import annotations

import json
from typing import Any


PRODUCT_TECHNICAL_TERMS = (
    "支持",
    "日志",
    "导出",
    "接口",
    "端口",
    "协议",
    "参数",
    "规格",
    "功能",
    "怎么用",
    "如何使用",
    "怎么设置",
    "怎么连接",
    "是否支持",
    "支持什么",
    "充电",
    "功率",
    "外接",
    "说明书",
    "兼容",
    "适配",
    "能不能接",
    "扩展坞",
    "充电器",
    "数据线",
)

AFTER_SALES_TECHNICAL_TERMS = (
    "指示灯",
    "状态灯",
    "故障灯",
    "黄灯",
    "红灯",
    "错误码",
    "故障码",
    "报错",
    "异响",
    "不工作",
    "无法开机",
    "开不了机",
    "充不进电",
    "发烫",
    "过热",
    "死机",
    "蓝屏",
    "花屏",
    "闪屏",
    "保修",
)

AFTER_SALES_POLICY_TERMS = (
    "退货",
    "换货",
    "保修",
    "维修政策",
    "七天",
    "寄修",
    "售后政策",
)


def should_prefetch_technical(agent_name: str, query: str) -> bool:
    """只对两个知识型 Agent 的高置信度关键词启用技术 RAG。"""

    normalized = query.casefold()
    terms = {
        "product_agent": PRODUCT_TECHNICAL_TERMS,
        "after_sales_agent": AFTER_SALES_TECHNICAL_TERMS,
    }.get(agent_name, ())
    return any(term in normalized for term in terms)


def should_prefetch_policy(agent_name: str, query: str) -> bool:
    """售后政策关键词命中时预先检索审核过的政策知识。"""

    if agent_name != "after_sales_agent":
        return False
    normalized = query.casefold()
    return any(term in normalized for term in AFTER_SALES_POLICY_TERMS)


def latest_user_query(state: dict[str, Any]) -> str:
    """优先检索分配的子问题，避免跨领域整句触发无关的预检索。"""

    if state.get("current_task"):
        return state["current_task"]["query"]

    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return ""


def compact_technical_evidence(raw_result: str) -> tuple[str, bool]:
    """裁剪技术检索结果，保留原文、适用型号、引用和重排来源。"""

    try:
        payload = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError):
        return "技术知识预检索返回了无法解析的结果。", False
    if payload.get("status") != "success":
        return str(payload.get("message", "技术知识库没有返回可靠证据。")), False
    data = payload.get("data", {})
    evidence = [
        {
            "document_id": item.get("document_id", ""),
            "product_model": item.get("product_model", ""),
            "title": item.get("title", ""),
            "section": item.get("section_path") or item.get("section", ""),
            "version": item.get("version", ""),
            "citation": item.get("citation", ""),
            "excerpt": item.get("excerpt", ""),
            "rerank_score": item.get("rerank_score"),
            "rerank_logit": item.get("rerank_logit"),
            "reranker_mode": item.get("reranker_mode", ""),
        }
        for item in data.get("results", [])
    ]
    from core.observability.live_events import emit
    emit("retrieval.completed", query=data.get("query", ""), results=evidence, stage="model_context", label="技术知识预检索 · 模型上下文")
    return json.dumps(
        {
            "query": data.get("query", ""),
            "resolved_product_models": data.get("resolved_product_models", []),
            "results": evidence,
        },
        ensure_ascii=False,
        indent=2,
    ), bool(evidence)


def compact_policy_evidence(raw_result: str) -> tuple[str, bool]:
    """裁剪政策检索结果，保留生效日期和可追溯引用。"""

    try:
        payload = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError):
        return "售后政策预检索返回了无法解析的结果。", False
    if payload.get("status") != "success":
        return str(payload.get("message", "售后政策库没有返回可靠证据。")), False
    data = payload.get("data", {})
    evidence = [
        {
            "document_id": item.get("document_id", ""),
            "title": item.get("title", ""),
            "section": item.get("section_path") or item.get("section", ""),
            "effective_date": item.get("effective_date", data.get("effective_date", "")),
            "citation": item.get("citation", ""),
            "excerpt": item.get("excerpt", ""),
        }
        for item in data.get("results", [])
    ]
    from core.observability.live_events import emit
    emit("retrieval.completed", query=data.get("query", ""), results=evidence, stage="model_context", label="售后政策预检索 · 模型上下文")
    return json.dumps(
        {
            "query": data.get("query", ""),
            "effective_date": data.get("effective_date", ""),
            "notice": data.get("notice", ""),
            "results": evidence,
        },
        ensure_ascii=False,
        indent=2,
    ), bool(evidence)
