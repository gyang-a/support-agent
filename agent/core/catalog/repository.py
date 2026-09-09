"""MySQL 商品目录仓储。

Agent 不接触数据库连接或 SQL，只能通过固定 MCP 工具触发这里的参数化查询。
列表查询最多返回少量摘要，详情查询只返回指定 SKU。
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core.persistence.models import Product, ProductOffer

from .database import get_catalog_engine


_CATEGORY_ALIASES = {
    "手机": "手机",
    "智能手机": "手机",
    "phone": "手机",
    "笔记本": "笔记本",
    "笔记本电脑": "笔记本",
    "电脑": "笔记本",
    "laptop": "笔记本",
}


def _normalize_category(category: str) -> str:
    normalized = category.strip().lower()
    return _CATEGORY_ALIASES.get(normalized, category.strip())


def _split_terms(value: str) -> list[str]:
    return [
        term.strip().lower()
        for term in re.split(r"[,，、;；\s]+", value or "")
        if term.strip()
    ]


def _plain_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def _as_product(
    product: Product,
    offer: ProductOffer | None,
    *,
    detail: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sku_id": product.sku_id,
        "category": product.category,
        "brand": product.brand,
        "model": product.model,
        "variant": product.variant,
        "price": _plain_number(offer.current_price) if offer else None,
        "stock": offer.stock if offer else None,
        "use_cases": list(product.use_cases or []),
        "key_specs": dict(product.specs or {}),
    }
    if detail:
        result.update(
            {
                "specs": dict(product.specs or {}),
                "keywords": list(product.keywords or []),
                "source": {
                    "name": product.source_name,
                    "url": product.source_url,
                    "product_id": product.source_product_id,
                    "verified_at": _serialize_datetime(product.verified_at),
                },
            }
        )
    return result


def get_catalog_overview(category: str = "") -> dict[str, Any]:
    """使用聚合 SQL 返回目录规模，不加载全部商品。"""
    conditions = [Product.status == "active"]
    if category:
        conditions.append(Product.category == _normalize_category(category))

    counts_stmt = (
        select(Product.category, func.count(Product.id))
        .where(*conditions)
        .group_by(Product.category)
    )
    brands_stmt = select(Product.brand).where(*conditions).distinct().order_by(Product.brand)
    metadata_stmt = select(
        func.max(Product.verified_at), func.count(func.distinct(Product.source_name))
    ).where(*conditions)

    with Session(get_catalog_engine()) as session:
        counts = {name: int(count) for name, count in session.execute(counts_stmt)}
        brands = list(session.execute(brands_stmt).scalars())
        verified_at, source_count = session.execute(metadata_stmt).one()

    return {
        "verified_at": _serialize_datetime(verified_at),
        "category_counts": counts,
        "brands": brands,
        "source_count": int(source_count or 0),
        "notice": "商品来自 MySQL",
    }


def search_products(
    keyword: str = "",
    category: str = "",
    brand: str = "",
    min_price: float = 0,
    max_price: float = 0,
    limit: int = 10,
    region: str = "CN",
    channel: str = "official",
) -> list[dict[str, Any]]:
    """在数据库内过滤、排序和截断，返回少量商品摘要。"""
    offer_scope = and_(
        ProductOffer.product_id == Product.id,
        ProductOffer.region == (region or "CN").strip().upper(),
        ProductOffer.channel == (channel or "official").strip().lower(),
    )
    conditions = [Product.status == "active"]
    if category:
        conditions.append(Product.category == _normalize_category(category))
    if brand:
        conditions.append(func.lower(Product.brand).like(f"%{brand.strip().lower()}%"))
    if min_price:
        conditions.append(ProductOffer.current_price >= float(min_price))
    if max_price:
        conditions.append(ProductOffer.current_price <= float(max_price))
    for term in _split_terms(keyword):
        like_term = f"%{term}%"
        conditions.append(
            or_(
                func.lower(Product.sku_id).like(like_term),
                func.lower(Product.brand).like(like_term),
                func.lower(Product.model).like(like_term),
                func.lower(Product.search_text).like(like_term),
            )
        )

    safe_limit = max(1, min(int(limit), 50))
    statement = (
        select(Product, ProductOffer)
        .outerjoin(ProductOffer, offer_scope)
        .where(*conditions)
        .order_by(
            ProductOffer.available.desc(),
            ProductOffer.current_price.asc(),
            Product.id.asc(),
        )
        .limit(safe_limit)
    )
    with Session(get_catalog_engine()) as session:
        rows = session.execute(statement).all()
    return [_as_product(product, offer, detail=False) for product, offer in rows]


def get_product_by_sku(
    sku_id: str,
    region: str = "CN",
    channel: str = "official",
) -> dict[str, Any] | None:
    """按唯一 SKU 查询一条完整商品记录。"""
    offer_scope = and_(
        ProductOffer.product_id == Product.id,
        ProductOffer.region == (region or "CN").strip().upper(),
        ProductOffer.channel == (channel or "official").strip().lower(),
    )
    statement = (
        select(Product, ProductOffer)
        .outerjoin(ProductOffer, offer_scope)
        .where(
            Product.status == "active",
            func.upper(Product.sku_id) == sku_id.strip().upper(),
        )
        .limit(1)
    )
    with Session(get_catalog_engine()) as session:
        row = session.execute(statement).first()
    return _as_product(row[0], row[1], detail=True) if row else None


def recommend_products(
    category: str,
    budget_max: float,
    use_cases: str = "",
    preferred_brands: str = "",
    limit: int = 3,
    region: str = "CN",
    channel: str = "official",
) -> list[dict[str, Any]]:
    """先由 SQL 按品类、预算和库存召回，再对少量候选做可解释排序。"""
    requested_cases = _split_terms(use_cases)
    preferred = _split_terms(preferred_brands)
    candidates = search_products(
        keyword=use_cases,
        category=category,
        max_price=budget_max,
        limit=50,
        region=region,
        channel=channel,
    )
    if not candidates and requested_cases:
        candidates = search_products(
            category=category,
            max_price=budget_max,
            limit=50,
            region=region,
            channel=channel,
        )

    ranked: list[tuple[float, dict[str, Any], list[str]]] = []
    for product in candidates:
        if not product.get("stock") or product["price"] is None:
            continue
        searchable = " ".join(
            [
                product["brand"],
                product["model"],
                product["variant"],
                *product.get("use_cases", []),
                *[str(value) for value in product.get("key_specs", {}).values()],
            ]
        ).lower()
        matched_cases = [case for case in requested_cases if case in searchable]
        brand_match = any(item in product["brand"].lower() for item in preferred)
        score = len(matched_cases) * 10 + (5 if brand_match else 0)
        if budget_max:
            score += min(float(product["price"]) / float(budget_max), 1.0) * 2
        ranked.append((score, product, matched_cases))

    ranked.sort(key=lambda item: (item[0], item[1].get("stock", 0)), reverse=True)
    safe_limit = max(1, min(int(limit), 5))
    results: list[dict[str, Any]] = []
    for score, product, matched_cases in ranked[:safe_limit]:
        item = dict(product)
        item["matched_use_cases"] = matched_cases
        item["recommendation_score"] = round(score, 2)
        results.append(item)
    return results
