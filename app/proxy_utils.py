"""Cross-service proxy helper utilities (extracted from proxy.py for 300-line compliance)."""
from __future__ import annotations

import os
import sys
import socket

import httpx
from xm_py_server.runtime_urls import LOOPBACK_HOST

from app import constants

# ─── 端口存活性缓存（避免每次请求都做 TCP 探测）────────────────────────────
_port_alive_cache: dict = {}  # {port: (alive: bool, timestamp: float)}
_PORT_CACHE_TTL_POSITIVE = 30  # 存活时缓存 30 秒
_PORT_CACHE_TTL_NEGATIVE = 10  # 未存活时仅缓存 10 秒


def prefer_prod_gateway() -> bool:
    """
    跨服务代理目标选择策略：
    1) 显式环境变量优先（XM_CROSS_SERVICE_MODE=prod/local）
    2) XM_ENV / NODE_ENV=production 时走线上
    3) 默认：PyInstaller 打包且非 --dev → 线上；否则本地端口优先
    """
    mode = os.getenv("XM_CROSS_SERVICE_MODE", "").strip().lower()
    if mode in {"prod", "online", "remote"}:
        return True
    if mode in {"local", "dev"}:
        return False
    if os.getenv("NODE_ENV") == "production" or os.getenv("XM_ENV") == "production":
        return True
    return bool(getattr(sys, "frozen", False)) and ("--dev" not in sys.argv)


def _bg_check_port(port: int):
    """后台检测端口存活并更新缓存"""
    import socket
    import time as _t
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            _port_alive_cache[port] = (True, _t.time())
    except (ConnectionRefusedError, OSError, TimeoutError):
        _port_alive_cache[port] = (False, _t.time())


def is_port_alive(port: int) -> bool:
    """检测本地端口是否存活（完全无阻塞，后台线程异步更新缓存）"""
    import socket
    import threading
    import time as _t
    now = _t.time()
    cached = _port_alive_cache.get(port)
    if cached:
        alive, timestamp = cached
        ttl = _PORT_CACHE_TTL_POSITIVE if alive else _PORT_CACHE_TTL_NEGATIVE
        if (now - timestamp) >= ttl:
            # 临时更新时间，防止并发请求重复创建检测线程
            _port_alive_cache[port] = (alive, now)
            try:
                threading.Thread(target=_bg_check_port, args=(port,), daemon=True).start()
            except Exception:
                pass
        return alive
    else:
        # 第一次访问无缓存时，为了不阻塞主线程，默认返回 False 并启动后台线程进行真实探测
        _port_alive_cache[port] = (False, now)
        try:
            threading.Thread(target=_bg_check_port, args=(port,), daemon=True).start()
        except Exception:
            pass
        return False


def resolve_target(prefix: str, path: str) -> tuple[str, bool]:
    """
    解析跨服务请求的目标 URL
    - 生产打包态默认强制线上（可被 XM_CROSS_SERVICE_MODE 覆盖）
    - 开发环境：本地端口存活 → 转发到本地（去掉前缀）
    - 开发环境本地不可达 → 转发到生产域名
    返回: (目标 URL, 是否命中本地)
    """
    svc = constants.CROSS_SERVICE_MAP[prefix]
    local_url = svc["local"]
    port = int(local_url.rsplit(":", 1)[1].split("/")[0])
    suffix = path[len(prefix):]
    if prefer_prod_gateway():
        return svc["prod"] + suffix, False
    if is_port_alive(port):
        return local_url + suffix, True
    return svc["prod"] + suffix, False


# ─── httpx 客户端工厂 ──────────────────────────────────────────────────────
_proxy_client: httpx.AsyncClient | None = None
# SSE 专用客户端：read 超时设为 None（长连接，不能有读超时截断）
_sse_proxy_client: httpx.AsyncClient | None = None


def get_proxy_client(is_sse: bool = False) -> httpx.AsyncClient:
    global _proxy_client, _sse_proxy_client
    if is_sse:
        if _sse_proxy_client is None or _sse_proxy_client.is_closed:
            # SSE 长连接：connect 5s，read 超时 None（永不超时读），pool 30s
            timeout = httpx.Timeout(None, connect=5.0, pool=30.0)
            _sse_proxy_client = httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True, trust_env=True)
        return _sse_proxy_client
    if _proxy_client is None or _proxy_client.is_closed:
        # 连接 5.0s，读取超时放宽到 120.0s 以防大模型/慢网响应
        timeout = httpx.Timeout(120.0, connect=5.0, read=120.0)
        _proxy_client = httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True, trust_env=True)
    return _proxy_client


def http_base_to_ws(url: str) -> str:
    """将 http(s) 基址转为 ws(s)，供 WebSocket 反代拼接上游。"""
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    return url
