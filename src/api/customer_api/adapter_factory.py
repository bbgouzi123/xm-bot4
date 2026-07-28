import logging
import asyncio
from typing import Dict, Any, Optional
from .base_adapter import BaseCustomerAdapter
from .standard_adapter import StandardCustomerAdapter
from .custom_adapter import CustomCustomerAdapter
from .validate_mobile_adapter import ValidateMobileAdapter

logger = logging.getLogger(__name__)

class CustomerAdapterFactory:
    """客户 API 适配器工厂（支持热加载配置，零重启）"""
    
    _config: Dict[str, Any] = {}
    _push_adapter: Optional[BaseCustomerAdapter] = None
    _mobile_adapter: Optional[ValidateMobileAdapter] = None

    @classmethod
    def load_config(cls, config: Dict[str, Any]):
        """加载或更新配置，更新内存中的适配器实例"""
        cls._config = config
        
        # 1. 初始化推送适配器
        push_cfg = config.get("push_settings", {})
        adapter_type = push_cfg.get("type", "standard")
        if adapter_type == "custom":
            cls._push_adapter = CustomCustomerAdapter(push_cfg)
        else:
            cls._push_adapter = StandardCustomerAdapter(push_cfg)
            
        # 2. 初始化手机号校验适配器
        mobile_cfg = config.get("mobile_validation_settings", {})
        cls._mobile_adapter = ValidateMobileAdapter(mobile_cfg)
        logger.info(f"[AdapterFactory] 热加载配置成功，推送适配器类型: {adapter_type}")

    @classmethod
    def get_push_adapter(cls) -> Optional[BaseCustomerAdapter]:
        return cls._push_adapter

    @classmethod
    def get_mobile_adapter(cls) -> Optional[ValidateMobileAdapter]:
        return cls._mobile_adapter


# ==================== 异步非阻塞事件推送队列 ====================

_event_queue: Optional[asyncio.Queue] = None
_worker_task: Optional[asyncio.Task] = None

def _get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        import app.state as app_state
        if hasattr(app_state, "main_loop") and app_state.main_loop:
            return app_state.main_loop
    except Exception:
        pass
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None

def _put_event(event_type: str, payload: Dict[str, Any]):
    global _event_queue
    if _event_queue is None:
        _event_queue = asyncio.Queue()
    _event_queue.put_nowait((event_type, payload))

async def _queue_worker():
    logger.info("[CustomerAdapterQueue] 队列消费 Worker 线程循环启动")
    while True:
        try:
            if _event_queue is None:
                await asyncio.sleep(1)
                continue
            event_type, payload = await _event_queue.get()
            adapter = CustomerAdapterFactory.get_push_adapter()
            if adapter:
                await adapter.notify_event(event_type, payload)
            _event_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[CustomerAdapterQueue] 队列推送消费异常: {e}")
            await asyncio.sleep(1)

def start_queue_worker():
    global _worker_task, _event_queue
    loop = _get_main_loop()
    if not loop or not loop.is_running():
        return

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop != loop:
        loop.call_soon_threadsafe(start_queue_worker)
        return

    if _event_queue is None:
        _event_queue = asyncio.Queue()

    if _worker_task is None or _worker_task.done():
        _worker_task = loop.create_task(_queue_worker())
        logger.info("[CustomerAdapterQueue] 队列消费 Worker 已注册并启动")

def submit_event(event_type: str, payload: Dict[str, Any]):
    """向异步队列提交推送事件，支持跨线程安全调用"""
    loop = _get_main_loop()
    if loop and loop.is_running():
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop == loop:
            _put_event(event_type, payload)
            start_queue_worker()
        else:
            loop.call_soon_threadsafe(_put_event, event_type, payload)
            loop.call_soon_threadsafe(start_queue_worker)
    else:
        logger.warning(f"[CustomerAdapterQueue] 未找到运行中的事件循环，事件 {event_type} 无法投递")

