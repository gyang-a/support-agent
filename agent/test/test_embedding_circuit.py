"""停机时有限请求、并行熔断、恢复探测和 Agent 硬停止。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain.agents.middleware.types import ModelRequest

from core.embedding_resilience import EmbeddingCircuit, EmbeddingUnavailable
from core.memory.tool_context import ToolResultBudgetMiddleware
from core.workflow.failure import AgentLoopAborted
from core.observability.execution import invoke_traced_agent


def test_parallel_outage_has_one_shared_retry_budget_then_recovers():
    async def run():
        now = [0.0]
        circuit = EmbeddingCircuit(clock=lambda: now[0])
        calls = []

        async def down():
            calls.append(1)
            await asyncio.sleep(0)
            raise ConnectionError("Ollama stopped")

        results = await asyncio.gather(*(circuit.call(down) for _ in range(8)), return_exceptions=True)
        assert all(isinstance(result, EmbeddingUnavailable) for result in results)
        assert len(calls) == 3
        now[0] = 61
        with pytest.raises(EmbeddingUnavailable):
            await circuit.call(down)
        assert len(calls) == 4  # 半开仅允许一次探测
        now[0] = 122

        async def recovered():
            return [0.1, 0.2]

        assert await circuit.call(recovered) == [0.1, 0.2]
        assert circuit.open_until == 0
    asyncio.run(run())


def test_embedding_timeout_is_bounded():
    async def run():
        circuit = EmbeddingCircuit(attempts=2, timeout=0.01)
        calls = []

        async def hanging():
            calls.append(1)
            await asyncio.Event().wait()

        with pytest.raises(EmbeddingUnavailable):
            await asyncio.wait_for(circuit.call(hanging), 0.5)
        assert len(calls) == 2
    asyncio.run(run())


def test_unavailable_tool_result_stops_before_another_model_request():
    guard = ToolResultBudgetMiddleware()
    request = ModelRequest(model=object(), messages=[ToolMessage(
        content=json.dumps({"status": "knowledge_unavailable"}), tool_call_id="a",
    )], tools=[])

    async def unexpected(_):
        pytest.fail("No model request should be made")

    with pytest.raises(AgentLoopAborted):
        asyncio.run(guard.awrap_model_call(request, unexpected))
    assert guard.model_calls == 0


def test_normal_progress_continues_past_twelve_model_calls():
    async def run():
        guard = ToolResultBudgetMiddleware()
        calls = []

        async def handler(_):
            calls.append(1)
            return AIMessage(content="continue")

        request = ModelRequest(model=object(), messages=[], tools=[])
        for _ in range(25):
            await guard.awrap_model_call(request, handler)
        assert len(calls) == 25
        assert guard.model_calls == 25
        # 正常推进后遇到真正的服务故障，仍在下一次模型请求前终止。
        unavailable = request.override(messages=[ToolMessage(
            content=json.dumps({"status": "knowledge_unavailable"}), tool_call_id="a",
        )])
        with pytest.raises(AgentLoopAborted):
            await guard.awrap_model_call(unavailable, handler)
        assert len(calls) == 25
    asyncio.run(run())


def test_abort_returns_failed_task_instead_of_crashing_request():
    class Inner:
        async def ainvoke(self, *_args, **_kwargs):
            raise AgentLoopAborted("Retrieval dependency unavailable")

    result = asyncio.run(invoke_traced_agent(
        Inner(), {"messages": []}, None, "product_agent",
    ))
    assert result["task_output"]["status"] == "failed"
    assert "查询已停止" in result["messages"][0].content


def test_unavailable_initialization_has_cooldown(monkeypatch):
    from core.knowledge.milvus_store import TechnicalKnowledgeStore
    from core.knowledge import tools

    store = TechnicalKnowledgeStore(host="localhost", port=19530, api_key=None,
        embedding_api_key=None, embedding_model="", embedding_base_url="", embedding_dimension=2)
    calls = []

    async def down(_):
        calls.append(1)
        raise EmbeddingUnavailable()

    store.embeddings = SimpleNamespace(aembed_query=down)

    async def get_store():
        await store.initialize()
        return store

    monkeypatch.setattr(tools, "get_technical_knowledge_store", get_store)

    async def run():
        for _ in range(3):
            response = await tools.search_technical_documents.ainvoke({"query": "接口"})
            assert json.loads(response)["status"] == "knowledge_unavailable"
        assert len(calls) == 1
    asyncio.run(run())
