"""动态物流轨迹数据访问层。

物流与库存一样属于高频变化数据，因此每次查询都重新读取快照。所有查询
先用当前 user_id 校验订单归属，再返回轨迹，避免通过物流单号旁路越权。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_ORDER_FILE = Path(__file__).resolve().parents[2] / "data" / "digital_orders.json"
_LOGISTICS_FILE = Path(__file__).resolve().parents[2] / "data" / "digital_logistics.json"


def _owned_order_ids(user_id: str) -> set[str]:
    """读取订单快照并返回当前用户拥有的订单号集合。"""
    with _ORDER_FILE.open("r", encoding="utf-8") as file:
        orders = json.load(file)["orders"]
    return {order["order_id"].upper() for order in orders if order["user_id"] == user_id}


def get_shipment_timeline(user_id: str, order_id: str) -> dict[str, Any] | None:
    """查询属于当前用户的动态物流轨迹；越权和不存在统一返回空。"""
    normalized_order_id = order_id.strip().upper()
    if normalized_order_id not in _owned_order_ids(user_id):
        return None

    with _LOGISTICS_FILE.open("r", encoding="utf-8") as file:
        snapshot = json.load(file)
    shipment = next(
        (
            item
            for item in snapshot["shipments"]
            if item["order_id"].upper() == normalized_order_id
        ),
        None,
    )
    if not shipment:
        return {
            "order_id": normalized_order_id,
            "status": "尚未发货",
            "events": [],
            "updated_at": snapshot["updated_at"],
            "snapshot_id": snapshot["snapshot_id"],
            "notice": snapshot["notice"],
        }

    result = dict(shipment)
    result["events"] = sorted(shipment.get("events", []), key=lambda item: item["time"])
    result["updated_at"] = snapshot["updated_at"]
    result["snapshot_id"] = snapshot["snapshot_id"]
    result["notice"] = snapshot["notice"]
    return result
