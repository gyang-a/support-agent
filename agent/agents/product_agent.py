
import asyncio
from typing import Any, Dict

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from core.workflow.tasks import SpecialistResponse, task_guidance
from core.workflow.failure import retrieval_unavailable, failed_task
from core.knowledge.progress import RagProgressMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

from core.security import UserContext, inject_user_id
from core.knowledge.tools import search_technical_documents
from core.knowledge.prefetch import (
    compact_technical_evidence,
    latest_user_query,
    should_prefetch_technical,
)
from core.mcp.config import load_mcp_connections
from core.model import create_chat_model
from core.memory.tool_context import ToolResultBudgetMiddleware
from core.observability import invoke_traced_agent
from core.workflow.state import AgentState
from core.workflow.prompting import history_source_guidance


class ProductAgentNode:
    """
    包装了 LangChain 1.x create_agent 的数码商品咨询节点。

    使用结构化商品目录、动态市场和商品对比工具回答参数与型号差异，避免
    旧云产品向量库与知识图谱污染数码场景答案。
    """

    def __init__(self):
        self.llm = create_chat_model(temperature=0.1)

        self.mcp_connections = load_mcp_connections()

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        """供主 LangGraph 调用的处理函数。"""
        memory_context = state.get("memory_context", "")
        user_context = UserContext(user_id=state.get("user_id", "unknown"))

        # 商品工具统一通过 MCP 暴露。商品查询不依赖用户身份，
        # 仍复用安全拦截器，便于后续接入会员价时保持调用链一致。
        client = MultiServerMCPClient(
            connections=self.mcp_connections,
            tool_interceptors=[inject_user_id],
        )
        retrieval_query = latest_user_query(state)
        rag_progress = RagProgressMiddleware()
        prefetch_by_rule = should_prefetch_technical("product_agent", retrieval_query)
        if prefetch_by_rule:
            all_tools, raw_technical_evidence = await asyncio.gather(
                client.get_tools(),
                search_technical_documents.ainvoke(
                    {"query": retrieval_query, "limit": 3}
                ),
            )
            if retrieval_unavailable(raw_technical_evidence):
                return failed_task("product_agent", ["search_technical_documents"])
            rag_progress.seed("search_technical_documents", {"query": retrieval_query, "limit": 3}, raw_technical_evidence)
            technical_evidence, has_technical_evidence = compact_technical_evidence(
                raw_technical_evidence
            )
        else:
            all_tools = await client.get_tools()
            technical_evidence = "规则未命中，本轮未执行技术知识预检索。"
            has_technical_evidence = False
        allowed_tool_names = {
            "get_catalog_overview",
            "search_product_catalog",
            "get_product_detail",
            "get_price_and_inventory",
            "compare_product_skus",
        }
        tools = [tool for tool in all_tools if tool.name in allowed_tool_names]
        # 规则未命中时由 Agent 自主决定是否检索；命中后也保留工具，以便
        # 现有证据不足时追加一次更具体的自然语言查询。
        tools.append(search_technical_documents)

        system_prompt = f"""你是数码商城的【商品咨询Agent】。
你的任务是回答手机和笔记本的参数、功能、型号差异、动态库存和价格快照。

可用工具：
1. get_catalog_overview：查询当前目录支持的品类、品牌和商品数量。
2. search_product_catalog：按型号、品牌、用途、预算搜索真实 SKU。
3. get_product_detail：拿到 sku_id 后查询完整规格。
4. get_price_and_inventory：刷新单个 SKU 的当前渠道价格与库存。
5. compare_product_skus：对 2-5 个明确 SKU 做统一维度对比。
6. search_technical_documents：用完整自然语言检索技术知识；规则命中时系统已预检索，未命中时可自主调用。

工作要求：
- 接口、功能、操作、规格限制和说明书事实优先使用技术知识预检索证据；证据已直接回答时不要再查询商品目录验证存在性。
- 商品目录只代表当前商城是否在售，不代表技术文档是否存在；目录未收录时，绝不能否定已命中的说明书及其中的商品事实。
- 价格、库存、购买、推荐和目录内结构化规格必须调用商品工具，不得凭模型记忆回答。
- 仅当问题涉及在售结构化规格、价格、库存或购买时，用户提供型号但没有 sku_id 才先调用 search_product_catalog，再对命中 SKU 调用 get_product_detail。
- 对比两个明确型号时，先搜索获得 SKU，再调用 compare_product_skus，不要手工拼接不同口径的字段。
- 技术证据没有命中时才说明知识库缺少资料，不得用商品目录无结果冒充知识库无结果。
- 引用技术文档时必须保留工具返回的 document_id、章节和页码；没有可靠证据时不得拿相似型号代替。
- 不得推荐目录中不存在的型号；购买决策型问题应简短说明可进一步按预算和用途筛选。
- 价格和库存来自 MySQL 渠道快照，回答中必须带上 market.updated_at 和来源说明；synthetic_seed 是测试数据。
- 工具无结果时如实说明，并建议用户补充品牌、完整型号或品类。
- 退换货、保修和维修政策应由售后 Agent 查询政策库，本 Agent 不编造售后条款。
- 回答保持专业、清晰，参数尽量使用表格或紧凑列表。

{history_source_guidance("product_agent")}

【系统提供的用户记忆/背景上下文】:
{memory_context if memory_context else "暂无背景上下文。"}

【技术知识预检索证据】
规则：{"命中并已执行" if prefetch_by_rule else "未命中，未预检索"}
状态：{"已命中证据" if has_technical_evidence else "无预检索证据"}
{technical_evidence}
"""

        # 使用 LangChain 1.x create_agent 创建内部执行器，由模型自主选择目录工具。
        system_prompt += task_guidance(state)
        tool_context_guard = ToolResultBudgetMiddleware()
        inner_agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=system_prompt,
            context_schema=UserContext,
            name="product_specialist",
            middleware=[rag_progress, tool_context_guard],
            response_format=ToolStrategy(SpecialistResponse) if state.get("current_task") else None,
        )

        print(
            "💡 [ProductAgent] 正在处理数码商品咨询请求..."
            f"（RAG预检索：{'rule_hit' if prefetch_by_rule else 'skipped'}）"
        )

        # 按预算传递完整历史或“摘要 + 近期轮次”，保证多轮指代可理解。
        # 只向主图追加最终答复，同时保留工具调用和耗时供质量节点评测。
        result = await invoke_traced_agent(
            inner_agent=inner_agent,
            state=state,
            context=user_context,
            agent_name="product_agent",
            summary_model=self.llm,
            system_prompt=system_prompt,
            tools=tools,
            tool_context_guard=tool_context_guard,
        )
        execution = result.setdefault("metadata", {}).setdefault("execution", {})
        tool_names = execution.setdefault("tool_names", [])
        if prefetch_by_rule:
            tool_names.insert(0, "search_technical_documents")
            execution["tool_call_count"] = int(execution.get("tool_call_count", 0)) + 1
        execution["technical_prefetch_rule_hit"] = prefetch_by_rule
        execution["technical_prefetch_hit"] = has_technical_evidence
        return result


def get_product_agent():
    """保留给独立测试用的兼容入口；主流程使用 ProductAgentNode。"""
    return ProductAgentNode()


if __name__ == "__main__":
    # 简单的初始化检查入口，交互式运行建议使用 agent/main.py。
    agent = get_product_agent()
    print("🤖 数码商品 ProductAgent 已初始化。")
    print("可测试：iPhone 16 Pro 有哪些规格？")
    print("可测试：小米 15 和 Find X8 的主要差异是什么？")
