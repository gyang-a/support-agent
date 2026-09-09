"""供进程内 Agent 使用的技术文档检索工具。"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from .milvus_store import get_technical_knowledge_store


logger = logging.getLogger(__name__)


@tool
async def search_technical_documents(
    query: str,
    limit: int = 3,
) -> str:
    """用独立、完整的自然语言问题检索技术文档并返回可引用证据。

    query 中直接包含用户提到的品牌、型号、商品和问题，不需要也不能猜测
    数据库内部的型号、类目或文档类型编码。
    """

    try:
        store = await get_technical_knowledge_store()
        if not store.available:
            raise RuntimeError("Knowledge store unavailable")
        results = await store.search_natural_language(
            query,
            limit=max(1, min(limit, 10)),
        )
    except Exception:
        # 检索基础设施故障不应击穿整个 Agent 图；返回明确状态，让模型停止
        # 猜测并引导用户稍后重试或转人工，同时保留完整异常供服务端排查。
        logger.exception("技术知识库检索失败")
        return json.dumps(
            {
                "status": "knowledge_unavailable",
                "message": "技术知识库暂时不可用，不能基于说明书可靠作答，请稍后重试或转人工。",
            },
            ensure_ascii=False,
        )
    if not results:
        return json.dumps(
            {
                "status": "not_found",
                "message": "已发布知识文档中没有找到可靠证据，请补充完整型号或转人工确认。",
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "success",
            "data": {
                "query": query,
                "results": results,
                "retrieval": "milvus_dense_bm25_rrf_rerank",
                "resolved_product_models": sorted(
                    {str(item.get("product_model", "")) for item in results}
                ),
            },
        },
        ensure_ascii=False,
    )
