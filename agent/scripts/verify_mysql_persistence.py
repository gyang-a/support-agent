"""真实 MySQL 持久化冒烟测试；执行后清理测试数据。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from sqlalchemy import delete, select


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from config import get_settings
from core.persistence import (
    ConversationRepository,
    DatabaseManager,
    PreferenceRepository,
    SQLAlchemyMySQLSaver,
    thread_id_for,
)
from core.persistence.models import (
    ConversationMessage,
    ConversationSession,
    GraphCheckpoint,
    GraphCheckpointWrite,
    UserPreference,
)


class SmokeState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_graph(checkpointer):
    builder = StateGraph(SmokeState)

    def respond(state: SmokeState):
        return {"messages": [AIMessage(content=f"ack:{state['messages'][-1].content}")]}

    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


async def run() -> None:
    settings = get_settings()
    user_id = "__mysql_smoke_user__"
    session_id = "__mysql_smoke_session__"
    thread_id = thread_id_for(user_id, session_id)
    config = {"configurable": {"thread_id": thread_id}}

    first_db = DatabaseManager(settings.mysql_url)
    await first_db.initialize()
    assert first_db.available and first_db.sessions is not None
    # 上一次测试若被中断，先清理同名隔离数据，保证本次结果可重复。
    async with first_db.sessions() as session:
        await session.execute(
            delete(GraphCheckpointWrite).where(GraphCheckpointWrite.thread_id == thread_id)
        )
        await session.execute(
            delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id)
        )
        await session.execute(
            delete(ConversationMessage).where(ConversationMessage.thread_id == thread_id)
        )
        await session.execute(
            delete(ConversationSession).where(ConversationSession.thread_id == thread_id)
        )
        await session.execute(
            delete(UserPreference).where(UserPreference.user_id == user_id)
        )
        await session.commit()
    first_graph = build_graph(SQLAlchemyMySQLSaver(first_db.sessions))
    first_result = await first_graph.ainvoke(
        {"messages": [HumanMessage(content="first")]}, config=config
    )
    first_conversations = ConversationRepository(first_db.sessions)
    first_preferences = PreferenceRepository(first_db.sessions)
    await first_conversations.save_turn(user_id, session_id, "first", "ack:first")
    await first_preferences.upsert(user_id, "用户偏好苹果品牌的手机。", "smoke")
    await first_preferences.upsert(user_id, "用户现在更偏好华为手机。", "smoke")
    assert len(first_result["messages"]) == 2
    await first_db.close()

    # 新连接和新图实例模拟应用重启。
    second_db = DatabaseManager(settings.mysql_url)
    await second_db.initialize()
    assert second_db.available and second_db.sessions is not None
    second_graph = build_graph(SQLAlchemyMySQLSaver(second_db.sessions))
    second_result = await second_graph.ainvoke(
        {"messages": [HumanMessage(content="second")]}, config=config
    )
    second_conversations = ConversationRepository(second_db.sessions)
    second_preferences = PreferenceRepository(second_db.sessions)
    await second_conversations.save_turn(user_id, session_id, "second", "ack:second")
    messages = await second_conversations.get_recent(user_id, session_id, limit=10)
    preferences = await second_preferences.list_preferences(user_id)

    checkpoint_rows = 0
    async with second_db.sessions() as session:
        checkpoint_rows = len(
            (
                await session.execute(
                    select(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id)
                )
            ).scalars().all()
        )

    assert [item.content for item in second_result["messages"]] == [
        "first",
        "ack:first",
        "second",
        "ack:second",
    ]
    assert [item["content"] for item in messages] == [
        "first",
        "ack:first",
        "second",
        "ack:second",
    ]
    assert preferences == ["用户现在更偏好华为手机。"]
    assert checkpoint_rows >= 2
    print("checkpoint_restart_recovery=ok")
    print("conversation_messages=4")
    print("preference_upsert=ok")
    print(f"checkpoint_rows={checkpoint_rows}")

    # 清理仅属于本测试的记录。
    async with second_db.sessions() as session:
        await session.execute(
            delete(GraphCheckpointWrite).where(GraphCheckpointWrite.thread_id == thread_id)
        )
        await session.execute(
            delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id)
        )
        await session.execute(
            delete(ConversationMessage).where(ConversationMessage.thread_id == thread_id)
        )
        await session.execute(
            delete(ConversationSession).where(ConversationSession.thread_id == thread_id)
        )
        await session.execute(
            delete(UserPreference).where(UserPreference.user_id == user_id)
        )
        await session.commit()
    await second_db.close()
    print("smoke_data_cleanup=ok")


if __name__ == "__main__":
    asyncio.run(run())
