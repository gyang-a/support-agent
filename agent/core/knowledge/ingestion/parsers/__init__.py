"""受支持文件格式的解析器。"""

from .docx import parse_docx
from .markdown import parse_markdown
from .pdf import parse_pdf

__all__ = ["parse_docx", "parse_markdown", "parse_pdf"]
