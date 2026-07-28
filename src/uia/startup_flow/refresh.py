import time
import ctypes
import logging
import win32gui
import win32api
from .utils import _log

logger = logging.getLogger(__name__)

_we_set_screen_reader = False

# ==================== 侵入层频率控制 ====================
# 侵入操作（鼠标移动、窗口微调、托盘点击）仅在轻量层失效后由调用方显式请求触发，
# 但仍需全局节流防止极端场景下密集触发。
_last_invasive_refresh = 0.0
_INVASIVE_COOLDOWN = 120.0  # 侵入操作全局冷却（秒），即使被显式请求也不会频于此


def force_accessibility_refresh(hwnd: int, root_ctrl=None, *, escalate: bool = False):
    """发送 WM_GETOBJECT 消息，强制 Qt 初始化 Accessibility Provider

    两层策略（与竞品 yokobot-v3 对齐）：
    - 轻量层（每次执行）：WM_GETOBJECT + SPI 标志位 + Refind，纯内核级零干扰
    - 侵入层（按需降级）：只有 escalate=True 时才执行鼠标移动/窗口微调/托盘点击
      → 调用方在轻量层连续失败后才传 escalate=True
      → 即使请求了也有 120s 全局冷却
      → 用户正在活跃操作时完全跳过

    Args:
        hwnd: 微信主窗口句柄
        root_ctrl: UIA 根控件（可选，用于 Refind）
        escalate: 是否请求侵入层操作（默认 False，由调用方在轻量层失败后显式传入）
    """
    global _last_invasive_refresh
    try:
        logger.debug(f"[UIA] 轻量刷新 hwnd={hwnd}")

        # ===== 轻量层（每次都执行，不干扰用户） =====

        # 1. 物理层开启屏幕阅读器标志 (让 Qt 5.15+ 认为有辅助软件在运行)
        SPI_SETSCREENREADER = 0x0047
        global _we_set_screen_reader
        try:
            ctypes.windll.user32.SystemParametersInfoW(SPI_SETSCREENREADER, True, None, 2)
            _we_set_screen_reader = True
        except Exception:
            pass

        # [底层 UIAutomation 请求] 虚拟化一个 COM 请求唤醒 Qt 辅助功能桥接
        try:
            import comtypes.client
            comtypes.CoInitialize()
            _uia_obj = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}")
        except Exception:
            pass

        # 2. 发送 WM_GETOBJECT 消息 (COM 通讯握手) — 这是竞品唯一使用的核心手段
        WM_GETOBJECT = 0x003D
        OBJID_CLIENT = -4          # MSAA 客户端对象
        UIA_ROOT_OBJECT_ID = -25   # UIA 根元素（Qt UIA 桥接响应此值）

        # 收集所有子窗口句柄
        targets = [hwnd]
        def enum_child_callback(child_hwnd, _):
            targets.append(child_hwnd)
            return True
        try:
            win32gui.EnumChildWindows(hwnd, enum_child_callback, None)
        except Exception:
            pass

        if root_ctrl:
            try:
                native_h = getattr(root_ctrl, 'NativeWindowHandle', None)
                if native_h and native_h not in targets:
                    targets.append(native_h)
            except Exception:
                pass

        for target in targets:
            try:
                ctypes.windll.user32.SendMessageW(int(target), WM_GETOBJECT, 0, UIA_ROOT_OBJECT_ID)
                ctypes.windll.user32.SendMessageW(int(target), WM_GETOBJECT, 0, OBJID_CLIENT)
            except Exception:
                pass

        time.sleep(0.2)

        # 3. Refind（与竞品一致：刷新控件引用）
        if root_ctrl:
            try:
                root_ctrl.Refind()
            except Exception:
                pass

        # ===== 侵入层（按需降级，非定时轮询） =====
        # 只有调用方显式请求（escalate=True）才执行，且受全局冷却保护
        if not escalate:
            return

        now = time.time()
        if now - _last_invasive_refresh < _INVASIVE_COOLDOWN:
            logger.debug(f"[UIA] 侵入层全局冷却中（{int(_INVASIVE_COOLDOWN - (now - _last_invasive_refresh))}s 后可用）")
            return

        # 用户正在活跃操作鼠标键盘时，完全跳过侵入层
        try:
            from src.utils.user_activity import is_user_active
            if is_user_active():
                logger.debug("[UIA] 用户正在操作，跳过侵入层")
                return
        except Exception:
            pass

        _last_invasive_refresh = now
        logger.info("[UIA] 轻量刷新失效，触发侵入层物理唤醒...")

        # 3a. 鼠标"触摸"唤醒
        if targets:
            _mouse_poke(int(targets[0]))

        # 3b. 白屏恢复（点击托盘图标）
        _recover_white_screen(hwnd)

        # 3c. 窗口微调 1 像素唤醒（兼容微信 4.1.8+）
        _nudge_window(hwnd)

    except Exception as e:
        _log("UIA", f"Refresh 异常: {e}")
    finally:
        try:
            import gc
            gc.collect()
        except Exception:
            pass


def _recover_white_screen(hwnd: int):
    """点击托盘图标恢复白屏保护（侵入操作，仅在 escalate 流程中调用）"""
    try:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return
        from src.uia.retry.tray import click_wechat_tray_icon
        click_wechat_tray_icon()
        time.sleep(0.3)
    except Exception:
        pass


def _mouse_poke(hwnd: int):
    """在窗口中心点模拟一个极细微的鼠标移动，强制触发 Qt 的 UI 线程活跃状态"""
    try:
        rect = win32gui.GetWindowRect(hwnd)
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        old_pos = win32api.GetCursorPos()
        win32api.SetCursorPos((cx, cy))
        time.sleep(0.01)
        win32api.SetCursorPos(old_pos)
    except Exception:
        pass


def _nudge_window(hwnd: int) -> bool:
    """通过微调窗口大小（1像素），强制 Windows 触发 WM_SIZE 和重绘，
    从而唤醒休眠的 Qt 辅助功能树。应对微信 4.1.8+ 主动冻结 Qt 树的终极物理唤醒手段。
    """
    try:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return False
        rect = win32gui.GetWindowRect(hwnd)
        x, y = rect[0], rect[1]
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]

        if w >= 200 and h >= 200:
            win32gui.MoveWindow(hwnd, x, y, w + 1, h + 1, True)
            time.sleep(0.05)
            win32gui.MoveWindow(hwnd, x, y, w, h, True)
            time.sleep(0.05)
            return True
    except Exception as e:
        try:
            _log("UIA", f"Nudge window 异常: {e}")
        except Exception:
            pass
    return False
