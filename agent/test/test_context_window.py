"""客服会话上下文预算、滚动摘要和剪枝测试。"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage
from langchain.agents.middleware.types import ModelRequest

from core.memory.context_window import (
    estimate_request_tokens,
    prepare_agent_context,
)
from core.memory.tool_context import (
    TOOL_RESULT_PLACEHOLDER,
    TRUNCATION_MARKER,
    ToolResultBudgetMiddleware,
)


class RecordingSummaryModel:
    def __init__(self, response: str = "目标：继续选购；历史动态数据需要重新查询。") -> None:
        self.response = response
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self.response)


def _rounds(count: int, *, old_payload: str = "") -> list:
    messages = []
    for index in range(count):
        payload = old_payload if index < 5 else ""
        messages.extend(
            [
                HumanMessage(content=f"用户第{index}轮{payload}"),
                AIMessage(content=f"回答第{index}轮", name="product_agent"),
            ]
        )
    return messages


def test_under_budget_keeps_complete_history_without_summary() -> None:
    messages = _rounds(3)
    model = RecordingSummaryModel()

    prepared = asyncio.run(
        prepare_agent_context(
            messages=messages,
            summary_model=model,
            token_budget=10_000,
        )
    )

    assert prepared.messages == messages
    assert prepared.compressed is False
    assert prepared.summarized_message_count == 0
    assert model.calls == []


def test_over_budget_summarizes_old_prefix_and_keeps_latest_twenty_rounds() -> None:
    messages = _rounds(25, old_payload="旧内容" * 300)
    original_contents = [message.content for message in messages]
    model = RecordingSummaryModel()

    prepared = asyncio.run(
        prepare_agent_context(
            messages=messages,
            summary_model=model,
            token_budget=1_200,
            recent_rounds=20,
            summary_max_chars=500,
        )
    )

    assert isinstance(prepared.messages[0], SystemMessage)
    retained = prepared.messages[1:]
    assert sum(message.type == "human" for message in retained) == 20
    assert retained[0].content.startswith("用户第5轮")
    assert prepared.summarized_message_count == 10
    assert prepared.compressed is True
    assert len(prepared.summary) <= 500
    assert len(model.calls) == 1
    assert [message.content for message in messages] == original_contents


def test_extreme_recent_history_drops_oldest_complete_rounds_but_keeps_current() -> None:
    messages = _rounds(22)
    messages[-2] = HumanMessage(content="当前问题" + "很长" * 600)
    model = RecordingSummaryModel("此前目标：查询商品。")

    prepared = asyncio.run(
        prepare_agent_context(
            messages=messages,
            summary_model=model,
            token_budget=500,
            recent_rounds=20,
        )
    )

    assert any("当前问题" in str(message.content) for message in prepared.messages)
    assert prepared.retained_rounds < 20
    assert prepared.budget_overflow is True
    assert len(messages) == 44


def test_existing_summary_is_updated_incrementally_from_saved_cursor() -> None:
    messages = _rounds(45)
    model = RecordingSummaryModel("更新后的摘要")

    prepared = asyncio.run(
        prepare_agent_context(
            messages=messages,
            summary_model=model,
            existing_summary="已有摘要",
            summarized_message_count=10,
            token_budget=1_000,
            recent_rounds=20,
        )
    )

    assert prepared.summary == "更新后的摘要"
    assert prepared.summarized_message_count == 50
    assert len(model.calls) == 1
    assert "已有摘要" in model.calls[0][0].content
    assert "用户第5轮" in model.calls[0][0].content
    assert "用户第24轮" in model.calls[0][0].content
    assert "用户第25轮" not in model.calls[0][0].content


def test_request_estimate_includes_system_prompt_and_tool_schema() -> None:
    class FakeTool:
        name = "query_order"
        description = "查询订单" * 20
        args_schema = {"type": "object", "properties": {"order_id": {"type": "string"}}}

    messages = [HumanMessage(content="查询")]
    messages_only = estimate_request_tokens(messages)
    complete = estimate_request_tokens(
        messages,
        system_prompt="系统提示" * 20,
        tools=[FakeTool()],
    )

    assert complete > messages_only


def test_internal_model_budget_clears_older_tool_result_and_keeps_latest() -> None:
    messages = [
        HumanMessage(content="查询商品"),
        AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call-1"}],
        ),
        ToolMessage(content="旧结果" * 500, tool_call_id="call-1", name="search"),
        AIMessage(
            content="",
            tool_calls=[{"name": "detail", "args": {}, "id": "call-2"}],
        ),
        ToolMessage(content="最新结果", tool_call_id="call-2", name="detail"),
    ]
    cleared = [
        *messages[:2],
        messages[2].model_copy(update={"content": TOOL_RESULT_PLACEHOLDER}),
        *messages[3:],
    ]
    budget = estimate_request_tokens(cleared, system_prompt="系统") + 5
    guard = ToolResultBudgetMiddleware(token_budget=budget)
    captured = []

    async def handler(request):
        captured.extend(request.messages)
        return AIMessage(content="完成")

    request = ModelRequest(
        model=object(),
        messages=messages,
        system_prompt="系统",
        tools=[],
    )
    asyncio.run(guard.awrap_model_call(request, handler))

    assert messages[2].content.startswith("旧结果")
    assert captured[2].content == TOOL_RESULT_PLACEHOLDER
    assert captured[-1].content == "最新结果"
    assert guard.pruned_tool_results == 1
    assert guard.truncated_tool_results == 0


def test_internal_model_budget_truncates_single_oversized_latest_result() -> None:
    messages = [
        HumanMessage(content="查询详情"),
        AIMessage(
            content="",
            tool_calls=[{"name": "detail", "args": {}, "id": "call-1"}],
        ),
        ToolMessage(content="超大结果" * 1_000, tool_call_id="call-1", name="detail"),
    ]
    guard = ToolResultBudgetMiddleware(token_budget=300)
    captured = []

    async def handler(request):
        captured.extend(request.messages)
        return AIMessage(content="完成")

    request = ModelRequest(model=object(), messages=messages, tools=[])
    asyncio.run(guard.awrap_model_call(request, handler))

    assert TRUNCATION_MARKER.strip() in captured[-1].content
    assert len(captured[-1].content) < len(messages[-1].content)
    assert messages[-1].content.startswith("超大结果")
    assert guard.truncated_tool_results == 1
    assert guard.budget_overflow is False
