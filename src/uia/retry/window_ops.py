"""窗口前台、唤醒与白屏修复相关能力。"""
import ctypes
import logging
import time
import win32gui
import win32con
from typing import Optional

# 从解耦模块导入，维持原有 API 暴露
from .wechat_healer import ensure_wechat_foreground, fix_white_screen_after_show
from .visible_for_automation import ensure_wechat_visible_for_automation
from .ghost_windows import close_wechat_ghost_windows

logger = logging.getLogger(__name__)

def try_bring_wechat_to_front() -> bool:
    """通过 Ctrl+Alt+W 快捷键唤起微信窗口。"""
    import platform
    if platform.system() != "Windows":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event(17, 0, 0, 0)  # Ctrl
    user32.keybd_event(18, 0, 0, 0)  # Alt
    user32.keybd_event(87, 0, 0, 0)  # W
    time.sleep(0.05)
    user32.keybd_event(87, 0, 2, 0)
    user32.keybd_event(18, 0, 2, 0)
    user32.keybd_event(17, 0, 2, 0)
    return True

def force_foreground(hwnd_or_title) -> bool:
    """强制将窗口设为前景（支持 hwnd 或窗口标题），融合 Heavy Path 置顶组合拳。"""
    if isinstance(hwnd_or_title, int):
        hwnd = hwnd_or_title
    elif isinstance(hwnd_or_title, str):
        hwnd = win32gui.FindWindow(None, hwnd_or_title)
    else:
        return False
    if not hwnd:
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if user32.GetForegroundWindow() == hwnd:
        return True

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    else:
        user32.ShowWindow(hwnd, 5)  # SW_SHOW

    if user32.SetForegroundWindow(hwnd):
        return True
    user32.SwitchToThisWindow(hwnd, True)
    if user32.GetForegroundWindow() == hwnd:
        return True

    try:
        try_bring_wechat_to_front()
        time.sleep(0.1)
    except Exception:
        pass
    if user32.GetForegroundWindow() == hwnd:
        return True

    try:
        foreground_window = user32.GetForegroundWindow()
        if foreground_window == hwnd:
            return True

        foreground_thread_id = user32.GetWindowThreadProcessId(foreground_window, None)
        current_thread_id = kernel32.GetCurrentThreadId()
        attached = False

        if foreground_thread_id != current_thread_id and foreground_window != 0:
            try:
                attached = user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)
                SPI_SETFOREGROUNDLOCKTIMEOUT = 8193
                SPIF_SENDWININICHANGE = 2
                SPIF_UPDATEINIFILE = 1
                old_timeout = ctypes.c_uint32()
                
                user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(old_timeout), 0)
                user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDWININICHANGE | SPIF_UPDATEINIFILE)
                
                ALT_KEY = 18
                KEYEVENTF_KEYUP = 2
                user32.keybd_event(ALT_KEY, 0, 0, 0)
                user32.keybd_event(ALT_KEY, 0, KEYEVENTF_KEYUP, 0)
                
                user32.SetForegroundWindow(hwnd)
                user32.SwitchToThisWindow(hwnd, True)
                time.sleep(0.05)
                result = user32.GetForegroundWindow() == hwnd
                
                user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(old_timeout.value), SPIF_SENDWININICHANGE | SPIF_UPDATEINIFILE)
                
                if attached:
                    user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)
                return result
            except:
                if attached:
                    try:
                        user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)
                    except:
                        pass
    except Exception:
        pass

    return user32.GetForegroundWindow() == hwnd

def position_wechat_window(hwnd):
    """调整微信窗口位置：靠右且高度最大化。"""
    import win32api
    if not win32gui.IsWindow(hwnd):
        return
    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    work_area = win32api.GetMonitorInfo(monitor)["Work"]
    work_height = work_area[3] - work_area[1]
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.1)
    rect = win32gui.GetWindowRect(hwnd)
    curr_w = rect[2] - rect[0]
    work_width = work_area[2] - work_area[0]
    new_w = min(max(curr_w, 680), work_width)
    win32gui.MoveWindow(hwnd, max(work_area[0], work_area[2] - new_w), work_area[1], new_w, work_height, True)

def ensure_wechat_visible_fallback(hwnd=None):
    """在锁定物理键鼠并执行自动化任务之前，确保微信主窗口置顶且在前台可见"""
    from src.utils.instance_manager import InstanceManagerV2

    try:
        wx_hwnd = hwnd
        if not wx_hwnd:
            active_inst = InstanceManagerV2.get_instance().get_active_instance()
            wx_hwnd = active_inst.get("window_handle") if active_inst else None
        
        if not wx_hwnd or not win32gui.IsWindow(wx_hwnd):
            hwnds = []
            def enum_cb(h, _):
                cls = win32gui.GetClassName(h)
                from src.uia.modules.core.connect import _is_wechat_title
                title = win32gui.GetWindowText(h)
                if cls.endswith("Qt51514QWindowIcon") and _is_wechat_title(title):
                    hwnds.append(h)
            win32gui.EnumWindows(enum_cb, None)
            if hwnds:
                wx_hwnd = hwnds[0]
                
        if wx_hwnd:
            ensure_wechat_visible_for_automation(wx_hwnd, timeout=3.0)
        else:
            logger.warning("[InputGuard] 未探测到有效的微信主窗口句柄，跳过自动置顶")
    except Exception as e:
        logger.warning(f"[InputGuard] 自动置顶微信主窗口异常: {e}")


def find_wechat_menu_popover_hwnd(main_hwnd: int) -> Optional[int]:
    """通过 win32gui 精准寻找跟微信主窗口 PID 相同的快捷弹出菜单窗口句柄，规避 COM 套间遍历。"""
    import win32process
    try:
        _, main_pid = win32process.GetWindowThreadProcessId(main_hwnd)
    except Exception:
        return None
    
    menu_hwnd = [None]
    def enum_callback(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                cls = win32gui.GetClassName(hwnd)
                if cls == "Qt51514QWindowIcon" or cls.startswith("mmui::"):
                    title = win32gui.GetWindowText(hwnd) or ""
                    if not title.strip() or "添加朋友" in title:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if pid == main_pid and hwnd != main_hwnd:
                            r = win32gui.GetWindowRect(hwnd)
                            w = r[2] - r[0]
                            h = r[3] - r[1]
                            if 50 <= w <= 400 and 50 <= h <= 450:
                                menu_hwnd[0] = hwnd
                                return False
        except Exception:
            pass
        return True
    
    try:
        win32gui.EnumWindows(enum_callback, None)
    except Exception:
        pass
    return menu_hwnd[0]
