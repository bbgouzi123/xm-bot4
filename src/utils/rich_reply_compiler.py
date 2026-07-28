import re
import os
import tempfile
import uuid
import logging
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

from src.utils.table_compiler import extract_and_convert_tables

logger = logging.getLogger(__name__)

import urllib.request
from urllib.parse import urlparse

# 定义支持的文件/图片后缀名检测正则
IMAGE_EXT_PATTERN = re.compile(r'\.(jpg|jpeg|png|gif|webp|bmp|svg|ico|tiff?)(?:\?.*)?$', re.IGNORECASE)
FILE_EXT_PATTERN = re.compile(r'\.(pdf|docx?|xlsx?|pptx?|txt|rtf|odt|ods|odp|zip|rar|7z|tar|gz)(?:\?.*)?$', re.IGNORECASE)

# 映射 MIME 到后缀名
MIME_TO_EXT = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
    'image/svg+xml': '.svg',
    'image/x-icon': '.ico',
    'application/pdf': '.pdf',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.ms-powerpoint': '.ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'text/plain': '.txt',
    'application/zip': '.zip',
    'application/x-zip-compressed': '.zip',
    'application/x-rar-compressed': '.rar',
    'application/x-7z-compressed': '.7z',
}

def determine_url_type_and_ext(url: str) -> Tuple[str, Optional[str]]:
    """
    判断 URL 类别和合适的文件后缀。
    返回: (type, ext)
        type: 'IMAGE', 'FILE', or 'TEXT' (网页/普通链接)
        ext: 例如 '.png', '.pdf'。如果为 TEXT 则为 None。
    """
    if not url:
        return 'TEXT', None
        
    if url.startswith("weixin://") or url.startswith("wxp://"):
        return 'TEXT', None
        
    # 1. 快速检查文件扩展名
    try:
        parsed = urlparse(url)
        path = parsed.path
        if IMAGE_EXT_PATTERN.search(path):
            ext = os.path.splitext(path)[1]
            return 'IMAGE', ext.lower()
        if FILE_EXT_PATTERN.search(path):
            ext = os.path.splitext(path)[1]
            return 'FILE', ext.lower()
    except Exception:
        pass
        
    # 2. 如果静态判断不出来，发起 HTTP HEAD 请求获取 Content-Type
    try:
        req = urllib.request.Request(url, method='HEAD')
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # 处理 SSL
        ssl_context = None
        if url.startswith("https://"):
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        with urllib.request.urlopen(req, timeout=3, context=ssl_context) as response:
            content_type = response.headers.get('Content-Type', '')
            if ';' in content_type:
                content_type = content_type.split(';')[0].strip()
            content_type = content_type.lower()
            
            # 判断 MIME 类别
            if content_type.startswith('image/'):
                ext = MIME_TO_EXT.get(content_type, '.png')
                return 'IMAGE', ext
                
            # 文档和压缩包 MIME
            document_mimes = [
                'application/pdf', 
                'application/msword', 
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 
                'application/vnd.ms-excel', 
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
                'application/vnd.ms-powerpoint', 
                'application/vnd.openxmlformats-officedocument.presentationml.presentation', 
                'text/plain', 
                'application/zip',
                'application/x-zip-compressed',
                'application/x-rar-compressed',
                'application/x-7z-compressed',
            ]
            if content_type in document_mimes or content_type.startswith('application/vnd.'):
                ext = MIME_TO_EXT.get(content_type, '.dat')
                return 'FILE', ext
                
    except Exception as e:
        logger.debug(f"[MIMETester] HEAD 探针探测失败 ({url}): {e}")
        
    return 'TEXT', None

# draw_table_to_image 和 extract_and_convert_tables 已重构至 src.utils.table_compiler.py 中


