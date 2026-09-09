"""多意图编排：依赖调度、隔离、失败传播、跨轮状态与接口来源。"""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from agents.orchestrator import OrchestratorAgent
from agents.task_planner import TaskPlannerAgent
from core.knowledge.prefetch import latest_user_query
from core.observability.execution import invoke_traced_agent
from core.observability.monitor import QualityMonitorNode, TraceStore
from core.workflow.graph_manager import AgentGraphManager
from core.workflow.scheduler import TaskScheduler
from core.workflow.synthesis import SynthesisNode
from core.workflow.tasks import TaskPlan, merge_task_results, task_guidance


def task(task_id, agent="order_agent", deps=None, query="查询订单"):
    return {"task_id": task_id, "agent_name": agent, "query": query, "depends_on": deps or []}


def output(response="已查询到该订单的信息。", status="completed", data=None):
    return {
        "task_output": {"status": status, "response": response, "data": data or {}},
        "messages": [AIMessage(content=response)],
        "metadata": {"execution": {"tool_names": ["query_order_detail"], "tool_call_count": 1}},
    }


def state(tasks):
    return {
        "messages": [HumanMessage(content="本轮完整问题")], "tasks": tasks,
        "task_results": [], "metadata": {}, "user_id": "user_1001",
        "session_id": "session", "memory_context": "喜欢轻薄机型",
    }


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return AIMessage(content=response)


@pytest.mark.parametrize("tasks", [
    [], [task("a"), task("a")], [task("a", deps=["missing"])],
    [task("a", deps=["a"])], [task("a", deps=["b"]), task("b", deps=["a"])],
    [task("a", agent="unknown")], [task("a", query=" ")],
    [task(str(index)) for index in range(9)],
])
def test_invalid_plan_cannot_execute(tasks):
    with pytest.raises(ValidationError):
        TaskPlan.model_validate({"tasks": tasks})


def test_results_append_by_task_id_and_explicitly_reset():
    first = {"task_id": "a", "agent_name": "order_agent", "status": "failed"}
    second = {"task_id": "b", "agent_name": "order_agent", "status": "completed"}
    updated = {**first, "status": "completed"}
    assert merge_task_results([first], [second, updated]) == [updated, second]
    assert merge_task_results([first], []) == [first]
    assert merge_task_results([first], None) == []


def test_dependencies_start_without_waiting_for_unrelated_task_and_isolate_state():
    async def run():
        independent_started = asyncio.Event()
        dependency_started = asyncio.Event()
        root_state = state([task("a"), task("b"), task("c", deps=["a"])])

        async def agent(local):
            task_id = local["current_task"]["task_id"]
            local["current_task"]["agent_name"] = "mutated-private-copy"
            assert local["user_id"] == "user_1001"
            assert local["memory_context"] == "喜欢轻薄机型"
            assert len(local["messages"]) == 1
            local["messages"].append(AIMessage(content="私有中间结果"))
            local["metadata"]["private"] = task_id
            if task_id == "a":
                await independent_started.wait()
                return output(data={"order_id": "DG-1001-0002"})
            if task_id == "b":
                independent_started.set()
                await dependency_started.wait()
                assert local["dependency_results"] == []
            if task_id == "c":
                assert local["dependency_results"][0]["data"]["order_id"] == "DG-1001-0002"
                assert local["dependency_results"][0]["agent_name"] == "order_agent"
                dependency_started.set()
            return output()

        result = await asyncio.wait_for(TaskScheduler({"order_agent": agent})(root_state), 2)
        assert [r["task_id"] for r in result["task_results"]] == ["a", "b", "c"]
        assert all(r["agent_name"] == "order_agent" for r in result["task_results"])
        assert len(root_state["messages"]) == 1
        assert root_state["metadata"] == {}
        assert "messages" not in result
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["needs_input", "failed", "exception", "timeout"])
def test_failure_or_clarification_blocks_only_dependent_tasks(outcome):
    async def run():
        executed = []

        async def agent(local):
            task_id = local["current_task"]["task_id"]
            executed.append(task_id)
            if task_id == "a":
                if outcome == "exception":
                    raise RuntimeError("private error")
                if outcome == "timeout":
                    await asyncio.Event().wait()
                return output("请提供需要查询的订单号。", status=outcome)
            return output()

        result = await TaskScheduler({"order_agent": agent}, timeout_seconds=0.02)(state([
            task("d", deps=["c"]), task("a"), task("b"), task("c", deps=["a"]),
        ]))
        statuses = {r["task_id"]: r["status"] for r in result["task_results"]}
        assert set(executed) == {"a", "b"}
        assert statuses["b"] == "completed"
        assert statuses["c"] == statuses["d"] == "blocked"
        assert statuses["a"] == (outcome if outcome in {"needs_input", "failed"} else "failed")
        assert "private error" not in str(result)
    asyncio.run(run())


