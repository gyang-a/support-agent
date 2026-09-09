"""数码商品售后诊断与人工工单 Agent。"""

import asyncio
from typing import Any, Dict

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from core.workflow.tasks import SpecialistResponse, task_guidance
from core.workflow.failure import retrieval_unavailable, failed_task
from core.knowledge.progress import RagProgressMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

from core.mcp.config import load_mcp_connections
from core.model import create_chat_model
from core.memory.tool_context import ToolResultBudgetMiddleware
from core.observability import invoke_traced_agent
from core.security import UserContext, inject_user_id
from core.knowledge.tools import search_technical_documents
from core.knowledge.prefetch import (
    compact_policy_evidence,
    compact_technical_evidence,
    latest_user_query,
    should_prefetch_policy,
    should_prefetch_technical,
)
from core.policy.tools import search_after_sales_policies
from core.workflow.state import AgentState
from core.workflow.prompting import history_source_guidance


class AfterSalesAgentNode:
    """协调政策检索、故障预诊断、工单创建和人工升级。"""

    def __init__(self):
        self.llm = create_chat_model(temperature=0.1)
        self.mcp_connections = load_mcp_connections()

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        """供主 LangGraph 调用的售后处理节点。"""
        memory_context = state.get("memory_context", "")
        user_context = UserContext(user_id=state.get("user_id", "unknown"))
        client = MultiServerMCPClient(
            connections=self.mcp_connections,
            tool_interceptors=[inject_user_id],
        )
        retrieval_query = latest_user_query(state)
        rag_progress = RagProgressMiddleware()
        technical_rule_hit = should_prefetch_technical(
            "after_sales_agent", retrieval_query
        )
        policy_rule_hit = should_prefetch_policy("after_sales_agent", retrieval_query)
        tasks = [client.get_tools()]
        if technical_rule_hit:
            tasks.append(
                search_technical_documents.ainvoke(
                    {"query": retrieval_query, "limit": 3}
                )
            )
        if policy_rule_hit:
            tasks.append(
                search_after_sales_policies.ainvoke(
                    {"query": retrieval_query, "limit": 3}
                )
            )
        task_results = await asyncio.gather(*tasks)
        all_tools = task_results[0]
        result_index = 1
        if technical_rule_hit:
            if retrieval_unavailable(task_results[result_index]):
                return failed_task("after_sales_agent", ["search_technical_documents"])
            rag_progress.seed("search_technical_documents", {"query": retrieval_query, "limit": 3}, task_results[result_index])
            technical_evidence, technical_prefetch_hit = compact_technical_evidence(
                task_results[result_index]
            )
            result_index += 1
        else:
            technical_evidence = "规则未命中，本轮未执行技术知识预检索。"
            technical_prefetch_hit = False
        if policy_rule_hit:
            rag_progress.seed("search_after_sales_policies", {"query": retrieval_query, "limit": 3}, task_results[result_index])
            policy_evidence, policy_prefetch_hit = compact_policy_evidence(
                task_results[result_index]
            )
        else:
            policy_evidence = "规则未命中，本轮未执行售后政策预检索。"
            policy_prefetch_hit = False
        allowed_tool_names = {
            "get_after_sales_policy_overview",
            "get_device_diagnostic_overview",
            "diagnose_device_issue",
            "search_product_catalog",
            "query_user_orders",
            "create_after_sales_ticket",
            "query_user_tickets",
            "query_ticket_detail",
            "escalate_ticket_to_human",
        }
        tools = [tool for tool in all_tools if tool.name in allowed_tool_names]
        # 知识检索直接在当前进程访问 Milvus，避免 stdio MCP 工具调用期间
        # 重复启动 Python 子进程并初始化 Embedding/Milvus 导致长时间等待。
        tools.extend([search_after_sales_policies, search_technical_documents])

        system_prompt = f"""你是数码商城的【售后诊断与工单Agent】。
你负责所有已入库商品文档覆盖的状态解释、故障预诊断、售后政策查询、售后工单和人工客服升级，不限于手机和笔记本。

【处理流程】
1. 退换货、保修、寄修和数据政策必须有 search_after_sales_policies 证据；系统预检索已命中时直接使用，否则调用工具，并引用 document_id 和生效日期。
1.1 用户询问某个具体商品的保修期限或部件保修期限时，还必须有 search_technical_documents 证据；以说明书回答具体期限，以商城政策说明受理边界。
2. 指示灯/状态灯含义、故障码和说明书操作问题必须有 search_technical_documents 证据；系统预检索已命中时不要重复调用，不要求先调用 diagnose_device_issue。
2.1 明确的物理故障或安全症状才调用 diagnose_device_issue；其信息不足或需要说明书步骤时，再检索 diagnostic_guide 或 product_manual。
2.2 调用 search_technical_documents 前把指代改写成包含品牌、型号和问题的独立自然语言查询；不要生成数据库型号、类目或文档类型参数。根据返回证据确认适用型号，有多个可能型号且结论不同时再追问。
3. 按工具结果原样区分严重度。critical/high 安全问题先给“停止使用”等安全措施，再解释后续流程。
4. 只有用户明确要求创建工单，或在本轮明确同意创建时，才能调用 create_after_sales_ticket。
5. 只有用户明确要求转人工时才能创建人工工单或调用 escalate_ticket_to_human；安全问题可以建议，但不能静默创建。
6. 查询工单时只使用 query_user_tickets / query_ticket_detail，不得猜测状态。

【安全要求】
- 不指导拆机、短接、电池穿刺、高温烘烤或绕过设备安全保护。
- 不承诺一定退款、换货、免费维修或固定完成时间；工单受理不等于审核通过。
- 工单中不得填写密码、验证码、支付信息、银行卡或完整证件号码。
- 系统强制注入 user_id，永远不要展示或尝试修改 user_id。
- 私有工具要求 user_id 参数时只传占位值，运行时会用可信身份强制覆盖。
- 工具返回 unknown 或信息不足时必须继续追问，不能自行确诊。
- 答复应清晰区分：立即安全措施、基础排查、政策依据、下一步。

{history_source_guidance("after_sales_agent")}

【系统提供的用户记忆/背景上下文】：
{memory_context if memory_context else "暂无背景上下文。"}

【技术知识预检索】
规则：{"命中并已执行" if technical_rule_hit else "未命中，未预检索"}
状态：{"已命中证据" if technical_prefetch_hit else "无预检索证据"}
{technical_evidence}

【售后政策预检索】
规则：{"命中并已执行" if policy_rule_hit else "未命中，未预检索"}
状态：{"已命中证据" if policy_prefetch_hit else "无预检索证据"}
{policy_evidence}
"""
        system_prompt += task_guidance(state)
        tool_context_guard = ToolResultBudgetMiddleware()
        inner_agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=system_prompt,
            context_schema=UserContext,
            name="after_sales_specialist",
            middleware=[rag_progress, tool_context_guard],
            response_format=ToolStrategy(SpecialistResponse) if state.get("current_task") else None,
        )

        print(
            "🛠️ [AfterSalesAgent] 正在处理售后诊断或工单请求..."
            f"（技术RAG：{'rule_hit' if technical_rule_hit else 'skipped'}，"
            f"政策RAG：{'rule_hit' if policy_rule_hit else 'skipped'}）"
        )
        result = await invoke_traced_agent(
            inner_agent=inner_agent,
            state=state,
            context=user_context,
            agent_name="after_sales_agent",
            summary_model=self.llm,
            system_prompt=system_prompt,
            tools=tools,
            tool_context_guard=tool_context_guard,
        )
        execution = result.setdefault("metadata", {}).setdefault("execution", {})
        tool_names = execution.setdefault("tool_names", [])
        added_calls = 0
        if technical_rule_hit:
            tool_names.insert(0, "search_technical_documents")
            added_calls += 1
        if policy_rule_hit:
            tool_names.insert(0, "search_after_sales_policies")
            added_calls += 1
        execution["tool_call_count"] = int(execution.get("tool_call_count", 0)) + added_calls
        execution["technical_prefetch_rule_hit"] = technical_rule_hit
        execution["technical_prefetch_hit"] = technical_prefetch_hit
        execution["policy_prefetch_rule_hit"] = policy_rule_hit
        execution["policy_prefetch_hit"] = policy_prefetch_hit
        return result