def compile_rich_reply(raw_text: str) -> Tuple[str, List[str]]:
    """
    核心入口：多模态富卡片逆向编译器。
    解析 Markdown 表格、图片和物理文档附件，自动静默下载并在大仓/微信底层中转化为原生多媒体推送。
    采用 MIME 探针识别，只剥离物理文件/图片，正常超链接（如网页）将原样保留。
    
    返回:
        clean_text: 去除多媒体链接和表格结构后的干净问候语
        downloaded_paths: 物理文件/图片本地路径列表
    """
    if not raw_text:
        return "", []

    # 1. 编译并提取 Markdown 表格
    clean_text, table_images = extract_and_convert_tables(raw_text)
    downloaded_paths = list(table_images)

    # 2. 从文本中匹配并解析 Markdown 图片: ![alt](url) -> 必定为物理图片，剥离并下载
    md_img_matches = re.findall(r'!\[.*?\]\((.*?)\)', clean_text)
    clean_text = re.sub(r'!\[.*?\]\((.*?)\)', '', clean_text).strip()

    # 3. 匹配并解析 Markdown 普通链接 [text](url) 与裸 URL
    media_urls = []
    
    # 3.1 提取并过滤 [text](url) 格式的超链接 (支持常规 https 和微信支付链接)
    md_links = re.findall(r'\[([^\]]*?)\]\(((?:https?://|weixin://|wxp://)[^\s<>"]+)\)', clean_text)
    for text, url in md_links:
        if url.startswith("weixin://") or url.startswith("wxp://"):
            clean_text = clean_text.replace(f"[{text}]({url})", "")
            try:
                import qrcode
                qr = qrcode.QRCode(version=1, box_size=10, border=4)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                
                temp_dir = os.path.join(tempfile.gettempdir(), "xm_bot4_materials")
                os.makedirs(temp_dir, exist_ok=True)
                qr_path = os.path.join(temp_dir, f"wxpay_{uuid.uuid4().hex[:12]}.png")
                img.save(qr_path)
                downloaded_paths.append(qr_path)
                logger.info(f"[RichReplyCompiler] Markdown微信支付链接已转换为本地二维码: {qr_path}")
            except Exception as qr_err:
                logger.error(f"[RichReplyCompiler] 二维码生成失败 ({url}): {qr_err}")
        else:
            url_type, _ = determine_url_type_and_ext(url)
            if url_type in ('IMAGE', 'FILE'):
                # 这是多媒体文件，移除这整个 Markdown 链接结构
                clean_text = clean_text.replace(f"[{text}]({url})", "")
                media_urls.append(url)
            
    # 3.2 提取并过滤剩余的裸微信支付链接
    wxpay_urls = re.findall(r'((?:weixin://wxpay/bizpayurl\?pr=|wxp://)[a-zA-Z0-9_#-]+)', clean_text)
    for url in wxpay_urls:
        clean_text = clean_text.replace(url, "")
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            
            temp_dir = os.path.join(tempfile.gettempdir(), "xm_bot4_materials")
            os.makedirs(temp_dir, exist_ok=True)
            qr_path = os.path.join(temp_dir, f"wxpay_{uuid.uuid4().hex[:12]}.png")
            img.save(qr_path)
            downloaded_paths.append(qr_path)
            logger.info(f"[RichReplyCompiler] 裸微信支付链接已转换为本地二维码: {qr_path}")
        except Exception as qr_err:
            logger.error(f"[RichReplyCompiler] 二维码生成失败 ({url}): {qr_err}")

    # 3.3 提取并过滤剩余的裸超链接
    raw_urls = re.findall(r'(https?://[^\s<>"]+)', clean_text)
    for url in raw_urls:
        if url in media_urls:
            continue
        url_type, _ = determine_url_type_and_ext(url)
        if url_type in ('IMAGE', 'FILE'):
            # 这是多媒体文件，将 URL 从正文中剥离
            clean_text = clean_text.replace(url, "")
            media_urls.append(url)

    # 重用底层下载机制进行静默下载
    from src.utils.material_utils import resolve_and_download_material

    # 合并下载任务，使用 dict.fromkeys 保持顺序去重，防止同一个 URL 触发多次物理下载
    all_target_urls = list(dict.fromkeys(md_img_matches + media_urls))
    for url in all_target_urls:
        try:
            local_path = resolve_and_download_material(url)
            if local_path and os.path.exists(local_path):
                downloaded_paths.append(local_path)
        except Exception as dl_err:
            logger.error(f"[RichReplyCompiler] 下载多媒体失败 ({url}): {dl_err}")

    # 合并多余的空行
    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
    return clean_text, downloaded_paths
