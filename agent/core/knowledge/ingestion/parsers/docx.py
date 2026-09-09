"""DOCX 到规范化语义块的解析器。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from ..cleaning import normalize_text
from ..models import BlockType, DocumentBlock, TableData


def _iter_body_items(document: Any) -> Iterator[tuple[str, Any]]:
    """按 OOXML 原始顺序交替遍历段落和表格。"""

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield "paragraph", Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield "table", Table(child, document)


def _heading_level(paragraph: Any) -> int | None:
    style_name = (paragraph.style.name if paragraph.style else "").strip()
    match = re.search(r"(?:Heading|标题)\s*([1-6])", style_name, re.I)
    if match:
        return int(match.group(1))

    # 一些模板没有使用标准标题样式，但会直接设置 outlineLvl。
    outline = paragraph._p.pPr
    if outline is not None and outline.outlineLvl is not None:
        return int(outline.outlineLvl.val) + 1
    return None


def _is_list(paragraph: Any) -> bool:
    style_name = (paragraph.style.name if paragraph.style else "").casefold()
    paragraph_properties = paragraph._p.pPr
    has_numbering = bool(paragraph_properties is not None and paragraph_properties.numPr is not None)
    return has_numbering or "list" in style_name or "列表" in style_name


def _table_to_data(table: Any, title: str = "") -> tuple[TableData, str]:
    matrix = [
        [normalize_text(cell.text, preserve_newlines=True) for cell in row.cells]
        for row in table.rows
    ]
    if not matrix:
        return TableData(headers=[], rows=[], title=title), ""
    headers, rows = matrix[0], matrix[1:]
    width = max([len(headers), *(len(row) for row in rows)], default=0)
    headers = (headers + [""] * width)[:width]
    rows = [(row + [""] * width)[:width] for row in rows]
    lines = ([f"表：{title}"] if title else []) + [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]
    return (
        TableData(
            headers=headers,
            rows=rows,
            title=title,
            row_number_end=len(rows),
        ),
        "\n".join(lines),
    )


def parse_docx(path: Path) -> tuple[list[DocumentBlock], dict, list[str]]:
    """读取 Word 标题样式、列表和表格；不伪造不稳定的分页信息。"""

    from docx import Document

    document = Document(path)
    blocks: list[DocumentBlock] = []
    pending_caption = ""
    list_items: list[str] = []

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

    def flush_list() -> None:
        if list_items:
            append_block(BlockType.LIST, "\n".join(list_items))
            list_items.clear()

    for item_type, item in _iter_body_items(document):
        if item_type == "table":
            flush_list()
            table_data, markdown = _table_to_data(item, pending_caption)
            append_block(BlockType.TABLE, markdown, table=table_data)
            pending_caption = ""
            continue

        text = normalize_text(item.text)
        if not text:
            flush_list()
            continue
        style_name = (item.style.name if item.style else "").casefold()
        if "caption" in style_name or "题注" in style_name or re.match(r"^(?:表|Table)\s*\d", text, re.I):
            flush_list()
            pending_caption = re.sub(r"^(?:表|Table)\s*[\d.\-]*[:：]?\s*", "", text, flags=re.I)
            continue

        level = _heading_level(item)
        if level:
            flush_list()
            append_block(BlockType.HEADING, text, heading_level=level)
        elif _is_list(item):
            # Word 的编号显示值不总是保存在段落文本中，因此保留条目边界而不猜编号。
            list_items.append(text)
        else:
            flush_list()
            block_type = BlockType.WARNING if re.match(r"^(?:警告|WARNING)\b", text, re.I) else BlockType.PARAGRAPH
            append_block(block_type, text)

    flush_list()
    warnings = ["DOCX 分页由渲染器决定，预处理结果不提供伪造页码。"]
    return blocks, {"pagination_reliable": False}, warnings
