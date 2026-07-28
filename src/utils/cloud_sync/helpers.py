import os
import sys
import json
import logging
from pathlib import Path
from typing import Any, Optional
from xm_py_server.runtime_urls import LOOPBACK_HOST, http_origin, prod_gateway_url

logger = logging.getLogger(__name__)
_CLOUD_CACHE_DIR = Path.home() / ".xm-ai-bot" / "cloud_cache"

# 生产环境线上网关地址（真正的远程同步后端）
_PROD_CLOUD_URL = prod_gateway_url("/api/xm-bot4-cloud")
# 本地开发时连接的本地 Rust 后端服务端口 (xm-bot4-cloud)
_LOCAL_CLOUD_URL = http_origin(LOOPBACK_HOST, 42040)


def scope_bot_wxid() -> str:
    """当前接管微信的数据隔离键（与 ~/.xm-ai-bot/accounts/{wxid}/ 目录一致）。"""
    try:
        from src.crm.account_data import get_active_account
        return get_active_account() or ""
    except Exception:
        return ""


def load_cloud_cache_fast(filename: str) -> Optional[Any]:
    """快速读取本地云缓存，不触发 CloudSyncClient 初始化。"""
    cache_file = _CLOUD_CACHE_DIR / filename
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def detect_cloud_url() -> str:
    """自动检测并获取同步服务地址：
    - 生产打包环境：自动切换连接生产环境线上网关地址（真正的远程同步后端）。
    - 本地开发环境（源码运行）：连接本地启动的 Rust 后端服务 `xm-bot4-cloud`（42040 端口）。
    """
    mode = os.getenv("XM_CROSS_SERVICE_MODE", "").strip().lower()
    if mode in {"prod", "online", "remote"}:
        return _PROD_CLOUD_URL
    if mode in {"local", "dev"}:
        return _LOCAL_CLOUD_URL
    if os.getenv("NODE_ENV") == "production" or os.getenv("XM_ENV") == "production":
        return _PROD_CLOUD_URL
    # PyInstaller 打包后 sys.frozen=True
    if getattr(sys, "frozen", False) and ("--dev" not in sys.argv):
        return _PROD_CLOUD_URL
    return _LOCAL_CLOUD_URL


def is_token_expired(token: str, preempt_seconds: int = 300) -> bool:
    """安全检查 JWT 是否过期（不验签，仅检查时间戳）

    Args:
        token: JWT 字符串
        preempt_seconds: 提前多少秒判定为「即将过期」并触发续期（默认 5 分钟）
            这样能在 token 真正失效前就开始刷新，避免请求在 token 恰好过期时失败。
    """
    if not token:
        return True
    try:
        import jwt
        import time
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        if exp is not None:
            # 提前 preempt_seconds 秒判定为过期，主动触发续期
            return int(exp) < int(time.time()) + preempt_seconds
    except Exception:
        pass
    return True


def try_load_sso_token() -> Optional[str]:
    """从本地 SSO 会话文件读取真实 access_token"""
    try:
        from src.sso_bridge import read_sso_session
        session = read_sso_session()
        if session and session.get("access_token"):
            token = session["access_token"]
            if is_token_expired(token):
                logger.warning("[同步服务] 🔑 从 SSO 读取到的 access_token 已过期，正在尝试静默刷新...")
                try:
                    from src.sso_bridge import refresh_sso_token
                    # ⚠️ 修复：必须传入正确的 license API 地址。
                    # 无参调用会在生产包中回退到 http://127.0.0.1:42001（本地端口），
                    # 而打包生产环境该端口根本不存在，导致刷新 100% 失败。
                    try:
                        from src.utils.license_validator.env import SA_LICENSE_API
                        _license_api = SA_LICENSE_API
                    except Exception:
                        _license_api = None
                    if refresh_sso_token(_license_api):
                        session = read_sso_session()
                        if session and session.get("access_token"):
                            token = session["access_token"]
                            # 验证刷新后的 token 确实有效（不使用预刷新缓冲，用精确判断）
                            if not is_token_expired(token, preempt_seconds=0):
                                logger.info("[同步服务] 🔑 SSO access_token 静默刷新成功")
                                return token
                except Exception as refresh_err:
                    logger.warning(f"[同步服务] 🔑 静默刷新 SSO Token 异常: {refresh_err}")
                logger.warning("[同步服务] 🔑 从 SSO 读取到的 access_token 已过期且静默刷新失败")
                return None
            user = session.get("user", {})
            uid = user.get("id", "?")[:8] if user else "?"
            logger.info(f"[同步服务] 🔑 从 SSO 获取到真实 access_token（用户 {uid}...）")
            return token
    except Exception as e:
        logger.debug(f"[同步服务] SSO token 读取失败（可能用户未登录）: {e}")
    return None


def try_load_sso_user_id() -> Optional[str]:
    """从本地 SSO 会话文件读取真实 user_id"""
    try:
        from src.sso_bridge import read_sso_session
        session = read_sso_session()
        if session:
            user = session.get("user", {})
            return user.get("id")
    except Exception:
        pass
    return None


def decode_token_sub(token: str) -> Optional[str]:
    """安全解码 JWT 中的 sub 字段（不验签，仅读取）"""
    try:
        import jwt
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("sub")
    except Exception:
        return None


def cache_to_local(filename: str, data: Any):
    """将同步后端数据缓存到本地文件"""
    _CLOUD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CLOUD_CACHE_DIR / filename
    cache_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_from_cache(filename: str) -> Optional[Any]:
    """从本地缓存加载数据"""
    return load_cloud_cache_fast(filename)


def generate_dev_jwt_token(sso_user_id: str = None) -> str:
    """生成开发模式自签 JWT 令牌"""
    import jwt
    import time
    secret = os.getenv("JWT_SECRET")
    if not secret:
        secret = "xm-core-dev-secret-2026-very-secure-32bytes"
        logger.debug("[同步服务] JWT_SECRET 未设置，使用通用回退密钥")

    uid = sso_user_id or "local-desktop-user"
    token = jwt.encode({
        "sub": uid,
        "tenant_id": "default",
        "role": "admin",
        "roles": ["admin"],
        "app_id": None,
        "managed_tenant_id": None,
        "device_fp": None,
        "session_id": None,
        "exp": int(time.time()) + 10 * 365 * 24 * 3600,
        "iat": int(time.time())
    }, secret, algorithm="HS256")
    
    if uid != "local-desktop-user":
        logger.info(f"[同步服务] 自签授权盾（绑定真实用户 {uid[:8]}...）")
    else:
        logger.info("[同步服务] 自签授权盾（匿名兜底模式）")
    return token


def load_queue_from_disk(queue_file: Path) -> list[dict]:
    """启动时从本地加载未上报事件，支持异常退出后的重放"""
    try:
        if not queue_file.exists():
            return []
        raw = json.loads(queue_file.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            if raw:
                logger.info(f"[同步服务] ♻️ 已恢复 {len(raw)} 条本地待上传事件")
            return raw
    except Exception as e:
        logger.warning(f"[同步服务] 加载本地事件队列失败: {e}")
    return []
