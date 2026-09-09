"""MySQL 价格与库存仓储。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.catalog.database import get_catalog_engine
from core.persistence.models import Product, ProductOffer


def _number(value: Decimal | float | None) -> float | None:
    return float(value) if value is not None else None


def _as_market(product: Product, offer: ProductOffer) -> dict[str, Any]:
    current_price = _number(offer.current_price)
    original_price = _number(offer.original_price)
    discount = None
    if current_price is not None and original_price is not None:
        discount = round(max(original_price - current_price, 0), 2)
    return {
        "sku_id": product.sku_id,
        "region": offer.region,
        "channel": offer.channel,
        "currency": offer.currency,
        "current_price": current_price,
        "original_price": original_price,
        "discount_amount": discount,
        "stock": offer.stock,
        "inventory_status": offer.inventory_status,
        "available": offer.available,
        "updated_at": offer.observed_at.isoformat(sep=" ", timespec="seconds"),
        "source": offer.source_name,
        "source_url": offer.source_url,
        "notice": "synthetic_seed 表示测试种子价格与库存，不代表真实成交信息。",
    }


def get_market_state(
    sku_id: str,
    region: str = "CN",
    channel: str = "official",
) -> dict[str, Any] | None:
    statement = (
        select(Product, ProductOffer)
        .join(ProductOffer, ProductOffer.product_id == Product.id)
        .where(
            Product.status == "active",
            func.upper(Product.sku_id) == sku_id.strip().upper(),
            ProductOffer.region == (region or "CN").strip().upper(),
            ProductOffer.channel == (channel or "official").strip().lower(),
        )
        .limit(1)
    )
    with Session(get_catalog_engine()) as session:
        row = session.execute(statement).first()
    return _as_market(row[0], row[1]) if row else None


def get_market_states(
    sku_ids: list[str],
    region: str = "CN",
    channel: str = "official",
) -> dict[str, dict[str, Any]]:
    normalized = list(dict.fromkeys(item.strip().upper() for item in sku_ids if item.strip()))
    if not normalized:
        return {}
    statement = (
        select(Product, ProductOffer)
        .join(ProductOffer, ProductOffer.product_id == Product.id)
        .where(
            Product.status == "active",
            func.upper(Product.sku_id).in_(normalized),
            ProductOffer.region == (region or "CN").strip().upper(),
            ProductOffer.channel == (channel or "official").strip().lower(),
        )
    )
    with Session(get_catalog_engine()) as session:
        rows = session.execute(statement).all()
    return {product.sku_id: _as_market(product, offer) for product, offer in rows}
