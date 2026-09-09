
from typing import Dict, Any
from langchain_core.messages import BaseMessage, SystemMessage, convert_to_messages

from core.model import create_chat_model
from core.workflow.state import AgentState
from core.routing import keyword_fallback, parse_route_decision

class OrchestratorAgent:
    """
    中心路由节点 (Orchestrator/Router)。

    负责识别数码商城用户意图，并将请求分发给商品咨询、选购推荐、
    订单查询或售后诊断 Agent。
    """
    def __init__(self):
        # Router 只判断入口；复合问题的具体计划由 TaskPlannerAgent 负责。
        self.llm = create_chat_model(temperature=0.1)

    @staticmethod
    def _keyword_fallback(message: str) -> str:
        """当模型未返回合法 JSON 时使用可解释的关键词兜底路由。"""
        return keyword_fallback(message)

    @classmethod
    def _parse_route(cls, raw_content: str, message: str) -> tuple[str, dict[str, Any]]:
        """解析模型 JSON，并保证非法输出不会进入未注册的图节点。"""
        return parse_route_decision(raw_content, message)

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

    async def route(self, state: AgentState) -> Dict[str, Any]:
        """
        根据用户的最新输入，决定路由走向。
        """
        # 获取最新的一条用户消息。
        messages = state.get("messages", [])
        if not messages:
            last_message = ""
        else:
            # langgraph 内部有时候会把 tuple 转成实际的 BaseMessage 子类
            last_msg_obj = messages[-1]
            if isinstance(last_msg_obj, tuple):
                last_message = last_msg_obj[1]
            elif hasattr(last_msg_obj, "content"):
                last_message = last_msg_obj.content
            else:
                last_message = str(last_msg_obj)

        memory_context = state.get("memory_context", "")
        agent_choices = (
            "product_agent|recommendation_agent|order_agent|"
            "after_sales_agent|task_planner"
        )

        system_prompt = f"""你是数码商城智能客服的总路由（Orchestrator）。
你的任务仅是判断入口：一个专业 Agent 能完成就直接选择它；需要跨专业 Agent
协作则 next_agent=task_planner。不要拆解子任务，这由编排 Agent 完成。
必须检查整条用户消息的全部意图，不得因命中某个关键词而忽略其他需求。
一个 Agent 内多次工具调用仍是单任务，例如订单和物流都可由 order_agent 完成。
需要先查订单，再解释对应商品的参数或售后条件时，选择 task_planner。
混合了当前不支持的业务意图时也选择 task_planner，不能静默忽略该意图。
你会收到最近最多三轮共享对话。历史 assistant 消息的 name 字段表示回答它的专业 Agent；
请结合这些来源理解省略和指代，但始终以最后一条用户消息作为本轮路由目标。

当前可用的子 Agent：
1. product_agent：查询已明确商品的参数、功能、型号差异、库存快照和使用说明。
2. recommendation_agent：根据品类、预算、用途和品牌偏好推荐商品；“买哪款”“怎么选”“值不值得换”都属于此类。
3. order_agent：查询当前登录用户的订单、支付状态、发货和物流信息。
4. after_sales_agent：各类已入库商品的状态灯、故障码、异常现象、退换货/保修、售后工单和人工客服升级。

以下职责规则仅在该 Agent 可以覆盖本轮全部需求时直接路由：
- 用户已经明确型号并询问具体参数或两个明确型号的差异，选择 product_agent。
- 用户要求从多个型号中做购买决策，即使提到了具体型号，也选择 recommendation_agent。
- 订单、物流、发货、支付状态由 order_agent 处理；混合其他领域任务时选择 task_planner。
- “是否兼容/适配/能否连接”以及配件、扩展坞、显示器等问题选择 product_agent；故障导致的无法充电选择 after_sales_agent。
- 状态灯/指示灯含义、故障码、报警、异响、不工作，以及无法开机、进液、鼓包、过热、死机、花屏等故障选择 after_sales_agent；不限于手机和笔记本。
- 退换货、保修、维修规则和售后工单选择 after_sales_agent；查询某笔退款到账进度选择 order_agent。
- 信息不足时仍路由到对应专业 Agent，由专业 Agent 追问，路由器不直接回答。

【背景记忆】：
{memory_context}
【较早会话摘要】：
{state.get("conversation_summary", "")}

仅输出合法 JSON，不要使用 Markdown：
{{
  "next_agent": "{agent_choices}",
  "intent": "简短意图名称",
  "confidence": 0.0,
  "entities": {{"category": "", "brand": "", "model": "", "budget_max": null}}
}}
无法判断时 next_agent 输出 product_agent。
"""

        recent_messages = self._recent_rounds(messages, max_rounds=3)
        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=system_prompt),
                *recent_messages,
            ])
            next_node, route_payload = self._parse_route(response.content, last_message)
        except Exception:
            next_node, route_payload = "task_planner", {"intent": "fallback"}
        # 模型输出损坏时不能用单关键词吞掉复合意图，转交有计划校验的编排入口。
        if route_payload.get("intent") == "fallback":
            next_node = "task_planner"
        route_payload["next_agent"] = next_node
        route_payload["source"] = (
            "fallback" if route_payload.get("intent") == "fallback" else "llm"
        )
        # 清空上一轮执行元数据，避免旧任务、错误和评估混入本轮。
        metadata = {}
        metadata["route"] = route_payload
        print(f"🧭 [Orchestrator] 数码场景路由至: {next_node}")

        # 返回更新后的 state，同时保留结构化路由元数据供后续观测与评估。
        tasks = [] if next_node == "task_planner" else [{
            "task_id": "t1", "agent_name": next_node,
            "query": last_message, "depends_on": [],
        }]
        return {
            "next_agent": next_node, "metadata": metadata,
            "tasks": tasks, "task_results": None, "planning_error": "",
        }
