# 这里没有 sys.path.insert！我们期望调用者 (main.py) 正确设置 sys.path。

import asyncio

from langgraph.graph import StateGraph, START, END
from core.workflow.state import AgentState
from agents.orchestrator import OrchestratorAgent
from agents.product_agent import ProductAgentNode
from agents.billing_agent import BillingAgentNode
from agents.recommendation_agent import RecommendationAgent
from agents.after_sales_agent import AfterSalesAgentNode
from core.observability import QualityMonitorNode
from agents.task_planner import TaskPlannerAgent
from core.workflow.scheduler import TaskScheduler
from core.workflow.synthesis import SynthesisNode

class AgentGraphManager:
    """
    负责组装 LangGraph 多 Agent 编排。

    当前覆盖商品咨询、选购推荐、订单物流与售后诊断。
    """
    def __init__(self):
        self.orchestrator = OrchestratorAgent()
        self.product_node = ProductAgentNode()
        self.billing_node = BillingAgentNode()
        self.recommendation_node = RecommendationAgent()
        self.after_sales_node = AfterSalesAgentNode()
        self.quality_monitor_node = QualityMonitorNode()
        self.task_planner = TaskPlannerAgent()
        self.synthesis_node = SynthesisNode()

    def _route_condition(self, state: AgentState) -> str:
        """根据 Orchestrator 的决策决定走向哪个 Agent 节点。"""
        return state.get("next_agent", "product_agent")

    def build_graph(self, checkpointer=None) -> StateGraph:
        """构建状态图"""
        builder = StateGraph(AgentState)

        # 1. 添加节点
        builder.add_node("orchestrator", self.orchestrator.route)
        builder.add_node("task_planner", self.task_planner)
        builder.add_node("task_scheduler", TaskScheduler({
            "product_agent": self.product_node,
            "order_agent": self.billing_node,
            "recommendation_agent": self.recommendation_node,
            "after_sales_agent": self.after_sales_node,
        }))
        builder.add_node("synthesis", self.synthesis_node)
        builder.add_node("quality_monitor", self.quality_monitor_node)

        # 2. 定义边
        builder.add_edge(START, "orchestrator")

        # 单领域直接生成一项任务；复合请求先经过计划节点。
        builder.add_conditional_edges(
            "orchestrator",
            self._route_condition,
            {
                "product_agent": "task_scheduler",
                "order_agent": "task_scheduler",
                "recommendation_agent": "task_scheduler",
                "after_sales_agent": "task_scheduler",
                "task_planner": "task_planner",
            }
        )

        builder.add_edge("task_planner", "task_scheduler")
        builder.add_edge("task_scheduler", "synthesis")
        builder.add_edge("synthesis", "quality_monitor")
        builder.add_edge("quality_monitor", END)

        return builder.compile(checkpointer=checkpointer)

async def test_graph():
    #创建实例
    manager = AgentGraphManager()
    #构建图
    graph = manager.build_graph()

    print("🚀 正在启动数码商城智能客服系统 (Multi-Agent 编排模式)...")
    print("="*60)
    
    # 模拟第一轮对话
    state: AgentState = {
        "messages": [("user", "iPhone 16 Pro 的主要参数是什么？")],
        "user_id": "user_1001",
        "session_id": "test_session_1",
        "memory_context": "",
        "next_agent": "",
        "metadata": {}
    }
    print(f"👤 用户: {state['messages'][0][1]}")
    
    result = await graph.ainvoke(state)
    print(f"🤖 AI: {result['messages'][-1].content}\n")

    # 模拟第二轮对话，测试路由
    state["messages"] = result["messages"]
    state["messages"].append(("user", "那帮我查一下我最近的订单和物流？"))
    
    print(f"👤 用户: {state['messages'][-1][1]}")
    result = await graph.ainvoke(state)
    print(f"🤖 AI: {result['messages'][-1].content}\n")

if __name__ == "__main__":
    asyncio.run(test_graph())
