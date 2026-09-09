"""数码商城第一阶段订单数据与用户隔离测试。"""

from core.order import get_order_detail, list_user_orders


def test_query_current_users_orders() -> None:
    """当前用户只能看到自己的订单列表。"""
    orders = list_user_orders("user_1001", limit=10)

    assert len(orders) == 2
    assert all(order["order_id"].startswith("DG-1001-") for order in orders)


def test_order_detail_contains_shipment() -> None:
    """订单详情包含对应商品与最新物流信息。"""
    order = get_order_detail("user_1001", "DG-1001-0002")

    assert order is not None
    assert order["shipment"]["carrier"] == "京东物流"
    assert order["items"][0]["sku_id"].startswith("LAPTOP-")


def test_cross_user_order_access_is_rejected() -> None:
    """相同订单号在非所属用户下必须返回空，避免越权枚举。"""
    order = get_order_detail("user_1002", "DG-1001-0002")

    assert order is None
