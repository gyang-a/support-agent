"""基于 LangChain 1.x Runtime Context 的用户身份注入。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_mcp_adapters.interceptors import MCPToolCallRequest


@dataclass(frozen=True)
class UserContext:
    """通过 ``create_agent(context_schema=...)`` 传递的可信运行时身份。"""

    user_id: str


async def inject_user_id(
    request: MCPToolCallRequest,
    handler: Callable[[MCPToolCallRequest], Awaitable[Any]],
) -> Any:
    """向用户私有 MCP 工具强制注入运行时身份。

    模型生成的 user_id 会被覆盖。公共商品工具不会收到多余参数，订单与
    售后工具则只能使用应用层传入的 ``UserContext``。
    """
    protected_tools = {
        "query_user_orders",
        "query_order_detail",
        "query_shipment_timeline",
        "create_after_sales_ticket",
        "query_user_tickets",
        "query_ticket_detail",
        "escalate_ticket_to_human",
    }
    if request.name not in protected_tools:
        return await handler(request)

    runtime_context: Any = request.runtime.context
    user_id = getattr(runtime_context, "user_id", "")
    if not user_id:
        # 身份缺失时不把模型提供的占位值发送到私有数据工具。
        raise PermissionError("缺少可信用户上下文，已拒绝调用用户私有工具。")

    new_args = {**request.args, "user_id": user_id}
    print(f"🔒 [安全拦截] 已向工具 {request.name} 注入当前登录用户身份")
    return await handler(request.override(args=new_args))
