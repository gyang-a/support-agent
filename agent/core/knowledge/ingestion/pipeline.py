"""文档清洗与结构恢复的统一编排入口。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .cleaning import clean_and_structure_blocks
from .models import ParsedDocument
from .parsers import parse_docx, parse_markdown, parse_pdf


class UnsupportedDocumentType(ValueError):
    """文件扩展名不在当前预处理范围内。"""


class DocumentPreprocessor:
    """把本地 PDF、DOCX 或 Markdown 转成统一的规范化文档。"""

    _PARSERS = {
        ".pdf": parse_pdf,
        ".docx": parse_docx,
        ".md": parse_markdown,
        ".markdown": parse_markdown,
    }

    def parse(self, source_path: str | Path, *, document_id: str | None = None) -> ParsedDocument:
        path = Path(source_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"文档不存在：{path}")
        parser = self._PARSERS.get(path.suffix.casefold())
        if parser is None:
            supported = ", ".join(sorted(self._PARSERS))
            raise UnsupportedDocumentType(f"暂不支持 {path.suffix or '无扩展名'}，当前支持：{supported}")

        source_bytes = path.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        blocks, metadata, warnings = parser(path)
        blocks = clean_and_structure_blocks(blocks)
        title = next(
            (block.text for block in blocks if block.heading_level == 1),
            path.stem,
        )
        return ParsedDocument(
            document_id=document_id or f"doc_{source_hash[:16]}",
            source_path=str(path),
            source_type=path.suffix.casefold().lstrip("."),
            source_hash=source_hash,
            title=title,
            blocks=blocks,
            metadata=metadata,
            warnings=warnings,
        )
