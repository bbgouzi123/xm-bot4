"""
UIA 任务统一门面（增强版，支持超时熔断与卡死预警）
"""
from __future__ import annotations

import asyncio
import time
import threading
import logging
import sys
import traceback
from datetime import datetime
from contextlib import contextmanager
from typing import Callable, TypeVar
from concurrent.futures import ThreadPoolExecutor

try:
    import win32gui
except ImportError:
    win32gui = None

from src.utils.uia_lock import uia_lock, UIATaskPriority
from contextlib import contextmanager

if win32gui is None:
    class UIAInterruptError(Exception):
        pass

    @contextmanager
    def physical_lock(msg):
        yield
else:
    from src.uia.input_guard import uia_lock as physical_lock, UIAInterruptError
from src.utils.stop_signal import stop_signal

logger = logging.getLogger(__name__)

T = TypeVar("T")

_maintenance_lock = threading.Lock()
_maintenance_depth = 0
_maintenance_reason = ""

# ==================== 异常熔断与报警状态 ====================
from src.utils.uia_circuit_breaker import (
    report_uia_success,
    report_uia_failure,
    is_engine_suspended,
    is_session_fused,
    resume_engine,
    suspend_engine,
)


def is_uia_maintenance_active() -> bool:
    """当前是否处于 UIA 维护窗口。"""
    with _maintenance_lock:
        if _maintenance_depth > 0:
            return True
    try:
        from src.utils.uia_lock import uia_lock
        return uia_lock.is_busy
    except Exception:
        return False


def get_uia_maintenance_reason() -> str:
    """维护窗口原因（用于日志）。"""
    with _maintenance_lock:
        return _maintenance_reason


@contextmanager
def uia_maintenance(reason: str):
    """标记一个会抢前台焦点的长 UIA 任务正在执行。"""
    global _maintenance_depth, _maintenance_reason
    with _maintenance_lock:
        _maintenance_depth += 1
        _maintenance_reason = reason
    try:
        yield
    finally:
        with _maintenance_lock:
            _maintenance_depth = max(0, _maintenance_depth - 1)
            if _maintenance_depth == 0:
                _maintenance_reason = ""


# ==================== 白屏预检（纯观测，不干扰用户操作） ====================

_precheck_last_time = 0.0           # 上次预检的时间戳，用于节流
_PRECHECK_MIN_INTERVAL = 30.0       # 最小预检间隔（秒）

def _pre_check_uia_health():
    """轻量与白屏预检相结合：实时检查微信窗口句柄存活状态与主线程响应度，卡死或失效时立即拦截以防 COM 崩溃。"""
    if win32gui is None:
        return
    try:
        from src.uia.modules.core import driver_registry
        driver = driver_registry.get_primary_driver()
        if not driver:
            return
        hwnd = getattr(driver, 'hwnd', None)
        if not hwnd:
            return

        # 1. 实时句柄活性预检（极其轻量，避开节流，确保随时能成功阻拦对已关闭微信窗口的 COM 误操作）
        if not win32gui.IsWindow(hwnd):
            msg = "[UIA预检] 微信窗口句柄已失效（微信可能已退出或崩溃），强行拦截后续 UIA 调用以防止 COM 崩溃"
            logger.error(msg)
            raise RuntimeError(msg)

        # 1.5 实时响应状态预检（预检微信主窗口是否卡死/无响应，防止底层的 COM 调用发生无限期挂起超时）
        try:
            import ctypes
            result_val = ctypes.c_ulong(0)
            ret = ctypes.windll.user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 2000, ctypes.byref(result_val))
            if not ret:
                msg = "[UIA预检] 检测到微信主窗口当前处于卡死/无响应状态，强行拦截 UIA 操作以防挂起"
                logger.warning(msg)
                raise RuntimeError(msg)
        except RuntimeError as re:
            raise re
        except Exception as e_resp:
            logger.debug(f"[UIA预检] 响应度检测异常: {e_resp}")
    except ImportError:
        return
    except RuntimeError as re:
        raise re
    except Exception as e:
        logger.debug(f"[UIA预检] 轻量活性预检异常: {e}")

    # 2. 窗口可见性检查（允许 30s 节流限制）
    global _precheck_last_time
    now = time.time()
    if now - _precheck_last_time < _PRECHECK_MIN_INTERVAL:
        return
    _precheck_last_time = now

    try:
        if not win32gui.IsWindowVisible(hwnd):
            logger.warning("[UIA预检] ⚠ 微信窗口不可见（可能已最小化或隐藏到托盘）")
    except Exception as e:
        logger.debug(f"[UIA预检] 可见性预检异常: {e}")


