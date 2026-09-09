"""使用 PyMuPDF 恢复版面，并用 pdfplumber 增强表格提取。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from ..cleaning import (
    find_repeated_margin_lines,
    is_obvious_noise,
    join_inline_fragments,
    normalize_text,
    repair_soft_line_breaks,
    repeated_line_signature,
)
from ..models import BlockType, DocumentBlock, TableData


_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.、]\s*\S+")
_BULLET = re.compile(r"^[•●▪◦·]\s*")
_NUMBERED_LIST = re.compile(r"^\d+[.)、]\s+")
_CAPTION = re.compile(r"^(?:表|Table)\s*[\d.\-]*[:：]?\s*(.+)", re.I)


@dataclass(slots=True)
class _PdfWord:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    font_name: str
    bold: bool


@dataclass(slots=True)
class _PdfLine:
    text: str
    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    font_name: str
    bold: bool
    words: list[_PdfWord] = field(default_factory=list)
    in_margin: bool = False


def _inside_bbox(x: float, y: float, bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    return x0 <= x <= x1 and top <= y <= bottom


def _span_styles(page: Any) -> list[dict[str, Any]]:
    """读取 span 字体信息；word API 本身不返回字号和粗体属性。"""

    styles: list[dict[str, Any]] = []
    for block in page.get_text("dict", sort=True).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = str(span.get("font", ""))
                flags = int(span.get("flags", 0))
                styles.append(
                    {
                        "bbox": tuple(float(value) for value in span.get("bbox", (0, 0, 0, 0))),
                        "size": float(span.get("size", 0)),
                        "font_name": font_name,
                        "bold": "bold" in font_name.casefold() or bool(flags & 16),
                    }
                )
    return styles


def _word_style(word: tuple, styles: list[dict[str, Any]]) -> dict[str, Any]:
    center_x = (float(word[0]) + float(word[2])) / 2
    center_y = (float(word[1]) + float(word[3])) / 2
    matches = [style for style in styles if _inside_bbox(center_x, center_y, style["bbox"])]
    if matches:
        return min(matches, key=lambda item: item["bbox"][2] - item["bbox"][0])
    return {"size": 10.0, "font_name": "", "bold": False}


def _extract_lines(
    page: Any,
    page_number: int,
    table_bboxes: list[tuple[float, float, float, float]],
) -> list[_PdfLine]:
    """从 PyMuPDF word 坐标恢复视觉行，同时保留每个词的列位置。"""

    styles = _span_styles(page)
    words: list[_PdfWord] = []
    for raw in page.get_text("words", sort=True):
        x0, top, x1, bottom = map(float, raw[:4])
        center_x, center_y = (x0 + x1) / 2, (top + bottom) / 2
        if any(_inside_bbox(center_x, center_y, bbox) for bbox in table_bboxes):
            continue
        style = _word_style(raw, styles)
        text = normalize_text(str(raw[4]))
        if text:
            words.append(
                _PdfWord(
                    text=text,
                    x0=x0,
                    x1=x1,
                    top=top,
                    bottom=bottom,
                    size=float(style["size"]),
                    font_name=str(style["font_name"]),
                    bold=bool(style["bold"]),
                )
            )

    groups: list[list[_PdfWord]] = []
    for word in sorted(words, key=lambda item: (round(item.top, 1), item.x0)):
        if not groups or abs(median(item.top for item in groups[-1]) - word.top) > 3.0:
            groups.append([word])
        else:
            groups[-1].append(word)

    lines: list[_PdfLine] = []
    for group in groups:
        ordered = sorted(group, key=lambda item: item.x0)
        text = join_inline_fragments(item.text for item in ordered)
        if not text:
            continue
        sizes = [item.size for item in ordered if item.size > 0]
        bold_width = sum(item.x1 - item.x0 for item in ordered if item.bold)
        total_width = max(1.0, sum(item.x1 - item.x0 for item in ordered))
        top = min(item.top for item in ordered)
        bottom = max(item.bottom for item in ordered)
        fonts = {item.font_name for item in ordered}
        lines.append(
            _PdfLine(
                text=text,
                page=page_number,
                x0=min(item.x0 for item in ordered),
                x1=max(item.x1 for item in ordered),
                top=top,
                bottom=bottom,
                size=median(sizes) if sizes else 10.0,
                font_name=max(fonts, key=lambda name: sum(1 for item in ordered if item.font_name == name)),
                bold=bold_width / total_width >= 0.6,
                words=ordered,
                in_margin=top <= float(page.rect.height) * 0.12
                or bottom >= float(page.rect.height) * 0.88,
            )
        )
    return lines


def _extract_ruled_tables(
    path: Path,
) -> tuple[list[list[tuple[float, tuple[float, float, float, float], TableData]]], int]:
    """pdfplumber 对有边框表格稳定，保留它作为专用表格解析器。"""

    import pdfplumber

    pages: list[list[tuple[float, tuple[float, float, float, float], TableData]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_tables: list[tuple[float, tuple[float, float, float, float], TableData]] = []
            for table in page.find_tables():
                matrix = [
                    [normalize_text(cell or "", preserve_newlines=True) for cell in row]
                    for row in (table.extract() or [])
                ]
                if not matrix:
                    continue
                bbox = tuple(float(value) for value in table.bbox)
                headers, rows = matrix[0], matrix[1:]
                page_tables.append(
                    (
                        bbox[1],
                        bbox,
                        TableData(headers=headers, rows=rows, row_number_end=len(rows)),
                    )
                )
            pages.append(page_tables)
        return pages, len(pdf.pages)


def _heading_level(
    line: _PdfLine,
    *,
    body_size: float,
    left_edge: float,
    first_content_line: bool,
    page_number: int,
) -> int | None:
    numbered = _NUMBERED_HEADING.match(line.text)
    if numbered and line.bold and line.x0 <= left_edge + 12:
        return min(2 + numbered.group(1).count("."), 6)
    if first_content_line and page_number == 1 and line.bold and line.x0 <= left_edge + 12:
        return 1
    if line.size >= body_size * 1.18 and len(line.text) <= 100:
        return 1
    return None


def _column_cells(words: list[_PdfWord], anchors: list[float]) -> list[str]:
    cells: list[list[str]] = [[] for _ in anchors]
    for word in words:
        column = 0
        # 表头起始坐标就是列边界；不能使用中点，否则“电量低于 15%”中
        # 靠近下一列的 15% 会被错误分配到“建议操作”列。
        while column + 1 < len(anchors) and word.x0 >= anchors[column + 1] - 1:
            column += 1
        cells[column].append(word.text)
    return [join_inline_fragments(cell) for cell in cells]


def _try_borderless_table(
    lines: list[_PdfLine],
    start: int,
    *,
    heading_levels: dict[int, int],
    page_width: float,
    title: str,
) -> tuple[int, TableData] | None:
    """仅在粗体表头、多列对齐和连续数据行同时成立时识别无边框表格。"""

    header = lines[start]
    if start in heading_levels or not header.bold or not 2 <= len(header.words) <= 6:
        return None
    anchors = [word.x0 for word in header.words]
    if any(right - left < 24 for left, right in zip(anchors, anchors[1:])):
        return None

    rows: list[list[str]] = []
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if index in heading_levels or line.x1 > page_width * 0.80:
            break
        cells = _column_cells(line.words, anchors)
        populated = sum(bool(cell) for cell in cells)
        if populated < 2 or abs(line.x0 - anchors[0]) > 15:
            break
        rows.append(cells)
        index += 1

    if not rows:
        return None
    return index, TableData(
        headers=_column_cells(header.words, anchors),
        rows=rows,
        title=re.sub(r"^\d+(?:\.\d+)*[.、]\s*", "", title).strip(),
        row_number_end=len(rows),
    )


def _table_markdown(table: TableData) -> str:
    width = max([len(table.headers), *(len(row) for row in table.rows)], default=0)
    headers = (table.headers + [""] * width)[:width]
    rows = [(row + [""] * width)[:width] for row in table.rows]
    lines = ([f"表：{table.title}"] if table.title else []) + [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]
    return "\n".join(lines)


def parse_pdf(path: Path) -> tuple[list[DocumentBlock], dict, list[str]]:
    """解析文本型 PDF；扫描件会返回明确告警，不静默产出空知识。"""

    import pymupdf

    ruled_tables, plumber_page_count = _extract_ruled_tables(path)
    page_lines: list[list[_PdfLine]] = []
    page_sizes: list[tuple[float, float]] = []
    with pymupdf.open(path) as pdf:
        page_count = len(pdf)
        for page_index, page in enumerate(pdf):
            table_bboxes = [item[1] for item in ruled_tables[page_index]]
            page_lines.append(_extract_lines(page, page_index + 1, table_bboxes))
            page_sizes.append((float(page.rect.width), float(page.rect.height)))

    if plumber_page_count != page_count:
        raise ValueError("PyMuPDF 与 pdfplumber 读取到的 PDF 页数不一致")

    margin_lines = [[line.text for line in lines if line.in_margin] for lines in page_lines]
    repeated = find_repeated_margin_lines(margin_lines)
    all_lines = [line for lines in page_lines for line in lines]
    body_size = median([line.size for line in all_lines if not line.bold] or [10.0])
    blocks: list[DocumentBlock] = []
    current_heading = ""

    def append_block(
        block_type: BlockType,
        text: str,
        *,
        page_number: int,
        line: _PdfLine | None = None,
        heading_level: int | None = None,
        table: TableData | None = None,
    ) -> None:
        blocks.append(
            DocumentBlock(
                block_id="",
                block_type=block_type,
                text=text,
                raw_text=text,
                order=len(blocks),
                heading_level=heading_level,
                page_start=page_number,
                page_end=page_number,
                table=table,
                metadata={
                    "font_size": round(line.size, 2),
                    "font_name": line.font_name,
                    "bbox": [round(line.x0, 2), round(line.top, 2), round(line.x1, 2), round(line.bottom, 2)],
                }
                if line is not None
                else {},
            )
        )

    for page_index, raw_lines in enumerate(page_lines, start=1):
        page_width, _ = page_sizes[page_index - 1]
        lines = [
            line
            for line in raw_lines
            if not is_obvious_noise(line.text)
            and not (line.in_margin and repeated_line_signature(line.text) in repeated)
        ]
        if not lines and not ruled_tables[page_index - 1]:
            continue
        left_edge = min((line.x0 for line in lines), default=0.0)
        heading_levels: dict[int, int] = {}
        for index, line in enumerate(lines):
            level = _heading_level(
                line,
                body_size=body_size,
                left_edge=left_edge,
                first_content_line=index == 0,
                page_number=page_index,
            )
            if level:
                heading_levels[index] = level

        custom_tables: dict[int, tuple[int, TableData]] = {}
        section = current_heading
        scan = 0
        while scan < len(lines):
            if scan in heading_levels:
                section = lines[scan].text
                scan += 1
                continue
            found = _try_borderless_table(
                lines,
                scan,
                heading_levels=heading_levels,
                page_width=page_width,
                title=section,
            )
            if found:
                custom_tables[scan] = found
                scan = found[0]
            else:
                scan += 1

        consumed = {
            index
            for start, (end, _) in custom_tables.items()
            for index in range(start, end)
        }
        events: list[tuple[float, str, Any]] = []
        for index, line in enumerate(lines):
            if index in custom_tables:
                events.append((line.top, "table", custom_tables[index][1]))
            elif index not in consumed:
                events.append((line.top, "line", (index, line)))
        events.extend((top, "table", table) for top, _, table in ruled_tables[page_index - 1])

        paragraph_lines: list[_PdfLine] = []
        list_lines: list[_PdfLine] = []

        def flush_paragraph() -> None:
            if not paragraph_lines:
                return
            text = repair_soft_line_breaks(line.text for line in paragraph_lines)
            kind = BlockType.NOTE if text.startswith(("注意", "提示")) else BlockType.PARAGRAPH
            append_block(kind, text, page_number=page_index, line=paragraph_lines[0])
            paragraph_lines.clear()

        def flush_list() -> None:
            if not list_lines:
                return
            rendered = []
            for item in list_lines:
                text = _BULLET.sub("", item.text).strip()
                rendered.append(f"- {text}" if not _NUMBERED_LIST.match(text) else text)
            append_block(BlockType.LIST, "\n".join(rendered), page_number=page_index, line=list_lines[0])
            list_lines.clear()

        for _, event_type, value in sorted(events, key=lambda item: item[0]):
            if event_type == "table":
                flush_paragraph()
                flush_list()
                table: TableData = value
                if not table.title and blocks:
                    caption = _CAPTION.match(blocks[-1].text)
                    if caption:
                        table.title = caption.group(1).strip()
                        blocks.pop()
                if not table.title:
                    table.title = re.sub(r"^\d+(?:\.\d+)*[.、]\s*", "", current_heading)
                append_block(BlockType.TABLE, _table_markdown(table), page_number=page_index, table=table)
                continue

            index, line = value
            if index in heading_levels:
                flush_paragraph()
                flush_list()
                current_heading = line.text
                append_block(
                    BlockType.HEADING,
                    line.text,
                    page_number=page_index,
                    line=line,
                    heading_level=heading_levels[index],
                )
                continue

            is_list = bool(_BULLET.match(line.text)) or (
                bool(_NUMBERED_LIST.match(line.text)) and line.x0 > left_edge + 8
            )
            if is_list:
                flush_paragraph()
                list_lines.append(line)
                continue

            flush_list()
            if paragraph_lines:
                previous = paragraph_lines[-1]
                # 只有上一行接近右页边时才视为排版换行；短行后开启新语义段落。
                wrapped = (
                    previous.x1 >= page_width * 0.82
                    and not previous.text.endswith(("。", "！", "？", "!", "?", "；", ";"))
                ) or previous.text.endswith(("-", "—"))
                if not wrapped:
                    flush_paragraph()
            paragraph_lines.append(line)

        flush_paragraph()
        flush_list()

    warnings: list[str] = []
    empty_pages = [
        page_number
        for page_number, (lines, tables) in enumerate(zip(page_lines, ruled_tables, strict=True), start=1)
        if not lines and not tables
    ]
    if not blocks and page_count:
        warnings.append("PDF 未提取到正文，可能是扫描件或文字层损坏；当前版本尚未启用 OCR。")
    elif empty_pages:
        warnings.append(
            "以下页面未提取到正文，可能是扫描页、空白页或文字层损坏："
            + ", ".join(map(str, empty_pages))
        )
    metadata = {
        "page_count": page_count,
        "pagination_reliable": True,
        "parser_engine": "pymupdf_layout+pdfplumber_tables",
        "pages_without_extractable_content": empty_pages,
        "removed_repeated_margin_signatures": sorted(repeated),
    }
    return blocks, metadata, warnings
