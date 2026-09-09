"""章节感知的知识文档切块。"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .models import BlockType, DocumentBlock, ParsedDocument, TableData


_TOKEN_PART = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")


def estimate_tokens(text: str) -> int:
    """离线估算 token 数，避免 tiktoken 首次运行联网下载词表。

    中文字符和标点按一个 token 估算；连续英文/数字按约四字符一个 token。
    该估算偏保守，适合限制 chunk 上限，但不声称等同于具体 Embedding 模型。
    """

    total = 0
    for part in _TOKEN_PART.findall(text or ""):
        if re.fullmatch(r"[A-Za-z0-9_]+", part):
            total += max(1, math.ceil(len(part) / 4))
        else:
            total += 1
    return total


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """切块参数；token 数使用 ``estimate_tokens`` 的保守估算。"""

    target_tokens: int = 650
    max_tokens: int = 900
    overlap_tokens: int = 100
    table_intro_max_tokens: int = 120
    table_overlap_rows: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.target_tokens <= self.max_tokens:
            raise ValueError("target_tokens 必须位于 1 和 max_tokens 之间")
        if not 0 <= self.overlap_tokens < self.target_tokens:
            raise ValueError("overlap_tokens 必须小于 target_tokens")
        if self.table_intro_max_tokens < 0:
            raise ValueError("table_intro_max_tokens 不能为负数")
        if self.table_overlap_rows < 0:
            raise ValueError("table_overlap_rows 不能为负数")


@dataclass(slots=True)
class KnowledgeChunk:
    """可直接写入向量库的知识切片。"""

    chunk_id: str
    document_id: str
    document_type: str
    title: str
    section: str
    section_path: str
    chunk_type: str
    content: str
    retrieval_text: str
    source_path: str
    version: str = ""
    status: str = "published"
    product_model: str = ""
    category: str = "general"
    page_start: int | None = None
    page_end: int | None = None
    table_row_start: int | None = None
    table_row_end: int | None = None
    parent_section_id: str = ""
    token_count: int = 0
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_langchain_document(self) -> Document:
        metadata = self.to_dict()
        metadata.pop("content")
        metadata.pop("retrieval_text")
        return Document(page_content=self.content, metadata=metadata)


class StructuredDocumentChunker:
    """优先尊重结构边界，仅对超长内容使用 LangChain 递归切分。"""

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(
        self,
        document: ParsedDocument,
        *,
        document_type: str,
        product_model: str = "",
        category: str = "general",
        version: str = "",
        status: str = "published",
    ) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        buffer: list[DocumentBlock] = []

        def emit_buffer(*, keep_overlap: bool) -> None:
            nonlocal buffer
            if not buffer:
                return
            chunks.append(
                self._make_chunk(
                    document,
                    buffer,
                    document_type=document_type,
                    product_model=product_model,
                    category=category,
                    version=version,
                    status=status,
                    chunk_type="text",
                )
            )
            buffer = self._overlap_tail(buffer) if keep_overlap else []

        for block in document.blocks:
            if block.block_type == BlockType.HEADING:
                # 标题已经通过 heading_path 绑定到后续块，不单独形成低信息量切片。
                emit_buffer(keep_overlap=False)
                continue
            if block.block_type == BlockType.TABLE and block.table is not None:
                intro_blocks = self._table_intro_blocks(buffer, block)
                if intro_blocks:
                    buffer = []
                else:
                    emit_buffer(keep_overlap=False)
                chunks.extend(
                    self._chunk_table(
                        document,
                        block,
                        intro_blocks=intro_blocks,
                        document_type=document_type,
                        product_model=product_model,
                        category=category,
                        version=version,
                        status=status,
                    )
                )
                continue

            if self._retrieval_token_count(document, [block]) > self.config.max_tokens:
                emit_buffer(keep_overlap=False)
                chunks.extend(
                    self._split_oversized_block(
                        document,
                        block,
                        document_type=document_type,
                        product_model=product_model,
                        category=category,
                        version=version,
                        status=status,
                    )
                )
                continue

            same_section = not buffer or buffer[-1].heading_path == block.heading_path
            projected = self._retrieval_token_count(document, [*buffer, block])
            if buffer and (not same_section or projected > self.config.target_tokens):
                emit_buffer(keep_overlap=same_section)
            buffer.append(block)

        emit_buffer(keep_overlap=False)
        # chunk_id 必须由最终顺序和内容共同决定，重复执行同一文档结果稳定。
        for index, chunk in enumerate(chunks, start=1):
            digest = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
            chunk.content_hash = digest
            chunk.chunk_id = f"{document.document_id}__{index:05d}__{digest[:12]}"
        return chunks

    def _make_chunk(
        self,
        document: ParsedDocument,
        blocks: list[DocumentBlock],
        *,
        document_type: str,
        product_model: str,
        category: str,
        version: str,
        status: str,
        chunk_type: str,
        content: str | None = None,
        row_start: int | None = None,
        row_end: int | None = None,
    ) -> KnowledgeChunk:
        first, last = blocks[0], blocks[-1]
        section_path = first.section_path
        body = content if content is not None else self._join_blocks(blocks)
        # 标题和完整章节路径参与检索，但不重复出现在给最终用户展示的正文中。
        retrieval_text = "\n".join(
            part for part in [document.title, section_path, body] if part
        )
        page_values = [block.page_start for block in blocks if block.page_start is not None]
        page_end_values = [block.page_end for block in blocks if block.page_end is not None]
        parent_source = f"{document.document_id}\n{section_path or '__root__'}"
        return KnowledgeChunk(
            chunk_id="",
            document_id=document.document_id,
            document_type=document_type,
            title=document.title,
            section=first.section,
            section_path=section_path,
            chunk_type=chunk_type,
            content=body,
            retrieval_text=retrieval_text,
            source_path=document.source_path,
            version=version,
            status=status,
            product_model=product_model,
            category=category,
            page_start=min(page_values) if page_values else None,
            page_end=max(page_end_values) if page_end_values else None,
            table_row_start=row_start,
            table_row_end=row_end,
            parent_section_id=hashlib.sha256(parent_source.encode("utf-8")).hexdigest()[:24],
            token_count=estimate_tokens(retrieval_text),
            metadata={"source_block_ids": [block.block_id for block in blocks]},
        )

    @staticmethod
    def _join_blocks(blocks: list[DocumentBlock]) -> str:
        return "\n\n".join(block.text for block in blocks if block.text).strip()

    def _retrieval_token_count(
        self, document: ParsedDocument, blocks: list[DocumentBlock], content: str | None = None
    ) -> int:
        section_path = blocks[0].section_path if blocks else ""
        body = content if content is not None else self._join_blocks(blocks)
        return estimate_tokens("\n".join(part for part in [document.title, section_path, body] if part))

    def _splitter_for(self, document: ParsedDocument, block: DocumentBlock) -> RecursiveCharacterTextSplitter:
        prefix_tokens = estimate_tokens("\n".join(part for part in [document.title, block.section_path] if part))
        body_budget = max(16, self.config.max_tokens - prefix_tokens)
        overlap = min(self.config.overlap_tokens, max(0, body_budget - 1))
        return RecursiveCharacterTextSplitter(
            chunk_size=body_budget,
            chunk_overlap=overlap,
            length_function=estimate_tokens,
            separators=["\n\n", "\n", "。", "！", "？", "；", ". ", " ", ""],
            keep_separator="end",
        )

    def _overlap_tail(self, blocks: list[DocumentBlock]) -> list[DocumentBlock]:
        tail: list[DocumentBlock] = []
        for block in reversed(blocks):
            # 语义块本身超过重叠预算时不应整块复制，否则“约 100 token 重叠”
            # 会退化成把整页正文带进下一个切片。超长单块由递归切分器自行重叠。
            if estimate_tokens(block.text) > self.config.overlap_tokens:
                break
            if estimate_tokens(self._join_blocks([block, *tail])) > self.config.overlap_tokens:
                break
            tail.insert(0, block)
        return tail

    def _split_oversized_block(self, document: ParsedDocument, block: DocumentBlock, **scope) -> list[KnowledgeChunk]:
        return [
            self._make_chunk(
                document,
                [block],
                content=part,
                chunk_type=block.block_type.value,
                **scope,
            )
            for part in self._splitter_for(document, block).split_text(block.text)
            if part.strip()
        ]

    def _table_intro_blocks(
        self, buffer: list[DocumentBlock], table_block: DocumentBlock
    ) -> list[DocumentBlock]:
        """返回可与紧随表格合并的短介绍段落。"""

        if not buffer or buffer[-1].heading_path != table_block.heading_path:
            return []
        if any(
            block.block_type not in {BlockType.PARAGRAPH, BlockType.NOTE}
            for block in buffer
        ):
            return []
        intro_text = self._join_blocks(buffer)
        if not intro_text or estimate_tokens(intro_text) > self.config.table_intro_max_tokens:
            return []
        return list(buffer)

    def _chunk_table(
        self,
        document: ParsedDocument,
        block: DocumentBlock,
        *,
        intro_blocks: list[DocumentBlock] | None = None,
        **scope,
    ) -> list[KnowledgeChunk]:
        table = block.table
        assert table is not None
        intro_blocks = intro_blocks or []
        context_blocks = [*intro_blocks, block]
        intro_text = self._join_blocks(intro_blocks)
        full_content = "\n\n".join(part for part in (intro_text, block.text) if part)
        if (
            self._retrieval_token_count(
                document, context_blocks, content=full_content
            )
            <= self.config.max_tokens
        ):
            return [
                self._make_chunk(
                    document,
                    context_blocks,
                    content=full_content,
                    chunk_type="table",
                    row_start=table.row_number_start,
                    row_end=table.row_number_end,
                    **scope,
                )
            ]

        chunks: list[KnowledgeChunk] = []
        rows: list[tuple[int, list[str]]] = []
        prefix_tokens = estimate_tokens(
            "\n".join(part for part in [document.title, block.section_path] if part)
        )
        body_budget = max(16, self.config.max_tokens - prefix_tokens)

        def render(selected: list[tuple[int, list[str]]]) -> str:
            title = f"表：{table.title}" if table.title else "表格"
            headers = "| 行号 | " + " | ".join(table.headers) + " |"
            separator = "| --- | " + " | ".join(["---"] * len(table.headers)) + " |"
            body = [f"| {number} | " + " | ".join(row) + " |" for number, row in selected]
            table_text = "\n".join([title, headers, separator, *body])
            return "\n\n".join(part for part in (intro_text, table_text) if part)

        def emit_rows(*, keep_overlap: bool) -> None:
            if not rows:
                return
            selected = list(rows)
            content = render(selected)
            chunks.append(
                self._make_chunk(
                    document,
                    context_blocks,
                    content=content,
                    chunk_type="table",
                    row_start=selected[0][0],
                    row_end=selected[-1][0],
                    **scope,
                )
            )
            rows[:] = (
                selected[-self.config.table_overlap_rows :]
                if keep_overlap and self.config.table_overlap_rows
                else []
            )

        for offset, row in enumerate(table.rows):
            row_number = table.row_number_start + offset
            candidate = [*rows, (row_number, row)]
            if rows and estimate_tokens(render(candidate)) > body_budget:
                emit_rows(keep_overlap=True)
                # 两行重叠是目标值；若极宽行导致超限，优先保证 chunk 上限。
                while rows and estimate_tokens(render([*rows, (row_number, row)])) > body_budget:
                    rows.pop(0)
            rows.append((row_number, row))
            if estimate_tokens(render(rows)) > body_budget:
                # 单行极端超长时仍保留标题、列名和行号，再对单元格文本递归切分。
                oversized = rows.pop()
                emit_rows(keep_overlap=False)
                prefix = render([(oversized[0], [])]).rsplit("\n", 1)[0]
                row_text = " | ".join(oversized[1])
                for part in self._splitter_for(document, block).split_text(row_text):
                    content = f"{prefix}\n| {oversized[0]} | {part} |"
                    chunks.append(
                        self._make_chunk(
                            document,
                            context_blocks,
                            content=content,
                            chunk_type="table",
                            row_start=oversized[0],
                            row_end=oversized[0],
                            **scope,
                        )
                    )
        emit_rows(keep_overlap=False)
        return chunks
