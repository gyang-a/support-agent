
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from core.workflow.tasks import SpecialistResponse, task_guidance
from core.workflow.state import AgentState
from typing import Dict, Any
from langchain_mcp_adapters.client import MultiServerMCPClient
from core.security import UserContext, inject_user_id
from core.mcp.config import load_mcp_connections
from core.model import create_chat_model
from core.memory.tool_context import ToolResultBudgetMiddleware
from core.observability import invoke_traced_agent
from core.workflow.prompting import history_source_guidance

class RecommendationAgent:
    """
    数码商品推荐 Agent：根据品类、预算、用途和品牌偏好推荐真实 SKU。

    候选召回由目录完成，并使用动态价格库存二次过滤。模型只负责追问、
    解释取舍和组织回答，不能自行创造商品或参数。
    """
    def __init__(self):
        # 推荐需要适度表达能力，但事实字段必须保持稳定。
        self.llm = create_chat_model(temperature=0.2)
        
        self.mcp_connections = load_mcp_connections()

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        memory_context = state.get("memory_context", "")
        user_context = UserContext(user_id=state.get("user_id", "unknown"))
        
        # 获取 MCP 商品工具。推荐 Agent 不直接访问 JSON，确保后续切换真实
        # 商品中心时无需修改 Agent 代码。
        client = MultiServerMCPClient(
            connections=self.mcp_connections,
            tool_interceptors=[inject_user_id]
        )
        all_tools = await client.get_tools()
        target_tools = [
            "get_catalog_overview",
            "search_product_catalog",
            "get_product_detail",
            "recommend_products",
            "compare_product_skus",
        ]
        mcp_tools = [t for t in all_tools if t.name in target_tools]
        tools = mcp_tools

        system_prompt = f"""你是数码商城的【智能选购推荐Agent】。
你的任务是根据用户的品类、预算、用途和品牌偏好，推荐最合适的手机或笔记本 SKU。

【工作流程】
1. 识别品类、最高预算、主要用途和品牌偏好。
2. 品类或预算缺失时，先用一个简短问题追问；不得猜测预算后直接推荐。
3. 条件完整后必须调用 recommend_products 获取目录中的候选 SKU。
4. 必要时对候选调用 get_product_detail；需要并排决策时调用 compare_product_skus。
5. 精选 1-3 款，解释每款满足了哪些用途，并明确给出首选和取舍。

【回答要求】
- 语气像专业但不过度推销的数码顾问。
- 必须包含完整型号、SKU、MySQL 渠道价格、库存状态和关键规格。
- 不得推荐工具返回列表之外的商品，不得补写目录中不存在的参数。
- 如果没有任何商品满足预算，应说明差距并询问是否提高预算或放宽用途。
- 必须展示 market.updated_at 和来源说明；synthetic_seed 是测试数据，不能描述成真实商城报价。
- 不生成购买链接；第一阶段尚未接入正式交易系统。
- 不把“推荐分数”展示给用户，它只用于内部候选排序。

{history_source_guidance("recommendation_agent")}

【系统提供的用户记忆/背景上下文】:
{memory_context if memory_context else "暂无背景上下文。"}
"""
        system_prompt += task_guidance(state)
        tool_context_guard = ToolResultBudgetMiddleware()
        inner_agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=system_prompt,
            context_schema=UserContext,
            name="recommendation_specialist",
            middleware=[tool_context_guard],
            response_format=ToolStrategy(SpecialistResponse) if state.get("current_task") else None,
        )
        
        print("🔍 [RecommendationAgent] 正在进行智能产品选型与推荐...")
        
        return await invoke_traced_agent(
            inner_agent=inner_agent,
            state=state,
            context=user_context,
            agent_name="recommendation_agent",
            summary_model=self.llm,
            system_prompt=system_prompt,
            tools=tools,
            tool_context_guard=tool_context_guard,
        )
