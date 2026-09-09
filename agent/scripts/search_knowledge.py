"""从命令行验证 Milvus 混合检索与重排结果。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


# Windows PowerShell 可能默认使用 GBK；检索结果包含项目符号等字符时会打印失败。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from core.knowledge import get_technical_knowledge_store  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    store = await get_technical_knowledge_store()
    if not store.available:
        raise RuntimeError("Milvus 或 Embedding 服务不可用")
    try:
        results = await store.search(
            args.query,
            document_type=args.document_type,
            product_model=args.product_model,
            category=args.category,
            limit=args.limit,
        )
        if not args.include_content:
            # 默认只展示用于核验的证据摘要；完整切片可按需显式打开，
            # 避免 content 与 excerpt 在控制台中重复出现。
            results = [
                {key: value for key, value in result.items() if key != "content"}
                for result in results
            ]
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="已经结合会话改写过的独立查询")
    parser.add_argument("--document-type", required=True)
    parser.add_argument("--product-model", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="同时输出完整切片正文；默认仅展示 excerpt",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
