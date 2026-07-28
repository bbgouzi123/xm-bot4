import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import asyncio

logger = logging.getLogger(__name__)

class UIAExecutor:
    """
    UIA 单线程套间串行执行器 (STA COM 串行队列)
    强制所有的 UIA 操作在唯一后台工作线程中排队执行，防止多账号或多任务并发时底层 COM 套间发生踩踏死锁。
    提供自动超时挂起检测与自愈轮换机制。
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_executor()
            return cls._instance

    def _init_executor(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia_worker")
        self._executor_lock = threading.Lock()
        self._generation = 1
        self._created_at = time.time()
        logger.info(f"UIAExecutor 初始化就绪, 当前 gen={self._generation}")

    def rotate_executor(self, reason: str):
        """
        轮换线程池自愈。丢弃并关闭已经挂起的线程池，启动全新的工作线程池。
        """
        with self._executor_lock:
            old_executor = self._executor
            self._generation += 1
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia_worker")
            self._created_at = time.time()
            if old_executor:
                try:
                    old_executor.shutdown(wait=False, cancel_futures=False)
                except Exception as ex:
                    logger.debug(f"释放旧 UIA 执行器异常: {ex}")
            logger.error(f"UIAExecutor 已触发自愈轮换: {reason}, 新 gen={self._generation}")

    async def execute(self, fn, *args, timeout: float = 12.0, **kwargs):
        """
        串行提交 UIA 动作并在指定超时内等待结果。
        """
        loop = asyncio.get_running_loop()
        
        with self._executor_lock:
            current_executor = self._executor

        def _wrapper():
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            try:
                return fn(*args, **kwargs)
            finally:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

        try:
            future = loop.run_in_executor(current_executor, _wrapper)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self.rotate_executor(f"操作 {fn.__name__ if hasattr(fn, '__name__') else str(fn)} 超时 ({timeout}s) 挂起")
            raise TimeoutError(f"UIA 操作超时挂起，已触发线程池轮换自愈机制")
