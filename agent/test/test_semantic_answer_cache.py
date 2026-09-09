"""Milvus 语义答案缓存的安全门和召回测试。"""

import asyncio

from core.cache import SemanticAnswerCache


class _Embeddings:
    async def aembed_query(self, _query):
        return [0.1, 0.2, 0.3]


class _Client:
    def __init__(self, score=0.98):
        self.score = score
        self.search_filter = ""
        self.inserted = []

    def search(self, **kwargs):
        self.search_filter = kwargs["filter"]
        return [
            [
                {
                    "distance": self.score,
                    "entity": {
                        "query": "USB-C 和 Lightning 有什么区别？",
                        "answer": "这是可复用的公开答案。",
                    },
                }
            ]
        ]

    def insert(self, **kwargs):
        self.inserted.extend(kwargs["data"])


def _cache(score=0.98):
    cache = SemanticAnswerCache(
        host="localhost",
        port=19530,
        api_key=None,
        embedding_api_key=None,
        embedding_model="",
        embedding_base_url="",
        embedding_dimension=3,
        similarity_threshold=0.96,
        knowledge_version="test-v1",
    )
    cache.embeddings = _Embeddings()
    cache.client = _Client(score)
    cache.available = True
    return cache


def test_semantic_hit_requires_metadata_and_threshold() -> None:
    cache = _cache()

    answer = asyncio.run(cache.get("Type-C 相比闪电接口有什么差别？"))

    assert answer == "这是可复用的公开答案。"
    assert 'intent == "comparison"' in cache.client.search_filter
    assert 'entity_signature == "lightning|usb-c"' in cache.client.search_filter
    assert 'knowledge_version == "test-v1"' in cache.client.search_filter


def test_semantic_hit_below_threshold_is_rejected() -> None:
    cache = _cache(score=0.95)

    assert asyncio.run(cache.get("Type-C 相比闪电接口有什么差别？")) is None


def test_semantic_cache_writes_only_safe_query_metadata() -> None:
    cache = _cache()

    asyncio.run(
        cache.set("USB-C 和 Lightning 有什么区别？", "这是可复用的公开答案。")
    )
    asyncio.run(cache.set("查询我的订单物流", "不应写入"))

    assert len(cache.client.inserted) == 1
    record = cache.client.inserted[0]
    assert record["intent"] == "comparison"
    assert record["entity_signature"] == "lightning|usb-c"
    assert record["knowledge_version"] == "test-v1"
    assert record["expires_at"] > record["created_at"]
