"""
微信白屏保活心跳守护模块

背景：
    微信 4.x 在某些场景（长时间后台、锁屏后恢复、DWM 重绘等）会触发白屏保护机制：
    将整个窗口渲染为纯白/纯黑冻结态。UIA 元素此时仍在内存树中（Exists()=True），
    但所有 UI 交互（点击、输入）均失效，导致自动回复彻底中断。

解决思路（心跳保活）：
    在无任何 UIA 任务占用、用户无鼠标键盘操作的空闲期，定期点击任务栏微信图标。
    该动作会触发微信窗口的前台激活与 UI 重绘，有效预防白屏状态积累。

关键设计：
    1. 随机间隔（3~10 分钟）：避免固定频率被检测，也减少对正常使用的干扰
    2. 三重空闲门控：
       - uia_lock.is_busy 为 False（无任何 UIA 任务在执行）
       - 用户空闲时间 > 30 秒（用户没在操作电脑）
       - stop_signal 未触发（系统未进入停止状态）
    3. 仅在微信进程存活时执行，进程不在线时自动跳过
    4. daemon 线程，随主进程退出，无需手动清理
"""

import time
import random
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ── 守护线程状态 ──────────────────────────────────────────────
_guard_thread: Optional[threading.Thread] = None
_guard_started = False
_guard_lock = threading.Lock()

# ── 可调参数 ─────────────────────────────────────────────────
# 心跳间隔随机范围（秒），3~10 分钟
_INTERVAL_MIN_SEC = 3 * 60    # 180s
_INTERVAL_MAX_SEC = 10 * 60   # 600s

# 执行心跳前要求用户空闲的最小时长（毫秒）
_USER_IDLE_REQUIRED_MS = 30_000   # 30 秒

# 初始延迟：启动后等待一段时间再开始巡检，给系统留出初始化时间
_INITIAL_DELAY_SEC = 90


def _is_safe_to_heartbeat() -> bool:
    """判断当前是否处于安全的空闲状态，可以执行心跳点击。

    条件（全部满足才返回 True）：
    1. 全局停止信号未触发
    2. UIA 全局锁未被任何任务占用（无回复/加人/朋友圈等任务进行中）
    3. 物理键鼠输入锁未锁定
    4. UIA 维护窗口未开启（如高危前台任务）
    5. 用户空闲时间超过要求的最小阈值（30s）
    """
    try:
        from src.utils.stop_signal import stop_signal
        if stop_signal.is_stopped:
            return False
    except Exception:
        pass

    try:
        from src.utils.uia_lock import uia_lock
        if uia_lock.is_busy:
            return False
    except Exception:
        pass

    try:
        from src.uia.input_guard import uia_lock as phys_lock
        if phys_lock.is_locked:
            return False
    except Exception:
        pass

    # 检查是否有进行中的 UIA 维护窗口（如朋友圈/加人等高危任务）
    try:
        from src.utils.uia_task_runner import is_uia_maintenance_active
        if is_uia_maintenance_active():
            return False
    except Exception:
        pass

    try:
        from src.utils.user_activity import get_idle_ms
        idle_ms = get_idle_ms()
        if idle_ms < _USER_IDLE_REQUIRED_MS:
            return False
    except Exception:
        pass

    return True


def _get_wechat_hwnd() -> int:
    """从已注册的主驱动实例中获取微信窗口句柄，进程未连接时返回 0。"""
    try:
        from src.uia.modules.core.driver_registry import get_primary_driver
        driver = get_primary_driver()
        if driver:
            hwnd = getattr(driver, "hwnd", 0)
            if hwnd:
                try:
                    import win32gui
                    if win32gui.IsWindow(hwnd):
                        return hwnd
                except Exception:
                    return hwnd
    except Exception:
        pass
    return 0


def _do_heartbeat(hwnd: int) -> bool:
    """执行一次心跳巡检：像素采样检测白屏，仅在白屏时才触发自愈点击。

    正常状态下仅做截屏采样（~30ms），对微信完全无感知，不产生任何窗口交互；
    只有检测到真实白屏时才点击任务栏图标触发 UI 重绘，使点击行为保持极低频率。

    Returns:
        True  — 完成本轮巡检（无论是否白屏）
        False — 因安全检查未通过而跳过
    """
    if not _is_safe_to_heartbeat():
        return False

    try:
        from src.uia.retry.wechat_healer import is_window_white_screen
        is_white = is_window_white_screen(hwnd)
    except Exception:
        is_white = False

    if is_white:
        # 白屏状态：执行完整自愈流程（托盘 → 任务栏两步降级）
        logger.warning("[白屏心跳] ⚠️ 巡检发现微信处于白屏冻结状态，触发自愈点击...")
        try:
            from src.uia.retry.wechat_healer import _heal_white_screen_if_needed
            _heal_white_screen_if_needed(hwnd)
        except Exception as e:
            logger.debug(f"[白屏心跳] 白屏自愈异常: {e}")
    else:
        # 正常状态：仅记录日志，不做任何点击操作
        # 原因：任务栏点击虽在微信进程外，但无端的规律性点击属于非自然行为，
        # 且白屏本身是低概率事件，无需预防性点击——像素采样本身就是最轻量的守卫。
        logger.debug("[白屏心跳] 微信 UI 正常，本轮无需干预")

    return True



def _guard_loop():
    """心跳守护线程主循环。"""
    logger.info(f"[白屏心跳] 守护线程已启动，初始延迟 {_INITIAL_DELAY_SEC}s 后开始巡检")
    time.sleep(_INITIAL_DELAY_SEC)

    while True:
        # 随机等待 3~10 分钟
        wait_sec = random.uniform(_INTERVAL_MIN_SEC, _INTERVAL_MAX_SEC)
        logger.info(f"[白屏心跳] 下次巡检将在 {wait_sec / 60:.1f} 分钟后进行")

        # 分段 sleep，每 5 秒检查一次停止信号，保证退出响应及时
        elapsed = 0.0
        while elapsed < wait_sec:
            time.sleep(5)
            elapsed += 5
            try:
                from src.utils.stop_signal import stop_signal
                if stop_signal.is_stopped:
                    logger.info("[白屏心跳] 检测到全局停止信号，守护线程退出")
                    return
            except Exception:
                pass

        hwnd = _get_wechat_hwnd()
        if not hwnd:
            logger.debug("[白屏心跳] 微信窗口未就绪，本轮跳过")
            continue

        if not _is_safe_to_heartbeat():
            logger.debug("[白屏心跳] 当前 UIA 繁忙或用户活跃，本轮跳过（将在下一个随机间隔后重试）")
            continue

        try:
            _do_heartbeat(hwnd)
        except Exception as e:
            logger.warning(f"[白屏心跳] 巡检心跳执行异常: {e}")


def start_white_screen_guard():
    """启动白屏心跳守护线程（幂等，重复调用无副作用）。

    应在 app lifespan 的 startup 阶段调用。
    """
    global _guard_thread, _guard_started

    with _guard_lock:
        if _guard_started and _guard_thread and _guard_thread.is_alive():
            logger.debug("[白屏心跳] 守护线程已在运行，跳过重复启动")
            return

        _guard_thread = threading.Thread(
            target=_guard_loop,
            daemon=True,
            name="wechat_white_screen_guard"
        )
        _guard_thread.start()
        _guard_started = True
        logger.info("[白屏心跳] 微信白屏保活守护线程已成功启动")
