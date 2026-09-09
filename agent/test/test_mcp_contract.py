"""最新版 langchain-mcp-adapters 与本地数码 MCP 服务的契约测试。"""

import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from core.mcp.config import load_mcp_connections


def test_digital_mcp_exposes_phase_two_tools() -> None:
    """通过真实 stdio 子进程加载工具，验证配置和工具名称保持一致。"""
    connections = load_mcp_connections()

    async def load_tool_names() -> set[str]:
        # MultiServerMCPClient 默认按工具调用管理会话，目录加载无需持久会话。
        client = MultiServerMCPClient(connections)
        tools = await client.get_tools()
        return {tool.name for tool in tools}

    tool_names = asyncio.run(load_tool_names())
    expected = {
        "get_catalog_overview",
        "search_product_catalog",
        "get_product_detail",
        "recommend_products",
        "query_user_orders",
        "query_order_detail",
        "get_price_and_inventory",
        "compare_product_skus",
        "get_compatibility_graph_overview",
        "query_product_compatibility",
        "query_shipment_timeline",
        "get_after_sales_policy_overview",
        "search_after_sales_policies",
        "get_device_diagnostic_overview",
        "diagnose_device_issue",
        "create_after_sales_ticket",
        "query_user_tickets",
        "query_ticket_detail",
        "escalate_ticket_to_human",
    }

    assert expected.issubset(tool_names)
