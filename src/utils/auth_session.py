"""
auth_session.py — xm-bot4 平台登录状态检测工具

提供轻量级的本地 SSO session 校验，不发起任何网络请求。
只要本地 SSO 文件存在且 access_token 的 JWT exp 未过期，即视为已登录。

典型用法：
    from src.utils.auth_session import has_active_platform_session
    if has_active_platform_session():
        # 启动需要授权的后台功能（如 WCDB 引擎）
        ...
"""
from __future__ import annotations

import json
import time


# 内存中的平台当前登录状态
# 严格锁定在“用户实际登录进入系统”后才允许连接微信数据库，防止登录前乱序解密
_platform_logged_in = False


def set_platform_logged_in(val: bool):
    """置位当前平台登录内存标志。"""
    global _platform_logged_in
    if _platform_logged_in != val:
        print(f"[认证状态] 平台登录内存标志变更: {_platform_logged_in} -> {val}")
        _platform_logged_in = val


def is_platform_logged_in() -> bool:
    """查询平台当前是否已经处于登录后的 Console 状态。"""
    return _platform_logged_in


def has_active_platform_session() -> bool:
    """
    检测 xm-bot4 平台用户是否已登录。

    引入双轨验证机制：
    1. 必须在内存标志 _platform_logged_in 为 True 时（即已过中间件认证或已存登录）才允许通过。
    2. 本地 SSO 会话文件存在且 Token 未过期。
    """
    if not is_platform_logged_in():
        return False

    try:
        from src.sso_bridge import read_sso_session
        session = read_sso_session()
        if not session:
            return False

        # session 可能是 dict（from xm_py_server）或包含 access_token 的结构
        if isinstance(session, str):
            try:
                session = json.loads(session)
            except Exception:
                return False

        # 兼容两种 session 结构：
        #   { "access_token": "...", "user": { "id": "..." } }
        #   { "user": { "access_token": "..." } }
        access_token: str = ""
        if isinstance(session, dict):
            access_token = (
                session.get("access_token")
                or session.get("user", {}).get("access_token")
                or ""
            )

        if not access_token:
            return False

        # JWT 快速解码：只检查 exp 字段，不验证签名
        parts = access_token.split(".")
        if len(parts) != 3:
            return False

        import base64
        # 补全 Base64url 填充（JWT payload 可能省略 =）
        b64 = parts[1].replace("-", "+").replace("_", "/")
        padding = 4 - len(b64) % 4
        if padding != 4:
            b64 += "=" * padding

        payload = json.loads(base64.b64decode(b64).decode("utf-8", errors="ignore"))
        exp = payload.get("exp")
        if not exp:
            # 没有 exp 字段的 token，保守认为有效（内部颁发的永久 token）
            return True

        # 留 30 秒缓冲，与前端 detectValidLocalToken 保持一致
        return (exp - 30) > time.time()

    except Exception:
        # 任何异常都保守返回 False，不允许未登录用户触发重型后台操作
        return False
