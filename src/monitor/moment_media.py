"""
朋友圈媒体路径解析工具 — 从 moment_scheduler.py 拆分

功能：将各种格式的图片 URL（本地上传、网络、直接路径）统一解析为本地绝对文件路径。
"""
import os
import logging
import tempfile
import uuid

logger = logging.getLogger(__name__)


def resolve_media_paths(urls: list) -> list:
    """将各种格式的图片 URL 统一解析为本地文件路径。

    支持格式：
    - /api/file/download/<file_id>  本地上传文件
    - http:// / https://             网络图片（自动下载到临时目录）
    - 普通本地路径                   直接校验存在性

    Returns:
        已解析的本地路径列表（不含无效项）
    """
    resolved = []
    for url in urls:
        if url.startswith('/api/file/download/'):
            file_id = url.split('/')[-1]
            try:
                from src.api.file_api import UPLOAD_DIR
                local_p = UPLOAD_DIR / file_id
                if local_p.exists():
                    resolved.append(str(local_p))
                    logger.debug(f"[朋友圈媒体] 图片路径解析成功（本地上传）: {local_p}")
                else:
                    logger.warning(
                        f"[朋友圈媒体] ⚠️ 本地上传文件不存在: {local_p} "
                        f"（原始 URL: {url}）。请确认文件未被清理或路径是否正确。"
                    )
            except Exception as e:
                logger.error(f"[朋友圈媒体] 解析本地上传路径失败 {url}: {e}")

        elif url.startswith('http://') or url.startswith('https://'):
            try:
                import requests
                from urllib.parse import urlparse
                logger.info(f"[朋友圈媒体] 开始下载网络图片: {url}")
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    ext = os.path.splitext(urlparse(url).path)[1] or '.png'
                    tmp_p = os.path.join(tempfile.gettempdir(), f"moment_{uuid.uuid4().hex[:8]}{ext}")
                    with open(tmp_p, 'wb') as f:
                        f.write(resp.content)
                    resolved.append(tmp_p)
                    logger.info(f"[朋友圈媒体] 网络图片已下载到临时文件: {tmp_p}")
                else:
                    logger.error(f"[朋友圈媒体] ⚠️ 下载网络图片失败，HTTP {resp.status_code}: {url}")
            except Exception as e:
                logger.error(f"[朋友圈媒体] 下载图片失败 {url}: {e}")

        else:
            if os.path.exists(url):
                resolved.append(url)
                logger.debug(f"[朋友圈媒体] 图片路径解析成功（直接路径）: {url}")
            else:
                logger.warning(
                    f"[朋友圈媒体] ⚠️ 本地图片文件不存在，跳过: {url}。"
                    "请确认文件路径是否正确或文件是否已被移动/删除。"
                )

    if urls and not resolved:
        logger.error(
            f"[朋友圈媒体] ❌ 全部 {len(urls)} 张图片路径解析失败，无可用本地图片！"
            "本次发布将被中止，请检查图片来源。"
        )
    elif len(resolved) < len(urls):
        logger.warning(
            f"[朋友圈媒体] ⚠️ 共 {len(urls)} 张图片，仅解析成功 {len(resolved)} 张，"
            "将以成功图片继续发布。"
        )

    return resolved
