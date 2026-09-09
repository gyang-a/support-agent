"""按 manifest 批量解析、切块并写入当前正式 Milvus 知识集合。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from core.knowledge import get_technical_knowledge_store  # noqa: E402
from core.knowledge.ingestion import DocumentPreprocessor  # noqa: E402


async def run(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = payload.get("documents", [])
    if not documents:
        raise ValueError("manifest 中没有 documents")

    store = await get_technical_knowledge_store()
    if not store.available:
        raise RuntimeError("Milvus 或 Embedding 服务不可用，文档未入库")

    preprocessor = DocumentPreprocessor()
    results = []
    try:
        for index, item in enumerate(documents, 1):
            source = (manifest_path.parent / item["path"]).resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            parsed = await asyncio.to_thread(
                preprocessor.parse,
                source,
                document_id=item["document_id"],
            )
            result = await store.ingest_document(
                parsed,
                document_type=item["document_type"],
                product_model=item.get("product_model", ""),
                category=item.get("category", "general"),
                version=item.get("version", payload.get("benchmark_version", "")),
                status="published",
            )
            results.append(result)
            print(
                f"[{index}/{len(documents)}] {item['document_id']}: "
                f"chunks={result['chunk_count']} inserted={result.get('insert_count', 0)} "
                f"unchanged={result.get('unchanged', False)}",
                flush=True,
            )
        await asyncio.to_thread(store.client.flush, store.collection)
        total = store.client.query(
            collection_name=store.collection,
            filter="",
            output_fields=["count(*)"],
        )[0]["count(*)"]
        return {
            "collection": store.collection,
            "manifest": str(manifest_path),
            "document_count": len(results),
            "chunk_count": sum(item["chunk_count"] for item in results),
            "insert_count": sum(item.get("insert_count", 0) for item in results),
            "collection_logical_row_count": int(total),
        }
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    result = asyncio.run(run(parser.parse_args().manifest))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
