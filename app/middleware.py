"""HTTP 中间件（双通道认证）。"""

from __future__ import annotations

from fastapi import Request, Response

from app import constants
from app.state import API_KEY
from src.utils.trace_context import (
    new_trace_id as _new_trace_id,
    reset_trace_id as _reset_trace_id,
    set_trace_id as _set_trace_id,
)
from src.utils.xm_user_auth import extract_token_from_request, verify_xm_user_token


async def api_key_middleware(request: Request, call_next):
    """
    双通道认证中间件（XM-User JWT + X-API-Key）
    认证优先级：
    1. XM-User JWT Token → 验证通过后注入 request.state.xm_user
    2. X-API-Key 头 → 兼容前端 request() 和旧版调用
    3. 都没有 → 401 Unauthorized
    """
    path = request.url.path
    trace_id = (
        request.headers.get("X-Trace-Id")
        or request.headers.get("x-trace-id")
        or _new_trace_id()
    )
    request.state.trace_id = trace_id
    _trace_token = _set_trace_id(trace_id)

    def _with_trace(resp: Response) -> Response:
        try:
            resp.headers["X-Trace-Id"] = trace_id
        except Exception:
            pass
        return resp

    try:
        if (
            not path.startswith("/api/")
            or path.startswith("/api/openapi/v1/")
            or path.startswith("/api/avatar/")
            or path.startswith("/api/file/download/")
            or path == "/api/moment/screenshot/image"
            or path == "/api/health"
            or path == "/api/system/local-ips"
            or path == "/api/system/sse"
            or path.startswith("/api/v1/sso/")
            or path == "/api/v1/chat/export"
            or path == "/api/v1/chat/export/excel"
            or path == "/api/v1/crypto/handshake"
            or path.startswith("/api/screenshots/")
            or path.startswith("/api/xm-bot4/screenshots/")
            or any(path.startswith(p) for p in constants.CROSS_SERVICE_PREFIXES)
        ):
            resp = await call_next(request)
            return _with_trace(resp)

        token = extract_token_from_request(request)
        if token:
            claims = verify_xm_user_token(token)
            if claims:
                request.state.xm_user = claims
                # 🌟 用户发送了合法的核心业务请求，代表平台已成功完成登录并进入 Console
                try:
                    from src.utils.auth_session import set_platform_logged_in
                    set_platform_logged_in(True)
                except Exception:
                    pass
                resp = await call_next(request)
                return _with_trace(resp)
            else:
                import logging
                logging.getLogger("xm_user_auth").debug(
                    f"[认证中间件] JWT 验证失败 path={path} token前16={token[:16]}..."
                )

        key = request.headers.get("X-API-Key", "")
        if key == API_KEY:
            request.state.xm_user = None
            resp = await call_next(request)
            return _with_trace(resp)

        return _with_trace(
            Response(
                content='{"error": "Unauthorized"}',
                status_code=401,
                media_type="application/json",
            )
        )
    finally:
        _reset_trace_id(_trace_token)
