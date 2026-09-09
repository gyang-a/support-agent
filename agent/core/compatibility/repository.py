"""本地兼容性图谱的实体解析与可解释查询。

图谱用“商品 -> 商品组 <- 配件”的路径表达兼容关系。查询结果不仅返回
兼容/不兼容，还返回命中的路径、条件、限制和证据，避免模型只凭常识猜测。
生产环境可将同一份节点与关系同步到 Neo4j，而不改变上层工具契约。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.catalog import get_product_by_sku, search_products


_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "compatibility_graph.json"
_GENERIC_GROUPS = {"GROUP-USBC-PHONE", "GROUP-USBC-LAPTOP"}
_STATUS_LABELS = {
    "compatible": "兼容",
    "conditional": "有条件兼容",
    "incompatible": "不兼容",
    "unknown": "无法确认",
}


@lru_cache(maxsize=1)
def _load_graph() -> dict[str, Any]:
    """加载已审核的兼容性规则图谱。"""
    with _DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_compatibility_overview() -> dict[str, Any]:
    """返回图谱版本、规模与可查询配件，供 Agent 了解能力边界。"""
    graph = _load_graph()
    return {
        "graph_version": graph["graph_version"],
        "verified_at": graph["verified_at"],
        "group_count": len(graph["groups"]),
        "accessory_count": len(graph["accessories"]),
        "relation_count": len(graph["relations"]),
        "accessories": [
            {"id": item["id"], "name": item["name"]}
            for item in graph["accessories"]
        ],
        "notice": graph["notice"],
    }


def _product_text(product: dict[str, Any]) -> str:
    """构建商品组选择器使用的归一化全文。"""
    return " ".join(
        [
            product.get("sku_id", ""),
            product.get("category", ""),
            product.get("brand", ""),
            product.get("model", ""),
            product.get("variant", ""),
            " ".join(str(value) for value in product.get("specs", {}).values()),
            " ".join(product.get("use_cases", [])),
            " ".join(product.get("keywords", [])),
        ]
    ).lower()


def _matches_group(product: dict[str, Any], group: dict[str, Any]) -> bool:
    """判断目录商品是否属于图谱商品组。"""
    selector = group["selector"]
    text = _product_text(product)
    if selector.get("category") and product["category"] != selector["category"]:
        return False
    if (
        selector.get("model_contains")
        and selector["model_contains"].lower() not in product["model"].lower()
    ):
        return False
    if selector.get("sku_prefix") and not product["sku_id"].startswith(selector["sku_prefix"]):
        return False
    if selector.get("text_any") and not any(
        value.lower() in text for value in selector["text_any"]
    ):
        return False
    if selector.get("use_cases_any") and not any(
        value in product.get("use_cases", []) for value in selector["use_cases_any"]
    ):
        return False
    return True


def _resolve_entity(value: str) -> dict[str, Any] | None:
    """将 SKU、型号、配件 ID、名称或别名解析为图谱实体。"""
    normalized = value.strip().lower()
    if not normalized:
        return None

    graph = _load_graph()
    for accessory in graph["accessories"]:
        names = [accessory["id"], accessory["name"], *accessory.get("aliases", [])]
        if normalized in {name.lower() for name in names}:
            return {"type": "accessory", **accessory}

    product = get_product_by_sku(value)
    if product:
        return {"type": "product", **product}

    # 只检索与输入相关的少量候选，不再把整个商品目录加载到进程。
    products = search_products(keyword=value, limit=20)
    exact = [
        item
        for item in products
        if normalized in {item["model"].lower(), f"{item['brand']} {item['model']}".lower()}
    ]
    if exact:
        return {"type": "product", **exact[0]}

    # 模糊匹配仅在能够唯一定位时使用，防止“iPhone”被随意解析成某个 SKU。
    fuzzy = [item for item in products if normalized in _product_text(item)]
    if len(fuzzy) == 1:
        return {"type": "product", **fuzzy[0]}
    return None


def _result_unknown(source: str, target: str, reason: str) -> dict[str, Any]:
    """构造无法确认结果，明确提示不能把未知当作兼容。"""
    graph = _load_graph()
    return {
        "status": "unknown",
        "status_label": _STATUS_LABELS["unknown"],
        "source": source,
        "target": target,
        "reason": reason,
        "path": [],
        "conditions": [],
        "limitations": ["没有规则不等于兼容，购买前请核对厂商规格。"],
        "verified_at": graph["verified_at"],
        "notice": graph["notice"],
    }


def _entity_identity(entity: dict[str, Any]) -> tuple[str, str]:
    """返回商品或配件的统一 ID 与展示名称。"""
    if entity["type"] == "product":
        return entity["sku_id"], entity["model"]
    return entity["id"], entity["name"]


def check_compatibility(
    source: str,
    target: str,
    *,
    relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """查询两个实体的兼容性，并返回图谱路径和限制条件。"""
    source_entity = _resolve_entity(source)
    target_entity = _resolve_entity(target)
    if not source_entity or not target_entity:
        unresolved = source if not source_entity else target
        return _result_unknown(source, target, f"无法唯一识别实体：{unresolved}")

    entities = [source_entity, target_entity]
    product = next((item for item in entities if item["type"] == "product"), None)
    accessory = next((item for item in entities if item["type"] == "accessory"), None)
    if not product or not accessory:
        return _result_unknown(source, target, "当前图谱主要支持目录商品与配件之间的兼容查询。")

    graph = _load_graph()
    groups = [group for group in graph["groups"] if _matches_group(product, group)]
    group_ids = {group["id"] for group in groups}
    candidates: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    severity = {"incompatible": 3, "conditional": 2, "compatible": 1}

    for relation in relations if relations is not None else graph["relations"]:
        if relation["source"] != accessory["id"] or relation["target"] not in group_ids:
            continue
        if (
            relation.get("model_exact")
            and relation["model_exact"].lower() != product["model"].lower()
        ):
            continue
        group = next(item for item in groups if item["id"] == relation["target"])
        specificity = (
            3
            if relation.get("model_exact")
            else (1 if group["id"] in _GENERIC_GROUPS else 2)
        )
        candidates.append((specificity, severity[relation["status"]], relation, group))

    if not candidates:
        return _result_unknown(source, target, "图谱中没有覆盖该商品与配件组合的已审核关系。")

    _, _, relation, group = max(candidates, key=lambda item: (item[0], item[1]))
    status = relation["status"]
    source_id, source_name = _entity_identity(source_entity)
    target_id, target_name = _entity_identity(target_entity)
    return {
        "status": status,
        "status_label": _STATUS_LABELS[status],
        "source": {"id": source_id, "name": source_name, "type": source_entity["type"]},
        "target": {"id": target_id, "name": target_name, "type": target_entity["type"]},
        "path": [
            {"id": accessory["id"], "name": accessory["name"], "type": "accessory"},
            {"relation": status.upper(), "evidence": relation["evidence"]},
            {"id": group["id"], "name": group["name"], "type": "product_group"},
            {"relation": "CONTAINS"},
            {"id": product["sku_id"], "name": product["model"], "type": "product"},
        ],
        "conditions": relation.get("conditions", []),
        "limitations": relation.get("limitations", []),
        "evidence": relation["evidence"],
        "verified_at": graph["verified_at"],
        "notice": graph["notice"],
    }
