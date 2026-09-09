"""无需外部向量库的可追溯售后政策检索。

第三阶段先使用关键词、短语和字符二元组混合召回，确保本地环境也能稳定
测试。返回结果始终携带文档 ID、章节、生效日期和原文片段；生产环境替换
成向量或混合检索时，应继续保留这些引用字段。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "after_sales_policies.json"


@lru_cache(maxsize=1)
def _load_knowledge_base() -> dict[str, Any]:
    """加载经过审核的售后政策知识库。"""
    with _DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def _terms(value: str) -> set[str]:
    """提取中英文词项和中文字符二元组，兼顾短查询召回。"""
    normalized = re.sub(r"\s+", "", value.lower())
    words = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    bigrams = {chinese[index : index + 2] for index in range(max(len(chinese) - 1, 0))}
    return words | bigrams


def get_policy_overview() -> dict[str, Any]:
    """返回知识库版本、文档数量和能力边界。"""
    knowledge_base = _load_knowledge_base()
    return {
        "knowledge_base_version": knowledge_base["knowledge_base_version"],
        "effective_date": knowledge_base["effective_date"],
        "document_count": len(knowledge_base["documents"]),
        "topics": [document["title"] for document in knowledge_base["documents"]],
        "notice": knowledge_base["notice"],
    }


def search_policies(query: str, limit: int = 3) -> dict[str, Any]:
    """检索与问题最相关的售后政策，并返回可供回答引用的证据。"""
    knowledge_base = _load_knowledge_base()
    normalized_query = query.strip().lower()
    query_terms = _terms(normalized_query)
    ranked: list[tuple[float, dict[str, Any]]] = []

    for document in knowledge_base["documents"]:
        title_and_keywords = " ".join(
            [document["title"], document["section"], *document["keywords"]]
        ).lower()
        full_text = f"{title_and_keywords} {document['content'].lower()}"
        score = sum(
            8
            for keyword in document["keywords"]
            if keyword.lower() in normalized_query
        )
        score += len(query_terms & _terms(full_text)) * 0.5
        if normalized_query and normalized_query in full_text:
            score += 8
        if score > 0:
            ranked.append((score, document))

    ranked.sort(key=lambda item: item[0], reverse=True)
    safe_limit = max(1, min(int(limit), 5))
    results = [
        {
            "document_id": document["document_id"],
            "title": document["title"],
            "section": document["section"],
            "excerpt": document["content"],
            "effective_date": knowledge_base["effective_date"],
            "relevance_score": round(score, 2),
            "citation": f"[{document['document_id']}] {document['title']} / {document['section']}",
        }
        for score, document in ranked[:safe_limit]
    ]
    return {
        "query": query,
        "results": results,
        "knowledge_base_version": knowledge_base["knowledge_base_version"],
        "effective_date": knowledge_base["effective_date"],
        "notice": knowledge_base["notice"],
    }
