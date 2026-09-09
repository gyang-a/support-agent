"""
数码商城 MCP 服务。

该文件只负责把领域服务暴露为 MCP Tools。商品检索、推荐排序和订单隔离
分别位于 core.catalog 与 core.order，避免把业务规则堆积在协议适配层。
"""

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from core.catalog import (
    get_catalog_overview as load_catalog_overview,
    get_product_by_sku,
    search_products,
)
from core.commerce import (
    compare_products as compare_catalog_products,
    get_product_with_market,
    recommend_available_products,
    search_products_with_market,
)
from core.compatibility import check_compatibility_graph, get_compatibility_overview
from core.diagnostics import diagnose_issue, get_diagnostic_overview
from core.logistics import get_shipment_timeline
from core.market import get_market_state
from core.order import get_order_detail as load_order_detail
from core.order import list_user_orders
from core.policy import get_policy_overview, search_policies
from core.knowledge import get_technical_knowledge_store
from core.ticket import (
    TicketValidationError,
    create_ticket,
    escalate_ticket,
    get_ticket_detail,
    list_user_tickets,
)


# ==============================================================================
# 初始化 FastMCP 服务器
# ==============================================================================
# 数码商城后端统一通过一个 stdio MCP 服务暴露领域工具。
mcp = FastMCP("DigitalCommerceMCPServer")


def _success(data: Any = None, message: str = "") -> str:
    """统一成功响应格式，便于 Agent 稳定解析工具结果。"""
    payload: dict[str, Any] = {"status": "success"}
    if message:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    return json.dumps(payload, ensure_ascii=False)


def _not_found(message: str) -> str:
    """统一无结果响应；无结果不是系统异常，不应返回 error。"""
    return json.dumps({"status": "not_found", "message": message}, ensure_ascii=False)


# ==============================================================================
# 商品目录工具
# ==============================================================================
@mcp.tool()
def get_catalog_overview(category: str = "") -> str:
    """从 MySQL 获取商品目录的品类、品牌、数量与数据说明。

    Args:
        category: 可选品类，目前支持“手机”和“笔记本”。
    """
    return _success(load_catalog_overview(category=category))


@mcp.tool()
def search_product_catalog(
    keyword: str = "",
    category: str = "",
    brand: str = "",
    min_price: float = 0,
    max_price: float = 0,
    limit: int = 10,
    region: str = "CN",
    channel: str = "official",
) -> str:
    """按关键词、品类、品牌和预算搜索真实存在于目录中的 SKU。

    Args:
        keyword: 型号、用途或规格关键词，例如“拍照 长焦”“RTX4060”。
        category: 商品品类，目前支持“手机”和“笔记本”。
        brand: 可选品牌，例如“Apple”“华为”“联想”。
        min_price: 最低价格，0 表示不限。
        max_price: 最高价格，0 表示不限。
        limit: 返回数量，范围 1-20。
        region: 地区代码，默认 CN。
        channel: 销售渠道，默认 official。
    """
    results = search_products_with_market(
        keyword=keyword,
        category=category,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
        region=region,
        channel=channel,
    )
    if not results:
        return _not_found("目录中没有符合当前条件的商品，请调整品牌、预算或用途。")

    overview = load_catalog_overview(category=category)
    return _success(
        {
            "items": results,
            "catalog_verified_at": overview["verified_at"],
            "price_notice": "价格和库存以每个商品 market 字段的动态快照为准。",
        }
    )


@mcp.tool()
def get_product_detail(
    sku_id: str,
    region: str = "CN",
    channel: str = "official",
) -> str:
    """按标准 SKU ID 查询完整规格、用途、价格和库存快照。

    Args:
        sku_id: 必须来自 search_product_catalog 或 recommend_products 的结果。
    """
    product = get_product_with_market(sku_id, region=region, channel=channel)
    if not product:
        return _not_found("未找到该 SKU，请先搜索商品目录获得有效的 sku_id。")

    product["catalog_verified_at"] = product.get("source", {}).get("verified_at")
    product["price_notice"] = "当前价格和库存以 market.updated_at 标记的动态快照为准。"
    return _success(product)


@mcp.tool()
def recommend_products(
    category: str,
    budget_max: float,
    use_cases: str = "",
    preferred_brands: str = "",
    limit: int = 3,
    region: str = "CN",
    channel: str = "official",
) -> str:
    """根据预算、用途和品牌偏好返回 1-5 个可购买候选 SKU。

    Args:
        category: 必填，目前支持“手机”和“笔记本”。
        budget_max: 最高预算；用户未提供预算时应先追问，不要猜测。
        use_cases: 多个用途可用逗号分隔，例如“游戏,拍照”。
        preferred_brands: 多个偏好品牌可用逗号分隔。
        limit: 推荐数量，默认 3，最大 5。
    """
    results = recommend_available_products(
        category=category,
        budget_max=budget_max,
        use_cases=use_cases,
        preferred_brands=preferred_brands,
        limit=limit,
        region=region,
        channel=channel,
    )
    if not results:
        return _not_found("当前预算和条件下没有有库存的候选商品，请调整条件。")

    overview = load_catalog_overview(category=category)
    return _success(
        {
            "items": results,
            "catalog_verified_at": overview["verified_at"],
            "price_notice": "推荐已按动态价格和库存过滤，以各商品 market 字段为准。",
        }
    )


