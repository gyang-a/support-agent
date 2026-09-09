"""第一阶段订单演示数据访问层。

所有查询都必须携带当前登录用户的 user_id，并同时使用 user_id 与订单号
过滤，确保即使模型被提示注入也无法跨用户读取订单。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.logistics import get_shipment_timeline


_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "digital_orders.json"


@lru_cache(maxsize=1)
def _load_orders() -> list[dict[str, Any]]:
    """读取本地订单快照；真实商城接入后由订单中心 API 替代。"""
    with _DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)["orders"]


def list_user_orders(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """查询当前登录用户的最近订单，不返回内部身份字段。"""
    matched = [order for order in _load_orders() if order["user_id"] == user_id]
    matched.sort(key=lambda item: item["created_at"], reverse=True)
    safe_limit = max(1, min(int(limit), 20))

    results: list[dict[str, Any]] = []
    for order in matched[:safe_limit]:
        shipment = get_shipment_timeline(user_id=user_id, order_id=order["order_id"])
        results.append({
            "order_id": order["order_id"],
            "status": order["status"],
            "total_amount": order["total_amount"],
            "created_at": order["created_at"],
            "items": order["items"],
            "shipment_status": shipment["status"] if shipment else "尚未发货",
            "shipment_updated_at": shipment.get("updated_at") if shipment else None,
        })
    return results


def get_order_detail(user_id: str, order_id: str) -> dict[str, Any] | None:
    """查询属于当前用户的订单详情；越权与不存在统一返回空结果。"""
    normalized_order_id = order_id.strip().upper()
    for order in _load_orders():
        if order["user_id"] == user_id and order["order_id"].upper() == normalized_order_id:
            shipment = get_shipment_timeline(user_id=user_id, order_id=order["order_id"])
            return {
                "order_id": order["order_id"],
                "status": order["status"],
                "total_amount": order["total_amount"],
                "created_at": order["created_at"],
                "items": order["items"],
                "shipment": shipment,
            }
    return None
