"""保守的在线规则入口：关键词只提供候选，完整句式才允许直达。"""

import re
from dataclasses import dataclass
from typing import Any


AGENT_KEYWORDS = {
    "order_agent": ("订单", "物流", "快递", "发货", "支付状态", "退款进度", "退款到账"),
    "product_agent": ("参数", "功能", "接口", "兼容", "适配", "库存", "使用说明"),
    "recommendation_agent": ("推荐", "买哪", "怎么选", "选购", "预算", "性价比", "挑一款"),
    "after_sales_agent": ("故障", "保修", "维修", "退货", "换货", "退换货", "售后", "工单", "转人工", "状态灯", "故障码"),
}
FALLBACK_SIGNALS = (
    "另外", "顺便", "同时", "以及", "再帮我", "如果", "否则", "要是",
    "不要", "不用", "不是", "别", "先", "然后",
)


@dataclass(frozen=True)
class DirectRule:
    rule_id: str
    agent_name: str
    pattern: re.Pattern[str]


# 仅允许明确订单标识的只读查询。不使用 .* 吞掉未识别的其他需求。
_PREFIX = r"(?:请|麻烦)?\s*(?:帮我)?\s*(?:查询|查一下|查查|查看)\s*"
_ORDER = r"(?:我的)?订单(?:号)?\s*[:：]?\s*(?P<order_id>DG-\d+(?:-\d+)*)\s*"
_SUFFIX = r"[。？！!?]?"
DIRECT_RULES = tuple(
    DirectRule(rule_id, "order_agent", re.compile(_PREFIX + _ORDER + target + _SUFFIX, re.IGNORECASE))
    for rule_id, target in (
        ("order_detail", r"(?:的?(?:详情|详细信息|信息|状态))?"),
        ("order_logistics", r"的?(?:物流信息|物流状态|物流|快递|发货状态)"),
        ("order_payment", r"的?支付状态"),
        ("order_refund", r"的?(?:退款进度|退款到账进度)"),
    )
)


def recognize_direct_route(message: str) -> dict[str, Any]:
    """返回直达结果或可观测的回退原因；不猜测未覆盖的语义。"""
    text = message.strip()
    hits = {
        agent: [word for word in words if word in text]
        for agent, words in AGENT_KEYWORDS.items()
    }
    hits = {agent: words for agent, words in hits.items() if words}
    result: dict[str, Any] = {
        "candidate_agents": list(hits), "matched_keywords": hits,
        "agent_name": None, "rule_id": None,
    }
    if len(hits) != 1:
        return {**result, "reason": "no_candidate" if not hits else "multiple_agents"}
    signals = [word for word in FALLBACK_SIGNALS if word in text]
    if signals:
        return {**result, "reason": "complexity_signal", "signals": signals}
    matches = [
        (rule, match) for rule in DIRECT_RULES
        if rule.agent_name in hits and (match := rule.pattern.fullmatch(text))
    ]
    if len(matches) != 1:
        return {**result, "reason": "no_full_match" if not matches else "ambiguous_rules"}
    rule, match = matches[0]
    return {
        **result, "reason": "full_match", "agent_name": rule.agent_name,
        "rule_id": rule.rule_id, "entities": match.groupdict(),
    }