def test_cancellation_cleans_up_running_specialists():
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def agent(local):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        future = asyncio.create_task(TaskScheduler({"order_agent": agent})(state([task("a")])))
        await entered.wait()
        future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await future
        assert cancelled.is_set()
    asyncio.run(run())


def test_prefetch_uses_task_query_while_original_question_remains_in_history():
    local = state([])
    local["current_task"] = task("a", "product_agent", query="该机型支持什么接口？")
    assert latest_user_query(local) == "该机型支持什么接口？"
    assert local["messages"][-1].content == "本轮完整问题"
    assert "用户原话是授权依据" in task_guidance(local)


def test_structured_specialist_result_is_not_the_structured_tool_message():
    class Inner:
        async def ainvoke(self, inputs, context):
            assert inputs["messages"][-1].content == "本轮完整问题"
            return {
                "structured_response": {"status": "needs_input", "response": "请提供完整型号。", "data": {}},
                "messages": [
                    AIMessage(content="", tool_calls=[{"name": "SpecialistResponse", "args": {}, "id": "r"}]),
                    ToolMessage(content="Returning structured response", tool_call_id="r"),
                ],
            }
    local = {**state([]), "current_task": task("a")}
    result = asyncio.run(invoke_traced_agent(Inner(), local, None, "order_agent"))
    assert result["task_output"]["status"] == "needs_input"
    assert result["messages"][-1].content == "请提供完整型号。"
    assert result["messages"][-1].name == "order_agent"
    assert result["metadata"]["execution"]["tool_names"] == []


def test_real_langchain_loop_runs_business_tool_then_returns_structured_result():
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ToolStrategy
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from core.workflow.tasks import SpecialistResponse

    called = []

    @tool
    def query_order_detail(order_id: str) -> dict:
        """查询测试订单。"""
        called.append(order_id)
        return {"order_id": order_id, "status": "shipped"}

    class ScriptedModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if any(isinstance(m, ToolMessage) and m.name == "query_order_detail" for m in messages):
                call = {"id": "final", "name": "SpecialistResponse", "args": {
                    "status": "completed", "response": "订单 DG-1001-0002 已发货。",
                    "data": {"order_id": "DG-1001-0002"},
                }}
            else:
                call = {"id": "lookup", "name": "query_order_detail", "args": {"order_id": "DG-1001-0002"}}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])

    local = {**state([]), "current_task": task("a")}
    inner = create_agent(
        model=ScriptedModel(), tools=[query_order_detail],
        response_format=ToolStrategy(SpecialistResponse),
    )
    result = asyncio.run(invoke_traced_agent(inner, local, None, "order_agent"))
    assert called == ["DG-1001-0002"]
    assert result["task_output"]["data"]["order_id"] == "DG-1001-0002"
    assert result["metadata"]["execution"]["tool_names"] == ["query_order_detail"]
    assert result["messages"][-1].content == "订单 DG-1001-0002 已发货。"


def test_bad_planner_output_executes_nothing():
    planner = object.__new__(TaskPlannerAgent)
    planner.llm = FakeLLM('{"tasks":[{"task_id":"x","agent_name":"unknown","query":"查询"}]}')
    planned = asyncio.run(planner(state([])))
    assert planned["tasks"] == []
    assert planned["planning_error"]
    assert asyncio.run(TaskScheduler({})({**state([]), **planned})) == {}


