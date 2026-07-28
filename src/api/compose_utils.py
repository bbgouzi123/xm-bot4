import os
import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def ensure_local_image_path(path_or_url: str) -> str:
    """如果是 HTTP 链接，下载到临时目录并返回本地路径，否则返回原路径"""
    if isinstance(path_or_url, str) and (path_or_url.startswith("http://") or path_or_url.startswith("https://")):
        import tempfile
        import hashlib
        import urllib.request
        temp_dir = os.path.join(tempfile.gettempdir(), "xm-bot-moment-images")
        os.makedirs(temp_dir, exist_ok=True)
        try:
            url_hash = hashlib.md5(path_or_url.encode()).hexdigest()
            ext = ".png"
            for known_ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]:
                if known_ext in path_or_url.lower():
                    ext = known_ext
                    break
            local_file = os.path.join(temp_dir, f"{url_hash}{ext}")
            if not os.path.exists(local_file):
                logger.info(f"[手动合成] 下载远程底图: {path_or_url[:80]}...")
                urllib.request.urlretrieve(path_or_url, local_file)
            return local_file
        except Exception as dl_err:
            logger.error(f"[手动合成] 下载远程底图失败: {path_or_url} -> {dl_err}")
            raise dl_err
    return path_or_url


def upload_file_to_oss(local_path: str) -> Optional[str]:
    """
    将本地物理文件以 Base64 上传到大仓统一的 OSS 服务。
    返回同步后端 public_url，上传失败则返回 None。
    """
    import base64
    import mimetypes
    import urllib.request as urllib_request
    import json
    from src.utils.cloud_sync import get_cloud_client

    if not os.path.exists(local_path):
        logger.warning(f"[OSS上传] 本地文件不存在: {local_path}")
        return None

    try:
        mime_type, _ = mimetypes.guess_type(local_path)
        if not mime_type:
            mime_type = "image/png" if local_path.endswith(".png") else "application/octet-stream"

        with open(local_path, "rb") as f:
            data = f.read()
        base64_data = base64.b64encode(data).decode("utf-8")

        client = get_cloud_client()
        if not client or not client.jwt_token:
            logger.warning("[OSS上传] 无法获取 CloudSyncClient 或未登录，跳过 OSS 上传")
            return None

        import sys
        mode = os.getenv("XM_CROSS_SERVICE_MODE", "").strip().lower()
        is_remote = mode in {"prod", "online", "remote"} or (os.getenv("XM_ENV") == "production") or (getattr(sys, "frozen", False) and ("--dev" not in sys.argv))

        if is_remote:
            from xm_py_server.runtime_urls import prod_gateway_url
            oss_url = prod_gateway_url("/api/xm-oss/api/v1/oss/upload/base64")
        else:
            from xm_py_server.runtime_urls import LOOPBACK_HOST, http_origin
            oss_url = f"{http_origin(LOOPBACK_HOST, 42042)}/api/v1/oss/upload/base64"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {client.jwt_token}",
            "x-xm-product": "xm-bot4",
            "x-xm-scene": "composed_moments"
        }

        if not is_remote:
            proxy_secret = os.getenv("XM_INTERNAL_PROXY_SECRET", "xm-internal-2026")
            headers["X-XM-Internal-Proxy"] = proxy_secret

        payload = {
            "name": os.path.basename(local_path),
            "mime_type": mime_type,
            "base64_data": base64_data
        }

        req = urllib_request.Request(
            oss_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        ssl_context = None
        if is_remote:
            import ssl
            ssl_context = ssl.create_default_context()

        with urllib_request.urlopen(req, timeout=30, context=ssl_context) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            if isinstance(res_json, dict) and res_json.get("code") == 20000:
                data_node = res_json.get("data", {})
                public_url = data_node.get("public_url")
                if public_url:
                    logger.info(f"[OSS上传] 成功上传本地文件 {os.path.basename(local_path)} -> {public_url}")
                    return public_url
            logger.warning(f"[OSS上传] 接口返回错误: {res_json}")
    except Exception as e:
        logger.warning(f"[OSS上传] 本地文件 {local_path} 上传 OSS 异常: {e}")

    return None


def _parse_style(data: dict) -> dict:
    """从请求体中解析合成样式参数，返回 compose_text_on_image 可用的 kwargs"""
    style = data.get("style", {}) or {}
    kwargs = {}

    if "font_size" in style:
        kwargs["font_size"] = int(style["font_size"])
    if "font_color" in style:
        kwargs["font_color"] = str(style["font_color"])
    if "overlay_opacity" in style:
        pct = int(style["overlay_opacity"])
        kwargs["overlay_opacity"] = math.floor(pct * 255 / 100)
    if "position" in style:
        pos = str(style["position"])
        if pos in ("bottom", "center", "top"):
            kwargs["position"] = pos

    return kwargs


def ensure_absolute_oss_url(p: str) -> str:
    """如果是相对的 OSS 路径，重构并拼接为能够下载的绝对 URL"""
    if isinstance(p, str) and p.startswith("/api/xm-oss/"):
        import sys
        mode = os.getenv("XM_CROSS_SERVICE_MODE", "").strip().lower()
        is_remote = mode in {"prod", "online", "remote"} or (os.getenv("XM_ENV") == "production") or (getattr(sys, "frozen", False) and ("--dev" not in sys.argv))
        if is_remote:
            from xm_py_server.runtime_urls import prod_gateway_url
            base = prod_gateway_url("")
            return f"{base}{p}"
        else:
            from xm_py_server.runtime_urls import LOOPBACK_HOST, http_origin
            base = http_origin(LOOPBACK_HOST, 42042)
            relative = p.replace("/api/xm-oss", "", 1)
            return f"{base}{relative}"
    return p
