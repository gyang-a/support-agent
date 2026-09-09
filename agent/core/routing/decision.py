"""不依赖大模型 SDK 的路由结果解析与兜底规则。"""

from __future__ import annotations

import json
import re
from typing import Any


ALLOWED_AGENT_NAMES = {
    "product_agent",
    "recommendation_agent",
    "order_agent",
    "after_sales_agent",
}


def deterministic_route(message: str) -> str | None:
    """高置信度规则路由；没有明确命中时交给模型判断。"""
    normalized = message.lower()
    if any(word in normalized for word in ("退款进度", "退款到账", "支付状态")):
        return "order_agent"
    if any(
        word in normalized
        for word in (
            "售后",
            "退货",
            "换货",
            "保修",
            "维修",
            "工单",
            "转人工",
            "人工客服",
            "无法开机",
            "开不了机",
            "进水",
            "进液",
            "鼓包",
            "发烫",
            "过热",
            "死机",
            "蓝屏",
            "花屏",
            "闪屏",
            "指示灯",
            "状态灯",
            "故障灯",
            "黄灯",
            "红灯",
            "报警灯",
            "故障码",
            "错误码",
            "报错",
            "异响",
            "不工作",
            "充不进电",
            "频繁重启",
        )
    ):
        return "after_sales_agent"
    if any(word in normalized for word in ("订单", "物流", "快递", "发货", "付款")):
        return "order_agent"
    if any(
        word in normalized
        for word in (
            "兼容",
            "适配",
            "能不能用",
            "能不能接",
            "充电器",
            "数据线",
            "扩展坞",
            "雷电4",
            "type-c耳机",
        )
    ):
        # 暂不启用独立兼容性 Agent；配件和连接问题交由商品 Agent 使用
        # 目录与技术文档工具处理。
        return "product_agent"
    if any(word in normalized for word in ("推荐", "买哪", "怎么选", "预算", "选购", "性价比")):
        return "recommendation_agent"
    return None


def keyword_fallback(message: str) -> str:
    """模型输出非法时复用规则，并安全回落到商品咨询。"""
    matched_agent = deterministic_route(message)
    if matched_agent is not None:
        return matched_agent
    return "product_agent"


def parse_route_decision(
    raw_content: str,
    message: str,
) -> tuple[str, dict[str, Any]]:
    """解析模型 JSON，并保证非法输出不会进入未注册的图节点。"""
    try:
        json_match = re.search(r"\{.*\}", raw_content, flags=re.DOTALL)
        payload = json.loads(json_match.group(0) if json_match else raw_content)
    except (json.JSONDecodeError, AttributeError, TypeError):
        payload = {}

    if not isinstance(payload, dict):
        payload = {}
    next_agent = payload.get("next_agent", "")
    if not isinstance(next_agent, str) or next_agent not in ALLOWED_AGENT_NAMES | {"task_planner"}:
        next_agent = keyword_fallback(message)
        payload = {
            "next_agent": next_agent,
            "intent": "fallback",
            "confidence": 0.5,
            "entities": {},
        }
    return next_agent, payload
