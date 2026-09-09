"""数码商品目录检索与推荐规则测试。"""

from core.catalog import get_catalog_overview, recommend_products, search_products


def test_catalog_contains_first_phase_categories() -> None:
    """MySQL 种子目录包含 200 个可查询 SKU，概览只做聚合。"""
    overview = get_catalog_overview()

    assert overview["category_counts"] == {"手机": 100, "笔记本": 100}
    assert overview["source_count"] == 1


def test_search_filters_category_brand_and_budget() -> None:
    """组合筛选不得返回跨品类、跨品牌或超预算商品。"""
    results = search_products(
        category="手机",
        brand="小米",
        max_price=5000,
        limit=10,
    )

    assert results
    assert all(item["category"] == "手机" for item in results)
    assert all(item["brand"] == "小米" for item in results)
    assert all(item["price"] <= 5000 for item in results)


def test_recommendation_respects_budget_and_use_case() -> None:
    """推荐候选必须在预算内，并优先命中用户的主要用途。"""
    results = recommend_products(
        category="笔记本",
        budget_max=9000,
        use_cases="重度游戏,视频剪辑",
        limit=3,
    )

    assert results
    assert all(item["price"] <= 9000 for item in results)
    assert any("重度游戏" in item["matched_use_cases"] for item in results)
