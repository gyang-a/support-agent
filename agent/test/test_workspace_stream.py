"""Public streaming contract, cancellation persistence and context isolation."""
import asyncio
import copy
import json
from app.service import workspace_stream as bridge
from core.observability.live_events import emit, event_sink


def test_stream_persists_answer_and_public_trace(monkeypatch):
    saved = []

    async def save(run):
        saved.append(copy.deepcopy(run))

    async def chat(*args):
        emit("task.started", task_id="t1", agent_name="product_agent", status="running")
        yield 'data: {"content":"你好"}\n\n'
        yield 'data: {"done":true}\n\n'

    monkeypatch.setattr(bridge, "save_run", save)
    monkeypatch.setattr(bridge, "stream_chat", chat)

    async def run():
        return [json.loads(frame[5:]) async for frame in bridge.execute_stream("test", "conversation", "hello")]

    events = asyncio.run(run())
    assert [e["type"] for e in events] == ["run.started", "task.started", "message.delta", "run.completed"]
    assert saved[-1]["response"] == "你好"
    assert saved[-1]["status"] == "completed"
    assert "test" not in bridge.active
    assert event_sink.get() is None


def test_cancel_stops_execution_and_saves_cancelled_state(monkeypatch):
    saved = []
    async def save(run):
        saved.append(copy.deepcopy(run))
    async def chat(*args):
        await asyncio.Event().wait()
        yield ""
    monkeypatch.setattr(bridge, "save_run", save)
    monkeypatch.setattr(bridge, "stream_chat", chat)

    async def run():
        stream = bridge.execute_stream("cancel", "conversation", "hello")
        await anext(stream)
        bridge.active["cancel"].cancel()
        frames = [frame async for frame in stream]
        assert any("run.cancelled" in frame for frame in frames)
    asyncio.run(run())
    assert saved[-1]["status"] == "cancelled"
    assert "cancel" not in bridge.active


def test_parallel_request_event_sinks_are_isolated():
    async def run():
        first, second = [], []
        async def request(target, label):
            token = event_sink.set(lambda kind, data: target.append(data["label"]))
            try:
                await asyncio.sleep(0)
                emit("test", label=label)
            finally:
                event_sink.reset(token)
        await asyncio.gather(request(first, "a"), request(second, "b"))
        assert first == ["a"]
        assert second == ["b"]
    asyncio.run(run())


def test_evidence_event_matches_compact_model_context():
    from core.knowledge.prefetch import compact_technical_evidence, compact_policy_evidence
    payload = json.dumps({"status": "success", "data": {"query": "充电说明", "results": [{"title": "说明书", "excerpt": "请使用指定电源", "document_id": "manual", "private_field": "not-public"}]}})
    for compact in (compact_technical_evidence, compact_policy_evidence):
        events = []
        token = event_sink.set(lambda kind, data: events.append((kind, data)))
        try:
            context, found = compact(payload)
        finally:
            event_sink.reset(token)
        assert found
        assert events[0][0] == "retrieval.completed"
        assert events[0][1]["results"] == json.loads(context)["results"]
        assert "private_field" not in events[0][1]["results"][0]
