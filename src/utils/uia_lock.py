import asyncio
import threading
import time
import logging
import heapq
import contextvars
from enum import IntEnum
from contextlib import asynccontextmanager, contextmanager
from typing import Dict, Set, Optional, List, Any, Tuple

logger = logging.getLogger(__name__)


class UIATaskPriority(IntEnum):
    """UIA 任务优先级"""
    LOW = 0        # 朋友圈排期自动发送
    NORMAL = 5     # 聊天自动回复
    HIGH = 10      # 用户手动操作


class PermissionRequest:
    """排队许可请求实体"""
    def __init__(self, task_name: str, priority: int, is_async: bool = True):
        self.task_name = task_name
        self.priority = priority
        self.timestamp = time.time()
        self.is_async = is_async
        self.event = asyncio.Event() if is_async else threading.Event()

    def __lt__(self, other: 'PermissionRequest') -> bool:
        if self.priority != other.priority:
            return self.priority > other.priority  # 数值越大越优先
        return self.timestamp < other.timestamp    # 先进先出


# 上下文变量，存储当前协程/同步调用栈持有的锁请求，用以支持重入
_current_holder = contextvars.ContextVar("_current_holder", default=None)


class UIALock:
    """全局 UIA 操作互斥锁与优先级排队管理器"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._meta_lock = threading.Lock()
        self._queue: List[PermissionRequest] = []
        self._current_request: Optional[PermissionRequest] = None
        self._owner = ""           # 当前持锁的任务名
        self._owner_priority = -1  # 当前任务优先级
        self._acquire_time = 0     # 获取锁的时间
        self._pause_count = 0      # 挂起的引用计数
        self._initialized = True

    @property
    def is_busy(self) -> bool:
        with self._meta_lock:
            return self._current_request is not None

    @property
    def current_task(self) -> str:
        with self._meta_lock:
            return self._owner

    @property
    def current_priority(self) -> int:
        with self._meta_lock:
            return self._owner_priority

    def _is_high_risk_task(self, task_name: str) -> bool:
        """判断该任务是否为高危前台模拟任务"""
        high_risk_keywords = [
            "朋友圈", "跟单", "加人", "加好友", "新朋友", "拉群", "群发", "添加好友",
            "moment", "friend", "group", "mass_send", "enroll", "follow"
        ]
        name_lower = task_name.lower()
        return any(k in name_lower for k in high_risk_keywords)

    def _on_lock_acquired(self, task_name: str):
        """成功持锁时的监控静默联动"""
        if self._is_high_risk_task(task_name):
            self._pause_count += 1
            if self._pause_count == 1:
                try:
                    from app import state
                    if hasattr(state, 'monitor') and state.monitor:
                        state.monitor.pause()
                        logger.info(f"[UIA 锁] 任务 '{task_name}' 属于高危模拟，已自动挂起主自动回复监控 (引用计数={self._pause_count})")
                    
                    if hasattr(state, 'account_manager') and state.account_manager:
                        for inst in getattr(state.account_manager, '_instances', {}).values():
                            if hasattr(inst, 'monitor') and inst.monitor:
                                inst.monitor.pause()
                        logger.info(f"[UIA 锁] 已自动挂起所有多账号实例的自动回复监控")
                except Exception as e:
                    logger.error(f"[UIA 锁] 尝试联动挂起自动回复监控异常: {e}")

    def _on_lock_released(self, task_name: str):
        """释放锁时的监控恢复联动"""
        if self._is_high_risk_task(task_name):
            self._pause_count = max(0, self._pause_count - 1)
            if self._pause_count == 0:
                try:
                    from app import state
                    if hasattr(state, 'monitor') and state.monitor:
                        state.monitor.resume()
                        logger.info(f"[UIA 锁] 任务 '{task_name}' 释放锁，已自动恢复主自动回复监控")
                    
                    if hasattr(state, 'account_manager') and state.account_manager:
                        for inst in getattr(state.account_manager, '_instances', {}).values():
                            if hasattr(inst, 'monitor') and inst.monitor:
                                inst.monitor.resume()
                        logger.info(f"[UIA 锁] 已自动恢复所有多账号实例的自动回复监控")
                except Exception as e:
                    logger.error(f"[UIA 锁] 尝试联动恢复自动回复监控异常: {e}")

    def try_acquire(self, task_name: str = "", priority: int = UIATaskPriority.NORMAL) -> bool:
        """非阻塞尝试获取锁"""
        with self._meta_lock:
            if self._current_request is None:
                req = PermissionRequest(task_name, priority, is_async=True)
                self._current_request = req
                self._owner = task_name
                self._owner_priority = priority
                self._acquire_time = time.time()
                logger.debug(f"[UIA 锁] try_acquire 成功: {task_name}")
                self._on_lock_acquired(task_name)
                return True
            return False

    def _release_without_check(self):
        """无校验释放锁"""
        if self._current_request is None:
            return
        
        old_task = self._owner
        self._on_lock_released(old_task)
        
        if self._queue:
            next_req = heapq.heappop(self._queue)
            self._current_request = next_req
            self._owner = next_req.task_name
            self._owner_priority = next_req.priority
            self._acquire_time = time.time()
            
            logger.debug(f"[UIA 锁] 释放 '{old_task}'，唤醒优先级最高任务: '{next_req.task_name}'")
            self._on_lock_acquired(next_req.task_name)
            
            if next_req.is_async:
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(next_req.event.set)
                except RuntimeError:
                    next_req.event.set()
            else:
                next_req.event.set()
        else:
            self._current_request = None
            self._owner = ""
            self._owner_priority = -1
            self._acquire_time = 0
            logger.debug(f"[UIA 锁] 释放 '{old_task}'，锁目前闲置")

    def release(self, request: Optional[PermissionRequest] = None):
        """释放锁，如果指定了 request，则仅在该 request 为当前持锁者时释放，防止残留线程错释放新锁"""
        with self._meta_lock:
            if self._current_request is None:
                return
            if request is not None and self._current_request != request:
                logger.debug(f"[UIA 锁] 拦截非当前持锁请求的释放尝试: request={request.task_name}, 当前持锁={self._owner}")
                return
            self._release_without_check()

    def force_release(self):
        """强行释放当前占用的锁，用于超时熔断等紧急恢复场景"""
        with self._meta_lock:
            if self._current_request is not None:
                logger.warning(f"[UIA 锁] ❗强行释放当前占用的锁: owner='{self._owner}'")
                self._release_without_check()

    async def acquire_async(self, priority: int, task_name: str, timeout: float = 60) -> bool:
        """异步排队获取锁"""
        req = PermissionRequest(task_name, priority, is_async=True)
        
        with self._meta_lock:
            if self._current_request is None:
                self._current_request = req
                self._owner = task_name
                self._owner_priority = priority
                self._acquire_time = time.time()
                logger.debug(f"[UIA 锁] 成功直接获取锁(异步): {task_name}")
                self._on_lock_acquired(task_name)
                return True
            
            heapq.heappush(self._queue, req)
            logger.debug(f"[UIA 锁] 排队等待(异步): {task_name} (当前被 '{self._owner}' 占用)")
            
        try:
            await asyncio.wait_for(req.event.wait(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError) as err:
            with self._meta_lock:
                if req in self._queue:
                    self._queue.remove(req)
                    heapq.heapify(self._queue)
                if self._current_request == req:
                    self._current_request = None
            raise TimeoutError(f"[UIA 锁] 异步等待超时 ({timeout}s)，当前被 '{self._owner}' 占用") from err

    @contextmanager
    def sync_acquire(self, priority: int = UIATaskPriority.NORMAL, 
                     task_name: str = "", timeout: float = 60):
        """同步上下文管理器（支持上下文重入）"""
        holder = _current_holder.get()
        if holder is not None:
            logger.debug(f"[UIA 锁] 检测到重入(同步): '{task_name}' (外层已持有: '{holder.task_name}')")
            yield
            return

        req = PermissionRequest(task_name, priority, is_async=False)
        
        with self._meta_lock:
            if self._current_request is None:
                self._current_request = req
                self._owner = task_name
                self._owner_priority = priority
                self._acquire_time = time.time()
                logger.debug(f"[UIA 锁] 成功直接获取锁(同步): {task_name}")
                self._on_lock_acquired(task_name)
                acquired = True
            else:
                heapq.heappush(self._queue, req)
                logger.debug(f"[UIA 锁] 排队等待(同步): {task_name} (当前被 '{self._owner}' 占用)")
                acquired = False
                
        if not acquired:
            success = req.event.wait(timeout=timeout)
            if not success:
                with self._meta_lock:
                    if req in self._queue:
                        self._queue.remove(req)
                        heapq.heapify(self._queue)
                    if self._current_request == req:
                        self._current_request = None
                raise TimeoutError(f"[UIA 锁] 同步等待超时 ({timeout}s)，当前被 '{self._owner}' 占用")
                
        token = _current_holder.set(req)
        try:
            yield
        finally:
            _current_holder.reset(token)
            self.release(req)

    @asynccontextmanager
    async def __call__(self, priority: int = UIATaskPriority.NORMAL,
                       task_name: str = "", timeout: float = 60):
        """异步上下文管理器（支持上下文重入）"""
        holder = _current_holder.get()
        if holder is not None:
            logger.debug(f"[UIA 锁] 检测到重入(异步): '{task_name}' (外层已持有: '{holder.task_name}')")
            yield
            return

        await self.acquire_async(priority, task_name, timeout)
        req = self._current_request
        token = _current_holder.set(req)
        try:
            yield
        finally:
            _current_holder.reset(token)
            self.release(req)

    def get_status(self) -> dict:
        """获取锁状态"""
        with self._meta_lock:
            return {
                "busy": self._current_request is not None,
                "current_task": self._owner,
                "priority": self._owner_priority,
                "held_seconds": round(time.time() - self._acquire_time, 1) if self._acquire_time else 0,
                "waiting_count": len(self._queue),
                "pause_count": self._pause_count,
            }


# 全局单例
uia_lock = UIALock()