@mcp.tool()
def get_price_and_inventory(
    sku_id: str,
    region: str = "CN",
    channel: str = "official",
) -> str:
    """查询单个 SKU 的动态价格与库存，不返回不确定的成交承诺。"""
    market = get_market_state(sku_id=sku_id, region=region, channel=channel)
    if not market:
        return _not_found("未找到该 SKU，请先搜索商品目录。")
    return _success(market)


@mcp.tool()
def compare_product_skus(
    sku_ids: str,
    region: str = "CN",
    channel: str = "official",
) -> str:
    """按统一规格维度比较 2-5 个 SKU。

    Args:
        sku_ids: 用英文或中文逗号分隔的 SKU ID，例如“SKU-A,SKU-B”。
        region: 地区代码，默认 CN。
        channel: 销售渠道，默认 official。
    """
    normalized = [
        item.strip()
        for item in sku_ids.replace("，", ",").split(",")
        if item.strip()
    ]
    try:
        result = compare_catalog_products(normalized, region=region, channel=channel)
    except ValueError as exc:
        return _not_found(str(exc))
    if result["missing_sku_ids"]:
        return _not_found(f"以下 SKU 不存在：{', '.join(result['missing_sku_ids'])}")
    return _success(result)


# ==============================================================================
# 配件兼容性图谱工具
# ==============================================================================
@mcp.tool()
def get_compatibility_graph_overview() -> str:
    """获取兼容性图谱版本、覆盖规模和支持查询的配件。"""
    return _success(get_compatibility_overview())


@mcp.tool()
async def query_product_compatibility(source: str, target: str) -> str:
    """查询目录商品与配件是否兼容，并返回条件、限制和图谱路径。

    Args:
        source: 商品 SKU、完整型号、配件 ID、名称或别名。
        target: 另一个待校验实体。
    """
    return _success(await check_compatibility_graph(source=source, target=target))


# ==============================================================================
# 售后知识、故障诊断与工单工具
# ==============================================================================
@mcp.tool()
async def search_technical_documents(
    query: str,
    limit: int = 5,
) -> str:
    """用完整自然语言问题检索已发布技术文档并返回原文引用。

    Args:
        query: 包含品牌、型号、商品和问题的独立自然语言查询。
        limit: 最终重排后返回的证据数量，范围 1-10。
    """
    store = await get_technical_knowledge_store()
    try:
        results = await store.search_natural_language(
            query,
            limit=max(1, min(limit, 10)),
        )
    finally:
        # Adapter 默认每次工具调用启动临时 stdio 子进程；必须释放 Milvus
        # gRPC 客户端，否则结果生成后子进程仍不退出，调用方会一直等待。
        await store.close()
    if not results:
        return _not_found("已发布知识文档中没有找到可靠证据，请补充完整型号或转人工确认。")
    return _success(
        {
            "query": query,
            "results": results,
            "retrieval": "milvus_dense_bm25_rrf_rerank",
        }
    )


@mcp.tool()
def get_after_sales_policy_overview() -> str:
    """获取售后政策知识库版本、覆盖主题和使用边界。"""
    return _success(get_policy_overview())


@mcp.tool()
async def search_after_sales_policies(query: str, limit: int = 3) -> str:
    """检索售后政策，并返回文档 ID、章节、生效日期和原文证据。

    Args:
        query: 用户的退换货、保修、送修、数据或物流政策问题。
        limit: 返回证据数量，范围 1-5。
    """
    store = await get_technical_knowledge_store()
    try:
        vector_results = await store.search(
            query,
            document_type="after_sales_policy",
            limit=limit,
        )
    finally:
        await store.close()
    if vector_results:
        overview = get_policy_overview()
        result = {
            "query": query,
            "results": vector_results,
            "knowledge_base_version": overview["knowledge_base_version"],
            "effective_date": overview["effective_date"],
            "notice": overview["notice"],
            "retrieval": "milvus_semantic",
        }
    else:
        result = search_policies(query=query, limit=limit)
        result["retrieval"] = "keyword_fallback"
    if not result["results"]:
        return _not_found("售后知识库中没有找到相关政策，请转人工客服确认。")
    return _success(result)


@mcp.tool()
def get_device_diagnostic_overview() -> str:
    """获取当前支持诊断的故障类型和安全边界。"""
    return _success(get_diagnostic_overview())


