"""规则驱动的设备故障预诊断引擎。

引擎只提供安全的基础排查和人工分流，不输出拆机维修步骤。严重安全症状
优先于普通故障规则，避免“无法充电”等次要描述掩盖鼓包、进液等风险。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.catalog import get_product_by_sku
from core.policy import search_policies


_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "diagnostic_rules.json"
_SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@lru_cache(maxsize=1)
def _load_rules() -> dict[str, Any]:
    """加载安全审核后的诊断规则。"""
    with _DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_diagnostic_overview() -> dict[str, Any]:
    """返回诊断规则版本和当前覆盖的故障类型。"""
    rules = _load_rules()
    return {
        "rule_version": rules["rule_version"],
        "verified_at": rules["verified_at"],
        "supported_issues": [item["name"] for item in rules["rules"]],
        "notice": rules["notice"],
    }


def diagnose_issue(
    symptom: str,
    sku_id: str = "",
    category: str = "",
) -> dict[str, Any]:
    """根据症状和可选 SKU 返回主要诊断、安全步骤及政策证据。"""
    product = get_product_by_sku(sku_id) if sku_id else None
    if sku_id and not product:
        return {
            "status": "unknown_product",
            "message": "未找到该 SKU，请先查询商品目录确认型号。",
            "sku_id": sku_id,
        }

    normalized_category = product["category"] if product else category.strip()
    normalized_symptom = symptom.strip().lower()
    rules = _load_rules()
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for rule in rules["rules"]:
        if normalized_category and normalized_category not in rule["categories"]:
            continue
        matched = [keyword for keyword in rule["keywords"] if keyword.lower() in normalized_symptom]
        if matched:
            candidates.append((len(matched), _SEVERITY_WEIGHT[rule["severity"]], rule))

    # 严重度优先，匹配词数量作为同等级下的次要信号。
    candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
    if not candidates:
        return {
            "status": "insufficient_information",
            "severity": "unknown",
            "questions": [
                "请补充完整型号或 SKU。",
                "故障从何时开始，能否稳定复现？",
                "是否发生过跌落、进液、鼓包、异味或异常高温？",
                "是否有错误码、照片或视频？",
            ],
            "safe_steps": ["在信息不足时不要拆机或反复强制通电。"],
            "ticket_recommended": False,
            "manual_required": False,
            "verified_at": rules["verified_at"],
            "notice": rules["notice"],
        }

    _, _, primary = candidates[0]
    policy_query = " ".join(primary.get("policy_queries", []))
    policies = search_policies(policy_query, limit=3) if policy_query else {"results": []}
    return {
        "status": "matched",
        "rule_id": primary["rule_id"],
        "issue_name": primary["name"],
        "severity": primary["severity"],
        "product": (
            {"sku_id": product["sku_id"], "brand": product["brand"], "model": product["model"]}
            if product
            else None
        ),
        "matched_keywords": [
            keyword for keyword in primary["keywords"] if keyword.lower() in normalized_symptom
        ],
        "questions": primary["questions"],
        "safe_steps": primary["safe_steps"],
        "stop_conditions": primary["stop_conditions"],
        "ticket_recommended": primary["ticket_recommended"],
        "manual_required": primary["manual_required"],
        "policy_evidence": policies["results"],
        "alternative_rules": [item[2]["name"] for item in candidates[1:3]],
        "verified_at": rules["verified_at"],
        "notice": rules["notice"],
    }
