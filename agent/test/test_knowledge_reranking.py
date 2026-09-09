"""混合召回候选的二阶段重排测试。"""

from core.knowledge.reranking import MetadataAwareReranker


def test_reranker_promotes_exact_terms_and_removes_duplicate_chunks() -> None:
    candidates = [
        {
            "document_id": "doc_other",
            "content_hash": "other",
            "title": "通用充电说明",
            "section_path": "充电",
            "content": "设备支持快速充电。",
        },
        {
            "document_id": "doc_iphone",
            "content_hash": "exact",
            "title": "iPhone 16 使用说明",
            "section_path": "电池与充电 > 有线充电",
            "content": "iPhone 16 使用 USB-C 适配器时支持 20 W 有线充电。",
        },
        {
            "document_id": "doc_iphone",
            "content_hash": "exact",
            "title": "iPhone 16 使用说明",
            "section_path": "电池与充电 > 有线充电",
            "content": "iPhone 16 使用 USB-C 适配器时支持 20 W 有线充电。",
        },
    ]

    results = MetadataAwareReranker().rerank(
        "iPhone 16 USB-C 有线充电功率",
        candidates,
        limit=3,
    )

    assert results[0]["document_id"] == "doc_iphone"
    assert len(results) == 2
    assert results[0]["rerank_score"] > results[1]["rerank_score"]
