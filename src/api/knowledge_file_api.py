"""
知识库文件解析 API — 支持上传多种文档文件到私域知识库

端点：
1. POST /api/crm/industry/parse-knowledge-file  — 解析文件内容为纯文本
"""
import logging
from urllib.parse import urlparse
from fastapi import APIRouter, Request
from src.utils.response import ok, err
from src.api.knowledge_file_parsers import (
    _PARSERS,
    _detect_file_type,
    SUPPORTED_FORMATS_LABEL
)

router = APIRouter()
logger = logging.getLogger(__name__)

# xm-oss 服务在本地的直连端口（绕过 bot4 代理层，避免 302 降级到线上后文件找不到）
_OSS_GATEWAY_PREFIX = "/api/xm-oss"
_OSS_LOCAL_PORT = 42042
_BOT4_LOCAL_PORT = 42041


async def _download_file(url: str) -> bytes:
    """从 xm-oss 下载文件内容

    支持三种 URL 形式：
    - /api/xm-oss/... 前缀的相对路径 -> 直接发给本地 xm-oss（42042），剥去 /api/xm-oss 网关前缀
      这样可绕过 bot4 自身代理层，避免本地 xm-oss 触发 302 降级到线上后因文件仅存于本地而 404
    - 其他 /api/... 或 /v1/... 相对路径 -> 拼接 http://127.0.0.1:42041
    - 绝对 http/https URL（预签名直链等）-> 直接请求

    重定向处理策略：
    - OSS 本地数据库找不到文件时会将请求 302 降级重定向到线上 xmcore.top，
      这是正常的降级兜底行为，应安全跟随。
    - 只有重定向目标仍指向本机 (127.0.0.1) 的同一 /redirect 端点时
      才判定为真正的死循环并拒绝跟随。
    """
    import httpx

    # 判断是否为 /api/xm-oss/ 前缀的相对路径（可直连本地 xm-oss 42042）
    is_oss_direct = url.startswith(_OSS_GATEWAY_PREFIX + "/")

    if is_oss_direct:
        # 关键修复：剥去 /api/xm-oss，直接发给 xm-oss 自身端口（42042）
        # 例：/api/xm-oss/api/v1/oss/public/files/{id}/redirect
        #  -> http://127.0.0.1:42042/api/v1/oss/public/files/{id}/redirect
        oss_path = url[len(_OSS_GATEWAY_PREFIX):]
        full_url = f"http://127.0.0.1:{_OSS_LOCAL_PORT}{oss_path}"
    elif url.startswith("/api/") or url.startswith("/v1/"):
        full_url = f"http://127.0.0.1:{_BOT4_LOCAL_PORT}" + url
    elif url.startswith("http"):
        full_url = url
    else:
        full_url = f"http://127.0.0.1:{_BOT4_LOCAL_PORT}" + url

    token = None
    try:
        import os
        token_file = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "xm-bot4", "sso_token.txt"
        )
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
    except Exception:
        pass

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        # 第一次请求不自动跟随重定向，方便手动判断
        try:
            resp = await client.get(full_url, headers=headers, timeout=30.0, follow_redirects=False)
        except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
            # 本地 xm-oss 直连失败（服务未启动/端口未监听）-> 降级到线上网关重试
            if is_oss_direct:
                oss_path = url[len(_OSS_GATEWAY_PREFIX):]
                online_url = f"https://xmcore.top{_OSS_GATEWAY_PREFIX}{oss_path}"
                logger.info(f"[知识库] 本地 xm-oss 连接失败，降级至线上重试: {online_url}")
                resp = await client.get(online_url, headers={}, timeout=30.0, follow_redirects=True)
                resp.raise_for_status()
                return resp.content
            raise conn_err

        # 本地 OSS 网关故障/文件丢失降级（404/502/503/504）：
        # 当本地 xm-oss 返回 404（本地库无此文件）或服务不可用时，自动将请求转发到线上生产 OSS 进行重试。
        # 适用于：本地开发时旧文件无 presigned_url、本地无该文件元数据、或本地 OSS 尚未启动的情况。
        if resp.status_code in (404, 502, 503, 504):
            parsed_orig = urlparse(full_url)
            if parsed_orig.hostname in ("127.0.0.1", "localhost"):
                if is_oss_direct:
                    # 直连 xm-oss（42042）失败，降级到线上网关（保留 /api/xm-oss 前缀供线上 nginx 路由）
                    oss_path = url[len(_OSS_GATEWAY_PREFIX):]
                    online_url = f"https://xmcore.top{_OSS_GATEWAY_PREFIX}{oss_path}"
                else:
                    # 经 bot4 代理（42041）请求失败，替换为线上等价路径
                    online_url = f"https://xmcore.top{parsed_orig.path}"
                if parsed_orig.query:
                    online_url += f"?{parsed_orig.query}"
                logger.info(
                    f"[知识库] 本地 OSS {resp.status_code}，降级至线上重试: {online_url}"
                )
                resp = await client.get(online_url, headers={}, timeout=30.0, follow_redirects=True)
                resp.raise_for_status()
                return resp.content

        # 处理可能的重定向
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            parsed_location = urlparse(location)
            parsed_original = urlparse(full_url)

            # 死循环检测：仅当重定向目标与原始请求指向同一本机主机+端口
            # 且路径仍是 /public/files/{id}/redirect 端点时，才判定为循环。
            # 场景：OSS 服务自身路由配置错误，导致自己重定向回自己。
            is_same_host = (
                parsed_location.netloc == parsed_original.netloc
                or (parsed_location.hostname in ("127.0.0.1", "localhost")
                    and parsed_original.hostname in ("127.0.0.1", "localhost"))
            )
            is_redirect_endpoint = (
                "oss/public/files" in parsed_location.path
                and parsed_location.path.endswith("/redirect")
            )
            if is_same_host and is_redirect_endpoint:
                logger.warning(
                    f"[知识库] 检测到本机 OSS 重定向死循环，已安全拦截: {full_url} -> {location}"
                )
                raise httpx.HTTPStatusError(
                    "文件不存在，已安全拦截本机重定向死循环",
                    request=resp.request,
                    response=resp,
                )

            # 非死循环（如降级到线上 xmcore.top）：安全跟随重定向
            logger.info(f"[知识库] OSS 降级重定向，跟随至: {location}")
            # 跟随到线上时无需携带内网认证 token（公网端点不认本地 token）
            follow_headers = {} if "xmcore.top" in location else headers
            resp = await client.get(location, headers=follow_headers, timeout=30.0, follow_redirects=True)

        resp.raise_for_status()
        return resp.content


