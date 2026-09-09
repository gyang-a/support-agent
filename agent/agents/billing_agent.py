
from typing import Any, Dict

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from core.workflow.tasks import SpecialistResponse, task_guidance
from langchain_mcp_adapters.client import MultiServerMCPClient

from core.mcp.config import load_mcp_connections
from core.model import create_chat_model
from core.memory.tool_context import ToolResultBudgetMiddleware
from core.observability import invoke_traced_agent
from core.security import UserContext, inject_user_id
from core.workflow.state import AgentState
from core.workflow.prompting import history_source_guidance


class BillingAgentNode:
    """
    包装了 MCP Client 和 LangChain 1.x create_agent 的节点类
    供主图编排时直接调用。

    第一阶段保留 BillingAgentNode 类名以兼容已有导入，业务职责已经从
    云账单/实例查询调整为数码商城订单与物流查询。
    """
    def __init__(self):
        self.llm = create_chat_model(temperature=0.1)

        self.mcp_connections = load_mcp_connections()
    #让实例对象可以像函数一样被调用
    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        """供主 LangGraph 调用的处理函数"""
        # 使用 LangChain 1.x Runtime Context 传递可信用户身份。
        user_context = UserContext(user_id=state.get("user_id", "unknown"))

        memory_context = state.get("memory_context", "")
        system_prompt = f"""你是数码商城的【订单与物流查询Agent】。
你可以使用工具查询当前登录用户的订单列表、订单商品、支付状态和物流信息。

工作要求：
- 用户询问“我的订单”“最近买了什么”时，调用 query_user_orders。
- 用户提供订单号并询问商品明细或支付时，调用 query_order_detail。
- 用户提供订单号并询问物流节点、预计送达或包裹位置时，调用 query_shipment_timeline。
- 用户只说“查物流”但没有订单号时，先调用 query_user_orders 获取本人订单，再根据上下文决定是否需要追问。
- 系统会强制注入 user_id。调用工具时 user_id 使用占位值即可，不得尝试读取或修改真实用户身份。
- 永远不要在回答中展示 user_id。用户要求查询他人订单时，应说明只能查询当前登录账号。
- 严禁伪造订单号、订单状态、物流单号或送达时间；所有动态状态必须来自工具结果。
- 严禁对用户说“工具不可用/工具坏了/接口异常/系统故障”。若工具调用失败，请给出中性表述并引导用户稍后重试。
- 金额统一标注为人民币；物流时间按工具返回内容原样说明。
- 获取到信息后，以专业、清晰的数码商城客服口吻汇报。

{history_source_guidance("order_agent")}

【系统提供的用户记忆/背景上下文】:
{memory_context if memory_context else "暂无背景上下文。"}
"""
        
        print("💡 [OrderAgent] 正在处理订单与物流查询请求...")

        # MultiServerMCPClient 默认按工具调用创建并清理会话；这些工具
        # 不依赖跨调用的服务端状态，因此无需显式持久化 ClientSession。
        client = MultiServerMCPClient(
            connections=self.mcp_connections,
            tool_interceptors=[inject_user_id],
        )
        all_tools = await client.get_tools()
        allowed_tool_names = {
            "query_user_orders",
            "query_order_detail",
            "query_shipment_timeline",
        }
        tools = [tool for tool in all_tools if tool.name in allowed_tool_names]

        system_prompt += task_guidance(state)
        tool_context_guard = ToolResultBudgetMiddleware()
        inner_agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=system_prompt,
            context_schema=UserContext,
            name="order_specialist",
            middleware=[tool_context_guard],
            response_format=ToolStrategy(SpecialistResponse) if state.get("current_task") else None,
        )

        return await invoke_traced_agent(
            inner_agent=inner_agent,
            state=state,
            context=user_context,
            agent_name="order_agent",
            summary_model=self.llm,
            system_prompt=system_prompt,
            tools=tools,
            tool_context_guard=tool_context_guard,
        )

def get_billing_agent() -> BillingAgentNode:
    """保留给独立测试和旧调用方使用的同步构造入口。"""
    return BillingAgentNode()



# state
#  │
#  │ user_id = "user_001"
#  ▼
# UserContext(user_id="user_001")
#  │
#  ▼
# LangChain Agent Runtime Context
#  │
#  │
#  │ 模型决定调用：
#  │ query_order_detail(
#  │     order_id="xxx",
#  │     user_id="placeholder"
#  │ )
#  ▼
# MCP Tool Interceptor
# inject_user_id
#  │
#  │ 从 Runtime Context 取得
#  │ user_id="user_001"
#  │
#  │ 覆盖模型参数
#  ▼
# MCP Server
# query_order_detail(
#     order_id="xxx",
#     user_id="user_001"
# )
