"""
XM-User 统一用户体系 — JWT 认证中间件
支持双认证策略：
1. XM-User JWT Token（Bearer 令牌 / Cookie）
2. 内部 X-API-Key（兼容现有前端调用）
"""
import os
import jwt
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("xm_user_auth")

# ======================== 配置 ========================

# XM-User JWT 签名密钥（与 XM-User 后端 .env 保持一致）
XM_USER_JWT_SECRET = os.environ.get("XM_USER_JWT_SECRET") or os.environ.get("JWT_SECRET")
if not XM_USER_JWT_SECRET:
    logger.warning("XM_USER_JWT_SECRET 未在环境变量中配置，使用通用回退密钥进行客户端验签保障")
    XM_USER_JWT_SECRET = "xm-core-dev-secret-2026-very-secure-32bytes"

# JWT 算法（XM-User 使用 HS256, 即 jsonwebtoken::Header::default()）
XM_USER_JWT_ALGORITHM = "HS256"


# ======================== 数据结构 ========================

@dataclass
class XMUserClaims:
    """从 XM-User JWT 解析出的用户信息"""
    sub: str          # 用户 ID (UUID)
    tenant_id: str    # 租户 ID (UUID)
    app_id: Optional[str] = None   # 应用 ID
    role: str = "user"             # 角色
    exp: int = 0                   # 过期时间戳
    iat: int = 0                   # 签发时间戳

    @property
    def user_id(self) -> str:
        """语义化访问：用户 ID = sub"""
        return self.sub


# 启动时打印密钥摘要，确认运行时密钥是否正确加载
#logger.warning(f"[xm_user_auth] JWT 密钥摘要: {XM_USER_JWT_SECRET[:8]}... (长度={len(XM_USER_JWT_SECRET)})")


# ======================== 核心函数 ========================

def verify_xm_user_token(token: str) -> Optional[XMUserClaims]:
    """
    验证 XM-User JWT Token，返回解析后的 Claims。
    验证失败返回 None（不抛异常，方便降级到 X-API-Key 认证）。
    """
    try:
        payload = jwt.decode(
            token,
            XM_USER_JWT_SECRET,
            algorithms=[XM_USER_JWT_ALGORITHM],
            leeway=10,  # P0修复：与前端 isTokenExpired 10s 缓冲保持一致（原为60s，误差过大）
            options={"verify_exp": True}
        )
        return XMUserClaims(
            sub=str(payload.get("sub", "")),
            tenant_id=str(payload.get("tenant_id", "")),
            app_id=str(payload["app_id"]) if payload.get("app_id") else None,
            role=payload.get("role", "user"),
            exp=payload.get("exp", 0),
            iat=payload.get("iat", 0),
        )
    except jwt.ExpiredSignatureError:
        logger.debug("Access Token 已过期，等待前端静默刷新")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"XM-User JWT 验签失败: {e} (密钥前缀={XM_USER_JWT_SECRET[:8]})")
        return None
    except Exception as e:
        logger.warning(f"XM-User JWT 解析异常: {e}")
        return None


def extract_token_from_request(request) -> Optional[str]:
    """
    从请求中提取 JWT Token。
    优先级：
    1. Authorization: Bearer <token>
    2. Cookie: xm_access_token=<token>
    """
    # 1. 从 Authorization 头提取
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    # 2. 从 Cookie 提取
    token = request.cookies.get("xm_access_token")
    if token:
        return token

    return None
