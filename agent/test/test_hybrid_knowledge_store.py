"""Milvus 混合知识库的集合契约与检索测试。"""

from __future__ import annotations

import asyncio

from pymilvus import MilvusClient

from core.knowledge import TechnicalKnowledgeStore
from core.knowledge.ingestion import KnowledgeChunk
from core.knowledge.tools import search_technical_documents
from core.knowledge.reranking import BgeCrossEncoderReranker, MetadataAwareReranker


class _Embeddings:
    async def aembed_query(self, _query: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _SchemaClient:
    def __init__(self) -> None:
        self.schema = None
        self.indexes = None

    def has_collection(self, _name: str) -> bool:
        return False

    @staticmethod
    def create_schema(**kwargs):
        return MilvusClient.create_schema(**kwargs)

    @staticmethod
    def prepare_index_params():
        return MilvusClient.prepare_index_params()

    def create_collection(self, **kwargs) -> None:
        self.schema = kwargs["schema"]
        self.indexes = kwargs["index_params"]


class _SearchClient:
    def __init__(self) -> None:
        self.requests = []
        self.ranker = None

    def hybrid_search(self, *, reqs, ranker, **_kwargs):
        self.requests = reqs
        self.ranker = ranker
        return [
            [
                {
                    "distance": 0.03,
                    "entity": {
                        "chunk_id": "doc_1__1",
                        "document_id": "doc_1",
                        "document_type": "product_manual",
                        "title": "iPhone 16 使用说明",
                        "section": "有线充电",
                        "section_path": "电池与充电 > 有线充电",
                        "source_path": "iphone_16.pdf",
                        "version": "v1",
                        "product_model": "iphone_16",
                        "category": "smartphone",
                        "chunk_type": "text",
                        "content_hash": "hash1",
                        "effective_date": "",
                        "page_start": 18,
                        "page_end": 19,
                        "table_row_start": -1,
                        "table_row_end": -1,
                        "content": "iPhone 16 使用 USB-C 适配器进行有线充电。",
                    },
                }
            ]
        ]


class _UnchangedClient:
    def query(self, **_kwargs):
        return [
            {
                "chunk_id": "doc_1__00001__abcdef",
                "content_hash": "abcdef",
                "document_type": "product_manual",
                "product_model": "iphone_16",
                "category": "smartphone",
                "version": "v1",
                "status": "published",
            }
        ]


class _UnexpectedEmbeddings:
    async def aembed_documents(self, _texts):
        raise AssertionError("未变化文档不应重复向量化")


def _store() -> TechnicalKnowledgeStore:
    return TechnicalKnowledgeStore(
        host="localhost",
        port=19530,
        api_key=None,
        embedding_api_key=None,
        embedding_model="",
        embedding_base_url="",
        embedding_dimension=3,
    )


def test_collection_schema_contains_dense_bm25_and_filter_metadata() -> None:
    store = _store()
    client = _SchemaClient()
    store.client = client

    store._ensure_collection()

    schema = client.schema.to_dict()
    fields = {field["name"]: field for field in schema["fields"]}
    assert fields["id"]["is_primary"] is True
    assert fields["id"]["auto_id"] is True
    assert fields["chunk_id"].get("is_primary", False) is False
    assert fields["retrieval_text"]["params"]["enable_analyzer"] is True
    assert "embedding" in fields
    assert "sparse_embedding" in fields
    assert "product_model" in fields
    assert "category" in fields
    assert schema["functions"][0]["name"] == "knowledge_bm25"


def test_search_uses_dense_and_bm25_then_returns_page_citation() -> None:
    store = _store()
    client = _SearchClient()
    store.client = client
    store.embeddings = _Embeddings()
    store.available = True

    results = asyncio.run(
        store.search(
            "iPhone 16 怎么使用 USB-C 充电",
            document_type="product_manual",
            product_model="iphone_16",
            category="smartphone",
            limit=3,
        )
    )

    assert [request.anns_field for request in client.requests] == [
        "embedding",
        "sparse_embedding",
    ]
    assert all('product_model == "iphone_16"' in request.expr for request in client.requests)
    assert results[0]["document_id"] == "doc_1"
    assert "第 18-19 页" in results[0]["citation"]
    assert results[0]["relevance_score"] > 0


def test_unchanged_document_skips_embedding_and_reinsertion() -> None:
    store = _store()
    store.client = _UnchangedClient()
    store.embeddings = _UnexpectedEmbeddings()
    store.available = True
    chunk = KnowledgeChunk(
        chunk_id="doc_1__00001__abcdef",
        document_id="doc_1",
        document_type="product_manual",
        title="说明书",
        section="充电",
        section_path="充电",
        chunk_type="text",
        content="正文",
        retrieval_text="说明书\n充电\n正文",
        source_path="manual.md",
        version="v1",
        product_model="iphone_16",
        category="smartphone",
        content_hash="abcdef",
    )

    result = asyncio.run(store.ingest_chunks([chunk]))

    assert result == {
        "document_id": "doc_1",
        "chunk_count": 1,
        "insert_count": 0,
        "unchanged": True,
    }


def test_excerpt_is_truncated_around_query_evidence() -> None:
    content = "前置内容" * 120 + "红灯闪烁 5 次表示激光雷达受阻。" + "后置内容" * 120

    excerpt = TechnicalKnowledgeStore._excerpt(content, "红灯闪烁五次是什么意思")

    assert len(excerpt) <= 362
    assert "红灯闪烁 5 次" in excerpt
    assert excerpt.startswith("…")
    assert excerpt.endswith("…")


def test_agent_tool_only_exposes_natural_language_query() -> None:
    """数据库过滤字段不能泄漏到 Agent 可见的工具参数中。"""

    properties = search_technical_documents.args_schema.model_json_schema()["properties"]

    assert set(properties) == {"query", "limit"}


def test_natural_language_search_recalls_all_technical_document_types() -> None:
    """自然语言入口使用服务端范围约束，不要求 Agent 选择文档类型。"""

    store = _store()
    client = _SearchClient()
    store.client = client
    store.embeddings = _Embeddings()
    store.available = True

    asyncio.run(store.search_natural_language("星云 AirBook 14 Pro 支持什么接口"))

    for request in client.requests:
        assert "document_type in" in request.expr
        assert '"product_manual"' in request.expr
        assert '"diagnostic_guide"' in request.expr
        assert '"faq"' in request.expr
        assert "product_model" not in request.expr


def test_reranker_resolves_longest_model_without_breaking_comparisons() -> None:
    """Pro 查询排除标准版，明确对比时则保留两个型号。"""

    candidates = [
        {
            "document_id": "pro",
            "content_hash": "pro-hash",
            "product_model": "airbook_14_pro",
            "content": "USB-C 1、USB-C 2、USB-A、HDMI 和 3.5mm 音频口。",
        },
        {
            "document_id": "standard",
            "content_hash": "standard-hash",
            "product_model": "airbook_14",
            "content": "USB-C、USB-A、HDMI 和 3.5mm 音频口。",
        },
    ]
    reranker = MetadataAwareReranker()

    single = reranker.rerank("AirBook 14 Pro 支持什么接口", candidates, limit=5)
    comparison = reranker.rerank(
        "AirBook 14 和 AirBook 14 Pro 的接口有什么区别", candidates, limit=5
    )

    assert {item["product_model"] for item in single} == {"airbook_14_pro"}
    assert {item["product_model"] for item in comparison} == {
        "airbook_14",
        "airbook_14_pro",
    }


def test_bge_failure_uses_explicitly_labeled_fallback(monkeypatch) -> None:
    """模型故障可以降级，但结果必须明确暴露而不能伪装成 BGE 分数。"""

    reranker = BgeCrossEncoderReranker(allow_heuristic_fallback=True)

    def fail(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(reranker, "_model_rerank", fail)
    results = reranker.rerank(
        "AirBook 14 Pro 接口",
        [
            {
                "document_id": "pro",
                "content_hash": "hash",
                "product_model": "airbook_14_pro",
                "content": "USB-C 和 HDMI 接口",
            }
        ],
        limit=1,
    )

    assert results[0]["reranker_mode"] == "heuristic_fallback"
    assert results[0]["reranker_error"] == "model_unavailable"
    assert results[0]["reranker_model"] == "BAAI/bge-reranker-v2-m3"


def test_evaluation_can_keep_similar_models_for_real_hard_negative_scoring() -> None:
    candidates = [
        {
            "document_id": "standard",
            "content_hash": "standard-hash",
            "product_model": "airbook_14",
            "content": "标准版接口说明",
        },
        {
            "document_id": "pro",
            "content_hash": "pro-hash",
            "product_model": "airbook_14_pro",
            "content": "Pro 版接口说明",
        },
    ]

    results = MetadataAwareReranker(enforce_model_scope=False).rerank(
        "AirBook 14 Pro 支持什么接口", candidates, limit=5
    )

    assert {item["product_model"] for item in results} == {
        "airbook_14",
        "airbook_14_pro",
    }
