"""把 200 条可识别的测试商品写入 MySQL。

该脚本只在人工执行时读取旧 JSON 作为 40 个基础模板；应用运行时完全从
MySQL 查询。重复执行会先替换 source_name=synthetic_seed 的记录，不影响
未来导入的真实商品。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from core.catalog.database import get_catalog_engine
from core.persistence.models import Base, Product, ProductOffer


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SOURCE_NAME = "synthetic_seed"


def _load_json(name: str) -> dict[str, Any]:
    with (DATA_DIR / name).open("r", encoding="utf-8") as file:
        return json.load(file)


def _inventory_status(stock: int) -> str:
    if stock <= 0:
        return "暂时缺货"
    if stock <= 8:
        return "库存紧张"
    return "有货"


def _build_records(count: int) -> list[dict[str, Any]]:
    catalog = _load_json("digital_catalog.json")["products"]
    market = _load_json("digital_market.json")
    overrides = {
        (item["sku_id"].upper(), item["region"].upper(), item["channel"].lower()): item
        for item in market.get("overrides", [])
    }
    observed_at = datetime.now().replace(microsecond=0)
    records: list[dict[str, Any]] = []

    for index in range(count):
        base = catalog[index % len(catalog)]
        edition = index // len(catalog) + 1
        is_original = edition == 1
        sku_id = base["sku_id"] if is_original else f"{base['sku_id']}-SEED{edition}"
        price_delta = (edition - 1) * 137 + (index % 5) * 29
        price = round(float(base["price"]) + price_delta, 2)
        stock = (int(base["stock"]) + index * 7) % 41
        variant = base["variant"] if is_original else f"{base['variant']} 测试批次{edition}"
        model = base["model"] if is_original else f"{base['model']} 测试版{edition}"

        official_override = overrides.get((base["sku_id"].upper(), "CN", "official"))
        if is_original and official_override:
            current_price = float(official_override["current_price"])
            original_price = float(official_override["original_price"])
            stock = int(official_override["stock"])
            inventory_status = official_override["inventory_status"]
        else:
            current_price = price
            original_price = round(price + 100 + (index % 4) * 50, 2)
            inventory_status = _inventory_status(stock)

        search_text = " ".join(
            [
                sku_id,
                base["category"],
                base["brand"],
                model,
                variant,
                *[str(value) for value in base.get("specs", {}).values()],
                *base.get("use_cases", []),
                *base.get("keywords", []),
            ]
        ).lower()
        offers = [
            {
                "region": "CN",
                "channel": "official",
                "currency": "CNY",
                "current_price": current_price,
                "original_price": original_price,
                "stock": stock,
                "inventory_status": inventory_status,
                "available": stock > 0,
                "observed_at": observed_at,
            }
        ]
        if is_original:
            extra_channels = [
                item
                for (override_sku, region, channel), item in overrides.items()
                if override_sku == base["sku_id"].upper() and channel != "official"
            ]
            offers.extend(
                {
                    "region": item["region"].upper(),
                    "channel": item["channel"].lower(),
                    "currency": market["currency"],
                    "current_price": float(item["current_price"]),
                    "original_price": float(item["original_price"]),
                    "stock": int(item["stock"]),
                    "inventory_status": item["inventory_status"],
                    "available": int(item["stock"]) > 0,
                    "observed_at": observed_at,
                }
                for item in extra_channels
            )

        records.append(
            {
                "sku_id": sku_id,
                "category": base["category"],
                "brand": base["brand"],
                "model": model,
                "variant": variant,
                "specs": base.get("specs", {}),
                "use_cases": base.get("use_cases", []),
                "keywords": base.get("keywords", []),
                "search_text": search_text,
                "offers": offers,
            }
        )
    return records


def seed(count: int) -> tuple[int, int]:
    if count < 1:
        raise ValueError("count 必须大于 0")
    engine = get_catalog_engine()
    Base.metadata.create_all(engine)
    records = _build_records(count)
    now = datetime.now().replace(microsecond=0)

    with Session(engine) as session:
        session.execute(delete(Product).where(Product.source_name == SOURCE_NAME))
        session.flush()
        for record in records:
            product = Product(
                sku_id=record["sku_id"],
                category=record["category"],
                brand=record["brand"],
                model=record["model"],
                variant=record["variant"],
                specs=record["specs"],
                use_cases=record["use_cases"],
                keywords=record["keywords"],
                search_text=record["search_text"],
                status="active",
                source_name=SOURCE_NAME,
                source_url=None,
                source_product_id=record["sku_id"],
                verified_at=now,
            )
            session.add(product)
            session.flush()
            for offer in record["offers"]:
                session.add(
                    ProductOffer(
                        product_id=product.id,
                        source_name=SOURCE_NAME,
                        source_url=None,
                        **offer,
                    )
                )
        session.commit()

        product_count = session.scalar(
            select(func.count(Product.id)).where(Product.source_name == SOURCE_NAME)
        )
        offer_count = session.scalar(
            select(func.count(ProductOffer.id)).where(
                ProductOffer.source_name == SOURCE_NAME
            )
        )
    return int(product_count or 0), int(offer_count or 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=200)
    args = parser.parse_args()
    product_count, offer_count = seed(args.count)
    print(f"synthetic_products={product_count}")
    print(f"synthetic_offers={offer_count}")


if __name__ == "__main__":
    main()
