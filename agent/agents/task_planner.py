"""复合请求规划器：生成有限、可校验的任务依赖图，不执行业务工具。"""

from langchain_core.messages import SystemMessage

from agents.orchestrator import OrchestratorAgent
from core.model import create_chat_model
from core.workflow.tasks import TaskPlan


class TaskPlannerAgent:
    def __init__(self):
        self.llm = create_chat_model(temperature=0.1)

    async def __call__(self, state):
        prompt = """你是数码商城任务编排器。覆盖本轮用户的全部意图，输出 JSON 任务计划。
可用 Agent：product_agent（商品参数、价格库存、技术说明），
order_agent（本人订单、支付状态、物流），recommendation_agent（选购推荐），
after_sales_agent（故障诊断、售后政策、工单及人工升级）。
同一 Agent 可完成的相关问题尽量合并；独立任务 depends_on=[]，可以并行。
任务需要其他任务的订单号、商品、金额或判断结论时，显式填写 depends_on。
query 写成清晰的自然语言子问题，保留用户限制、否定、条件和精确标识，
解析历史指代但不能虚构订单号、SKU、预算或用户授权。每个任务最多执行一次。
用户未明确要求的写操作不得添加。写操作必须保留原始条件，依赖必要的查询任务。
没有专业 Agent 支持的需求不得伪装成可完成任务或悄悄丢弃；这种情况下输出
{"error":"unsupported_intent"}，由主流程提示用户拆分/澄清请求。
计划格式：{"tasks":[{"task_id":"t1","agent_name":"order_agent",
"query":"查询指定订单","depends_on":[]}]}。最多 8 个任务，ID 唯一，不允许循环依赖。
只输出 JSON，不要 Markdown，不要直接回答用户。
"""
        prompt += "\n历史摘要：" + state.get("conversation_summary", "")
        prompt += "\n用户记忆：" + state.get("memory_context", "")
        metadata = dict(state.get("metadata", {}))
        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=prompt),
                *OrchestratorAgent._recent_rounds(state.get("messages", [])),
            ])
            plan = TaskPlan.model_validate_json(response.content)
            tasks = [task.model_dump() for task in plan.tasks]
            metadata["planning"] = {"status": "completed", "task_count": len(tasks)}
            return {"tasks": tasks, "metadata": metadata}
        except Exception as exc:
            # 不运行不完整/非法计划，也不重试已执行的业务写操作。
            metadata["planning"] = {"status": "failed", "error_type": type(exc).__name__}
            return {
                "tasks": [], "metadata": metadata,
                "planning_error": "暂时无法完整处理这次请求，请分别说明需要查询或办理的事项；如果涉及当前未支持的业务，我会说明处理范围。",
            }
