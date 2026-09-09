"""无需 Milvus 服务的 Dense + BM25 + RRF 检索基准。"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import jieba

from .ingestion import DocumentPreprocessor, StructuredDocumentChunker
from .reranking import CandidateReranker


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*|[\u3400-\u9fff]+")


def _tokens(text: str) -> list[str]:
    """模拟 Milvus 中文 analyzer：中文用 jieba，型号和技术词保持为整体。"""

    tokens: list[str] = []
    for part in _WORD.findall(text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            tokens.extend(token.strip() for token in jieba.lcut(part) if token.strip())
        else:
            tokens.append(part)
    return tokens


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


class OfflineHybridBenchmark:
    """复用生产切块和重排逻辑，对检索各阶段做可重复评测。"""

    def __init__(self, embeddings: Any, reranker: CandidateReranker) -> None:
        self.embeddings = embeddings
        self.reranker = reranker
        self.chunks: list[dict[str, Any]] = []
        self.vectors: list[list[float]] = []
        self.term_frequencies: list[Counter[str]] = []
        self.document_frequencies: Counter[str] = Counter()
        self.average_length = 0.0

    async def build(self, manifest_path: Path) -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = manifest_path.parent
        preprocessor = DocumentPreprocessor()
        chunker = StructuredDocumentChunker()
        chunks = []
        for item in manifest["documents"]:
            parsed = preprocessor.parse(root / item["path"], document_id=item["document_id"])
            chunks.extend(
                chunker.chunk(
                    parsed,
                    document_type=item["document_type"],
                    product_model=item.get("product_model", ""),
                    category=item.get("category", "general"),
                    version=item.get("version", ""),
                )
            )
        self.chunks = [chunk.to_dict() for chunk in chunks]
        self.vectors = []
        for start in range(0, len(chunks), 32):
            batch = chunks[start : start + 32]
            self.vectors.extend(
                await self.embeddings.aembed_documents([chunk.retrieval_text for chunk in batch])
            )

        tokenized = [_tokens(chunk.retrieval_text) for chunk in chunks]
        self.term_frequencies = [Counter(tokens) for tokens in tokenized]
        for tokens in tokenized:
            self.document_frequencies.update(set(tokens))
        self.average_length = sum(map(len, tokenized)) / max(1, len(tokenized))

    async def rank(
        self, query: str, filters: dict[str, str], *, limit: int = 5, candidate_limit: int = 20
    ) -> dict[str, list[dict[str, Any]]]:
        query_vector = await self.embeddings.aembed_query(query)
        eligible = [
            index for index, chunk in enumerate(self.chunks) if self._matches(chunk, filters)
        ]
        dense = sorted(
            eligible,
            key=lambda index: _cosine(query_vector, self.vectors[index]),
            reverse=True,
        )[:candidate_limit]
        bm25_scores = {
            index: self._bm25(_tokens(query), index)
            for index in eligible
        }
        sparse = sorted(eligible, key=lambda index: bm25_scores[index], reverse=True)[
            :candidate_limit
        ]
        hybrid_scores: Counter[int] = Counter()
        for ranking in (dense, sparse):
            for rank, index in enumerate(ranking, start=1):
                hybrid_scores[index] += 1.0 / (60 + rank)
        hybrid = [
            index for index, _ in hybrid_scores.most_common(candidate_limit)
        ]

        def materialize(
            indexes: list[int], score_name: str, result_limit: int = limit
        ) -> list[dict[str, Any]]:
            return [
                {
                    **self.chunks[index],
                    score_name: (
                        _cosine(query_vector, self.vectors[index])
                        if score_name == "dense_score"
                        else (
                            bm25_scores.get(index, 0.0)
                            if score_name == "bm25_score"
                            else hybrid_scores.get(index, 0.0)
                        )
                    ),
                }
                for index in indexes[:result_limit]
            ]

        # 二阶段重排应看到完整候选池，而不是只重排已经截断的最终 Top-K。
        hybrid_candidates = materialize(hybrid, "hybrid_score", candidate_limit)
        reranked = self.reranker.rerank(query, hybrid_candidates, limit=limit)
        return {
            "dense": materialize(dense, "dense_score"),
            "bm25": materialize(sparse, "bm25_score"),
            "hybrid_rrf": hybrid_candidates[:limit],
            "reranked": reranked,
        }

    @staticmethod
    def _matches(chunk: dict[str, Any], filters: dict[str, str]) -> bool:
        if chunk["document_type"] != filters.get("document_type", chunk["document_type"]):
            return False
        product_model = filters.get("product_model", "")
        if product_model and chunk.get("product_model") != product_model:
            return False
        category = filters.get("category", "")
        return not category or chunk.get("category") in {category, "general"}

    def _bm25(self, query_tokens: list[str], index: int) -> float:
        frequencies = self.term_frequencies[index]
        document_length = sum(frequencies.values())
        score = 0.0
        k1, b = 1.5, 0.75
        total_documents = max(1, len(self.chunks))
        for term in query_tokens:
            frequency = frequencies[term]
            if not frequency:
                continue
            document_frequency = self.document_frequencies[term]
            inverse_frequency = math.log(
                1 + (total_documents - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + k1 * (
                1 - b + b * document_length / max(1.0, self.average_length)
            )
            score += inverse_frequency * frequency * (k1 + 1) / denominator
        return score


def _is_relevant(result: dict[str, Any], targets: list[dict[str, str]]) -> bool:
    return any(
        result.get("document_id") == target["document_id"]
        and target["section"] in result.get("section_path", "")
        for target in targets
    )


def _target_hits(results: list[dict[str, Any]], targets: list[dict[str, str]]) -> int:
    return sum(
        any(
            result.get("document_id") == target["document_id"]
            and target["section"] in result.get("section_path", "")
            for result in results
        )
        for target in targets
    )


def _normalize_fact(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


async def evaluate_questions(
    benchmark: OfflineHybridBenchmark, questions_path: Path, *, top_k: int = 5
) -> dict[str, Any]:
    payload = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = payload["questions"]
    stages = ("dense", "bm25", "hybrid_rrf", "reranked")
    accumulators = {
        stage: {"top1": 0.0, "hit3": 0.0, "recall3": 0.0, "mrr": 0.0, "ndcg3": 0.0,
                "fact_recall3": 0.0, "answerable3": 0.0, "model_leakage3": 0.0}
        for stage in stages
    }
    details = []

    for question in questions:
        rankings = await benchmark.rank(
            question["rewritten_query"], question["filters"], limit=top_k
        )
        question_detail = {"id": question["id"], "question": question["user_question"], "stages": {}}
        for stage in stages:
            results = rankings[stage]
            targets = question["relevant_targets"]
            relevances = [1 if _is_relevant(result, targets) else 0 for result in results]
            first_rank = next((rank for rank, value in enumerate(relevances, start=1) if value), None)
            top3 = results[:3]
            # 同一标准目标可能产生多个相邻/重叠 chunk；nDCG 只奖励首次覆盖，
            # 否则重复块会让 DCG 超过按目标数计算的理想 DCG。
            covered_targets: set[int] = set()
            top3_relevances: list[int] = []
            for result in top3:
                matches = {
                    index
                    for index, target in enumerate(targets)
                    if result.get("document_id") == target["document_id"]
                    and target["section"] in result.get("section_path", "")
                }
                new_matches = matches - covered_targets
                top3_relevances.append(1 if new_matches else 0)
                covered_targets.update(new_matches)
            target_recall = _target_hits(top3, targets) / max(1, len(targets))
            dcg = sum(value / math.log2(rank + 1) for rank, value in enumerate(top3_relevances, start=1))
            ideal_count = min(3, len(targets))
            ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
            evidence = _normalize_fact("\n".join(result.get("content", "") for result in top3))
            facts = question.get("required_facts", [])
            found_facts = [fact for fact in facts if _normalize_fact(fact) in evidence]
            product_model = question["filters"].get("product_model", "")
            leakage = sum(
                1
                for result in top3
                if product_model and result.get("product_model") != product_model
            ) / max(1, len(top3))

            metrics = accumulators[stage]
            metrics["top1"] += float(bool(relevances and relevances[0]))
            metrics["hit3"] += float(any(top3_relevances))
            metrics["recall3"] += target_recall
            metrics["mrr"] += 1 / first_rank if first_rank else 0.0
            metrics["ndcg3"] += dcg / ideal_dcg if ideal_dcg else 0.0
            metrics["fact_recall3"] += len(found_facts) / max(1, len(facts))
            metrics["answerable3"] += float(len(found_facts) == len(facts))
            metrics["model_leakage3"] += leakage
            question_detail["stages"][stage] = {
                "top_documents": [
                    f"{result['document_id']}::{result['section']}" for result in top3
                ],
                "relevant_rank": first_rank,
                "target_recall_at_3": round(target_recall, 4),
                "found_facts": found_facts,
                "missing_facts": [fact for fact in facts if fact not in found_facts],
            }
        details.append(question_detail)

    count = max(1, len(questions))
    summary = {
        stage: {name: round(value / count, 4) for name, value in metrics.items()}
        for stage, metrics in accumulators.items()
    }
    return {
        "benchmark_version": payload["benchmark_version"],
        "question_count": len(questions),
        "chunk_count": len(benchmark.chunks),
        "metric_definitions": {
            "top1": "首条结果命中正确文档和章节的比例",
            "hit3": "前三条至少命中一个正确目标的比例",
            "recall3": "前三条覆盖标准相关目标的比例",
            "mrr": "第一个正确目标排名的平均倒数",
            "ndcg3": "前三条二元相关性的归一化折损累计增益",
            "fact_recall3": "前三条证据覆盖标准答案关键事实的比例",
            "answerable3": "前三条证据覆盖全部关键事实的题目比例，并非 LLM 最终答案评分",
            "model_leakage3": "前三条中违反 product_model 过滤的结果比例，越低越好",
        },
        "summary": summary,
        "details": details,
    }


async def evaluate_online_store(
    store: Any, questions_path: Path, *, top_k: int = 5
) -> dict[str, Any]:
    """使用真实 TechnicalKnowledgeStore 统计同口径在线指标。"""

    payload = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = payload["questions"]
    totals = {
        "top1": 0.0,
        "hit3": 0.0,
        "recall3": 0.0,
        "mrr": 0.0,
        "ndcg3": 0.0,
        "fact_recall3": 0.0,
        "answerable3": 0.0,
        "model_leakage3": 0.0,
    }
    details = []

    for question in questions:
        filters = question["filters"]
        results = await store.search(
            question["rewritten_query"],
            document_type=filters["document_type"],
            product_model=filters.get("product_model", ""),
            category=filters.get("category", ""),
            limit=top_k,
            candidate_limit=20,
        )
        targets = question["relevant_targets"]
        relevances = [1 if _is_relevant(result, targets) else 0 for result in results]
        first_rank = next((rank for rank, value in enumerate(relevances, start=1) if value), None)
        top3 = results[:3]

        covered_targets: set[int] = set()
        unique_relevances: list[int] = []
        for result in top3:
            matches = {
                index
                for index, target in enumerate(targets)
                if result.get("document_id") == target["document_id"]
                and target["section"] in result.get("section_path", "")
            }
            new_matches = matches - covered_targets
            unique_relevances.append(1 if new_matches else 0)
            covered_targets.update(new_matches)

        target_recall = _target_hits(top3, targets) / max(1, len(targets))
        dcg = sum(
            value / math.log2(rank + 1)
            for rank, value in enumerate(unique_relevances, start=1)
        )
        ideal_count = min(3, len(targets))
        ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        evidence = _normalize_fact("\n".join(result.get("content", "") for result in top3))
        facts = question.get("required_facts", [])
        found_facts = [fact for fact in facts if _normalize_fact(fact) in evidence]
        product_model = filters.get("product_model", "")
        leakage = sum(
            1
            for result in top3
            if product_model and result.get("product_model") != product_model
        ) / max(1, len(top3))

        totals["top1"] += float(bool(relevances and relevances[0]))
        totals["hit3"] += float(any(relevances[:3]))
        totals["recall3"] += target_recall
        totals["mrr"] += 1 / first_rank if first_rank else 0.0
        totals["ndcg3"] += dcg / ideal_dcg if ideal_dcg else 0.0
        totals["fact_recall3"] += len(found_facts) / max(1, len(facts))
        totals["answerable3"] += float(len(found_facts) == len(facts))
        totals["model_leakage3"] += leakage
        details.append(
            {
                "id": question["id"],
                "question": question["user_question"],
                "rewritten_query": question["rewritten_query"],
                "relevant_rank": first_rank,
                "target_recall_at_3": round(target_recall, 4),
                "found_facts": found_facts,
                "missing_facts": [fact for fact in facts if fact not in found_facts],
                "results": [
                    {
                        "document_id": result.get("document_id"),
                        "section": result.get("section"),
                        "hybrid_rank": result.get("hybrid_rank"),
                        "hybrid_score": result.get("hybrid_score"),
                        "rerank_score": result.get("rerank_score"),
                        "citation": result.get("citation"),
                    }
                    for result in results
                ],
            }
        )

    count = max(1, len(questions))
    return {
        "benchmark_version": payload["benchmark_version"],
        "mode": "milvus_online_dense_bm25_rrf_rerank",
        "question_count": len(questions),
        "summary": {name: round(value / count, 4) for name, value in totals.items()},
        "details": details,
    }
