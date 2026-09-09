"""RAG 标准问答与知识文档标注一致性测试。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.knowledge.ingestion import DocumentPreprocessor, StructuredDocumentChunker


BENCHMARK_ROOT = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "benchmark"


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def test_benchmark_targets_and_required_facts_exist_in_source_chunks() -> None:
    manifest = json.loads((BENCHMARK_ROOT / "manifest.json").read_text(encoding="utf-8"))
    questions = json.loads((BENCHMARK_ROOT / "questions.json").read_text(encoding="utf-8"))[
        "questions"
    ]
    chunks = []
    for item in manifest["documents"]:
        document = DocumentPreprocessor().parse(
            BENCHMARK_ROOT / item["path"], document_id=item["document_id"]
        )
        chunks.extend(
            StructuredDocumentChunker().chunk(
                document,
                document_type=item["document_type"],
                product_model=item["product_model"],
                category=item["category"],
                version=item["version"],
            )
        )

    assert len(questions) == 18
    assert len({question["id"] for question in questions}) == len(questions)
    for question in questions:
        evidence = []
        for target in question["relevant_targets"]:
            matching = [
                chunk
                for chunk in chunks
                if chunk.document_id == target["document_id"]
                and target["section"] in chunk.section_path
            ]
            assert matching, f"{question['id']} 的目标章节不存在：{target}"
            evidence.extend(chunk.content for chunk in matching)
        normalized_evidence = _normalized("\n".join(evidence))
        for fact in question["required_facts"]:
            assert _normalized(fact) in normalized_evidence, (
                f"{question['id']} 标注事实不在目标证据中：{fact}"
            )
