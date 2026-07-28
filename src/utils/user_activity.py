"""
用户活跃度检测模块 — 移植自 xm-auto-click 的三层保护机制

功能:
    pass
1. GetLastInputInfo: 检测用户最后一次键盘/鼠标操作时间
2. is_user_editing(): 检查前台窗口是否有活跃的文本光标（用户在输入框打字）
3. get_idle_ms(): 获取用户空闲时间（毫秒）

原理:
    pass
- GetLastInputInfo.dwTime 在每次键盘/鼠标事件时更新
- 自动化操作（SendInput）也会更新此值
- 通过记录自动化操作后的 dwTime，能区分"真实用户输入"和"自动化输入"
"""
import ctypes
import ctypes.wintypes
import time


# Win32 API 结构体
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.UINT),
        ('dwTime', ctypes.wintypes.DWORD),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.DWORD),
        ('flags', ctypes.wintypes.DWORD),
        ('hwndActive', ctypes.wintypes.HWND),
        ('hwndFocus', ctypes.wintypes.HWND),
        ('hwndCapture', ctypes.wintypes.HWND),
        ('hwndMenuOwner', ctypes.wintypes.HWND),
        ('hwndMoveSize', ctypes.wintypes.HWND),
        ('hwndCaret', ctypes.wintypes.HWND),
        ('rcCaret', ctypes.wintypes.RECT),
    ]


# Win32 常量
GUI_CARETBLINKING = 0x00000001

# 用户输入冷却时间（毫秒）— 用户操作后等这么久才恢复自动化
USER_INPUT_COOLDOWN_MS = 3000


def get_idle_ms() -> int:
    """获取用户空闲了多少毫秒（从最后一次键盘/鼠标操作开始计算）

    返回: 空闲毫秒数。用户刚操作过 → 返回小值; 用户离开了 → 返回大值
    """
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            now = ctypes.windll.kernel32.GetTickCount()
            # 处理 tick 溢出（约49.7天溢出一次）
            idle = (now - info.dwTime) & 0xFFFFFFFF
            return idle
    except Exception:
        pass
    return 0


def get_last_input_tick() -> int:
    """获取最后一次输入的 tick 值（用于区分自动化 vs 用户输入）"""
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return info.dwTime
    except Exception:
        pass
    return 0


def is_user_editing() -> bool:
    """检测用户是否正在编辑文本（前台窗口有活跃的文本光标）

    原理: 通过 GetGUIThreadInfo 检查前台窗口 the GUI 线程是否有 caret（闪烁的文本光标）
    有 caret = 用户在输入框中，即使鼠标停顿很久也不应该抢走焦点
    """
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False

        # 🌟 规避微信自聚焦光标死锁防线
        # 如果前台窗口就是被我们接管的微信窗口之一，我们必须忽略它的 caret 状态！
        # 因为我们自动回复置顶微信并聚焦输入框时，必定产生闪烁光标，不能将其误判为“用户客服正在输入打字”
        try:
            hwnds = []
            try:
                from app.state import account_manager
                if account_manager:
                    for drv in getattr(account_manager, "drivers", {}).values():
                        h = getattr(drv, "hwnd", None)
                        if h:
                            hwnds.append(h)
            except Exception:
                pass
            
            if not hwnds:
                try:
                    from src.uia.modules.core.driver_registry import get_primary_driver
                    primary = get_primary_driver()
                    if primary and getattr(primary, "hwnd", None):
                        hwnds.append(primary.hwnd)
                except Exception:
                    pass
            
            if not hwnds:
                try:
                    from app.state import driver
                    if driver and getattr(driver, "hwnd", None):
                        hwnds.append(driver.hwnd)
                except Exception:
                    pass

            if hwnd in hwnds:
                return False
        except Exception:
            pass

        thread_id = ctypes.windll.user32.GetWindowThreadProcessId(
            hwnd, ctypes.c_void_p(0)
        )
        if not thread_id:
            return False

        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if ctypes.windll.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            # 文本光标在闪烁 或 caret 窗口句柄不为空
            return (info.flags & GUI_CARETBLINKING != 0) or (info.hwndCaret != 0)
    except Exception:
        pass
    return False


def is_user_active(cooldown_ms: int = USER_INPUT_COOLDOWN_MS, check_caret: bool = False) -> bool:
    """综合判断用户是否正在活跃操作（核心判断函数）

    三层保护:
        pass
    1. 用户空闲时间 < 冷却时间 → 用户活跃
    2. 用户正在编辑文本 (已默认关闭) → 用户活跃
    3. 用户有键盘按下 → 用户活跃

    参数:
        cooldown_ms: 用户操作后的冷却等待时间（默认 3 秒）
        check_caret: 是否检测光标。默认 False（如果用户真在打字，idle 必然 < cooldown_ms，无需强制检测光标导致假死）

    返回: True = 用户正在操作，自动化应暂停
    """
    # 0. 极速模式短路判定
    try:
        from src.utils.config_cache import config_cache
        if config_cache.get("speed_mode", False):
            return False
    except Exception:
        pass

    # 检查1: 空闲时间检测（GetLastInputInfo）
    idle = get_idle_ms()
    if idle < cooldown_ms:
        # 🌟 规避自动化自锁机制 (UIA Input Lock 避让保护)
        # 如果当前正处于物理键鼠锁 uia_lock 的保护周期内，或正在执行 UIA 自动化任务，
        # 所有在这个时间窗口内产生的 GetLastInputInfo 鼠标键盘变化，
        # 百分之百是自动化本身模拟输入（如 physical_click/SendKeys）产生的，应该主动放行，避免产生自己撞自己的自锁拦截。
        try:
            from src.uia.input_guard import uia_lock as phys_lock
            if phys_lock.is_locked:
                return False
        except Exception:
            pass
        try:
            from src.utils.uia_lock import uia_lock as task_lock
            if task_lock.is_busy:
                return False
        except Exception:
            pass
        return True

    # 检查2: 文本编辑检测 (只有开启且光标存活)
    if check_caret and is_user_editing():
        return True

    return False


def check_user_input(last_auto_tick: int) -> tuple:
    """判断自上次自动化操作后，用户是否有新的输入

    参数:
        last_auto_tick: 上次自动化操作后记录的 GetLastInputInfo.dwTime

    返回: (有用户新输入: bool, 距离上次输入的毫秒数: int)
    """
    # 0. 极速模式短路判定
    try:
        from src.utils.config_cache import config_cache
        if config_cache.get("speed_mode", False):
            return (False, 999999)
    except Exception:
        pass

    current_tick = get_last_input_tick()
    now = ctypes.windll.kernel32.GetTickCount()
    ms_since = (now - current_tick) & 0xFFFFFFFF

    has_new = current_tick != last_auto_tick
    return (has_new, ms_since)
