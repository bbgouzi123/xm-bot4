import os
import sys
import tempfile
import uuid
import logging
from urllib import request as urllib_request
from urllib.parse import urlparse
from typing import Optional

logger = logging.getLogger(__name__)

def resolve_and_download_material(file_or_url: str) -> Optional[str]:
    """如果是 URL (网络路径或本地相对 OSS 路由)，将其下载到本地临时文件夹，并返回本地磁盘绝对路径；否则直接返回原本地路径。"""
    if not file_or_url:
        return None

    # 1. 检查是否已经是存在的本地路径
    if os.path.exists(file_or_url):
        return file_or_url

    # 2. 如果是相对 OSS 路径如 /api/xm-oss/...，将其重构为完整 URL
    target_url = file_or_url
    if file_or_url.startswith("/api/xm-oss/"):
        from xm_py_server.runtime_urls import XMCORE_ORIGIN, LOOPBACK_HOST
        mode = os.getenv("XM_CROSS_SERVICE_MODE", "").strip().lower()
        is_prod = False
        if mode in {"prod", "online", "remote"}:
            is_prod = True
        elif mode in {"local", "dev"}:
            is_prod = False
        elif os.getenv("NODE_ENV") == "production" or os.getenv("XM_ENV") == "production":
            is_prod = True
        elif getattr(sys, "frozen", False) and ("--dev" not in sys.argv):
            is_prod = True
            
        if is_prod:
            target_url = f"{XMCORE_ORIGIN}{file_or_url}"
        else:
            # 本地 OSS 后端端口 42042，去掉网关前缀 /api/xm-oss
            relative = file_or_url.replace("/api/xm-oss", "", 1)
            target_url = f"http://{LOOPBACK_HOST}:42042{relative}"

    # 3. 校验是否是 http/https URL
    parsed = urlparse(target_url)
    if not (parsed.scheme in ("http", "https") and parsed.netloc):
        return None

    # 4. 下载到临时文件夹
    try:
        temp_dir = os.path.join(tempfile.gettempdir(), "xm_bot4_materials")
        os.makedirs(temp_dir, exist_ok=True)
        
        path_part = parsed.path
        ext = os.path.splitext(path_part)[1]
        
        # 挂载 HEAD 探针探测真实的 MIME 类型以自适应映射最正确的后缀
        mime_ext = None
        if not ext or len(ext) > 5 or '?' in target_url:
            try:
                probe_req = urllib_request.Request(target_url, method='HEAD')
                probe_req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                
                ssl_context = None
                if target_url.startswith("https://"):
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    
                with urllib_request.urlopen(probe_req, timeout=3, context=ssl_context) as probe_res:
                    content_type = probe_res.headers.get('Content-Type', '')
                    if ';' in content_type:
                        content_type = content_type.split(';')[0].strip()
                    content_type = content_type.lower()
                    
                    from src.utils.rich_reply_compiler import MIME_TO_EXT
                    mime_ext = MIME_TO_EXT.get(content_type)
            except Exception as probe_err:
                logger.debug(f"[MaterialUtils] 下载时 HEAD 探针探测 MIME 失败: {probe_err}")
                
        if mime_ext:
            ext = mime_ext
        elif not ext or len(ext) > 5:
            ext = ".png"
            
        local_filename = f"material_{uuid.uuid4().hex[:12]}{ext}"
        local_path = os.path.join(temp_dir, local_filename)
        
        logger.info(f"[MaterialUtils] 开始从 URL 下载物料: {target_url} -> {local_path}")
        
        headers = {"User-Agent": "xm-bot4-backend/1.0"}
        try:
            from src.utils.cloud_sync import get_cloud_client
            client = get_cloud_client()
            if client and getattr(client, "jwt_token", None):
                headers["Authorization"] = f"Bearer {client.jwt_token}"
        except Exception:
            pass

        req = urllib_request.Request(target_url, headers=headers)
        
        ssl_context = None
        if target_url.startswith("https://"):
            import ssl
            ssl_context = ssl.create_default_context()
            if os.getenv("XM_ENV") == "dev" or "--dev" in sys.argv:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        with urllib_request.urlopen(req, timeout=15, context=ssl_context) as response:
            with open(local_path, "wb") as f:
                f.write(response.read())
                
        logger.info(f"[MaterialUtils] 物料下载成功: {local_path}")
        return local_path
    except Exception as e:
        logger.error(f"[MaterialUtils] 物料下载失败 ({target_url}): {e}", exc_info=True)
        return None


def record_screen_gif(duration: int = 10, fps: int = 4, scale: float = 0.5) -> Optional[str]:
    """录制当前屏幕 duration 秒，以指定的 fps 和 scale 转换压缩为本地临时 gif 文件（代理调用全局公共录屏方法）"""
    from xm_py_server import record_screen_to_gif
    # 自适应生成临时文件并返回路径
    return record_screen_to_gif(duration=duration, fps=fps, max_width=1280, max_height=720)
