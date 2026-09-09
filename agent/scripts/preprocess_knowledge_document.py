"""将本地知识文档解析为清洗后的结构化 JSON。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# 解析摘要可能包含中文路径，显式使用 UTF-8 便于日志和自动化工具读取。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from core.knowledge.ingestion import DocumentPreprocessor  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="待解析的 PDF、DOCX 或 Markdown 文件")
    parser.add_argument("--output", type=Path, help="输出 JSON；默认与源文件同名")
    parser.add_argument("--document-id", help="可选的稳定文档 ID")
    args = parser.parse_args()

    result = DocumentPreprocessor().parse(args.source, document_id=args.document_id)
    output = args.output or args.source.with_suffix(args.source.suffix + ".parsed.json")
    result.write_json(output)
    print(
        f"parsed={result.source_type} blocks={len(result.blocks)} "
        f"warnings={len(result.warnings)} output={output.resolve()}"
    )


if __name__ == "__main__":
    main()
