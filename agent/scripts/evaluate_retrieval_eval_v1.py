"""评测 retrieval_eval_v1 的文档、章节和证据级检索质量。"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from langchain_openai import OpenAIEmbeddings  # noqa: E402

from config import get_settings  # noqa: E402
from core.knowledge.evaluation import OfflineHybridBenchmark  # noqa: E402
from core.knowledge.reranking import create_reranker  # noqa: E402


BENCHMARK_ROOT = AGENT_ROOT / "data" / "knowledge" / "retrieval_eval_v1"
DEFAULT_OUTPUT = AGENT_ROOT / "runtime" / "retrieval_eval_v1_report.json"
STAGES = ("dense", "bm25", "hybrid_rrf", "reranked")
MODE_LABELS = {
    "no_model_filter": "不使用型号过滤（保留品类过滤）",
    "metadata_filtered": "使用完整元数据过滤",
}
METRIC_NAMES = (
    "document_hit@1",
    "document_hit@3",
    "document_hit@5",
    "section_hit@1",
    "section_hit@3",
    "section_hit@5",
    "evidence_recall@1",
    "evidence_recall@3",
    "evidence_recall@5",
    "context_precision@5",
    "answerable@5",
    "mrr",
    "ndcg@5",
    "hard_negative_outrank_rate",
)


class CachedQueryEmbeddings:
    """复用两种过滤模式下相同问题的查询向量。"""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.query_cache: dict[str, list[float]] = {}

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.delegate.aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        if text not in self.query_cache:
            self.query_cache[text] = await self.delegate.aembed_query(text)
        return self.query_cache[text]

    async def preload_queries(self, texts: list[str], *, batch_size: int = 64) -> None:
        """批量计算查询向量，避免逐题产生一次远程请求。"""

        missing = list(dict.fromkeys(text for text in texts if text not in self.query_cache))
        for start in range(0, len(missing), batch_size):
            batch = missing[start : start + batch_size]
            vectors = await self.delegate.aembed_documents(batch)
            self.query_cache.update(zip(batch, vectors, strict=True))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _matches_document(result: dict[str, Any], targets: list[dict[str, Any]]) -> bool:
    return any(result.get("document_id") == target["document_id"] for target in targets)


def _matching_target_indexes(
    result: dict[str, Any], targets: list[dict[str, Any]]
) -> set[int]:
    return {
        index
        for index, target in enumerate(targets)
        if result.get("document_id") == target["document_id"]
        and target["section"] in result.get("section_path", "")
    }


def _evidence_coverage(
    results: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> tuple[int, int, list[str], list[str]]:
    found: list[str] = []
    missing: list[str] = []
    for target in targets:
        target_text = _normalize(
            "\n".join(
                result.get("content", "")
                for result in results
                if result.get("document_id") == target["document_id"]
                and target["section"] in result.get("section_path", "")
            )
        )
        for fragment in target.get("evidence_fragments", []):
            (found if _normalize(fragment) in target_text else missing).append(fragment)
    return len(found), len(found) + len(missing), found, missing


def _contains_target_evidence(
    result: dict[str, Any], targets: list[dict[str, Any]]
) -> bool:
    """判断单个 Chunk 是否属于正确章节且包含至少一个标准证据片段。"""

    content = _normalize(result.get("content", ""))
    if not content:
        return False
    return any(
        result.get("document_id") == target["document_id"]
        and target["section"] in result.get("section_path", "")
        and any(
            normalized_fragment and normalized_fragment in content
            for normalized_fragment in map(
                _normalize, target.get("evidence_fragments", [])
            )
        )
        for target in targets
    )


def score_ranking(
    results: list[dict[str, Any]], question: dict[str, Any], *, top_k: int
) -> tuple[dict[str, float], dict[str, Any]]:
    """对单题单阶段排名计算文档、章节、证据和难负例指标。"""

    targets = question["relevant_targets"]
    metrics: dict[str, float] = {}
    for k in (1, 3, 5):
        selected = results[: min(k, top_k)]
        metrics[f"document_hit@{k}"] = float(
            any(_matches_document(result, targets) for result in selected)
        )
        metrics[f"section_hit@{k}"] = float(
            any(_matching_target_indexes(result, targets) for result in selected)
        )
        found_count, total_count, _, _ = _evidence_coverage(selected, targets)
        metrics[f"evidence_recall@{k}"] = found_count / max(1, total_count)

    exact_relevances = [bool(_matching_target_indexes(result, targets)) for result in results]
    first_exact_rank = next(
        (rank for rank, relevant in enumerate(exact_relevances, start=1) if relevant), None
    )
    metrics["mrr"] = 1.0 / first_exact_rank if first_exact_rank else 0.0

    covered_targets: set[int] = set()
    gains: list[int] = []
    for result in results[: min(5, top_k)]:
        new_targets = _matching_target_indexes(result, targets) - covered_targets
        gains.append(1 if new_targets else 0)
        covered_targets.update(new_targets)
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_count = min(5, len(targets))
    ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    metrics["ndcg@5"] = dcg / ideal_dcg if ideal_dcg else 0.0

    _, _, found_evidence, missing_evidence = _evidence_coverage(
        results[: min(5, top_k)], targets
    )
    context_precision_k = min(5, top_k)
    evidence_context_count = sum(
        _contains_target_evidence(result, targets)
        for result in results[:context_precision_k]
    )
    metrics["context_precision@5"] = (
        evidence_context_count / context_precision_k
        if context_precision_k
        else 0.0
    )
    metrics["answerable@5"] = float(not missing_evidence)

    relevant_document_rank = next(
        (
            rank
            for rank, result in enumerate(results, start=1)
            if _matches_document(result, targets)
        ),
        None,
    )
    hard_negatives = set(question.get("hard_negative_document_ids", []))
    hard_negative_rank = next(
        (
            rank
            for rank, result in enumerate(results, start=1)
            if result.get("document_id") in hard_negatives
        ),
        None,
    )
    hard_negative_outranks = hard_negative_rank is not None and (
        relevant_document_rank is None or hard_negative_rank < relevant_document_rank
    )
    metrics["hard_negative_outrank_rate"] = float(hard_negative_outranks)

    detail = {
        "relevant_rank": first_exact_rank,
        "relevant_document_rank": relevant_document_rank,
        "hard_negative_rank": hard_negative_rank,
        "hard_negative_outranks": hard_negative_outranks,
        "found_evidence": found_evidence,
        "missing_evidence": missing_evidence,
        "results": [
            {
                "rank": rank,
                "document_id": result.get("document_id", ""),
                "section": result.get("section", ""),
                "section_path": result.get("section_path", ""),
                "document_match": _matches_document(result, targets),
                "section_match": bool(_matching_target_indexes(result, targets)),
                "evidence_match": _contains_target_evidence(result, targets),
                "reranker_mode": result.get("reranker_mode"),
                "reranker_model": result.get("reranker_model"),
                "rerank_score": result.get("rerank_score"),
                "rerank_logit": result.get("rerank_logit"),
                "content_excerpt": re.sub(r"\s+", " ", result.get("content", ""))[:240],
            }
            for rank, result in enumerate(results, start=1)
        ],
    }
    return metrics, detail


def _filters_for(question: dict[str, Any], mode: str) -> dict[str, str]:
    filters = dict(question["filters"])
    if mode == "no_model_filter":
        filters.pop("product_model", None)
    return filters


async def evaluate(
    benchmark: OfflineHybridBenchmark,
    questions: list[dict[str, Any]],
    *,
    modes: tuple[str, ...],
    top_k: int,
    candidate_limit: int,
) -> dict[str, Any]:
    totals = {
        mode: {stage: {metric: 0.0 for metric in METRIC_NAMES} for stage in STAGES}
        for mode in modes
    }
    details: list[dict[str, Any]] = []

    for number, question in enumerate(questions, start=1):
        question_detail = {
            "id": question["id"],
            "query": question["query"],
            "standard_answer": question["standard_answer"],
            "case_type": question["case_type"],
            "targets": question["relevant_targets"],
            "hard_negative_document_ids": question.get("hard_negative_document_ids", []),
            "modes": {},
        }
        for mode in modes:
            rankings = await benchmark.rank(
                question["query"],
                _filters_for(question, mode),
                limit=top_k,
                candidate_limit=candidate_limit,
            )
            question_detail["modes"][mode] = {}
            for stage in STAGES:
                metrics, stage_detail = score_ranking(
                    rankings[stage], question, top_k=top_k
                )
                for name in METRIC_NAMES:
                    totals[mode][stage][name] += metrics[name]
                question_detail["modes"][mode][stage] = {
                    "metrics": metrics,
                    **stage_detail,
                }
        details.append(question_detail)
        if number % 10 == 0 or number == len(questions):
            print(f"progress={number}/{len(questions)}", flush=True)

    count = max(1, len(questions))
    summary = {
        mode: {
            stage: {
                name: round(value / count, 4)
                for name, value in stage_totals.items()
            }
            for stage, stage_totals in mode_totals.items()
        }
        for mode, mode_totals in totals.items()
    }
    return {"summary": summary, "details": details}


def _write_markdown(report: dict[str, Any], output: Path) -> None:
    compact_metrics = (
        "document_hit@1",
        "section_hit@1",
        "section_hit@5",
        "evidence_recall@5",
        "context_precision@5",
        "answerable@5",
        "mrr",
        "ndcg@5",
        "hard_negative_outrank_rate",
    )
    lines = [
        "# retrieval_eval_v1 检索评测报告",
        "",
        f"- 数据集版本：`{report['benchmark_version']}`",
        f"- 文档数：{report['document_count']}",
        f"- 问题数：{report['question_count']}",
        f"- 切片数：{report['chunk_count']}",
        f"- Top-K：{report['top_k']}",
        f"- 候选池：{report['candidate_limit']}",
        f"- 重排模型：`{report['reranker']['model']}`",
        f"- 重排设备：`{report['reranker']['device']}`",
        "- 规则兜底：关闭；模型加载或推理失败时评测直接失败。",
        "- 范围：仅检索，不调用生成 LLM。",
        "",
    ]
    for mode in report["modes"]:
        lines.extend([
            f"## {MODE_LABELS[mode]}",
            "",
            "| 阶段 | " + " | ".join(compact_metrics) + " |",
            "| --- | " + " | ".join("---:" for _ in compact_metrics) + " |",
        ])
        for stage in STAGES:
            values = report["summary"][mode][stage]
            lines.append(
                f"| {stage} | "
                + " | ".join(f"{values[name]:.4f}" for name in compact_metrics)
                + " |"
            )

        failures = []
        for detail in report["details"]:
            final = detail["modes"][mode]["reranked"]
            if (
                final["metrics"]["section_hit@5"] < 1.0
                or final["metrics"]["answerable@5"] < 1.0
                or final["hard_negative_outranks"]
            ):
                failures.append((detail, final))
        lines.extend(["", f"### 重排失败案例（{len(failures)}）", ""])
        if not failures:
            lines.append("本轮没有章节 Top-5 未命中、证据缺失或难负例压过正确文档的案例。")
        else:
            for detail, final in failures:
                top_documents = [
                    f"{item['document_id']}::{item['section_path']}"
                    for item in final["results"]
                ]
                lines.append(
                    f"- `{detail['id']}` {detail['query']}：相关排名={final['relevant_rank']}；"
                    f"缺失证据={final['missing_evidence']}；Top-K={top_documents}"
                )
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    if not (
        settings.embedding_api_key
        and settings.embedding_model
        and settings.embedding_base_url
    ):
        raise RuntimeError("需要在配置中提供 Embedding API Key、模型和 Base URL")

    raw_embeddings = OpenAIEmbeddings(
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
    )
    embeddings = CachedQueryEmbeddings(raw_embeddings)
    reranker = create_reranker(settings.model_copy(update={"reranker_allow_heuristic_fallback": False}), enforce_model_scope=False)
    benchmark = OfflineHybridBenchmark(embeddings, reranker=reranker)
    await benchmark.build(BENCHMARK_ROOT / "manifest.json")

    payload = json.loads((BENCHMARK_ROOT / "questions.json").read_text(encoding="utf-8"))
    questions = payload["questions"][: args.max_questions or None]
    await embeddings.preload_queries([question["query"] for question in questions])
    modes = (
        ("no_model_filter", "metadata_filtered")
        if args.mode == "both"
        else (args.mode,)
    )
    evaluated = await evaluate(
        benchmark,
        questions,
        modes=modes,
        top_k=args.top_k,
        candidate_limit=args.candidate_limit,
    )
    manifest = json.loads((BENCHMARK_ROOT / "manifest.json").read_text(encoding="utf-8"))
    report = {
        "benchmark_version": payload["benchmark_version"],
        "document_count": len(manifest["documents"]),
        "question_count": len(questions),
        "chunk_count": len(benchmark.chunks),
        "top_k": args.top_k,
        "candidate_limit": args.candidate_limit,
        "reranker": {
            "type": "bge_cross_encoder",
            "model": settings.reranker_model,
            "device": reranker._device,
            "batch_size": settings.reranker_batch_size,
            "max_length": settings.reranker_max_length,
            "heuristic_fallback": False,
            "enforce_model_scope": False,
        },
        "modes": list(modes),
        "metric_definitions": {
            "document_hit@k": "Top-K 至少出现一个正确 document_id 的题目比例",
            "section_hit@k": "Top-K 至少出现一个正确文档且正确章节的题目比例",
            "evidence_recall@k": "正确文档与章节的 Top-K 切片覆盖标准证据片段的比例",
            "context_precision@5": "Top-5 中属于正确文档与章节且包含至少一个标准证据片段的 Chunk 数量除以 5",
            "answerable@5": "Top-5 正确目标切片覆盖全部标准证据的题目比例",
            "mrr": "第一个正确文档且正确章节结果排名的平均倒数",
            "ndcg@5": "按唯一标准目标计算的章节级 nDCG@5",
            "hard_negative_outrank_rate": "相似错误文档排在正确文档之前的题目比例，越低越好",
        },
        **evaluated,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, args.output.with_suffix(".md"))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="BGE 重排设备；auto 在 CUDA 可用时自动使用 GPU。",
    )
    parser.add_argument(
        "--mode",
        choices=("both", "no_model_filter", "metadata_filtered"),
        default="both",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=0,
        help="仅用于快速试跑；0 表示全部问题。",
    )
    args = parser.parse_args()
    if args.top_k < 5:
        parser.error("--top-k 不能小于 5，因为报告固定计算到 @5")
    if args.candidate_limit < args.top_k:
        parser.error("--candidate-limit 不能小于 --top-k")
    if args.max_questions < 0:
        parser.error("--max-questions 不能为负数")
    return args


def main() -> None:
    args = parse_args()
    report = asyncio.run(run(args))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report={args.output.resolve()}")


if __name__ == "__main__":
    main()
