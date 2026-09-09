"""数码配件兼容性专业 Agent。"""

from typing import Any, Dict

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from core.mcp.config import load_mcp_connections
from core.model import create_chat_model
from core.memory.tool_context import ToolResultBudgetMiddleware
from core.observability import invoke_traced_agent
from core.security import UserContext, inject_user_id
from core.workflow.state import AgentState
from core.workflow.prompting import history_source_guidance


class CompatibilityAgentNode:
    """使用已审核图谱回答接口、协议、功率和配件适配问题。"""

    def __init__(self):
        self.llm = create_chat_model(temperature=0.1)
        self.mcp_connections = load_mcp_connections()

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        """供主 LangGraph 调用的兼容性查询节点。"""
        memory_context = state.get("memory_context", "")
        user_context = UserContext(user_id=state.get("user_id", "unknown"))
        client = MultiServerMCPClient(
            connections=self.mcp_connections,
            tool_interceptors=[inject_user_id],
        )
        all_tools = await client.get_tools()
        allowed_tool_names = {
            "get_compatibility_graph_overview",
            "query_product_compatibility",
            "search_product_catalog",
        }
        tools = [tool for tool in all_tools if tool.name in allowed_tool_names]

        system_prompt = f"""你是数码商城的【配件兼容性Agent】。
你的任务是判断手机、笔记本与充电器、线材、扩展坞、显示器、耳机、硬盘和内存是否适配。

工作要求：
- 必须调用 query_product_compatibility 获取结论，不得仅凭模型常识回答。
- 型号不明确时，先调用 search_product_catalog；配件不明确时可调用图谱概览。
- 严格区分“兼容”“有条件兼容”“不兼容”和“无法确认”。无法确认绝不能说成兼容。
- 回答要说明命中的接口/功率/协议条件与限制；购买前提醒核对厂商最新规格。
- 如果图谱只覆盖某个机型组，要说明结论来自组规则及工具返回的核验日期。
- 不编造转接器、充电功率、显示分辨率或可升级性。

{history_source_guidance("compatibility_agent")}

【系统提供的用户记忆/背景上下文】：
{memory_context if memory_context else "暂无背景上下文。"}
"""
        tool_context_guard = ToolResultBudgetMiddleware()
        inner_agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=system_prompt,
            context_schema=UserContext,
            name="compatibility_specialist",
            middleware=[tool_context_guard],
        )

        print("🔌 [CompatibilityAgent] 正在查询配件兼容性图谱...")
        return await invoke_traced_agent(
            inner_agent=inner_agent,
            state=state,
            context=user_context,
            agent_name="compatibility_agent",
            summary_model=self.llm,
            system_prompt=system_prompt,
            tools=tools,
            tool_context_guard=tool_context_guard,
        )
