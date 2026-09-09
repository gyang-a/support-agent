"""第二阶段兼容性、对比、动态市场和物流领域测试。"""

from core.commerce import compare_products, get_product_with_market
from core.compatibility import check_compatibility, get_compatibility_overview
from core.logistics import get_shipment_timeline
from core.market import get_market_state
from core.order import get_order_detail


def test_dynamic_market_rows_come_from_mysql() -> None:
    """价格与库存均来自 MySQL 报价表，并保留测试数据来源。"""
    overridden = get_market_state("PHONE-APPLE-IP16PRO-256-TI")
    fallback = get_market_state("PHONE-APPLE-IP16PLUS-128-BLK")

    assert overridden is not None
    assert overridden["current_price"] == 7799
    assert overridden["source"] == "synthetic_seed"
    assert fallback is not None
    assert fallback["source"] == "synthetic_seed"


def test_product_comparison_uses_common_dimensions_and_dynamic_price() -> None:
    """对比结果应按同一字段输出，并使用当前市场价格。"""
    result = compare_products(
        ["PHONE-APPLE-IP16-128-BLK", "PHONE-APPLE-IP16PRO-256-TI"]
    )
    price_dimension = next(item for item in result["dimensions"] if item["field"] == "price")

    assert result["missing_sku_ids"] == []
    assert len(result["products"]) == 2
    assert price_dimension["values"]["PHONE-APPLE-IP16PRO-256-TI"] == 7799
    assert get_product_with_market("PHONE-APPLE-IP16PRO-256-TI")["market"]["updated_at"]


def test_compatibility_graph_returns_explainable_paths() -> None:
    """兼容、条件兼容和不兼容都必须带可解释图谱路径。"""
    compatible = check_compatibility("iPhone 16 Pro", "Apple 20W USB-C 电源适配器")
    incompatible = check_compatibility("iPhone 16 Pro", "Lightning线")
    gaming_limit = check_compatibility(
        "LAPTOP-LENOVO-Y7000P-I7-16-1T-RTX4060",
        "65W笔记本充电器",
    )

    assert compatible["status"] == "compatible"
    assert compatible["path"]
    assert incompatible["status"] == "incompatible"
    assert gaming_limit["status"] == "incompatible"
    assert get_compatibility_overview()["accessory_count"] == 20


def test_unknown_compatibility_never_defaults_to_compatible() -> None:
    """未覆盖的实体或关系必须返回 unknown。"""
    result = check_compatibility("iPhone 16 Pro", "不存在的神奇转接器")

    assert result["status"] == "unknown"
    assert "没有规则不等于兼容" in result["limitations"][0]


def test_logistics_is_dynamic_and_user_scoped() -> None:
    """物流轨迹按用户隔离，并被订单详情复用。"""
    shipment = get_shipment_timeline("user_1001", "DG-1001-0002")
    forbidden = get_shipment_timeline("user_1002", "DG-1001-0002")
    order = get_order_detail("user_1001", "DG-1001-0002")

    assert shipment is not None
    assert shipment["status"] == "运输中"
    assert len(shipment["events"]) == 3
    assert forbidden is None
    assert order is not None
    assert order["shipment"]["snapshot_id"] == shipment["snapshot_id"]