@mcp.tool()
def diagnose_device_issue(
    symptom: str,
    sku_id: str = "",
    category: str = "",
) -> str:
    """根据症状生成安全预诊断、追问项和人工分流建议。

    Args:
        symptom: 用户描述的完整故障现象。
        sku_id: 可选标准 SKU；未知时先搜索商品目录。
        category: 可选品类，目前支持手机和笔记本。
    """
    return _success(diagnose_issue(symptom=symptom, sku_id=sku_id, category=category))


@mcp.tool()
def create_after_sales_ticket(
    subject: str,
    description: str,
    category: str = "device_issue",
    order_id: str = "",
    sku_id: str = "",
    priority: str = "normal",
    manual_required: bool = False,
    user_id: str = "",
) -> str:
    """为当前登录用户创建售后工单；必须由用户明确要求或确认。

    Args:
        subject: 简短工单主题。
        description: 不包含密码、验证码和证件信息的故障描述。
        category: 工单分类，默认 device_issue。
        order_id: 可选本人订单号，系统会校验归属。
        sku_id: 可选商品 SKU；与订单同时提供时必须属于该订单。
        priority: low、normal、high 或 critical。
        manual_required: 是否直接升级人工处理。
        user_id: [系统注入] 当前登录用户标识，模型不得自行指定。
    """
    try:
        ticket = create_ticket(
            user_id=user_id,
            subject=subject,
            description=description,
            category=category,
            order_id=order_id,
            sku_id=sku_id,
            priority=priority,
            manual_required=manual_required,
        )
    except TicketValidationError as exc:
        return _not_found(str(exc))
    return _success(ticket, message="售后工单已受理；创建成功不代表退款、换货或免费维修已审核通过。")


@mcp.tool()
def query_user_tickets(user_id: str, limit: int = 10) -> str:
    """查询当前登录用户的售后工单列表。"""
    tickets = list_user_tickets(user_id=user_id, limit=limit)
    if not tickets:
        return _success(message="当前登录账号暂无售后工单。")
    return _success(tickets)


@mcp.tool()
def query_ticket_detail(ticket_id: str, user_id: str = "") -> str:
    """查询属于当前登录用户的售后工单详情和处理时间线。"""
    ticket = get_ticket_detail(user_id=user_id, ticket_id=ticket_id)
    if not ticket:
        return _not_found("未找到该工单，请确认工单号属于当前登录账号。")
    return _success(ticket)


@mcp.tool()
def escalate_ticket_to_human(
    ticket_id: str,
    reason: str,
    user_id: str = "",
) -> str:
    """将当前用户的未关闭工单升级为高优先级人工处理。"""
    try:
        ticket = escalate_ticket(user_id=user_id, ticket_id=ticket_id, reason=reason)
    except TicketValidationError as exc:
        return _not_found(str(exc))
    if not ticket:
        return _not_found("未找到该工单，请确认工单号属于当前登录账号。")
    return _success(ticket, message="工单已升级人工售后处理。")


# ==============================================================================
# 订单工具
# ==============================================================================
@mcp.tool()
def query_user_orders(user_id: str, limit: int = 5) -> str:
    """查询当前登录用户最近的数码商品订单。

    Args:
        user_id: [系统注入] 当前登录用户标识，模型不得自行指定。
        limit: 返回的最大订单数，默认 5。
    """
    orders = list_user_orders(user_id=user_id, limit=limit)
    if not orders:
        return _success(message="当前登录账号暂无订单记录。")
    return _success(orders)


@mcp.tool()
def query_order_detail(order_id: str, user_id: str = "") -> str:
    """查询当前登录用户某笔订单的商品和物流详情。

    Args:
        order_id: 用户提供或 query_user_orders 返回的订单号。
        user_id: [系统注入] 当前登录用户标识，模型不得自行指定。
    """
    order = load_order_detail(user_id=user_id, order_id=order_id)
    if not order:
        # 不区分订单不存在与不属于当前用户，避免利用返回信息枚举订单。
        return _not_found("未找到该订单，请确认订单号属于当前登录账号。")
    return _success(order)


@mcp.tool()
def query_shipment_timeline(order_id: str, user_id: str = "") -> str:
    """查询当前登录用户某笔订单的最新物流时间线。

    Args:
        order_id: 用户提供或 query_user_orders 返回的订单号。
        user_id: [系统注入] 当前登录用户标识，模型不得自行指定。
    """
    shipment = get_shipment_timeline(user_id=user_id, order_id=order_id)
    if not shipment:
        return _not_found("未找到该订单，请确认订单号属于当前登录账号。")
    return _success(shipment)


# ==============================================================================
# 服务启动入口
# ==============================================================================
if __name__ == "__main__":
    sys.stderr.write("🚀 正在启动 Digital Commerce MCP Server (stdio 模式)...\n")
    # FastMCP 通过标准输入/输出与 LangChain MCP Client 通信。
    mcp.run()
