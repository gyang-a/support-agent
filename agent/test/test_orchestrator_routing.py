"""总路由全意图判断、上下文裁剪与非法输出兜底测试。"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from agents.orchestrator import OrchestratorAgent
from core.routing import deterministic_route, parse_route_decision


def test_parse_structured_order_route() -> None:
    """合法结构化结果应保留意图和实体元数据。"""
    raw = (
        '{"next_agent":"order_agent","intent":"shipment_query",'
        '"confidence":0.96,"entities":{}}'
    )

    next_agent, payload = parse_route_decision(raw, "查一下我的物流")

    assert next_agent == "order_agent"
    assert payload["intent"] == "shipment_query"


def test_invalid_model_output_uses_keyword_fallback() -> None:
    """模型输出非法时，订单关键词仍不能落入默认商品咨询节点。"""
    next_agent, payload = parse_route_decision(
        "无法确定",
        "帮我看看订单什么时候发货",
    )

    assert next_agent == "order_agent"
    assert payload["intent"] == "fallback"


def test_compatibility_keyword_falls_back_to_product_agent() -> None:
    """停用独立兼容性 Agent 后，配件问题由商品 Agent 处理。"""
    next_agent, payload = parse_route_decision(
        "not-json",
        "这个雷电4扩展坞能不能接 MacBook Air？",
    )

    assert next_agent == "product_agent"
    assert payload["intent"] == "fallback"


def test_device_failure_uses_after_sales_fallback() -> None:
    """故障、安全和售后问题应进入售后诊断 Agent。"""
    next_agent, payload = parse_route_decision(
        "not-json",
        "手机充电时电池鼓包而且很烫，我要转人工客服",
    )

    assert next_agent == "after_sales_agent"
    assert payload["intent"] == "fallback"


def test_status_light_question_routes_directly_to_after_sales() -> None:
    assert deterministic_route("云雀扫地机器人黄灯一直亮是啥意思") == "after_sales_agent"


def test_refund_progress_remains_order_route() -> None:
    """退款规则归售后，具体退款到账进度仍归订单 Agent。"""
    next_agent, _ = parse_route_decision("invalid", "我的退款进度到哪里了？")

    assert next_agent == "order_agent"


def test_deterministic_route_returns_none_for_ambiguous_follow_up() -> None:
    assert deterministic_route("那这个呢？") is None


def test_keyword_match_still_checks_all_intents_with_model() -> None:
    class RoutingLLM:
        async def ainvoke(self, _messages):
            return AIMessage(content='{"next_agent":"task_planner"}')

    orchestrator = object.__new__(OrchestratorAgent)
    orchestrator.llm = RoutingLLM()
    result = asyncio.run(
        orchestrator.route(
            {
                "messages": [HumanMessage(content="查一下我的订单，再解释该商品的接口参数")],
                "metadata": {},
                "memory_context": "",
            }
        )
    )

    assert result["next_agent"] == "task_planner"
    assert result["metadata"]["route"]["source"] == "llm"
    assert result["task_results"] is None


def test_llm_route_receives_only_latest_three_rounds_with_agent_names() -> None:
    class RecordingLLM:
        def __init__(self):
            self.messages = []

        async def ainvoke(self, messages):
            self.messages = messages
            return AIMessage(
                content=(
                    '{"next_agent":"product_agent","intent":"follow_up",'
                    '"confidence":0.8,"entities":{}}'
                )
            )

    llm = RecordingLLM()
    orchestrator = object.__new__(OrchestratorAgent)
    orchestrator.llm = llm
    history = [
        HumanMessage(content="第一轮"),
        AIMessage(content="回答一", name="product_agent"),
        HumanMessage(content="第二轮"),
        AIMessage(content="回答二", name="order_agent"),
        HumanMessage(content="第三轮"),
        AIMessage(content="回答三", name="after_sales_agent"),
        HumanMessage(content="第四轮"),
        AIMessage(content="回答四", name="recommendation_agent"),
        HumanMessage(content="那这个呢？"),
    ]

    result = asyncio.run(
        orchestrator.route(
            {"messages": history, "metadata": {}, "memory_context": ""}
        )
    )

    routed_history = llm.messages[1:]
    assert [message.content for message in routed_history] == [
        "第三轮",
        "回答三",
        "第四轮",
        "回答四",
        "那这个呢？",
    ]
    assert routed_history[1].name == "after_sales_agent"
    assert routed_history[3].name == "recommendation_agent"
    assert result["metadata"]["route"]["source"] == "llm"
