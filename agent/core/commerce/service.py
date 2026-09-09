"""组合商品目录、价格中心和库存中心的应用服务。

目录层只保存稳定规格，市场层提供频繁变化的价格和库存。把二者组合放在
应用服务中，可以防止 MCP 协议层承担筛选与对比等业务规则。
"""

from __future__ import annotations

from typing import Any

from core.catalog import get_product_by_sku, recommend_products, search_products
from core.market import get_market_state, get_market_states


def _attach_market(
    product: dict[str, Any],
    region: str,
    channel: str,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """复制商品并使用动态市场字段覆盖目录中的参考价格和库存。"""
    result = dict(product)
    if market is None:
        market = get_market_state(product["sku_id"], region=region, channel=channel)
    result["catalog_reference_price"] = result.pop("price", None)
    result["catalog_reference_stock"] = result.pop("stock", None)
    result["market"] = market
    if market:
        # 保留扁平字段可兼容第一阶段 Agent，同时把完整可追溯信息放在 market。
        result["price"] = market["current_price"]
        result["stock"] = market["stock"]
        result["inventory_status"] = market["inventory_status"]
    return result


def get_product_with_market(
    sku_id: str,
    region: str = "CN",
    channel: str = "official",
) -> dict[str, Any] | None:
    """获取完整规格，并附加指定渠道的最新市场状态。"""
    product = get_product_by_sku(sku_id, region=region, channel=channel)
    return _attach_market(product, region, channel) if product else None


def search_products_with_market(
    keyword: str = "",
    category: str = "",
    brand: str = "",
    min_price: float = 0,
    max_price: float = 0,
    limit: int = 10,
    region: str = "CN",
    channel: str = "official",
) -> list[dict[str, Any]]:
    """搜索商品，并使用动态价格进行最终预算过滤。"""
    # 品类、品牌、价格条件均在 MySQL 内完成，工具只接收 LIMIT 后的候选。
    candidates = search_products(
        keyword=keyword,
        category=category,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
        region=region,
        channel=channel,
    )
    market_states = get_market_states(
        [item["sku_id"] for item in candidates], region=region, channel=channel
    )
    enriched = [
        _attach_market(item, region, channel, market_states.get(item["sku_id"]))
        for item in candidates
    ]
    filtered = [
        item
        for item in enriched
        if (not min_price or item.get("price", 0) >= float(min_price))
        and (not max_price or item.get("price", 0) <= float(max_price))
    ]
    safe_limit = max(1, min(int(limit), 20))
    return filtered[:safe_limit]


def recommend_available_products(
    category: str,
    budget_max: float,
    use_cases: str = "",
    preferred_brands: str = "",
    limit: int = 3,
    region: str = "CN",
    channel: str = "official",
) -> list[dict[str, Any]]:
    """根据动态价格和库存对目录候选进行二次过滤和排序。"""
    # 数据库先按预算和有报价的 SKU 召回，服务层再做用途与偏好排序。
    candidates = recommend_products(
        category=category,
        budget_max=budget_max,
        use_cases=use_cases,
        preferred_brands=preferred_brands,
        limit=5,
        region=region,
        channel=channel,
    )
    market_states = get_market_states(
        [item["sku_id"] for item in candidates], region=region, channel=channel
    )
    enriched = [
        _attach_market(item, region, channel, market_states.get(item["sku_id"]))
        for item in candidates
    ]
    available = [
        item
        for item in enriched
        if item.get("stock", 0) > 0
        and (not budget_max or item.get("price", 0) <= float(budget_max))
    ]
    available.sort(
        key=lambda item: (item.get("recommendation_score", 0), item.get("stock", 0)),
        reverse=True,
    )
    safe_limit = max(1, min(int(limit), 5))
    return available[:safe_limit]


def compare_products(
    sku_ids: list[str],
    region: str = "CN",
    channel: str = "official",
) -> dict[str, Any]:
    """按统一维度比较 2-5 个 SKU，并保留缺失规格而不臆造数据。"""
    normalized_ids = list(
        dict.fromkeys(item.strip().upper() for item in sku_ids if item.strip())
    )
    if not 2 <= len(normalized_ids) <= 5:
        raise ValueError("商品对比需要提供 2-5 个不同的 SKU。")

    products = [
        product
        for sku_id in normalized_ids
        if (product := get_product_with_market(sku_id, region=region, channel=channel))
    ]
    found_sku_ids = {product["sku_id"] for product in products}
    missing = [sku_id for sku_id in normalized_ids if sku_id not in found_sku_ids]
    dimensions: list[dict[str, Any]] = []
    base_fields = ["brand", "model", "variant", "price", "stock", "inventory_status"]
    spec_fields = sorted({key for product in products for key in product.get("specs", {})})

    for field in base_fields + spec_fields:
        values = {
            product["sku_id"]: (
                product.get(field)
                if field in base_fields
                else product.get("specs", {}).get(field, "未提供")
            )
            for product in products
        }
        dimensions.append(
            {
                "field": field,
                "values": values,
                "same": len({str(value) for value in values.values()}) <= 1,
            }
        )

    return {
        "products": products,
        "dimensions": dimensions,
        "missing_sku_ids": missing,
        "region": region.upper(),
        "channel": channel.lower(),
        "comparison_notice": "仅比较目录与当前市场快照中的字段，不代表厂商永久规格或最终成交价。",
    }
