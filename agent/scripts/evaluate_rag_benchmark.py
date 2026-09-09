"""运行合成 Markdown 知识库的离线 RAG 检索基准。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from langchain_openai import OpenAIEmbeddings  # noqa: E402

from config import get_settings  # noqa: E402
from core.knowledge.evaluation import OfflineHybridBenchmark, evaluate_questions  # noqa: E402
from core.knowledge.reranking import BgeCrossEncoderReranker  # noqa: E402


BENCHMARK_ROOT = AGENT_ROOT / "data" / "knowledge" / "benchmark"


def _write_markdown_report(report: dict, path: Path) -> None:
    """生成便于人工查看和版本对比的简明 Markdown 报告。"""

    labels = {
        "dense": "Dense",
        "bm25": "BM25",
        "hybrid_rrf": "Dense + BM25 + RRF",
        "reranked": "最终二阶段重排",
    }
    metric_names = ["top1", "hit3", "recall3", "mrr", "ndcg3", "fact_recall3", "answerable3", "model_leakage3"]
    lines = [
        "# RAG 基准报告",
        "",
        f"- 基准版本：`{report['benchmark_version']}`",
        f"- 标准问题：{report['question_count']}",
        f"- 知识切片：{report['chunk_count']}",
        "- 说明：`answerable3` 衡量前三条证据能否覆盖标准答案全部关键事实，不是 LLM 最终措辞评分。",
        "",
        "| 阶段 | " + " | ".join(metric_names) + " |",
        "| --- | " + " | ".join(["---:"] * len(metric_names)) + " |",
    ]
    for stage, metrics in report["summary"].items():
        lines.append(
            f"| {labels.get(stage, stage)} | "
            + " | ".join(f"{metrics[name]:.4f}" for name in metric_names)
            + " |"
        )
    failures = [
        detail
        for detail in report["details"]
        if detail["stages"]["reranked"]["relevant_rank"] != 1
        or detail["stages"]["reranked"]["missing_facts"]
    ]
    lines.extend(["", "## 最终阶段失败案例", ""])
    if not failures:
        lines.append("本轮没有 Top-1 错误或关键事实缺失。")
    else:
        for detail in failures:
            final = detail["stages"]["reranked"]
            lines.append(
                f"- `{detail['id']}` {detail['question']}：相关排名={final['relevant_rank']}，"
                f"缺失事实={final['missing_facts']}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(output: Path) -> dict:
    settings = get_settings()
    if not (
        settings.embedding_api_key
        and settings.embedding_model
        and settings.embedding_base_url
    ):
        raise RuntimeError("离线基准仍需要配置 Embedding 服务")
    embeddings = OpenAIEmbeddings(
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
    )
    reranker = BgeCrossEncoderReranker(
        model_name=settings.reranker_model,
        device="cpu",
        batch_size=settings.reranker_batch_size,
        max_length=settings.reranker_max_length,
        cache_dir=settings.reranker_cache_dir,
        allow_heuristic_fallback=False,
    )
    benchmark = OfflineHybridBenchmark(embeddings, reranker=reranker)
    await benchmark.build(BENCHMARK_ROOT / "manifest.json")
    report = await evaluate_questions(benchmark, BENCHMARK_ROOT / "questions.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown_report(report, output.with_suffix(".md"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=AGENT_ROOT / "runtime" / "rag_benchmark_report.json",
    )
    args = parser.parse_args()
    report = asyncio.run(run(args.output))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"questions={report['question_count']} chunks={report['chunk_count']} output={args.output.resolve()}")


if __name__ == "__main__":
    main()
