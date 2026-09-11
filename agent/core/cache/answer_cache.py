"""Milvus 语义答案缓存；仅服务公开、稳定、无上下文的技术 FAQ。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from langchain_openai import OpenAIEmbeddings
from core.embedding_resilience import create_embeddings


logger = logging.getLogger(__name__)

_PRIVATE_DYNAMIC_OR_RISKY = re.compile(
    r"(?:我的|我喜欢|记住|偏好|订单|物流|工单|会员|账户|账号|推荐|预算|"
    r"价格|多少钱|库存|有货|优惠|下单|购买|促销|退款|保修|退货|售后|"
    r"故障|坏了|无法开机|开不了机|进水|进液|鼓包|发烫|过热|冒烟|"
    r"这个|那个|刚才|上面|之前说的|继续)"
)
_PUBLIC_KNOWLEDGE = re.compile(
    r"(?:是什么|什么意思|含义|区别|差别|相比|对比|为什么|原理|怎么|如何)"
)
_INTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("comparison", re.compile(r"(?:区别|差别|相比|对比|\bvs\b)", re.IGNORECASE)),
    ("definition", re.compile(r"(?:是什么|什么意思|含义|介绍)")),
    ("explanation", re.compile(r"(?:为什么|原理)")),
    ("how_to", re.compile(r"(?:怎么|如何)")),
)
_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "usb-c": ("usb-c", "usb c", "type-c", "type c", "typec"),
    "lightning": ("lightning", "闪电接口"),
    "thunderbolt": ("thunderbolt", "雷电接口", "雷电3", "雷电4", "雷雳"),
    "oled": ("oled",),
    "lcd": ("lcd",),
    "mini-led": ("mini-led", "mini led"),
    "ip68": ("ip68",),
    "ram": ("ram", "运行内存"),
    "storage": ("rom", "存储空间", "机身存储"),
    "wifi": ("wi-fi", "wifi", "无线网络"),
    "bluetooth": ("bluetooth", "蓝牙"),
    "nfc": ("nfc",),
    "esim": ("esim", "e-sim"),
    "fast-charging": ("快充", "快速充电"),
    "wireless-charging": ("无线充电",),
    "refresh-rate": ("刷新率", "hz"),
    "resolution": ("分辨率",),
}


@dataclass(frozen=True)
class CacheQueryProfile:
    intent: str
    entity_signature: str


class SemanticAnswerCache:
    """通过 Milvus 召回可安全复用的公开 FAQ 答案。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        api_key: str | None,
        embedding_api_key: str | None,
        embedding_model: str,
        embedding_base_url: str,
        embedding_dimension: int,
        similarity_threshold: float = 0.96,
        ttl_seconds: int = 604800,
        knowledge_version: str = "digital-support-v1",
        embedding_namespace: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key
        self.dimension = embedding_dimension
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.knowledge_version = knowledge_version
        self.collection = f"digital_semantic_answer_cache_v1_{embedding_dimension}"
        if embedding_namespace:
            self.collection += "_" + embedding_namespace
        self.client: Any = None
        self.available = False
        self.embeddings: OpenAIEmbeddings | None = None
        if embedding_api_key and embedding_model and embedding_base_url:
            self.embeddings = create_embeddings(
                api_key=embedding_api_key,
                model=embedding_model,
                base_url=embedding_base_url,
                check_embedding_ctx_length=False,
                model_kwargs={"encoding_format": "float"},
            )

    async def initialize(self) -> None:
        if self.embeddings is None:
            logger.info("Semantic answer cache disabled: embedding is not configured")
            return
        try:
            from pymilvus import MilvusClient

            kwargs: dict[str, Any] = {"uri": f"http://{self.host}:{self.port}"}
            if self.api_key:
                kwargs["token"] = self.api_key
            probe = await self.embeddings.aembed_query("semantic answer cache")
            if len(probe) != self.dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.dimension}, got {len(probe)}"
                )
            from config import get_settings
            kwargs["timeout"] = get_settings().embedding_timeout
            self.client = await asyncio.to_thread(MilvusClient, **kwargs)
            await asyncio.to_thread(self._ensure_collection)
            self.available = True
            logger.info("Milvus semantic answer cache ready")
        except Exception as exc:
            logger.warning("Semantic answer cache unavailable: %s", exc)
            self.available = False

    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        if self.client.has_collection(self.collection):
            return
        schema = self.client.create_schema()
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        for name, length in (
            ("intent", 32),
            ("entity_signature", 512),
            ("knowledge_version", 64),
            ("query", 2048),
        ):
            schema.add_field(name, DataType.VARCHAR, max_length=length)
        schema.add_field("answer", DataType.VARCHAR, max_length=16384)
        schema.add_field("created_at", DataType.INT64)
        schema.add_field("expires_at", DataType.INT64)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.dimension)
        indexes = self.client.prepare_index_params()
        indexes.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128},
        )
        self.client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=indexes,
        )

    @classmethod
    def classify(cls, query: str) -> CacheQueryProfile | None:
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        if (
            not normalized
            or len(normalized) > 300
            or _PRIVATE_DYNAMIC_OR_RISKY.search(normalized)
            or not _PUBLIC_KNOWLEDGE.search(normalized)
        ):
            return None

        intent = next(
            (name for name, pattern in _INTENT_RULES if pattern.search(normalized)),
            "",
        )
        entities = sorted(
            canonical
            for canonical, aliases in _ENTITY_ALIASES.items()
            if any(alias in normalized for alias in aliases)
        )
        if not intent or not entities:
            return None
        return CacheQueryProfile(intent=intent, entity_signature="|".join(entities))

    @classmethod
    def is_cacheable(cls, query: str) -> bool:
        return cls.classify(query) is not None

    async def get(self, query: str) -> str | None:
        profile = self.classify(query)
        if not self.available or self.embeddings is None or profile is None:
            return None
        try:
            vector = await self.embeddings.aembed_query(query)
            now = int(time.time())
            expression = (
                f'intent == "{profile.intent}" and '
                f'entity_signature == "{profile.entity_signature}" and '
                f'knowledge_version == "{self.knowledge_version}" and '
                f"expires_at > {now}"
            )
            results = await asyncio.to_thread(
                self.client.search,
                collection_name=self.collection,
                data=[vector],
                filter=expression,
                limit=3,
                output_fields=["answer", "query"],
            )
            hits = [hit for group in results for hit in group]
            if not hits:
                return None
            best = max(hits, key=lambda hit: float(hit.get("distance", 0)))
            score = float(best.get("distance", 0))
            if score < self.similarity_threshold:
                return None
            answer = best.get("entity", {}).get("answer", "")
            return answer if isinstance(answer, str) and answer.strip() else None
        except Exception as exc:
            logger.warning("Semantic answer cache read failed: %s", exc)
            return None

    async def set(self, query: str, answer: str) -> None:
        profile = self.classify(query)
        if (
            not self.available
            or self.embeddings is None
            or profile is None
            or not answer.strip()
            or len(answer) > 16384
        ):
            return
        try:
            vector = await self.embeddings.aembed_query(query)
            now = int(time.time())
            await asyncio.to_thread(
                self.client.insert,
                collection_name=self.collection,
                data=[
                    {
                        "intent": profile.intent,
                        "entity_signature": profile.entity_signature,
                        "knowledge_version": self.knowledge_version,
                        "query": query[:2048],
                        "answer": answer,
                        "created_at": now,
                        "expires_at": now + self.ttl_seconds,
                        "embedding": vector,
                    }
                ],
            )
        except Exception as exc:
            logger.warning("Semantic answer cache write failed: %s", exc)

    async def close(self) -> None:
        if self.client is not None:
            await asyncio.to_thread(self.client.close)
        self.available = False
