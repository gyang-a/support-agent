"""Public evidence from actual RAG tool results, including task-local cache hits."""
import json

from .live_events import emit


def emit_tool_retrieval(call: dict, raw, *, cached: bool) -> None:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(payload, dict):
        return
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return
    results = []
    if payload.get("status") == "success":
        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            # Only document evidence, never raw tool payloads or connection metadata.
            evidence = {key: item[key] for key in (
                "document_id", "chunk_id", "title", "product_model", "citation",
                "rerank_score", "reranker_mode", "page_start", "page_end", "version",
            ) if key in item}
            evidence["section"] = item.get("section_path") or item.get("section", "")
            evidence["excerpt"] = item.get("excerpt") or item.get("content", "")
            results.append(evidence)
    label = "技术知识工具检索" if call["name"] == "search_technical_documents" else "售后政策工具检索"
    emit(
        "retrieval.completed", query=data.get("query") or call.get("args", {}).get("query", ""),
        results=results, stage="tool_result", label=label + (" · 缓存复用" if cached else ""),
        cached=cached, status=payload.get("status", "unknown"), tool_call_id=call.get("id"),
    )
