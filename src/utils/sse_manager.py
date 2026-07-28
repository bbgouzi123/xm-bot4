"""
SSE (Server-Sent Events) 单向推送广播管理器
"""
import asyncio
import logging
from typing import List

logger = logging.getLogger(__name__)


class SSEManager:
    """管理 SSE 连接订阅者的单例工具，支持跨线程安全推送通知"""

    def __init__(self):
        self._listeners: List[asyncio.Queue] = []

    def add_listener(self) -> asyncio.Queue:
        """为每一个 SSE 连接创建一个独立的事件通道"""
        queue = asyncio.Queue()
        self._listeners.append(queue)
        logger.debug(f"[SSE] 新增订阅者，当前在线连接数: {len(self._listeners)}")
        return queue

    def remove_listener(self, queue: asyncio.Queue):
        """连接断开时，移除对应的事件通道"""
        if queue in self._listeners:
            self._listeners.remove(queue)
            logger.debug(f"[SSE] 移除订阅者，当前在线连接数: {len(self._listeners)}")

    def notify(self, action: str, message: str = ""):
        """广播推送事件给所有客户端连接（支持在主线程、异步 Loop 以及普通工作线程中安全调用）"""
        if not self._listeners:
            return

        payload = {"action": action, "message": message}
        logger.info(f"[SSE] 广播通知事件: action={action}, message={message}")

        # 1. 尝试当前线程正在运行的 asyncio Event Loop
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if loop and loop.is_running():
            for queue in self._listeners:
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            return

        # 2. 尝试主应用的全局主 Event Loop（如果在同步子线程里）
        try:
            import app.state as app_state
            main_loop = getattr(app_state, "main_loop", None)
            if main_loop and main_loop.is_running():
                for queue in self._listeners:
                    main_loop.call_soon_threadsafe(queue.put_nowait, payload)
                return
        except Exception:
            pass

        # 3. 兜底策略：在当前进程上下文中直接投放（例如极其边缘的无 Loop 场景）
        for queue in self._listeners:
            try:
                queue.put_nowait(payload)
            except Exception as e:
                logger.warning(f"[SSE] 推送消息失败: {e}")


# 全局单例
sse_manager = SSEManager()
