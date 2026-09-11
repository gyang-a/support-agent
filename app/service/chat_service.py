"""FastAPI 与 LangGraph、MySQL Checkpoint、Milvus 语义缓存的集成层。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from config import get_settings
from core.cache import SemanticAnswerCache
from core.memory.preference_extractor import (
    PreferenceExtractor,
    is_explicit_preference_message,
)
from core.memory.preference_worker import PreferenceExtractionWorker
from core.persistence import (
    ConversationRepository,
    DatabaseManager,
    PreferenceRepository,
    SQLAlchemyMySQLSaver,
    thread_id_for,
)
from core.workflow.graph_manager import AgentGraphManager

graph = None
database: DatabaseManager | None = None
conversations: ConversationRepository | None = None
preferences: PreferenceRepository | None = None
answer_cache: SemanticAnswerCache | None = None
orchestrator_llm = None
preference_worker_task: asyncio.Task | None = None
preference_worker: PreferenceExtractionWorker | None = None


async def _run_preference_worker(worker: PreferenceExtractionWorker, interval: int) -> None:
    """启动即处理一次，之后按配置的两小时间隔运行。"""
    while True:
        try:
            await worker.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Preference worker iteration failed")
        await asyncio.sleep(interval)


async def init_agent_system() -> None:
    global graph, database, conversations, preferences, answer_cache, orchestrator_llm
    global preference_worker, preference_worker_task
    if graph is not None:
        return
    settings = get_settings()
    database = DatabaseManager(settings.mysql_url, echo=settings.mysql_echo)
    await database.initialize()
    if database.available and database.sessions is not None:
        checkpointer = SQLAlchemyMySQLSaver(database.sessions)
        conversations = ConversationRepository(database.sessions)
        preferences = PreferenceRepository(database.sessions)
        print("[OK] MySQL Checkpoint、会话和用户偏好已启用")
    else:
        checkpointer = InMemorySaver()
        conversations = None
        preferences = None
        print("[WARN] MySQL 不可用，临时降级为进程内 Checkpoint")

    graph_manager = AgentGraphManager()
    graph = graph_manager.build_graph(checkpointer=checkpointer)
    orchestrator_llm = graph_manager.orchestrator.llm
    if preferences is not None:
        preference_worker = PreferenceExtractionWorker(
            preferences,
            PreferenceExtractor(orchestrator_llm),
            user_limit=settings.preference_extraction_user_limit,
            message_limit=settings.preference_extraction_message_limit,
        )
        preference_worker_task = asyncio.create_task(
            _run_preference_worker(
                preference_worker, settings.preference_extraction_interval_seconds
            ),
            name="preference-extraction-worker",
        )
    answer_cache = SemanticAnswerCache(
        host=settings.milvus_host,
        port=settings.milvus_port,
        api_key=settings.milvus_api_key,
        embedding_api_key=settings.embedding_api_key,
        embedding_model=settings.embedding_model,
        embedding_base_url=settings.embedding_base_url,
        embedding_dimension=settings.embedding_dimension,
        embedding_namespace=settings.embedding_namespace,
        similarity_threshold=settings.semantic_cache_similarity_threshold,
        ttl_seconds=settings.semantic_cache_ttl_seconds,
        knowledge_version=settings.semantic_cache_knowledge_version,
    )
    await answer_cache.initialize()
    print("[OK] Agent 系统初始化完成")


async def close_agent_system() -> None:
    global graph, database, conversations, preferences, answer_cache, orchestrator_llm
    global preference_worker, preference_worker_task
    if preference_worker_task is not None:
        preference_worker_task.cancel()
        try:
            await preference_worker_task
        except asyncio.CancelledError:
            pass
        preference_worker_task = None
    preference_worker = None
    if answer_cache is not None:
        await answer_cache.close()
    if database is not None:
        await database.close()
    graph = None
    database = None
    conversations = None
    preferences = None
    answer_cache = None
    orchestrator_llm = None


async def _preference_context(user_id: str) -> str:
    if preferences is None:
        return ""
    items = await preferences.list_preferences(user_id)
    if not items:
        return ""
    return "【长期用户偏好】:\n" + "\n".join(f"- {item}" for item in items)


async def _save_explicit_preferences(user_id: str, query: str) -> None:
    if (
        preference_worker is None
        or not is_explicit_preference_message(query)
    ):
        return
    # 显式“记住”仍即时生效；它与后台任务走完全相同的完整快照流程。
    await preference_worker.process_user(user_id)


async def _record_turn(
    user_id: str,
    session_id: str,
    query: str,
    response: str,
    metadata: dict | None = None,
) -> None:
    if conversations is None:
        return
    metadata = metadata or {}
    execution = metadata.get("execution", {})
    await conversations.save_turn(
        user_id,
        session_id,
        query,
        response,
        agent_name=execution.get("agent_name"),
        trace_id=metadata.get("trace_id"),
    )


async def _yield_sse(response_text: str, *, cached: bool = False, task_results=None) -> AsyncIterator[str]:
    for index in range(0, len(response_text), 5):
        chunk = response_text[index : index + 5]
        yield f"data: {json.dumps({'content': chunk})}\n\n"
        await asyncio.sleep(0.02)
    # 只返回用户可见任务摘要，不泄露内部工具数据、身份或异常详情。
    public_results = [
        {key: result[key] for key in ("task_id", "agent_name", "status", "response")}
        for result in (task_results or [])
    ]
    final_event = {"done": True, "cached": cached, "task_results": public_results}
    yield f"data: {json.dumps(final_event)}\n\n"


async def stream_chat(query: str, user_id: str, session_id: str, *, account_id: str | None = None):
    """以 MySQL 保存上下文，以 Milvus 复用严格筛选后的公开 FAQ。"""
    if graph is None:
        raise RuntimeError("Agent system is not initialized")
    # Account identity owns memory; user_id remains the commerce tool identity.
    memory_user_id = account_id or user_id
    config = {
        "configurable": {
            "thread_id": thread_id_for(memory_user_id, session_id),
            "user_id": user_id,
        }
    }

    cache_eligible = bool(answer_cache and answer_cache.is_cacheable(query))
    cached_response = await answer_cache.get(query) if cache_eligible else None
    if cached_response:
        await graph.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage(content=query),
                    AIMessage(content=cached_response, name="semantic_cache"),
                ],
                "user_id": user_id,
                "session_id": session_id,
                "memory_context": "",
                "next_agent": "",
                "metadata": {"cache": {"hit": True}},
                "tasks": [], "task_results": None, "planning_error": "",
            },
        )
        await _record_turn(memory_user_id, session_id, query, cached_response)
        async for event in _yield_sse(cached_response, cached=True):
            yield event
        return

    state = {
        "messages": [HumanMessage(content=query)],
        "user_id": user_id,
        "session_id": session_id,
        # 可共享 FAQ 必须在无用户画像的条件下生成，避免缓存个性化内容。
        "memory_context": "" if cache_eligible else await _preference_context(memory_user_id),
        "next_agent": "",
        "metadata": {},
    }
    result = await graph.ainvoke(state, config=config)
    response_text = result["messages"][-1].content
    metadata = result.get("metadata", {})
    await _record_turn(memory_user_id, session_id, query, response_text, metadata)
    await _save_explicit_preferences(memory_user_id, query)
    if (
        cache_eligible
        and len(result.get("tasks", [])) <= 1
        and all(task["status"] == "completed" for task in result.get("task_results", []))
        and answer_cache is not None
        and metadata.get("quality", {}).get("passed") is True
    ):
        await answer_cache.set(query, response_text)

    async for event in _yield_sse(response_text, task_results=result.get("task_results", [])):
        yield event