_task_depth = 0
_depth_lock = threading.Lock()


@contextmanager
def run_uia_task(
    task_name: str,
    priority: int = UIATaskPriority.NORMAL,
    timeout: float = 60,
    pause_background_tasks: bool = False,
    use_physical_lock: bool = False,
):
    """同步 UIA 任务上下文：统一加锁 + 白屏预检 + 可选维护窗口 + ESC 中断处理。"""
    global _task_depth
    with _depth_lock:
        _task_depth += 1

    lock_msg = f"正在执行: {task_name}"
    try:
        if stop_signal.is_stopped:
            logger.warning(f"[UIA 任务] {task_name} 因全局停止信号被拦截，跳过执行")
            raise UIAInterruptError(f"任务 {task_name} 已由于全局停止信号取消")

        if pause_background_tasks:
            with uia_maintenance(task_name):
                with uia_lock.sync_acquire(priority=priority, task_name=task_name, timeout=timeout):
                    with stop_signal.ignore_esc():
                        if use_physical_lock:
                            with physical_lock(lock_msg):
                                _pre_check_uia_health()
                                yield
                        else:
                            _pre_check_uia_health()
                            yield
        else:
            with uia_lock.sync_acquire(priority=priority, task_name=task_name, timeout=timeout):
                if stop_signal.is_stopped:
                    logger.warning(f"[UIA 锁] 已获取锁但检测到停止信号，放弃执行: {task_name}")
                    raise UIAInterruptError(f"任务 {task_name} 在锁获取后检测到停止信号")
                    
                from src.uia.privacy_shield import get_privacy_shield
                with get_privacy_shield().bypass_shield():
                    with stop_signal.ignore_esc():
                        if use_physical_lock:
                            with physical_lock(lock_msg):
                                _pre_check_uia_health()
                                yield
                        else:
                            _pre_check_uia_health()
                            yield
    except UIAInterruptError:
        logger.warning(f"[UIA 任务] {task_name} 任务由于中断取消")
        raise
    except Exception as e:
        logger.error(f"[UIA 任务] {task_name} 运行异常: {e}")
        raise
    finally:
        with _depth_lock:
            _task_depth = max(0, _task_depth - 1)


def run_uia_task_func(
    func: Callable[..., T],
    task_name: str,
    priority: int = UIATaskPriority.NORMAL,
    timeout: float = 60,
    pause_background_tasks: bool = False,
    use_physical_lock: bool = False,
    *args,
    **kwargs
) -> T:
    """包装 run_uia_task 上下文，运行传入的函数并返回结果。"""
    with run_uia_task(
        task_name=task_name,
        priority=priority,
        timeout=timeout,
        pause_background_tasks=pause_background_tasks,
        use_physical_lock=use_physical_lock,
    ):
        return func(*args, **kwargs)


# ==================== 超时熔断核心实现 ====================

_executor_lock = threading.Lock()
_current_executor: ThreadPoolExecutor | None = None
_executor_generation = 0

def _init_com_on_thread():
    import comtypes
    try:
        comtypes.CoInitialize()
        logger.debug("[UIA线程池] 工作线程 COM 初始化成功")
    except Exception as e:
        logger.warning(f"[UIA线程池] 工作线程 COM 初始化失败: {e}")

def _get_uia_executor() -> ThreadPoolExecutor:
    global _current_executor, _executor_generation
    with _executor_lock:
        if _current_executor is None:
            _executor_generation += 1
            _current_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"uia_gen{_executor_generation}",
                initializer=_init_com_on_thread
            )
            logger.info(f"[UIA线程池] 创建新线程池世代: uia_gen{_executor_generation}")
        return _current_executor

def _rotate_uia_executor(reason: str):
    global _current_executor, _executor_generation
    with _executor_lock:
        if _current_executor is not None:
            old_gen = _executor_generation
            _current_executor.shutdown(wait=False)
            _current_executor = None
            logger.warning(f"[UIA线程池] 检测到挂起卡死({reason})，强行废弃并销毁旧线程池世代 uia_gen{old_gen}")

