"""Cross-service reverse proxy (HTTP + WebSocket)."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from xm_py_server.proxy_forward import PROXY_FORWARD_HEADERS, PROXY_SKIP_RESPONSE_HEADERS

from app import constants
from app.proxy_utils import resolve_target, get_proxy_client, http_base_to_ws

router = APIRouter()


def _http_base_to_ws(url: str) -> str:
    return http_base_to_ws(url)


@router.websocket("/api/xm-user/ws/{path:path}")
async def cross_service_ws_xm_user(websocket: WebSocket, path: str):
    """
    xm-user 设备事件 WebSocket 透明代理。
    桌面 pywebview 直连本机 42041 时，浏览器会连 ws://127.0.0.1:42041/api/xm-user/ws/...；
    若仅走 httpx HTTP 反代，握手会得到 500。此处用 websockets 客户端与上游建连并双向转发。
    """
    import logging

    _log = logging.getLogger("proxy")
    req_path = f"/api/xm-user/ws/{path}"
    try:
        target_http, _ = resolve_target("/api/xm-user", req_path)
    except Exception as e:
        _log.error(f"[WS代理] 解析 xm-user 目标失败: {e}")
        await websocket.close(code=1008)
        return
    upstream = _http_base_to_ws(target_http)
    q = websocket.url.query
    if q:
        upstream = f"{upstream}?{q}"
    try:
        import websockets
        from websockets.exceptions import ConnectionClosed
    except ImportError:
        _log.error("[WS代理] 未安装 websockets 包，无法代理 xm-user WebSocket")
        await websocket.close(code=1011)
        return

    await websocket.accept()
    try:
        import ssl
        ssl_context = None
        if upstream.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(
            upstream,
            ping_interval=20,
            ping_timeout=60,
            max_size=10 * 1024 * 1024,
            ssl=ssl_context,
        ) as remote:

            async def pump_browser_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        mtype = msg.get("type")
                        if mtype == "websocket.disconnect":
                            break
                        if mtype != "websocket.receive":
                            continue
                        if "text" in msg:
                            await remote.send(msg["text"])
                        elif "bytes" in msg:
                            await remote.send(msg["bytes"])
                except (WebSocketDisconnect, ConnectionClosed):
                    pass

            async def pump_upstream_to_browser():
                try:
                    async for raw in remote:
                        if isinstance(raw, str):
                            await websocket.send_text(raw)
                        else:
                            await websocket.send_bytes(raw)
                except (WebSocketDisconnect, ConnectionClosed):
                    pass

            t_in = asyncio.create_task(pump_browser_to_upstream())
            t_out = asyncio.create_task(pump_upstream_to_browser())
            try:
                await asyncio.wait(
                    (t_in, t_out),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                t_in.cancel()
                t_out.cancel()
                try:
                    await t_in
                except (asyncio.CancelledError, WebSocketDisconnect, ConnectionClosed):
                    pass
                try:
                    await t_out
                except (asyncio.CancelledError, WebSocketDisconnect, ConnectionClosed):
                    pass
    except Exception as e:
        _log.error(f"[WS代理] xm-user WebSocket 上游异常: {upstream} — {type(e).__name__}: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass



@router.api_route("/api/xm-user/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/api/xm-store/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/api/xm-bot4-cloud/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/api/xm-dragonscale/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/api/xm-oss/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/api/xm-sentinel/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/api/xm-mashangchaqi/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/api/xm-regionhub/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def _cross_service_proxy(request: Request, path: str):
    """跨服务反向代理 — 打包后桌面应用的统一网关"""
    # 匹配请求对应的服务前缀
    req_path = request.url.path
    matched_prefix = None
    for prefix in constants.CROSS_SERVICE_PREFIXES:
        if req_path.startswith(prefix):
            matched_prefix = prefix
            break
    if not matched_prefix:
        return Response(content='{"error": "proxy: unknown service"}', status_code=502)

    target_url, target_is_local = resolve_target(matched_prefix, req_path)
    # 拼接查询参数
    if request.url.query:
        target_url += "?" + request.url.query

    # 构造转发请求头（仅透传白名单头，避免 host/connection 等干扰）
    def safe_latin1(s: str) -> str:
        return s.encode("latin-1", "ignore").decode("latin-1")

    forward_headers = {}
    for k, v in request.headers.items():
        if k.lower() in PROXY_FORWARD_HEADERS:
            forward_headers[k] = safe_latin1(v)

    # 注入内网信任标记：让目标服务的加密中间件放行明文请求
    # 生产环境下各服务默认强制加密，此头告知"请求来自可信的同机代理"
    _internal_secret = os.getenv("XM_INTERNAL_PROXY_SECRET", "xm-internal-2026")
    forward_headers["X-XM-Internal-Proxy"] = _internal_secret

    is_sse_request = False
    try:
        body = await request.body()
        # SSE 请求识别：Accept 含 event-stream 或路径以 /stream 结尾
        accept_hdr = request.headers.get("accept", "").lower()
        is_sse_request = ("event-stream" in accept_hdr or req_path.endswith("/stream"))
        client = get_proxy_client(is_sse=is_sse_request)
        req = client.build_request(
            method=request.method,
            url=target_url,
            headers=forward_headers,
            content=body if body else None,
        )

        try:
            if target_is_local and not is_sse_request:
                # 对于本地开发服务，使用专用短超时客户端以防本地进程卡死导致前端无限等待
                local_timeout = httpx.Timeout(3.0, connect=1.5, read=3.0)
                async with httpx.AsyncClient(timeout=local_timeout, verify=False, follow_redirects=True, trust_env=True) as local_client:
                    resp = await local_client.send(req, stream=True)
            else:
                resp = await client.send(req, stream=True)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            # 本地连接失败或超时，且目标是本地，则容灾回退到线上网关
            if target_is_local:
                prod_url = constants.CROSS_SERVICE_MAP[matched_prefix]["prod"] + req_path[len(matched_prefix):]
                if request.url.query:
                    prod_url += "?" + request.url.query
                req = client.build_request(
                    method=request.method,
                    url=prod_url,
                    headers=forward_headers,
                    content=body if body else None,
                )
                resp = await client.send(req, stream=True)
            else:
                raise e

        # 本地服务偶发异常时，自动回退到线上网关再尝试一次（仅在本地命中 + 5xx 生效）
        if target_is_local and resp.status_code >= 500:
            await resp.aclose()
            prod_url = constants.CROSS_SERVICE_MAP[matched_prefix]["prod"] + req_path[len(matched_prefix):]
            if request.url.query:
                prod_url += "?" + request.url.query
            req = client.build_request(
                method=request.method,
                url=prod_url,
                headers=forward_headers,
                content=body if body else None,
            )
            resp = await client.send(req, stream=True)

        # 构造回传响应头
        response_headers = {}
        media_type = None
        for k, v in resp.headers.items():
            if k.lower() not in PROXY_SKIP_RESPONSE_HEADERS:
                response_headers[k] = safe_latin1(v)
            if k.lower() == "content-type":
                media_type = v

        is_event_stream = media_type and "event-stream" in media_type.lower()

        if is_event_stream:
            async def stream_iterator():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                except httpx.TimeoutException:
                    logging.getLogger("proxy").warning(f"[代理] 流迭代读取超时: {target_url}")
                except httpx.HTTPError as he:
                    logging.getLogger("proxy").warning(f"[代理] 流迭代发生 HTTP 异常: {he} — {target_url}")
                except Exception as e:
                    logging.getLogger("proxy").error(f"[代理] 流迭代未知异常: {e}")
                finally:
                    await resp.aclose()

            return StreamingResponse(
                stream_iterator(),
                status_code=resp.status_code,
                headers=response_headers,
                media_type=media_type,
            )
        else:
            # 非 SSE 响应一次性读完，带 Content-Length 返回，彻底消除 Tauri 读体挂起
            content = await resp.aread()
            await resp.aclose()
            return Response(
                content=content,
                status_code=resp.status_code,
                headers=response_headers,
                media_type=media_type,
            )
    except httpx.ConnectError:
        if is_sse_request:
            async def empty_stream():
                yield b""
            return StreamingResponse(empty_stream(), media_type="text/event-stream")
        return Response(
            content='{"error": "proxy: target service unreachable"}',
            status_code=502,
            media_type="application/json",
        )
    except httpx.TimeoutException:
        if is_sse_request:
            async def empty_stream():
                yield b""
            return StreamingResponse(empty_stream(), media_type="text/event-stream")
        return Response(
            content='{"error": "proxy: target service timeout"}',
            status_code=504,
            media_type="application/json",
        )
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e)
        if err_name in ("CancelledError", "RuntimeError", "ConnectionStateError", "WebSocketDisconnect", "ClientDisconnect") or "stream consumed" in err_msg.lower():
            logging.getLogger("proxy").debug(f"[代理] 转发已取消或断开 ({err_name}): {err_msg}")
        else:
            logging.getLogger("proxy").warning(f"[代理] 转发异常 ({err_name}): {err_msg}")
        if is_sse_request:
            async def empty_stream():
                yield b""
            return StreamingResponse(empty_stream(), media_type="text/event-stream")
        return Response(
            content='{"error": "proxy: internal error"}',
            status_code=502,
            media_type="application/json",
        )


cross_service_router = router
