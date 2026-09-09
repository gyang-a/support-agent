"""文档预处理阶段使用的规范化中间结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class BlockType(StrEnum):
    """解析后保留的语义块类型。"""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    NOTE = "note"
    WARNING = "warning"


@dataclass(slots=True)
class TableData:
    """保留表格结构，避免在清洗阶段退化成不可恢复的纯文本。"""

    headers: list[str]
    rows: list[list[str]]
    title: str = ""
    row_number_start: int = 1
    row_number_end: int = 0


@dataclass(slots=True)
class DocumentBlock:
    """规范化文档中的一个有序语义块。"""

    block_id: str
    block_type: BlockType
    text: str
    order: int
    raw_text: str = ""
    heading_level: int | None = None
    heading_path: list[str] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    printed_page_start: str | None = None
    printed_page_end: str | None = None
    table: TableData | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def section(self) -> str:
        return self.heading_path[-1] if self.heading_path else ""

    @property
    def section_path(self) -> str:
        return " > ".join(self.heading_path)


@dataclass(slots=True)
class ParsedDocument:
    """可序列化、可重建且尚未切片的清洗结果。"""

    document_id: str
    source_path: str
    source_type: str
    source_hash: str
    title: str
    blocks: list[DocumentBlock]
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    parser_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, output_path: str | Path) -> None:
        """将中间结构写入 UTF-8 JSON，供人工检查或后续切片使用。"""

        import json

        Path(output_path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