def _dump_hang_snapshot(tag: str):
    """转储当前所有线程的堆栈快照到 log 中，分析排查是哪段 UIA 调用永久卡死"""
    try:
        snapshot_lines = [f"=== UIA HANG SNAPSHOT: {tag} ==="]
        for thread_id, frame in sys._current_frames().items():
            snapshot_lines.append(f"\n--- Thread ID: {thread_id} ---")
            snapshot_lines.extend(traceback.format_stack(frame))
        snapshot_text = "".join(snapshot_lines)
        logger.error(snapshot_text)
    except Exception as e:
        logger.error(f"[UIA快照] 无法转储 hang 快照: {e}")

async def run_uia_with_timeout(func, __timeout_sec: float, *args, **kwargs):
    """使用 asyncio.wait_for 对耗时且存在卡死隐患的 UIA 动作包裹超时，并使用轮换线程池保护"""
    try:
        _pre_check_uia_health()
    except RuntimeError as re:
        # 将预检失效/卡死异常转化为 TimeoutError 抛出，保持接口异常契约一致性，触发上层的降级熔断逻辑
        raise asyncio.TimeoutError(str(re))

    loop = asyncio.get_running_loop()
    executor = _get_uia_executor()
    current_gen = _executor_generation
    def _wrapped_with_com():
        import gc
        try:
            res = func(*args, **kwargs)
            try:
                from src.utils.uia_circuit_breaker import report_uia_success
                report_uia_success()
            except Exception:
                pass
            return res
        finally:
            # 💡 致命崩溃防护：只有当当前线程池世代没有被 rotate 旋转废弃时，才允许执行垃圾回收。
            # 如果当前世代已经发生改变（说明任务超时，当前线程已被废弃成为野线程），
            # 在本线程注销的 COM 套间内执行全局 gc.collect() 会极易引发跨套间 COM 销毁冲突，
            # 导致 Windows 硬件级致命异常：access violation 崩溃！
            if _executor_generation == current_gen:
                try:
                    gc.collect()
                except Exception:
                    pass

    try:
        with stop_signal.ignore_esc():
            return await asyncio.wait_for(
                loop.run_in_executor(executor, _wrapped_with_com),
                timeout=__timeout_sec
            )
    except asyncio.TimeoutError:
        func_name = func.__name__ if hasattr(func, '__name__') else str(func)
        logger.error(f"[UIA超时] 操作 {func_name} 发生 {__timeout_sec}s 超时被强行熔断")
        _dump_hang_snapshot(f"timeout_on_{func_name}")

        # 🌟 熔断上报：将单次超时登记为 UIA 失败，以维护熔断计数状态
        try:
            from src.utils.uia_circuit_breaker import report_uia_failure
            report_uia_failure()
        except Exception as e_breaker:
            logger.debug(f"[UIA超时] 上报熔断计数异常: {e_breaker}")

        # 🌟 超时自愈：尝试强制刷新微信的 UIA 无障碍树，打破潜在的 UI 挂起状态
        try:
            from src.uia.modules.core import driver_registry
            driver = driver_registry.get_primary_driver()
            if driver and getattr(driver, "hwnd", None):
                logger.info(f"[UIA超时] 检测到挂起，尝试执行无障碍树强刷进行物理唤醒自愈... hwnd={driver.hwnd}")
                from src.uia.startup_flow import force_accessibility_refresh
                force_accessibility_refresh(driver.hwnd, getattr(driver, "root", None), escalate=True)
        except Exception as refresh_err:
            logger.debug(f"[UIA超时] 自愈强刷无障碍树发生异常: {refresh_err}")

        _rotate_uia_executor(f"timeout_on_{func_name}")
        try:
            from src.utils.uia_lock import uia_lock
            uia_lock.force_release()
        except Exception as lock_err:
            logger.error(f"[UIA超时] 强行释放 UIA 锁异常: {lock_err}")
        try:
            from src.uia.input_guard import uia_lock as physical_lock
            physical_lock.force_release()
        except Exception as phys_lock_err:
            logger.error(f"[UIA超时] 强行释放物理输入锁异常: {phys_lock_err}")
        raise


async def run_in_uia_thread(func, *args, **kwargs):
    """在专用 UIA 单线程池中执行同步 UIA 操作，支持超时熔断与卡死自愈。"""
    timeout_sec = kwargs.pop("__timeout_sec", 45.0)
    return await run_uia_with_timeout(func, timeout_sec, *args, **kwargs)



# Circuit breaker functions are imported from uia_circuit_breaker.py
