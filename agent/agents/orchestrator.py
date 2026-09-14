"""统一入口：严格规则直达，否则一次模型调用完成意图识别与规划。"""

from typing import Any
from langchain_core.messages import BaseMessage, SystemMessage, convert_to_messages

from core.model import create_chat_model
from core.workflow.state import AgentState
from core.workflow.tasks import TaskPlan
from core.routing.rules import recognize_direct_route


class OrchestratorAgent:
    def __init__(self):
        self.llm = create_chat_model(temperature=0.1)

    @staticmethod
    def _recent_rounds(messages: list[Any], max_rounds: int = 3) -> list[BaseMessage]:
        """返回最近若干个以用户消息开始的对话轮次，保留 Agent 来源名。"""
        normalized = convert_to_messages(messages)
        human_indexes = [
            index
            for index, message in enumerate(normalized)
            if getattr(message, "type", "") == "human"
        ]
        if not human_indexes:
            return normalized[-1:]
        round_count = min(max(1, max_rounds), len(human_indexes))
        start_index = human_indexes[-round_count]
        return normalized[start_index:]

    async def route(self, state: AgentState) -> dict[str, Any]:
        """生成校验后的计划，并重置上一轮任务和观测信息。"""
        prompt = """你是数码商城统一路由与任务规划器。每轮一次性识别全部意图、选择专业 Agent 并输出 JSON 任务计划。
简单请求也必须输出一个任务；跨领域或需要分步协作的请求输出多个任务及依赖。
可用 Agent：product_agent（商品参数、价格库存、技术说明），
order_agent（本人订单、支付状态、物流），recommendation_agent（选购推荐），
after_sales_agent（故障诊断、售后政策、工单及人工升级）。
业务边界：明确型号的参数、型号差异、兼容/适配问题交给 product_agent；
购买决策（买哪款、怎么选、是否值得换）交给 recommendation_agent，即使已提到型号。
订单、物流、支付状态及具体退款到账进度交给 order_agent；退换货/保修规则、
状态灯、故障码、设备异常及工单交给 after_sales_agent。
必须检查整条消息，不能因命中订单等关键词而遗漏其他需求。
最近最多三轮对话中 assistant 的 name 表示来源 Agent，用于理解省略和指代；
始终以最后一条用户消息为本轮目标，不能重复执行历史任务。
缺少业务参数但领域明确时，仍生成对应任务，由专业 Agent 追问。
同一 Agent 可完成的相关问题尽量合并（如订单和物流），不按工具调用次数拆任务；独立任务 depends_on=[]，可以并行。
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
        # 每轮重新生成元数据；失败时也必须清空旧任务、结果和错误。
        update = {
            "tasks": [], "task_results": None, "planning_error": "",
            "next_agent": "task_orchestrator", "metadata": {},
        }
        metadata = update["metadata"]
        try:
            recent = self._recent_rounds(state.get("messages", []))
            # 仅匹配本轮用户原话，历史关键词不能触发直达。
            query = recent[-1].content if recent and recent[-1].type == "human" else ""
            rule = recognize_direct_route(query if isinstance(query, str) else "")
            metadata["rule_routing"] = rule
            if rule["agent_name"] is not None:
                plan = TaskPlan.model_validate({"tasks": [{
                    "task_id": "t1", "agent_name": rule["agent_name"],
                    "query": query, "depends_on": [],
                }]})
                source = "rule"
            else:
                response = await self.llm.ainvoke([
                    SystemMessage(content=prompt), *recent,
                ])
                plan = TaskPlan.model_validate_json(response.content)
                source = "llm"
            tasks = [task.model_dump() for task in plan.tasks]
            agent_names = list(dict.fromkeys(task["agent_name"] for task in tasks))
            next_agent = tasks[0]["agent_name"] if len(tasks) == 1 else "task_orchestrator"
            update.update(tasks=tasks, next_agent=next_agent)
            metadata["route"] = {
                "source": source, "next_agent": next_agent, "agent_names": agent_names,
                "rule_id": rule["rule_id"], "rule_reason": rule["reason"],
                "matched_keywords": rule["matched_keywords"],
            }
            metadata["planning"] = {"status": "completed", "task_count": len(tasks), "source": source}
        except Exception as exc:
            # 不执行非法或不完整计划，不以关键词降级到单个专业 Agent。
            metadata["route"] = {"source": "planning_failed", "next_agent": "task_orchestrator"}
            metadata["planning"] = {"status": "failed", "error_type": type(exc).__name__}
            update["planning_error"] = "暂时无法完整处理这次请求，请分别说明需要查询或办理的事项；如果涉及当前未支持的业务，我会说明处理范围。"
        return update

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        return await self.route(state)