def test_invalid_router_output_goes_to_planner_instead_of_single_keyword():
    router = object.__new__(OrchestratorAgent)
    router.llm = FakeLLM('[]')
    result = asyncio.run(router.route(state([])))
    assert result["next_agent"] == "task_planner"


def test_synthesis_fallback_preserves_completed_and_unfinished_tasks():
    node = object.__new__(SynthesisNode)
    node.llm = FakeLLM(RuntimeError("model down"))
    local = state([task("a"), task("b")])
    local["task_results"] = [
        {"task_id": "a", "agent_name": "order_agent", "status": "completed", "response": "订单 DG-1001-0002 已发货。", "data": {}},
        {"task_id": "b", "agent_name": "order_agent", "status": "needs_input", "response": "请提供另一笔订单号。", "data": {}},
    ]
    result = asyncio.run(node(local))
    assert "DG-1001-0002" in result["messages"][0].content
    assert "请提供另一笔订单号" in result["messages"][0].content
    assert result["metadata"]["synthesis"]["source"] == "fallback"


def test_full_graph_multi_then_single_preserves_history_and_resets_tasks(tmp_path):
    async def run():
        manager = object.__new__(AgentGraphManager)
        manager.orchestrator = object.__new__(OrchestratorAgent)
        manager.orchestrator.llm = FakeLLM('{"next_agent":"task_planner"}', '{"next_agent":"order_agent"}')
        manager.task_planner = object.__new__(TaskPlannerAgent)
        manager.task_planner.llm = FakeLLM(json.dumps({"tasks": [task("a"), task("b", "product_agent")]}))
        manager.synthesis_node = object.__new__(SynthesisNode)
        manager.synthesis_node.llm = FakeLLM("订单 DG-1001-0002 已发货，商品参数也已查询。")
        manager.quality_monitor_node = QualityMonitorNode(TraceStore(tmp_path / "trace.jsonl", tmp_path / "alert.jsonl"))
        inputs = []

        async def agent(local):
            inputs.append(local)
            return output()

        manager.product_node = manager.billing_node = agent
        manager.recommendation_node = manager.after_sales_node = agent
        graph = manager.build_graph(InMemorySaver())
        config = {"configurable": {"thread_id": "multi-test"}}
        first = await graph.ainvoke(state([]), config)
        assert len(first["messages"]) == 2
        assert len(first["task_results"]) == 2
        assert first["messages"][-1].name == "task_orchestrator"
        assert first["metadata"]["quality"]["passed"]
        second = await graph.ainvoke({"messages": [HumanMessage(content="那什么时候送到？")]}, config)
        assert len(second["messages"]) == 4
        assert [r["task_id"] for r in second["task_results"]] == ["t1"]
        assert second["messages"][-1].name == "order_agent"
        assert inputs[-1]["messages"][-2].content.startswith("订单 DG-1001-0002")
        assert inputs[-1]["messages"][-2].name == "task_orchestrator"
        assert inputs[-1]["dependency_results"] == []
        assert len(manager.synthesis_node.llm.calls) == 1
        # 缓存命中从图外更新状态，也必须显式清除上一轮结果。
        await graph.aupdate_state(config, {
            "messages": [HumanMessage(content="缓存问题"), AIMessage(content="缓存答复", name="semantic_cache")],
            "tasks": [], "task_results": None, "planning_error": "",
        })
        snapshot = await graph.aget_state(config)
        assert snapshot.values["task_results"] == []
        assert len(snapshot.values["messages"]) == 6
    asyncio.run(run())


def test_sse_done_includes_task_sources_without_internal_data():
    from app.service.chat_service import _yield_sse

    async def run():
        events = [event async for event in _yield_sse("完成", task_results=[{
            "task_id": "a", "agent_name": "order_agent", "status": "completed",
            "response": "已查询到订单", "data": {"user_id": "private"}, "metadata": {"error": "secret"},
        }])]
        payload = json.loads(events[-1].removeprefix("data: "))
        assert payload["done"] and not payload["cached"]
        assert payload["task_results"][0]["agent_name"] == "order_agent"
        assert "private" not in events[-1] and "secret" not in events[-1]
    asyncio.run(run())
