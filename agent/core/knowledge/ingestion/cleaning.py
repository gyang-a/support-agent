"""跨文件类型复用的文本清洗和章节路径恢复。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from .models import BlockType, DocumentBlock


_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HORIZONTAL_SPACE = re.compile(r"[\t\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000 ]+")
_PAGE_NUMBER = re.compile(
    r"^(?:第\s*)?[—–-]?\s*(?:page\s*)?\d{1,5}\s*(?:页|/\s*\d{1,5})?\s*[—–-]?$",
    re.IGNORECASE,
)
_SENTENCE_END = set("。！？!?；;：:")
_CJK = re.compile(r"[\u3400-\u9fff]$")
_CJK_START = re.compile(r"^[\u3400-\u9fff]")


def normalize_text(text: str, *, preserve_newlines: bool = False) -> str:
    """做保守规范化；不改写型号、单位、版本号和政策原文。"""

    value = unicodedata.normalize("NFC", text or "")
    value = _ZERO_WIDTH.sub("", value)
    value = _CONTROL.sub("", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    if preserve_newlines:
        return "\n".join(line for line in lines if line).strip()
    return " ".join(line for line in lines if line).strip()


def join_inline_fragments(parts: Iterable[str]) -> str:
    """连接版面提取出的词；中文之间不强行插入空格。"""

    result = ""
    for raw_part in parts:
        part = normalize_text(raw_part)
        if not part:
            continue
        if not result:
            result = part
        elif _CJK.search(result) and _CJK_START.search(part):
            result += part
        elif result.endswith(("-", "/", "(", "（")) or part.startswith(
            ("-", "/", ")", "）", ",", "，", ".", "。", ":", "：", ";", "；")
        ):
            result += part
        else:
            result += " " + part
    return result


def repair_soft_line_breaks(lines: Iterable[str]) -> str:
    """修复 PDF/Markdown 段落中的排版换行，同时保守保留语义边界。"""

    cleaned = [normalize_text(line) for line in lines]
    cleaned = [line for line in cleaned if line]
    if not cleaned:
        return ""

    result = cleaned[0]
    for line in cleaned[1:]:
        # 英文排版常用行尾连字符断词；只在两侧都是字母时移除连字符。
        if re.search(r"[A-Za-z]-$", result) and re.match(r"^[a-z]", line):
            result = result[:-1] + line
        elif result.endswith(("，", ",", "、")) and _CJK_START.search(line):
            # 中文逗号后的视觉换行仍属于同一句，不引入多余空格。
            result += line
        elif result[-1] in _SENTENCE_END:
            result += " " + line
        elif _CJK.search(result) and _CJK_START.search(line):
            result += line
        else:
            result += " " + line
    return result.strip()


def repeated_line_signature(text: str) -> str:
    """忽略变化页码后比较跨页重复行，用于识别页眉、页脚和水印。"""

    normalized = normalize_text(text).casefold()
    normalized = re.sub(r"\d+", "#", normalized)
    return re.sub(r"\s+", " ", normalized).strip(" -—–|#")


def find_repeated_margin_lines(
    margin_lines_by_page: list[list[str]], *, minimum_ratio: float = 0.5
) -> set[str]:
    """返回出现在至少一半页面上的边缘行签名。"""

    page_count = len(margin_lines_by_page)
    if page_count < 2:
        return set()
    counter: Counter[str] = Counter()
    for lines in margin_lines_by_page:
        counter.update({repeated_line_signature(line) for line in lines if line})
    required = max(2, int(page_count * minimum_ratio + 0.999))
    return {signature for signature, count in counter.items() if signature and count >= required}


def is_obvious_noise(text: str, *, repeated_signatures: set[str] | None = None) -> bool:
    """只删除高置信噪声，短警告和数值参数仍会保留。"""

    normalized = normalize_text(text)
    if not normalized or _PAGE_NUMBER.fullmatch(normalized):
        return True
    return bool(
        repeated_signatures
        and repeated_line_signature(normalized) in repeated_signatures
    )


def clean_and_structure_blocks(blocks: Iterable[DocumentBlock]) -> list[DocumentBlock]:
    """规范化块内容、删除空块，并为正文绑定完整章节路径。"""

    cleaned: list[DocumentBlock] = []
    heading_stack: list[str] = []
    for block in blocks:
        preserve_newlines = block.block_type in {BlockType.TABLE, BlockType.CODE, BlockType.LIST}
        block.raw_text = block.raw_text or block.text
        block.text = normalize_text(block.text, preserve_newlines=preserve_newlines)
        if not block.text or is_obvious_noise(block.text):
            continue

        if block.block_type == BlockType.HEADING:
            level = max(1, min(block.heading_level or 1, 6))
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(block.text)
            block.heading_level = level
            block.heading_path = list(heading_stack)
        else:
            block.heading_path = list(heading_stack)

        block.order = len(cleaned)
        block.block_id = f"block_{len(cleaned) + 1:05d}"
        cleaned.append(block)
    return cleaned
