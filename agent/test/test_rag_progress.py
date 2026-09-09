"""成功检索的收敛控制，不限制正常任务的总步数。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.knowledge.prefetch import should_prefetch_technical
from core.knowledge.progress import RagProgressMiddleware
from core.workflow.failure import AgentLoopAborted

NAME = "search_technical_documents"


def evidence(chunk="same", score=1):
    return json.dumps({"status": "success", "data": {"results": [{
        "document_id": "airsense", "chunk_id": chunk, "excerpt": "日志可导出。", "rerank_score": score,
    }]}})


def request(query, call_id="a"):
    return SimpleNamespace(tool_call={"name": NAME, "args": {"query": query}, "id": call_id})


def test_original_question_hits_prefetch_rule():
    assert should_prefetch_technical("product_agent", "AirSense Pro支持日志存储和导出吗")


def test_stalled_retrieval_is_not_reported_as_embedding_outage():
    from core.workflow.failure import RetrievalStalled
    from core.observability.execution import invoke_traced_agent

    class Inner:
        async def ainvoke(self, *_args, **_kwargs):
            raise RetrievalStalled("stopped")

    result = asyncio.run(invoke_traced_agent(Inner(), {"messages": []}, None, "product_agent"))
    assert result["metadata"]["execution"]["termination_reason"] == "retrieval_no_progress"
    assert "没有获得更多资料" in result["task_output"]["response"]


def test_prefetch_reused_without_embedding_and_stops_duplicate_loop():
    async def run():
        guard = RagProgressMiddleware()
        guard.seed(NAME, {"query": "原问题", "limit": 3}, evidence())

        async def forbidden(_):
            pytest.fail("预检索相同问题不得重新请求 Embedding")

        first = await guard.awrap_tool_call(request("原问题", "a"), forbidden)
        second = await guard.awrap_tool_call(request("原问题", "b"), forbidden)
        assert first.tool_call_id == "a" and second.tool_call_id == "b"
        assert guard.closed == {NAME}
        with pytest.raises(AgentLoopAborted):
            await guard.awrap_tool_call(request("原问题"), forbidden)
    asyncio.run(run())


def test_rephrasing_and_reranker_scores_do_not_count_as_new_evidence():
    async def run():
        guard = RagProgressMiddleware()
        guard.seed(NAME, {"query": "原问题"}, evidence(score=1))

        async def same(call):
            return ToolMessage(content=evidence(score=99), name=NAME, tool_call_id=call.tool_call["id"])

        await guard.awrap_tool_call(request("日志如何导出"), same)
        await guard.awrap_tool_call(request("是否支持导出日志"), same)
        assert NAME in guard.closed
    asyncio.run(run())


def test_new_evidence_keeps_complex_task_running_and_resets_stagnation():
    async def run():
        guard = RagProgressMiddleware()

        async def new(call):
            return ToolMessage(content=evidence(chunk=call.tool_call["args"]["query"]), name=NAME, tool_call_id="a")

        for index in range(25):
            await guard.awrap_tool_call(request(str(index)), new)
            await guard.awrap_tool_call(request(str(index)), new)  # 一次重复，随后新证据清零
        assert guard.closed == set()
        assert guard.stagnant[NAME] == 1
    asyncio.run(run())


def test_real_agent_stops_retrieving_and_answers_from_existing_evidence():
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ToolStrategy
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from core.workflow.tasks import SpecialistResponse

    bindings, calls = [], []

    @tool
    async def search_technical_documents(query: str) -> str:
        """查询技术证据。"""
        calls.append(query)
        return evidence()

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            bindings.append([getattr(t, "name", "") for t in tools])
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if NAME in bindings[-1]:
                call = {"name": NAME, "args": {"query": f"改写问题{len(calls)}"}, "id": f"search-{len(calls)}"}
            else:
                call = {"name": "SpecialistResponse", "args": {
                    "status": "completed", "response": "根据 airsense 文档，日志可导出。", "data": {},
                }, "id": "final"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])

    guard = RagProgressMiddleware()
    guard.seed(NAME, {"query": "原问题"}, evidence())
    agent = create_agent(model=Model(), tools=[search_technical_documents], middleware=[guard],
                         response_format=ToolStrategy(SpecialistResponse))
    result = asyncio.run(agent.ainvoke({"messages": [("user", "日志可导出吗？")]}))
    assert len(calls) == 2
    assert len(bindings) == 3
    assert result["structured_response"].status == "completed"
    assert "日志可导出" in result["structured_response"].response
