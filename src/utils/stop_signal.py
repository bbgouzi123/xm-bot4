import threading
import time
import ctypes
import logging

logger = logging.getLogger(__name__)

class StopSignal:
    """全局自动化停止信号（单例）
    
    信号策略：
    - ESC 按下后信号持久保持，不会自动重置
    - 只有任务上下文管理器的 finally 块中才调用 reset() 清除信号
    - 这样确保即使任务循环中的检查点间隔较长，信号也不会丢失
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._stop_requested = False
                cls._instance._listener_thread = None
                cls._instance._ignored = False
            return cls._instance

    @property
    def is_stopped(self) -> bool:
        return self._stop_requested

    def ignore_esc(self):
        """支持 with 语句的上下文管理器，用于临时忽略模拟发送 ESC 产生的停止信号"""
        class EscIgnorer:
            def __init__(self, parent):
                self.parent = parent
            def __enter__(self):
                self.parent._ignored = True
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                # 冷却 200ms，避开按键消息队列和物理轮询延迟
                time.sleep(0.2)
                self.parent._ignored = False
        return EscIgnorer(self)

    def request_stop(self, reason: str = "用户按下 ESC"):
        if not self._stop_requested:
            self._stop_requested = True
            print(f"\n[停止信号] {reason}，请求立即终止所有自动化任务")
            
            # 强制解除物理锁定
            try:
                from src.uia.input_guard import uia_lock as input_guard
                input_guard._block_input(False)
                input_guard._notify_frontend("unlock", "已通过 ESC 紧急停止")
            except Exception:
                pass
            
            # 发送 WebSocket 通知
            try:
                from src.utils.websocket_manager import ws_manager
                import asyncio
                payload = {"type": "stop_signal", "reason": reason}
                
                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        asyncio.ensure_future(ws_manager.broadcast(payload))
                except RuntimeError:
                    pass
            except Exception:
                pass

    def reset(self):
        """重置停止信号。仅应由任务完成/退出后调用。"""
        self._stop_requested = False

    def start_listener(self):
        """物理级轮询线程已被安全废弃。
        
        锁定期间的 ESC 检测由低级钩子（input_guard）以高精度（支持模拟按键过滤）直接拦截处理。
        非锁定期间，用户键鼠自由，无须通过 ESC 键中断后台。这能杜绝任何非锁定状态下的误触发。
        """
        logger.info("[停止信号] 全局 ESC 物理轮询已按需停用，改为由低级输入钩子(InputGuard)在锁定期间精准接管")
        return

stop_signal = StopSignal()
