"""用 CRUD-RAG 的 1000 篇文档单独评测当前检索链路（不调用生成 LLM）。

数据来源：https://github.com/IAAR-Shanghai/CRUD_RAG

本脚本只使用 questanswer_1doc/2docs/3docs 的问题和相关原文标注。为避免随机
截取 1000 篇后正例不在库中，先纳入所选问题的 news1/news2/news3，再从官方
80000_docs 补足干扰文档。评测 current Dense + BM25 + RRF + reranker 链路，
输出候选池及重排后的 Hit/Precision/Recall/MRR/nDCG，不调用 DeepSeek 等生成模型。

示例：
    python agent/scripts/evaluate_crud_rag_retrieval.py \
      --crud-root D:/datasets/CRUD_RAG --document-count 1000 --query-count 100

先检查数据选择而不连接 Embedding/Milvus：
    python agent/scripts/evaluate_crud_rag_retrieval.py \
      --crud-root D:/datasets/CRUD_RAG --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from config import get_settings  # noqa: E402
from core.knowledge.ingestion import (  # noqa: E402
    BlockType,
    DocumentBlock,
    ParsedDocument,
)
from core.knowledge.milvus_store import TechnicalKnowledgeStore  # noqa: E402
from core.knowledge.reranking import create_reranker  # noqa: E402


QA_TASKS = ("questanswer_1doc", "questanswer_2docs", "questanswer_3docs")
DEFAULT_OUTPUT = AGENT_ROOT / "runtime" / "crud_rag_retrieval_report.json"
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class EvalDocument:
    document_id: str
    text: str
    source: str


@dataclass(frozen=True, slots=True)
class EvalQuery:
    query_id: str
    task: str
    query: str
    relevant_document_ids: tuple[str, ...]


class CrudEvaluationStore(TechnicalKnowledgeStore):
    """独立评测集合；禁止把业务售后政策自动写入评测语料。"""

    async def seed_after_sales_policies(self) -> None:
        return None


def _normalized(text: str) -> str:
    return _SPACE.sub("", text).casefold()


def _document_id(text: str) -> str:
    digest = hashlib.sha256(_normalized(text).encode("utf-8")).hexdigest()
    return f"crud_{digest[:24]}"


def _load_payload(dataset_path: Path) -> dict[str, list[dict[str, Any]]]:
    if not dataset_path.is_file():
        raise FileNotFoundError(f"找不到 CRUD-RAG 评测文件：{dataset_path}")
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    missing = [task for task in QA_TASKS if task not in payload]
    if missing:
        raise ValueError(f"评测文件缺少任务：{missing}")
    return payload


def _positive_texts(item: dict[str, Any], task: str) -> list[str]:
    expected = int(task.removeprefix("questanswer_").removesuffix("docs").removesuffix("doc"))
    texts = [str(item.get(f"news{index}", "")).strip() for index in range(1, expected + 1)]
    if not all(texts):
        raise ValueError(f"{task} 样本 {item.get('ID')} 缺少 news1..news{expected}")
    return texts


def select_queries_and_positives(
    payload: dict[str, list[dict[str, Any]]], *, query_count: int, document_count: int, seed: int
) -> tuple[list[EvalQuery], dict[str, EvalDocument]]:
    """跨 1/2/3 文档任务轮询抽样，并保证所有查询正例都在文档预算内。"""

    rng = random.Random(seed)
    task_samples: dict[str, list[dict[str, Any]]] = {}
    for task in QA_TASKS:
        task_samples[task] = list(payload[task])
        rng.shuffle(task_samples[task])

    positives: dict[str, EvalDocument] = {}
    selected: list[EvalQuery] = []
    offsets = {task: 0 for task in QA_TASKS}
    while len(selected) < query_count:
        progressed = False
        for task in QA_TASKS:
            samples = task_samples[task]
            while offsets[task] < len(samples):
                item = samples[offsets[task]]
                offsets[task] += 1
                query = str(item.get("questions", "")).strip()
                if not query:
                    continue
                texts = _positive_texts(item, task)
                ids = tuple(dict.fromkeys(_document_id(text) for text in texts))
                new_count = sum(document_id not in positives for document_id in ids)
                if len(positives) + new_count > document_count:
                    continue
                for text in texts:
                    document_id = _document_id(text)
                    positives.setdefault(
                        document_id,
                        EvalDocument(document_id, text, f"{task}:{item.get('ID', '')}"),
                    )
                selected.append(
                    EvalQuery(
                        query_id=f"{task}:{item.get('ID', offsets[task] - 1)}",
                        task=task,
                        query=query,
                        relevant_document_ids=ids,
                    )
                )
                progressed = True
                break
            if len(selected) >= query_count:
                break
        if not progressed:
            break

    if len(selected) < query_count:
        raise ValueError(
            f"只能在 {document_count} 篇预算内选择 {len(selected)} 条有效查询，"
            f"小于要求的 {query_count} 条"
        )
    return selected, positives


def _corpus_files(documents_path: Path) -> list[Path]:
    if documents_path.is_file():
        return [documents_path]
    if not documents_path.is_dir():
        raise FileNotFoundError(f"找不到 CRUD-RAG 语料：{documents_path}")
    files = sorted(path for path in documents_path.glob("documents_dup_part*") if path.is_file())
    if not files:
        raise FileNotFoundError(f"{documents_path} 下没有 documents_dup_part* 文件")
    return files


def _iter_distractors(documents_path: Path, *, seed: int) -> Iterator[str]:
    """以固定种子改变分片顺序；逐行读取，避免把 8 万篇全文一次载入内存。"""

    files = _corpus_files(documents_path)
    random.Random(seed).shuffle(files)
    for path in files:
        with path.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                text = line.strip()
                if text:
                    yield text


def build_corpus(
    positives: dict[str, EvalDocument], documents_path: Path, *, document_count: int, seed: int
) -> list[EvalDocument]:
    documents = dict(positives)
    for text in _iter_distractors(documents_path, seed=seed):
        document_id = _document_id(text)
        if document_id in documents:
            continue
        documents[document_id] = EvalDocument(document_id, text, "80000_docs")
        if len(documents) == document_count:
            break
    if len(documents) != document_count:
        raise ValueError(f"去重后只有 {len(documents)} 篇文档，无法补足 {document_count} 篇")
    return list(documents.values())


def _parsed_document(document: EvalDocument) -> ParsedDocument:
    source_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    return ParsedDocument(
        document_id=document.document_id,
        source_path=document.source,
        source_type="crud_rag_news",
        source_hash=source_hash,
        title=document.text[:80].replace("\n", " "),
        blocks=[
            DocumentBlock(
                block_id=f"{document.document_id}__body",
                block_type=BlockType.PARAGRAPH,
                text=document.text,
                raw_text=document.text,
                order=1,
            )
        ],
    )


def _collection_name(documents: Sequence[EvalDocument], dimension: int) -> str:
    digest = hashlib.sha256("\n".join(sorted(doc.document_id for doc in documents)).encode()).hexdigest()
    return f"crud_rag_retrieval_{dimension}_{digest[:12]}"


async def _build_index(
    store: CrudEvaluationStore, documents: Sequence[EvalDocument], *, rebuild: bool
) -> int:
    chunks = [
        chunk
        for document in documents
        for chunk in store.chunker.chunk(_parsed_document(document), document_type="crud_rag_eval")
    ]
    if rebuild and store.client.has_collection(store.collection):
        store.client.drop_collection(store.collection)
        store._ensure_collection()
    elif not rebuild:
        count = store.client.query(
            collection_name=store.collection,
            filter='document_type == "crud_rag_eval"',
            output_fields=["count(*)"],
        )[0]["count(*)"]
        if int(count) == len(chunks):
            return len(chunks)
        raise RuntimeError(
            f"已有集合含 {count} 个切片，预期 {len(chunks)}；请去掉 --reuse-index 重建"
        )

    assert store.embeddings is not None
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), 32):
        batch = chunks[start : start + 32]
        vectors.extend(await store.embeddings.aembed_documents([chunk.retrieval_text for chunk in batch]))
    if any(len(vector) != store.dimension for vector in vectors):
        raise ValueError("Embedding 返回维度与 EMBEDDING_DIMENSION 不一致")
    for start in range(0, len(chunks), 200):
        batch = chunks[start : start + 200]
        rows = [
            store._chunk_row(chunk, vector)
            for chunk, vector in zip(batch, vectors[start : start + 200], strict=True)
        ]
        store.client.insert(collection_name=store.collection, data=rows)
    store.client.flush(store.collection)
    return len(chunks)


def _deduplicate_documents(results: Iterable[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(item.get("document_id", "")) for item in results if item.get("document_id")))


async def _retrieve(
    store: CrudEvaluationStore, query: str, *, candidate_limit: int
) -> tuple[list[str], list[str]]:
    from pymilvus import AnnSearchRequest, RRFRanker

    assert store.embeddings is not None
    vector = await store.embeddings.aembed_query(query)
    expression = 'status == "published" and document_type == "crud_rag_eval"'
    requests = [
        AnnSearchRequest(
            data=[vector], anns_field="embedding", param={"metric_type": "COSINE", "params": {}},
            limit=candidate_limit, expr=expression,
        ),
        AnnSearchRequest(
            data=[query], anns_field="sparse_embedding", param={"metric_type": "BM25", "params": {}},
            limit=candidate_limit, expr=expression,
        ),
    ]
    results = store.client.hybrid_search(
        collection_name=store.collection,
        reqs=requests,
        ranker=RRFRanker(k=60),
        limit=candidate_limit,
        output_fields=[
            "chunk_id", "document_id", "document_type", "title", "section", "section_path",
            "source_path", "version", "product_model", "category", "chunk_type", "content",
        ],
    )
    candidates = [
        {
            **hit.get("entity", {}),
            "hybrid_score": float(hit.get("distance", 0.0)),
            "hybrid_rank": rank,
        }
        for group in results
        for rank, hit in enumerate(group, start=1)
    ]
    hybrid_documents = _deduplicate_documents(candidates)
    reranked = await asyncio.to_thread(store.reranker.rerank, query, candidates, limit=candidate_limit)
    return hybrid_documents, _deduplicate_documents(reranked)


def _query_metrics(ranking: Sequence[str], gold: set[str], ks: Sequence[int]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    first_rank = next((rank for rank, document_id in enumerate(ranking, 1) if document_id in gold), None)
    metrics["mrr"] = 1.0 / first_rank if first_rank else 0.0
    for k in ks:
        top = ranking[:k]
        hits = len(gold.intersection(top))
        metrics[f"hit@{k}"] = float(hits > 0)
        metrics[f"precision@{k}"] = hits / k
        metrics[f"recall@{k}"] = hits / len(gold)
        dcg = sum(
            1.0 / math.log2(rank + 1)
            for rank, document_id in enumerate(top, 1)
            if document_id in gold
        )
        ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(gold)) + 1))
        metrics[f"ndcg@{k}"] = dcg / ideal if ideal else 0.0
    return metrics


async def _evaluate(
    store: CrudEvaluationStore, queries: Sequence[EvalQuery], *, candidate_limit: int
) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]]]:
    stages = ("hybrid_rrf", "reranked")
    totals: dict[str, dict[str, float]] = {stage: {} for stage in stages}
    details: list[dict[str, Any]] = []
    ks = (1, 3, 5, 10)
    for index, item in enumerate(queries, 1):
        started = time.perf_counter()
        hybrid, reranked = await _retrieve(store, item.query, candidate_limit=candidate_limit)
        rankings = {"hybrid_rrf": hybrid, "reranked": reranked}
        detail: dict[str, Any] = {
            "query_id": item.query_id,
            "task": item.task,
            "query": item.query,
            "relevant_document_ids": list(item.relevant_document_ids),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "stages": {},
        }
        gold = set(item.relevant_document_ids)
        for stage, ranking in rankings.items():
            metrics = _query_metrics(ranking, gold, ks)
            metrics["candidate_recall"] = len(gold.intersection(ranking)) / len(gold)
            for name, value in metrics.items():
                totals[stage][name] = totals[stage].get(name, 0.0) + value
            detail["stages"][stage] = {"metrics": metrics, "top10_document_ids": ranking[:10]}
        details.append(detail)
        print(f"[{index}/{len(queries)}] {item.query_id}")
    summary = {
        stage: {name: round(value / len(queries), 4) for name, value in values.items()}
        for stage, values in totals.items()
    }
    return summary, details


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    metric_names = ("hit@1", "hit@3", "hit@5", "hit@10", "recall@5", "recall@10", "mrr", "ndcg@10", "candidate_recall")
    lines = [
        "# CRUD-RAG 检索评测报告",
        "",
        f"- 文档数：{report['document_count']}",
        f"- 查询数：{report['query_count']}",
        f"- 切片数：{report['chunk_count']}",
        f"- 候选池：{report['candidate_limit']}",
        f"- 集合：`{report['collection']}`",
        "- 范围：仅检索，不调用生成 LLM。正例来自 QA 样本的 news1/news2/news3，其余由 80000_docs 补齐。",
        "",
        "| 阶段 | " + " | ".join(metric_names) + " |",
        "| --- | " + " | ".join(["---:"] * len(metric_names)) + " |",
    ]
    for stage, metrics in report["summary"].items():
        lines.append(f"| {stage} | " + " | ".join(f"{metrics[name]:.4f}" for name in metric_names) + " |")
    failures = [item for item in report["details"] if item["stages"]["reranked"]["metrics"]["hit@5"] == 0]
    lines.extend(["", "## 重排后 Top-5 未命中", ""])
    lines.extend(
        [f"- `{item['query_id']}` {item['query']}" for item in failures]
        or ["本轮没有 Top-5 未命中。"]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.crud_root / "data" / "crud_split" / "split_merged.json"
    documents_path = args.crud_root / "data" / "80000_docs"
    payload = _load_payload(dataset_path)
    queries, positives = select_queries_and_positives(
        payload, query_count=args.query_count, document_count=args.document_count, seed=args.seed
    )
    documents = build_corpus(
        positives, documents_path, document_count=args.document_count, seed=args.seed
    )
    selection = {
        "document_count": len(documents),
        "positive_document_count": len(positives),
        "distractor_document_count": len(documents) - len(positives),
        "query_count": len(queries),
        "queries_by_task": {task: sum(query.task == task for query in queries) for task in QA_TASKS},
        "seed": args.seed,
        "sampling_policy": "gold_preserving_then_fill_from_80000_docs",
    }
    if args.dry_run:
        print(json.dumps(selection, ensure_ascii=False, indent=2))
        return selection

    settings = get_settings()
    if not (settings.embedding_api_key and settings.embedding_model and settings.embedding_base_url):
        raise RuntimeError("请先配置 EMBEDDING_API_KEY、EMBEDDING_MODEL、EMBEDDING_BASE_URL")
    reranker = create_reranker(settings.model_copy(update={"reranker_allow_heuristic_fallback": False}))
    store = CrudEvaluationStore(
        host=settings.milvus_host,
        port=settings.milvus_port,
        api_key=settings.milvus_api_key,
        embedding_api_key=settings.embedding_api_key,
        embedding_model=settings.embedding_model,
        embedding_base_url=settings.embedding_base_url,
        embedding_dimension=settings.embedding_dimension,
        reranker=reranker,
    )
    store.collection = _collection_name(documents, settings.embedding_dimension)
    if settings.embedding_namespace:
        store.collection += "_" + settings.embedding_namespace
    try:
        await store.initialize()
        if not store.available:
            raise RuntimeError("Milvus 或 Embedding 服务不可用")
        chunk_count = await _build_index(store, documents, rebuild=not args.reuse_index)
        summary, details = await _evaluate(store, queries, candidate_limit=args.candidate_limit)
        report = {
            "benchmark": "CRUD-RAG retrieval-only subset",
            **selection,
            "chunk_count": chunk_count,
            "candidate_limit": args.candidate_limit,
            "collection": store.collection,
            "summary": summary,
            "details": details,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_markdown(report, args.output.with_suffix(".md"))
        return report
    finally:
        await store.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crud-root", type=Path, required=True, help="CRUD_RAG 官方仓库根目录")
    parser.add_argument("--document-count", type=int, default=1000)
    parser.add_argument("--query-count", type=int, default=100)
    parser.add_argument("--candidate-limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="只验证数据并打印抽样统计")
    parser.add_argument("--reuse-index", action="store_true", help="复用完全匹配的已有评测集合")
    args = parser.parse_args()
    if args.document_count < 1 or args.query_count < 1:
        parser.error("document-count 和 query-count 必须大于 0")
    if not 10 <= args.candidate_limit <= 100:
        parser.error("candidate-limit 必须位于 10 到 100")
    return args


def main() -> None:
    args = _parse_args()
    report = asyncio.run(run(args))
    if not args.dry_run:
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"report={args.output.resolve()}")


if __name__ == "__main__":
    main()
