"""供进程内 Agent 使用的售后政策检索工具。"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from core.knowledge import get_technical_knowledge_store

from .repository import get_policy_overview, search_policies


logger = logging.getLogger(__name__)


@tool
async def search_after_sales_policies(query: str, limit: int = 3) -> str:
    """检索售后政策，返回文档 ID、章节、生效日期和原文证据。

    该工具与 Agent 在同一进程中访问 Milvus，避免每次政策查询都启动
    临时 stdio MCP 子进程。向量库异常时退回本地关键词知识库。
    """

    safe_limit = max(1, min(int(limit), 5))
    vector_results = []
    try:
        store = await get_technical_knowledge_store()
        vector_results = await store.search(
            query,
            document_type="after_sales_policy",
            limit=safe_limit,
        )
    except Exception:
        # 通用政策还有本地审核版本，Milvus 临时不可用时仍可可靠降级。
        logger.exception("Milvus 售后政策检索失败，使用关键词降级")

    overview = get_policy_overview()
    if vector_results:
        result = {
            "query": query,
            "results": vector_results,
            "knowledge_base_version": overview["knowledge_base_version"],
            "effective_date": overview["effective_date"],
            "notice": overview["notice"],
            "retrieval": "milvus_dense_bm25_rrf_rerank",
        }
    else:
        result = search_policies(query=query, limit=safe_limit)
        result["retrieval"] = "keyword_fallback"

    if not result["results"]:
        return json.dumps(
            {
                "status": "not_found",
                "message": "售后知识库中没有找到相关政策，请转人工客服确认。",
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {"status": "success", "data": result},
        ensure_ascii=False,
    )