@router.post("/api/crm/industry/parse-knowledge-file")
async def parse_knowledge_file(request: Request):
    """解析上传到 xm-oss 的知识库文件，提取纯文本内容

    请求体: { url, name, mime_type, download_url? }
      - download_url: 可选，OSS 预签名直链（优先于 url 使用，无重定向、有时效）
      - url: OSS 公共重定向 URL（/api/xm-oss/.../redirect）
    响应: { text: "提取的纯文本" }
    """
    data = await request.json()
    # 优先使用预签名直链（直接指向 MinIO，无重定向，更稳定）
    download_url = data.get("download_url", "") or data.get("url", "")
    name = data.get("name", "")
    mime_type = data.get("mime_type", "")

    if not download_url:
        return err(40000, "缺少文件 URL")

    file_type = _detect_file_type(name, mime_type)

    if file_type not in _PARSERS:
        return err(40001, f"不支持的文件格式: {name}。支持: {SUPPORTED_FORMATS_LABEL}")

    try:
        raw_bytes = await _download_file(download_url)
    except Exception as e:
        logger.error(f"[知识库] 下载文件失败: {download_url} -> {e}")
        return err(50001, f"下载文件失败: {e}")

    if not raw_bytes:
        return err(50002, "文件内容为空")

    try:
        import inspect
        parser_func = _PARSERS[file_type]
        if inspect.iscoroutinefunction(parser_func):
            text = await parser_func(raw_bytes)
        else:
            text = parser_func(raw_bytes)
    except RuntimeError as e:
        return err(50003, str(e))
    except Exception as e:
        logger.error(f"[知识库] 文件解析失败: {name} -> {e}")
        return err(50004, f"文件解析失败: {e}")

    cleaned = text.strip()
    if not cleaned:
        return err(50005, "文件解析后内容为空，可能是扫描件或加密文档")

    logger.info(
        f"[知识库] 解析成功: {name} "
        f"({file_type}, {len(raw_bytes)}B -> {len(cleaned)} chars)"
    )
    return ok({"text": cleaned})
