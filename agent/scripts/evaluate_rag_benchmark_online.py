"""把合成文档真实写入 Milvus 并执行端到端 RAG 检索基准。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from core.knowledge import get_technical_knowledge_store  # noqa: E402
from core.knowledge.evaluation import evaluate_online_store  # noqa: E402
from core.knowledge.ingestion import DocumentPreprocessor  # noqa: E402


BENCHMARK_ROOT = AGENT_ROOT / "data" / "knowledge" / "benchmark"


def _write_markdown(report: dict, output: Path) -> None:
    metrics = report["summary"]
    failures = [
        detail
        for detail in report["details"]
        if detail["relevant_rank"] != 1 or detail["missing_facts"]
    ]
    lines = [
        "# Milvus 在线 RAG 全链路基准",
        "",
        f"- 基准版本：`{report['benchmark_version']}`",
        f"- 模式：`{report['mode']}`",
        f"- 标准问题：{report['question_count']}",
        f"- 实际入库切片：{report['ingestion']['chunk_count']}",
        f"- Milvus 集合：`{report['collection']}`",
        "",
        "| top1 | hit3 | recall3 | mrr | ndcg3 | fact_recall3 | answerable3 | model_leakage3 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| " + " | ".join(f"{metrics[name]:.4f}" for name in metrics) + " |",
        "",
        "## 失败案例",
        "",
    ]
    if not failures:
        lines.append("本轮没有 Top-1 错误或关键事实缺失。")
    else:
        for detail in failures:
            lines.append(
                f"- `{detail['id']}` {detail['question']}：相关排名={detail['relevant_rank']}，"
                f"缺失事实={detail['missing_facts']}"
            )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(output: Path) -> dict:
    manifest = json.loads((BENCHMARK_ROOT / "manifest.json").read_text(encoding="utf-8"))
    store = await get_technical_knowledge_store()
    if not store.available:
        raise RuntimeError("Milvus 或 Embedding 服务不可用")
    preprocessor = DocumentPreprocessor()
    ingestion_results = []
    try:
        for item in manifest["documents"]:
            parsed = preprocessor.parse(
                BENCHMARK_ROOT / item["path"], document_id=item["document_id"]
            )
            ingestion_results.append(
                await store.ingest_document(
                    parsed,
                    document_type=item["document_type"],
                    product_model=item.get("product_model", ""),
                    category=item.get("category", "general"),
                    version=item.get("version", ""),
                )
            )
        # 强制落盘并刷新统计，避免 get_collection_stats 返回尚未更新的 0。
        store.client.flush(store.collection)
        report = await evaluate_online_store(
            store, BENCHMARK_ROOT / "questions.json", top_k=5
        )
        report["collection"] = store.collection
        report["ingestion"] = {
            "document_count": len(ingestion_results),
            "chunk_count": sum(item["chunk_count"] for item in ingestion_results),
            "documents": ingestion_results,
        }
        logical_total = store.client.query(
            collection_name=store.collection,
            filter="",
            output_fields=["count(*)"],
        )[0]["count(*)"]
        benchmark_total = store.client.query(
            collection_name=store.collection,
            filter='document_id like "bench_%"',
            output_fields=["count(*)"],
        )[0]["count(*)"]
        report["collection_stats"] = {
            "logical_row_count": int(logical_total),
            "benchmark_row_count": int(benchmark_total),
            # 原始统计可能暂时包含尚未压缩的删除版本，仅供排障。
            "storage_reported_row_count": int(
                store.client.get_collection_stats(store.collection).get("row_count", 0)
            ),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_markdown(report, output.with_suffix(".md"))
        return report
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=AGENT_ROOT / "runtime" / "rag_online_benchmark_report.json",
    )
    args = parser.parse_args()
    report = asyncio.run(run(args.output))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(report["ingestion"], ensure_ascii=False, indent=2))
    print(f"collection_stats={report['collection_stats']} output={args.output.resolve()}")


if __name__ == "__main__":
    main()
