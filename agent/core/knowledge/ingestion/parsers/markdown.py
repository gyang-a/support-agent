"""Markdown 到规范化语义块的轻量解析器。"""

from __future__ import annotations

import re
from pathlib import Path

from ..cleaning import repair_soft_line_breaks
from ..models import BlockType, DocumentBlock, TableData


_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT = re.compile(r"^\s*(=+|-+)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+(.+)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _split_table_row(line: str) -> list[str]:
    value = line.strip().strip("|")
    # 支持常见的反斜杠转义竖线，避免单元格被错误拆开。
    cells = re.split(r"(?<!\\)\|", value)
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _table_to_markdown(headers: list[str], rows: list[list[str]], title: str = "") -> str:
    width = max([len(headers), *(len(row) for row in rows)], default=0)
    padded_headers = (headers + [""] * width)[:width]
    padded_rows = [(row + [""] * width)[:width] for row in rows]
    lines = []
    if title:
        lines.append(f"表：{title}")
    lines.extend(
        [
            "| " + " | ".join(padded_headers) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
    )
    lines.extend("| " + " | ".join(row) + " |" for row in padded_rows)
    return "\n".join(lines)


def parse_markdown(path: Path) -> tuple[list[DocumentBlock], dict, list[str]]:
    """保留标题、段落、列表、代码块和 GFM 表格。"""

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    blocks: list[DocumentBlock] = []
    paragraph: list[str] = []
    index = 0

    def append_block(block_type: BlockType, text: str, **kwargs) -> None:
        blocks.append(
            DocumentBlock(
                block_id="",
                block_type=block_type,
                text=text,
                raw_text=text,
                order=len(blocks),
                **kwargs,
            )
        )

    def flush_paragraph() -> None:
        if paragraph:
            append_block(BlockType.PARAGRAPH, repair_soft_line_breaks(paragraph))
            paragraph.clear()

    # YAML front matter属于文档级配置，不作为消费者可检索正文。
    if lines and lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
            index = end + 1
        except ValueError:
            index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        heading = _ATX_HEADING.match(stripped)
        if heading:
            flush_paragraph()
            append_block(
                BlockType.HEADING,
                heading.group(2),
                heading_level=len(heading.group(1)),
            )
            index += 1
            continue

        if index + 1 < len(lines) and stripped and _SETEXT.fullmatch(lines[index + 1]):
            flush_paragraph()
            append_block(
                BlockType.HEADING,
                stripped,
                heading_level=1 if lines[index + 1].lstrip().startswith("=") else 2,
            )
            index += 2
            continue

        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph()
            fence = stripped[:3]
            language = stripped[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(fence):
                code_lines.append(lines[index])
                index += 1
            index += 1 if index < len(lines) else 0
            append_block(BlockType.CODE, "\n".join(code_lines), metadata={"language": language})
            continue

        if index + 1 < len(lines) and "|" in line and _TABLE_SEPARATOR.fullmatch(lines[index + 1]):
            flush_paragraph()
            headers = _split_table_row(line)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_table_row(lines[index]))
                index += 1
            title = ""
            if blocks and blocks[-1].block_type == BlockType.PARAGRAPH:
                match = re.fullmatch(r"(?:表|Table)\s*[\d.\-]*[:：]?\s*(.+)", blocks[-1].text, re.I)
                if match:
                    title = match.group(1).strip()
                    blocks.pop()
            table = TableData(
                headers=headers,
                rows=rows,
                title=title,
                row_number_end=len(rows),
            )
            append_block(BlockType.TABLE, _table_to_markdown(headers, rows, title), table=table)
            continue

        if _LIST_ITEM.match(line):
            flush_paragraph()
            items: list[str] = []
            while index < len(lines) and _LIST_ITEM.match(lines[index]):
                items.append(lines[index].strip())
                index += 1
            append_block(BlockType.LIST, "\n".join(items))
            continue

        if not stripped:
            flush_paragraph()
        elif stripped.startswith("<!--"):
            flush_paragraph()
            while index < len(lines) and "-->" not in lines[index]:
                index += 1
        else:
            paragraph.append(line)
        index += 1

    flush_paragraph()
    return blocks, {"pagination_reliable": False}, []
