"""解析、切块并把本地知识文档写入 Milvus 混合索引。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


# 保证中文告警和路径在 Windows 终端中不会乱码或触发编码异常。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from core.knowledge import get_technical_knowledge_store  # noqa: E402
from core.knowledge.ingestion import DocumentPreprocessor  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    parsed = DocumentPreprocessor().parse(args.source, document_id=args.document_id)
    store = await get_technical_knowledge_store()
    if not store.available:
        raise RuntimeError("Milvus 或 Embedding 服务不可用，文档未入库")
    try:
        result = await store.ingest_document(
            parsed,
            document_type=args.document_type,
            product_model=args.product_model,
            category=args.category,
            version=args.version,
        )
        print(json.dumps({**result, "warnings": parsed.warnings}, ensure_ascii=False, indent=2))
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="本地 PDF、DOCX 或 Markdown")
    parser.add_argument("--document-id", required=True, help="稳定文档 ID")
    parser.add_argument(
        "--document-type",
        required=True,
        choices=["product_manual", "diagnostic_guide", "after_sales_policy", "faq"],
    )
    parser.add_argument("--product-model", default="", help="标准化商品型号；通用文档留空")
    parser.add_argument("--category", default="general", help="标准化商品类目")
    parser.add_argument("--version", default="", help="文档版本")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
