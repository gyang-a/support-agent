"""商品目录与动态市场数据的应用服务。"""

from .service import (
    compare_products,
    get_product_with_market,
    recommend_available_products,
    search_products_with_market,
)

__all__ = [
    "compare_products",
    "get_product_with_market",
    "recommend_available_products",
    "search_products_with_market",
]
