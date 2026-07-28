import logging
import asyncio

logger = logging.getLogger(__name__)

class AccountReadyBarrier:
    """原子就绪栅栏：阻塞前端 API 请求直至当前微信实例切换、重载与同步完毕"""
    def __init__(self):
        self._event = None
        self._loop = None
        self._trace_id = ""

    def _init_event_in_loop(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._event is None or self._loop != loop:
            self._event = asyncio.Event()
            self._event.set()  # 默认就绪
            self._loop = loop

    def clear(self, trace_id: str = ""):
        self._trace_id = trace_id
        
        # 寻找运行中的 asyncio event loop
        from app import state as app_state
        loop = getattr(app_state, "main_loop", None)
        if not loop:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                pass
                
        if loop and loop.is_running():
            def _clear():
                self._init_event_in_loop()
                if self._event:
                    self._event.clear()
            loop.call_soon_threadsafe(_clear)
            logger.info(f"[ReadyBarrier] 🚨 [ThreadSafe] 已清空就绪信号，阻塞后续 CRM/联系人请求 (Trace ID: {trace_id})")
        else:
            self._init_event_in_loop()
            if self._event:
                self._event.clear()
            logger.info(f"[ReadyBarrier] 🚨 [Sync] 已清空就绪信号，阻塞后续 CRM/联系人请求 (Trace ID: {trace_id})")

    def set(self):
        from app import state as app_state
        loop = getattr(app_state, "main_loop", None)
        if not loop:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                pass
                
        if loop and loop.is_running():
            def _set():
                self._init_event_in_loop()
                if self._event:
                    self._event.set()
            loop.call_soon_threadsafe(_set)
            logger.info(f"[ReadyBarrier] ✅ [ThreadSafe] 已设置就绪信号，释放所有阻塞请求 (Trace ID: {self._trace_id})")
        else:
            self._init_event_in_loop()
            if self._event:
                self._event.set()
            logger.info(f"[ReadyBarrier] ✅ [Sync] 已设置就绪信号，释放所有阻塞请求 (Trace ID: {self._trace_id})")

    async def wait_until_ready(self, timeout: float = 30.0):
        self._init_event_in_loop()
        if self._event is None:
            return
        if not self._event.is_set():
            logger.info(f"[ReadyBarrier] ⏳ 信号未就绪，正在阻塞 API 请求 (Trace ID: {self._trace_id})...")
            try:
                await asyncio.wait_for(self._event.wait(), timeout=timeout)
                logger.info(f"[ReadyBarrier] 🚀 阻塞请求已释放 (Trace ID: {self._trace_id})")
            except asyncio.TimeoutError:
                logger.warning(f"[ReadyBarrier] ⚠️ 阻塞超时 ({timeout}s)，强制释放请求 (Trace ID: {self._trace_id})")

ready_barrier = AccountReadyBarrier()
