"""新存储边界的回归测试。"""

import asyncio
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from core.cache import SemanticAnswerCache
from core.persistence.models import GraphCheckpoint, GraphCheckpointWrite
from core.persistence.repositories import preference_key_for, thread_id_for


def test_semantic_cache_only_accepts_stable_public_questions() -> None:
    assert SemanticAnswerCache.is_cacheable("USB-C 和 Lightning 有什么区别？") is True
    assert SemanticAnswerCache.is_cacheable("Type-C 接口相比闪电接口有什么差别？") is True
    assert SemanticAnswerCache.is_cacheable("手机电池鼓包怎么办？") is False
    assert SemanticAnswerCache.is_cacheable("查询我的订单物流") is False
    assert SemanticAnswerCache.is_cacheable("按我的预算推荐手机") is False
    assert SemanticAnswerCache.is_cacheable("iPhone 现在多少钱，有库存吗") is False


def test_semantic_cache_requires_matching_intent_and_entities() -> None:
    first = SemanticAnswerCache.classify("USB-C 和 Lightning 有什么区别？")
    paraphrase = SemanticAnswerCache.classify("Type-C 相比闪电接口有什么差别？")
    definition = SemanticAnswerCache.classify("USB-C 是什么？")

    assert first == paraphrase
    assert first != definition


def test_thread_id_is_stable_isolated_and_does_not_expose_user_id() -> None:
    first = thread_id_for("user_1001", "session-a")
    assert first == thread_id_for("user_1001", "session-a")
    assert first != thread_id_for("user_1001", "session-b")
    assert "user_1001" not in first
    assert len(first) == 64


def test_structured_preference_slots_replace_same_kind() -> None:
    assert preference_key_for("用户偏好苹果品牌的手机。") == "phone.brand"
    assert preference_key_for("用户现在更偏好华为手机。") == "phone.brand"
    assert preference_key_for("用户预算不超过5000元。") == "digital.budget"


def test_checkpoint_tables_compile_for_mysql() -> None:
    checkpoint_sql = str(CreateTable(GraphCheckpoint.__table__).compile(dialect=mysql.dialect()))
    writes_sql = str(CreateTable(GraphCheckpointWrite.__table__).compile(dialect=mysql.dialect()))
    assert "graph_checkpoints" in checkpoint_sql
    assert "graph_checkpoint_writes" in writes_sql
    assert "LONGBLOB" in checkpoint_sql


def test_cache_hit_can_be_written_into_fresh_checkpoint() -> None:
    class State(TypedDict):
        messages: Annotated[list, add_messages]

    builder = StateGraph(State)
    builder.add_node("noop", lambda _state: {})
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "cache-hit-thread"}}

    async def scenario():
        await graph.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage(content="什么是 USB-C？"),
                    AIMessage(content="USB-C 是一种接口。"),
                ]
            },
        )
        return await graph.aget_state(config)

    snapshot = asyncio.run(scenario())
    assert [message.content for message in snapshot.values["messages"]] == [
        "什么是 USB-C？",
        "USB-C 是一种接口。",
    ]
