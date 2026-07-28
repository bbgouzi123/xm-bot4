"""
知识库各种文档文件解析器的底层实现整合模块
支持格式：.txt .md .csv .docx .doc .pdf .xlsx .xls .html .pptx
"""
import logging
from src.api.knowledge_file_parsers_basic import (
    _parse_txt,
    _parse_csv,
    _parse_html,
    _parse_docx,
    _parse_doc
)
from src.api.knowledge_file_parsers_media import (
    _parse_pdf,
    _parse_xlsx,
    _parse_xls,
    _parse_pptx
)

logger = logging.getLogger(__name__)

# 支持的文件类型映射（MIME → 类型标识）
SUPPORTED_MIME_TYPES = {
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
    "text/html": "html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}

# 扩展名 → 类型标识（MIME 不准确时的后备）
_EXT_MAP = {
    ".txt": "txt", ".md": "md", ".markdown": "md",
    ".csv": "csv", ".html": "html", ".htm": "html",
    ".docx": "docx", ".doc": "doc", ".pdf": "pdf",
    ".xlsx": "xlsx", ".xls": "xls", ".pptx": "pptx",
}

# 用户可见的格式提示
SUPPORTED_FORMATS_LABEL = ".txt .md .csv .docx .pdf .xlsx .xls .html .pptx"


def _detect_file_type(name: str, mime_type: str) -> str:
    """根据 MIME 类型和文件名推断文件类型"""
    if mime_type in SUPPORTED_MIME_TYPES:
        return SUPPORTED_MIME_TYPES[mime_type]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    return _EXT_MAP.get(ext, "")


# ── 类型 → 解析器映射 ──────────────────────────────────

_PARSERS = {
    "txt": _parse_txt,
    "md": _parse_txt,
    "csv": _parse_csv,
    "html": _parse_html,
    "docx": _parse_docx,
    "doc": _parse_doc,
    "pdf": _parse_pdf,
    "xlsx": _parse_xlsx,
    "xls": _parse_xls,
    "pptx": _parse_pptx,
}
