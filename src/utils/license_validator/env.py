"""
环境与网络配置模块
"""
import os
import sys
import logging
from xm_py_server.runtime_urls import LOOPBACK_HOST, http_origin, prod_gateway_url
from src.utils.http_client import XMClient

logger = logging.getLogger(__name__)

# 线上授权服务器地址（打包后的 exe 默认连接统一网关）
_PRODUCTION_API = prod_gateway_url("/api/xm-user")
# 本地开发地址
_DEV_API = http_origin(LOOPBACK_HOST, 42001)

def _detect_license_api() -> str:
    """
    自动检测授权服务器地址，优先级：
    1. 环境变量 SA_LICENSE_API（手动覆盖，最高优先级）
    2. PyInstaller 打包环境 → 线上地址
    3. 普通 Python 运行环境 → 本地地址
    """
    env_api = os.environ.get("SA_LICENSE_API")
    if env_api:
        logger.info(f"[授权] 使用环境变量指定的授权服务器: {env_api}")
        return env_api

    is_frozen = getattr(sys, 'frozen', False)
    if is_frozen:
        logger.info(f"[授权] 生产环境 (exe)，连接线上: {_PRODUCTION_API}")
        return _PRODUCTION_API
    else:
        logger.info(f"[授权] 开发环境 (python)，连接本地: {_DEV_API}")
        return _DEV_API

SA_LICENSE_API = _detect_license_api()
license_client = XMClient(SA_LICENSE_API, timeout=5, encryption=True)
