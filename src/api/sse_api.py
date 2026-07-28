"""
SSE (Server-Sent Events) API 路由服务
"""
import asyncio
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from src.utils.sse_manager import sse_manager

router = APIRouter()


@router.get("/api/system/sse")
async def system_sse_stream(request: Request):
    """Server-Sent Events (SSE) 单向推送通道，用于推送配置更新、规则话术等系统消息"""
    async def event_generator():
        queue = sse_manager.add_listener()
        try:
            # 首次建立连接，推送连接建立成功的握手包
            yield "data: {\"action\": \"connected\", \"message\": \"SSE connection established\"}\n\n"
            while True:
                # 检查连接是否断开
                if await request.is_disconnected():
                    break
                try:
                    # 等待消息，并配置 15 秒的心跳保持
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 推送心跳数据，防止代理网关层因为长连接无数据吞吐而强行掐断
                    yield "data: {\"action\": \"ping\", \"message\": \"keep-alive\"}\n\n"
        finally:
            sse_manager.remove_listener(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 告知 Nginx 立即推送，不进行 Buffer 堆积
        }
    )
