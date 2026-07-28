"""
知识库基础文本与 Word 格式解析模块
"""
import io
import csv
import logging
import re
import tempfile
import os
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


def _parse_txt(raw: bytes) -> str:
    """纯文本 / Markdown — 自动编码探测"""
    try:
        from charset_normalizer import from_bytes
        res = from_bytes(raw).best()
        if res and res.encoding:
            return str(res)
    except Exception as e:
        logger.warning(f"[知识库] charset_normalizer 自动探测编码失败: {e}")

    for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_csv(raw: bytes) -> str:
    """CSV — 逐行拼成易读文本，每行用 | 分隔"""
    text = _parse_txt(raw)
    reader = csv.reader(io.StringIO(text))
    lines = []
    for row in reader:
        lines.append(" | ".join(cell.strip() for cell in row if cell.strip()))
    return "\n".join(lines)


class _HTMLTextExtractor(HTMLParser):
    """极简 HTML 纯文本提取器"""
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _parse_html(raw: bytes) -> str:
    """HTML — 剥离标签，提取纯文本"""
    html_str = _parse_txt(raw)
    extractor = _HTMLTextExtractor()
    extractor.feed(html_str)
    return extractor.get_text()


def _parse_docx(raw: bytes) -> str:
    """Word (.docx) — 提取段落与表格文本"""
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("需要安装 python-docx: pip install python-docx")
    doc = Document(io.BytesIO(raw))
    
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    parts = []
    body = doc.element.body
    for child in body:
        if child.tag.endswith('p'):
            p = Paragraph(child, doc)
            txt = p.text.strip()
            if txt:
                parts.append(txt)
        elif child.tag.endswith('tbl'):
            table = Table(child, doc)
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    cell_text = cell.text.strip()
                    if cell_text:
                        cells.append(cell_text.replace('\n', ' '))
                if cells:
                    parts.append(" | ".join(cells))

    # 额外提取可能存在的文本框数据
    try:
        txbx_texts = []
        for txbx in doc.element.xpath('//w:txbxContent'):
            for p_elem in txbx.xpath('.//w:p'):
                p = Paragraph(p_elem, doc)
                txt = p.text.strip()
                if txt and txt not in parts:
                    txbx_texts.append(txt)
        if txbx_texts:
            parts.append("\n[文本框数据]:")
            parts.extend(txbx_texts)
    except Exception:
        pass

    return "\n".join(parts)


def _parse_doc(raw: bytes) -> str:
    """旧版 .doc 格式解析器 — 支持 Windows 本地 Office 接口与零依赖二进制字符串提取双保险"""
    try:
        import win32com.client
        import pythoncom

        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(raw)
        
        word_app = None
        doc = None
        try:
            pythoncom.CoInitialize()
            
            # 顺序尝试 Dispatch 和 DispatchEx 启动 Word/WPS
            dispatch_names = ["Word.Application", "WPS.Application", "Kwps.Application", "Wps.Application"]
            for name in dispatch_names:
                try:
                    word_app = win32com.client.Dispatch(name)
                    if word_app:
                        logger.info(f"[知识库] 成功通过 Dispatch 绑定 Office 实例: {name}")
                        break
                except Exception:
                    continue
            
            if not word_app:
                for name in dispatch_names:
                    try:
                        word_app = win32com.client.DispatchEx(name)
                        if word_app:
                            logger.info(f"[知识库] 成功通过 DispatchEx 启动 Office 进程: {name}")
                            break
                    except Exception:
                        continue
                        
            if not word_app:
                raise RuntimeError("未在当前系统检测到可用的 Word 或 WPS 自动化接口")
                
            word_app.Visible = False
            word_app.DisplayAlerts = False
            
            doc = word_app.Documents.Open(tmp_path, ReadOnly=True)
            text = doc.Content.Text
            if text and text.strip():
                return text
        finally:
            if doc:
                try:
                    doc.Close(False)
                except Exception:
                    pass
            if word_app:
                try:
                    word_app.Quit()
                except Exception:
                    pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[知识库] win32com 解析 .doc 失败: {e}，将尝试基于多编码二进制流的备用提取器")

    try:
        cleaned = []
        cjk_latin_pattern = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u0020-\u007e]{4,}")
        
        # 1. 尝试以 utf-16-le 解码 (Office 97+ 默认双字节编码)
        try:
            decoded_u16 = raw.decode("utf-16le", errors="ignore")
            for s in cjk_latin_pattern.findall(decoded_u16):
                s_stripped = s.strip()
                if len(s_stripped) >= 4 and not any(kw in s_stripped for kw in ["Normal.dotm", "Microsoft Word", "Title", "Subject", "Author", "Template", "Prnt"]):
                    if s_stripped not in cleaned:
                        cleaned.append(s_stripped)
        except Exception:
            pass
            
        # 2. 尝试以 gbk / gb18030 解码 (旧版单字节中文字符)
        for enc in ("gb18030", "gbk", "utf-8"):
            try:
                decoded_ansi = raw.decode(enc, errors="ignore")
                for s in cjk_latin_pattern.findall(decoded_ansi):
                    s_stripped = s.strip()
                    if len(s_stripped) >= 4 and not any(kw in s_stripped for kw in ["Normal.dotm", "Microsoft Word", "Title", "Subject", "Author", "Template", "Prnt"]):
                        if s_stripped not in cleaned:
                            cleaned.append(s_stripped)
            except Exception:
                pass
        
        if cleaned:
            return "\n".join(cleaned)
    except Exception as e2:
        logger.error(f"[知识库] 二进制流解析 .doc 失败: {e2}")

    raise RuntimeError("解析 .doc 文件失败，请确保文件未加密，或另存为 .docx 后重新上传")
