"""Regression: autonomous RAG tool calls must publish evidence, not just tool names."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from core.knowledge.prefetch import should_prefetch_technical
from core.knowledge.progress import RagProgressMiddleware
from core.observability.live_events import event_sink, task_identity

QUESTION = "Aurora X1 Pro的320Mhz信道在什么频段用"


@pytest.mark.parametrize("name", ["search_technical_documents", "search_after_sales_policies"])
def test_autonomous_and_cached_results_publish_same_evidence(name):
    assert not should_prefetch_technical("product_agent", QUESTION)
    raw = json.dumps({"status": "success", "data": {"results": [{
        "title": "设备指南", "document_id": "aurora", "chunk_id": "wifi", "content": "320 MHz 位于 6 GHz 频段。",
        "page_start": 3, "rerank_score": 0.82, "private_field": "must-not-leak",
    }]}})
    async def run():
        guard = RagProgressMiddleware()
        events = []
        sink_token = event_sink.set(lambda kind, data: events.append((kind, data)))
        task_token = task_identity.set({"task_id": "t1", "agent_name": "product_agent"})
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            return ToolMessage(content=raw, tool_call_id=request.tool_call["id"], name=name)
        try:
            for call_id in ("first", "cached"):
                request = SimpleNamespace(tool_call={"name": name, "args": {"query": QUESTION}, "id": call_id})
                result = await guard.awrap_tool_call(request, handler)
                assert result.content == raw
                assert result.tool_call_id == call_id
        finally:
            event_sink.reset(sink_token)
            task_identity.reset(task_token)
        assert calls == 1
        assert len(events) == 2
        for kind, data in events:
            assert kind == "retrieval.completed"
            assert data["query"] == QUESTION
            assert data["task_id"] == "t1"
            assert data["stage"] == "tool_result"
            assert data["results"][0]["excerpt"] == "320 MHz 位于 6 GHz 频段。"
            assert "private_field" not in data["results"][0]
        assert not events[0][1]["cached"]
        assert events[1][1]["cached"]
    asyncio.run(run())


@pytest.mark.parametrize("status", ["not_found", "knowledge_unavailable"])
def test_failed_or_empty_retrieval_emits_no_fabricated_evidence(status):
    from core.observability.retrieval_events import emit_tool_retrieval
    events = []
    token = event_sink.set(lambda kind, data: events.append(data))
    try:
        emit_tool_retrieval({"name": "search_technical_documents", "args": {"query": QUESTION}}, json.dumps({"status": status}), cached=False)
    finally:
        event_sink.reset(token)
    assert events[0]["status"] == status
    assert events[0]["results"] == []
