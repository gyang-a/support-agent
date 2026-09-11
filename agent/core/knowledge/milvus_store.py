"""Milvus 技术文档混合检索知识库。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from langchain_openai import OpenAIEmbeddings
from time import monotonic
from core.embedding_resilience import create_embeddings
from config import get_settings

from .ingestion import ParsedDocument, StructuredDocumentChunker
from .ingestion.chunking import KnowledgeChunk
from .reranking import create_reranker, CandidateReranker, MetadataAwareReranker


logger = logging.getLogger(__name__)
_POLICY_FILE = Path(__file__).resolve().parents[2] / "data" / "after_sales_policies.json"


def _quoted(value: str) -> str:
    """使用 JSON 字符串语法安全构造 Milvus 标量过滤值。"""

    return json.dumps(value.strip(), ensure_ascii=False)


class TechnicalKnowledgeStore:
    """保存文档切片并提供稠密向量 + BM25 混合检索。"""

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
        reranker: CandidateReranker | None = None,
        embedding_namespace: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key
        self.dimension = embedding_dimension
        # v3 使用 Milvus 推荐的 INT64 AutoID 作为物理主键。chunk_id 仍然是
        # 应用层稳定标识，但不再参与 hybrid_search 的结果融合，规避部分
        # Milvus 3.x 版本对 VARCHAR 搜索结果 ID 报 unsupported ID type。
        self.collection = f"digital_technical_knowledge_v3_{embedding_dimension}"
        if embedding_namespace:
            self.collection += "_" + embedding_namespace
        self.client: Any = None
        self.embeddings: OpenAIEmbeddings | None = None
        self.reranker = reranker or MetadataAwareReranker()
        self.chunker = StructuredDocumentChunker()
        self.available = False
        self.retry_after = 0.0
        if embedding_api_key and embedding_model and embedding_base_url:
            self.embeddings = create_embeddings(
                api_key=embedding_api_key,
                model=embedding_model,
                base_url=embedding_base_url,
                check_embedding_ctx_length=False,
                model_kwargs={"encoding_format": "float"},
            )

    async def initialize(self) -> None:
        if monotonic() < self.retry_after:
            return
        if self.embeddings is None:
            return
        try:
            from pymilvus import MilvusClient

            kwargs: dict[str, Any] = {"uri": f"http://{self.host}:{self.port}"}
            if self.api_key:
                kwargs["token"] = self.api_key
            probe = await self.embeddings.aembed_query("technical knowledge")
            if len(probe) != self.dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.dimension}, got {len(probe)}"
                )
            kwargs["timeout"] = get_settings().embedding_timeout
            if self.client is not None:
                await asyncio.to_thread(self.client.close)
            self.client = await asyncio.to_thread(MilvusClient, **kwargs)
            await asyncio.to_thread(self._ensure_collection)
            self.available = True
            await self.seed_after_sales_policies()
            self.retry_after = 0.0
            logger.info("Milvus hybrid technical knowledge store ready")
        except Exception as exc:
            logger.warning("Milvus technical knowledge unavailable: %s", exc)
            self.available = False
            self.retry_after = monotonic() + get_settings().embedding_circuit_cooldown

    def _ensure_collection(self) -> None:
        """创建带中文 BM25 内置函数的混合检索集合。"""

        from pymilvus import DataType, Function, FunctionType

        if self.client.has_collection(self.collection):
            return
        schema = self.client.create_schema(enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=256)
        for name, length in (
            ("document_id", 128),
            ("document_type", 64),
            ("title", 512),
            ("section", 512),
            ("section_path", 2048),
            ("source_path", 2048),
            ("version", 64),
            ("status", 32),
            ("product_model", 128),
            ("category", 64),
            ("chunk_type", 32),
            ("content_hash", 64),
            ("parent_section_id", 64),
            ("effective_date", 32),
        ):
            schema.add_field(name, DataType.VARCHAR, max_length=length)
        for name in ("page_start", "page_end", "table_row_start", "table_row_end", "token_count"):
            schema.add_field(name, DataType.INT64)
        schema.add_field("metadata", DataType.JSON)
        schema.add_field("content", DataType.VARCHAR, max_length=16384)
        schema.add_field(
            "retrieval_text",
            DataType.VARCHAR,
            max_length=20000,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.dimension)
        schema.add_field("sparse_embedding", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name="knowledge_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["retrieval_text"],
                output_field_names=["sparse_embedding"],
            )
        )

        indexes = self.client.prepare_index_params()
        indexes.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        indexes.add_index(
            field_name="sparse_embedding",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"inverted_index_algo": "DAAT_MAXSCORE"},
        )
        self.client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=indexes,
            consistency_level="Strong",
        )

    async def ingest_document(
        self,
        document: ParsedDocument,
        *,
        document_type: str,
        product_model: str = "",
        category: str = "general",
        version: str = "",
        status: str = "published",
    ) -> dict[str, Any]:
        """切块并替换同 document_id 的全部在线切片。"""

        if not self.available or self.embeddings is None:
            raise RuntimeError("技术知识库不可用，请检查 Embedding 和 Milvus 配置")
        chunks = self.chunker.chunk(
            document,
            document_type=document_type,
            product_model=product_model,
            category=category,
            version=version,
            status=status,
        )
        return await self.ingest_chunks(chunks)

    async def ingest_chunks(self, chunks: Iterable[KnowledgeChunk]) -> dict[str, Any]:
        """批量向量化并写入；相同文档先整体删除以避免旧切片残留。"""

        if not self.available or self.embeddings is None:
            raise RuntimeError("技术知识库不可用，请检查 Embedding 和 Milvus 配置")
        materialized = list(chunks)
        if not materialized:
            return {"document_id": "", "chunk_count": 0}
        document_ids = {chunk.document_id for chunk in materialized}
        if len(document_ids) != 1:
            raise ValueError("一次 ingest_chunks 只能写入一个 document_id")
        document_id = next(iter(document_ids))

        signature_fields = (
            "chunk_id",
            "content_hash",
            "document_type",
            "product_model",
            "category",
            "version",
            "status",
        )
        existing_rows = self.client.query(
            collection_name=self.collection,
            filter=f"document_id == {_quoted(document_id)}",
            output_fields=list(signature_fields),
            limit=16384,
        )
        existing_signatures = {
            tuple(str(row.get(field, "")) for field in signature_fields)
            for row in existing_rows
        }
        expected_signatures = {
            tuple(str(getattr(chunk, field)) for field in signature_fields)
            for chunk in materialized
        }
        if existing_signatures == expected_signatures:
            return {
                "document_id": document_id,
                "chunk_count": len(materialized),
                "insert_count": 0,
                "unchanged": True,
            }

        vectors: list[list[float]] = []
        # 小批次避免本地 Embedding 服务因单次正文过多而超时。
        for start in range(0, len(materialized), 32):
            batch = materialized[start : start + 32]
            vectors.extend(
                await self.embeddings.aembed_documents([chunk.retrieval_text for chunk in batch])
            )
        if any(len(vector) != self.dimension for vector in vectors):
            raise ValueError("文档 Embedding 维度与 Milvus 集合不一致")

        self.client.delete(
            collection_name=self.collection,
            filter=f"document_id == {_quoted(document_id)}",
        )
        rows = [
            self._chunk_row(chunk, vector)
            for chunk, vector in zip(materialized, vectors, strict=True)
        ]
        result = self.client.insert(collection_name=self.collection, data=rows)
        return {
            "document_id": document_id,
            "chunk_count": len(rows),
            "insert_count": int(result.get("insert_count", len(rows))),
            "unchanged": False,
        }

    @staticmethod
    def _chunk_row(chunk: KnowledgeChunk, vector: list[float]) -> dict[str, Any]:
        row = chunk.to_dict()
        row.pop("chunk_id", None)
        # Milvus INT64 不保存 None；-1 明确表示源格式没有可靠位置。
        return {
            "chunk_id": chunk.chunk_id,
            **row,
            "page_start": chunk.page_start if chunk.page_start is not None else -1,
            "page_end": chunk.page_end if chunk.page_end is not None else -1,
            "table_row_start": chunk.table_row_start if chunk.table_row_start is not None else -1,
            "table_row_end": chunk.table_row_end if chunk.table_row_end is not None else -1,
            "effective_date": str(chunk.metadata.get("effective_date", "")),
            "embedding": vector,
        }

    async def seed_after_sales_policies(self) -> None:
        """把项目内审核过的政策作为稳定切片写入新版混合集合。"""

        if not self.available or self.embeddings is None:
            return
        payload = json.loads(_POLICY_FILE.read_text(encoding="utf-8"))
        version = payload["knowledge_base_version"]
        existing = self.client.query(
            collection_name=self.collection,
            filter=f'document_type == "after_sales_policy" and version == {_quoted(version)}',
            output_fields=["document_id"],
            limit=1000,
        )
        existing_ids = {item["document_id"] for item in existing}
        new_documents = [
            item for item in payload["documents"] if item["document_id"] not in existing_ids
        ]
        if not new_documents:
            return
        retrieval_texts = [
            f"{item['title']}\n{item['section']}\n{item['content']}" for item in new_documents
        ]
        vectors = await self.embeddings.aembed_documents(retrieval_texts)
        rows = []
        for item, retrieval_text, vector in zip(
            new_documents, retrieval_texts, vectors, strict=True
        ):
            content_hash = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
            rows.append(
                {
                    "chunk_id": f"{item['document_id']}__policy__{content_hash[:12]}",
                    "document_id": item["document_id"],
                    "document_type": "after_sales_policy",
                    "title": item["title"],
                    "section": item["section"],
                    "section_path": item["section"],
                    "source_path": str(_POLICY_FILE),
                    "version": version,
                    "status": "published",
                    "product_model": "",
                    "category": "general",
                    "chunk_type": "text",
                    "content_hash": content_hash,
                    "parent_section_id": item["document_id"],
                    "effective_date": payload["effective_date"],
                    "page_start": -1,
                    "page_end": -1,
                    "table_row_start": -1,
                    "table_row_end": -1,
                    "token_count": 0,
                    "metadata": {"keywords": item.get("keywords", [])},
                    "content": item["content"][:16384],
                    "retrieval_text": retrieval_text[:20000],
                    "embedding": vector,
                }
            )
        self.client.insert(collection_name=self.collection, data=rows)

    async def search(
        self,
        query: str,
        *,
        document_type: str | Sequence[str],
        limit: int = 3,
        product_model: str = "",
        category: str = "",
        version: str = "",
        candidate_limit: int = 20,
    ) -> list[dict[str, Any]]:
        """执行 dense + BM25 召回、RRF 融合和二阶段重排。"""

        if not self.available or self.embeddings is None or not query.strip():
            return []
        from pymilvus import AnnSearchRequest, RRFRanker

        vector = await self.embeddings.aembed_query(query)
        expression = self._search_filter(
            document_type=document_type,
            product_model=product_model,
            category=category,
            version=version,
        )
        safe_candidates = max(limit, min(candidate_limit, 100))
        requests = [
            AnnSearchRequest(
                data=[vector],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {}},
                limit=safe_candidates,
                expr=expression,
            ),
            AnnSearchRequest(
                data=[query],
                anns_field="sparse_embedding",
                param={"metric_type": "BM25", "params": {}},
                limit=safe_candidates,
                expr=expression,
            ),
        ]
        output_fields = [
            "chunk_id",
            "document_id",
            "document_type",
            "title",
            "section",
            "section_path",
            "source_path",
            "version",
            "product_model",
            "category",
            "chunk_type",
            "content_hash",
            "effective_date",
            "page_start",
            "page_end",
            "table_row_start",
            "table_row_end",
            "content",
        ]
        results = self.client.hybrid_search(
            collection_name=self.collection,
            reqs=requests,
            ranker=RRFRanker(k=60),
            limit=safe_candidates,
            output_fields=output_fields,
        )
        candidates = [
            {
                **hit.get("entity", {}),
                "hybrid_score": round(float(hit.get("distance", 0)), 6),
            }
            for group in results
            for hit in group
        ]
        # Cross-Encoder 推理是同步计算，放入工作线程避免阻塞 Agent 事件循环。
        reranked = await asyncio.to_thread(
            self.reranker.rerank,
            query,
            candidates,
            limit=max(1, min(limit, 10)),
        )
        return [self._public_result(item, query=query) for item in reranked]

    async def search_natural_language(
        self,
        query: str,
        *,
        limit: int = 3,
        candidate_limit: int = 40,
    ) -> list[dict[str, Any]]:
        """仅根据自然语言检索所有技术知识，并在召回后判断适用商品。

        Agent 不掌握数据库内部的型号和类目编码，因此这里不接受这些硬
        过滤条件。发布状态与技术文档范围属于服务端可信约束；商品型号、
        品牌和类目则由混合召回及重排从问题和候选元数据中完成软匹配。
        """

        return await self.search(
            query,
            document_type=("product_manual", "diagnostic_guide", "faq"),
            limit=limit,
            candidate_limit=candidate_limit,
        )

    @staticmethod
    def _search_filter(
        *,
        document_type: str | Sequence[str],
        product_model: str = "",
        category: str = "",
        version: str = "",
    ) -> str:
        document_types = (
            [document_type]
            if isinstance(document_type, str)
            else [item for item in document_type if item]
        )
        clauses = [f"status == {_quoted('published')}"]
        if len(document_types) == 1:
            clauses.append(f"document_type == {_quoted(document_types[0])}")
        elif document_types:
            quoted_types = ", ".join(_quoted(item) for item in document_types)
            clauses.append(f"document_type in [{quoted_types}]")
        if product_model:
            clauses.append(f"product_model == {_quoted(product_model)}")
        if category:
            clauses.append(f"category in [{_quoted(category)}, {_quoted('general')}]")
        if version:
            clauses.append(f"version == {_quoted(version)}")
        return " and ".join(clauses)

    @staticmethod
    def _excerpt(content: str, query: str, *, max_chars: int = 360) -> str:
        """截取命中位置附近的证据，避免调试输出重复打印完整切片。"""

        text = content.strip()
        if len(text) <= max_chars:
            return text
        folded = text.casefold()
        query_terms = re.findall(r"[A-Za-z0-9_.+-]+|[\u3400-\u9fff]{2,}", query.casefold())
        probes: list[str] = []
        for term in query_terms:
            probes.append(term)
            if re.fullmatch(r"[\u3400-\u9fff]+", term):
                probes.extend(term[index : index + 2] for index in range(len(term) - 1))
        positions = [folded.find(probe) for probe in probes if folded.find(probe) >= 0]
        anchor = min(positions) if positions else 0
        start = max(0, anchor - max_chars // 3)
        end = min(len(text), start + max_chars)
        start = max(0, end - max_chars)
        return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")

    @staticmethod
    def _public_result(item: dict[str, Any], *, query: str = "") -> dict[str, Any]:
        page_start = int(item.get("page_start", -1))
        page_end = int(item.get("page_end", -1))
        page_label = ""
        if page_start >= 1:
            page_label = f"，第 {page_start} 页"
            if page_end > page_start:
                page_label = f"，第 {page_start}-{page_end} 页"
        section = item.get("section_path") or item.get("section") or "正文"
        return {
            **item,
            "excerpt": TechnicalKnowledgeStore._excerpt(str(item.get("content", "")), query),
            "relevance_score": item.get("rerank_score", 0),
            "citation": f"[{item.get('document_id', '')}] {item.get('title', '')} / {section}{page_label}",
        }

    async def delete_document(self, document_id: str) -> None:
        if self.client is not None:
            self.client.delete(
                collection_name=self.collection,
                filter=f"document_id == {_quoted(document_id)}",
            )

    async def close(self) -> None:
        if self.client is not None:
            await asyncio.to_thread(self.client.close)
        self.available = False


_store: TechnicalKnowledgeStore | None = None
_lock = asyncio.Lock()


async def get_technical_knowledge_store() -> TechnicalKnowledgeStore:
    global _store
    async with _lock:
        if _store is None:
            from config import get_settings

            # FastMCP 正在处理工具请求时，不在事件循环线程中执行 Pydantic
            # Settings 与 OpenAI 客户端的同步初始化，避免阻塞协议响应。
            settings = await asyncio.to_thread(get_settings)
            reranker = create_reranker(settings)
            _store = await asyncio.to_thread(
                TechnicalKnowledgeStore,
                host=settings.milvus_host,
                port=settings.milvus_port,
                api_key=settings.milvus_api_key,
                embedding_api_key=settings.embedding_api_key,
                embedding_model=settings.embedding_model,
                embedding_base_url=settings.embedding_base_url,
                embedding_dimension=settings.embedding_dimension,
                embedding_namespace=settings.embedding_namespace,
                reranker=reranker,
            )
            await _store.initialize()
        elif not _store.available:
            # 临时 stdio MCP 工具调用结束时会关闭 Milvus；同一进程再次调用
            # 知识工具时重新连接，避免复用一个已关闭的客户端。
            await _store.initialize()
        return _store
