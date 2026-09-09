"""数码商城多 Agent CLI；会话状态由 MySQL Checkpoint 持久化。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from config import get_settings
from core.memory.preference_extractor import (
    PreferenceExtractor,
    is_explicit_preference_message,
)
from core.persistence import (
    ConversationRepository,
    DatabaseManager,
    PreferenceRepository,
    SQLAlchemyMySQLSaver,
    thread_id_for,
)
from core.workflow.graph_manager import AgentGraphManager


if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Digital Commerce Multi-Agent System")
    parser.add_argument("--query", "-q")
    parser.add_argument("--user", "-u", default="user_1001")
    parser.add_argument("--session", "-s")
    parser.add_argument("--debug", "-d", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    setup_logging("DEBUG" if args.debug else settings.log_level)

    user_id = args.user
    session_id = args.session or f"session_{uuid.uuid4().hex[:8]}"
    database = DatabaseManager(settings.mysql_url, echo=settings.mysql_echo)
    await database.initialize()
    if database.available and database.sessions is not None:
        checkpointer = SQLAlchemyMySQLSaver(database.sessions)
        conversations = ConversationRepository(database.sessions)
        preferences = PreferenceRepository(database.sessions)
    else:
        checkpointer = InMemorySaver()
        conversations = None
        preferences = None

    manager = AgentGraphManager()
    graph = manager.build_graph(checkpointer=checkpointer)
    config = {
        "configurable": {
            "thread_id": thread_id_for(user_id, session_id),
            "user_id": user_id,
        }
    }

    async def invoke(query: str) -> str:
        preference_items = (
            await preferences.list_preferences(user_id) if preferences else []
        )
        context = (
            "【长期用户偏好】:\n" + "\n".join(f"- {item}" for item in preference_items)
            if preference_items
            else ""
        )
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=query)],
                "user_id": user_id,
                "session_id": session_id,
                "memory_context": context,
                "next_agent": "",
                "metadata": {},
            },
            config=config,
        )
        response = result["messages"][-1].content
        if conversations:
            await conversations.save_turn(user_id, session_id, query, response)
        if preferences and is_explicit_preference_message(query):
            items = await PreferenceExtractor(manager.orchestrator.llm).extract(
                f"user: {query}", existing=preference_items
            )
            for item in items:
                await preferences.upsert(user_id, item, source_text=query)
        return response

    try:
        if args.query:
            print(f"\n👤 User: {args.query}")
            print(f"\n🤖 AI: {await invoke(args.query)}\n")
        else:
            print(f"User={user_id} Session={session_id}; 输入 quit 退出")
            while True:
                try:
                    query = input("\n👤 You: ").strip()
                except EOFError:
                    break
                if query.lower() in {"quit", "exit", "q"}:
                    break
                if query:
                    print(f"\n🤖 AI: {await invoke(query)}")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
