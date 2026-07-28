"""
多开专用：自动化操作前确保目标微信窗口可见且在物理屏幕内
从 window_ops 剥离以对齐单文件 300 行质量红线限制
"""
import ctypes
import logging
import time
import win32gui
import win32con
from src.utils.stop_signal import stop_signal
from src.utils.user_activity import is_user_active

logger = logging.getLogger(__name__)

def ensure_wechat_visible_for_automation(hwnd: int, timeout: float = 5.0) -> bool:
    """多开专用：自动化操作前确保目标微信窗口可见且在物理屏幕内"""
    user32 = ctypes.windll.user32

    if not hwnd:
        return False

    try:
        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            try:
                rect = win32gui.GetWindowRect(hwnd)
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                screen_w = user32.GetSystemMetrics(0)
                screen_h = user32.GetSystemMetrics(1)
                if w >= 300 and h >= 300 and rect[0] < screen_w and rect[1] < screen_h:
                    if user32.GetForegroundWindow() == hwnd:
                        return True

                    wait_start = time.time()
                    while is_user_active(cooldown_ms=3000):
                        if stop_signal.is_stopped:
                            return False
                        if time.time() - wait_start > timeout:
                            logger.debug(f"[多开-置前] hwnd={hwnd} 用户持续活跃，保持后台扫描模式（不抢焦点）")
                            return True
                        time.sleep(0.2)
                    from .window_ops import force_foreground
                    force_foreground(hwnd)
                    return True
            except Exception:
                pass
    except Exception:
        pass

    if stop_signal.is_stopped:
        return False

    wait_start = time.time()
    while is_user_active(cooldown_ms=2000):
        if stop_signal.is_stopped:
            return False
        if time.time() - wait_start > timeout:
            print(f"[多开-置前] hwnd={hwnd} 用户持续活跃，跳过避让直接操作")
            break
        time.sleep(0.2)

    print(f"[多开-置前] hwnd={hwnd} 检测到窗口不可见或最小化，开始精准唤回...")

    try:
        if not win32gui.IsWindow(hwnd):
            print(f"[多开-置前] hwnd={hwnd} 窗口句柄已失效")
            return False

        if user32.IsIconic(hwnd):
            print(f"[多开-置前] hwnd={hwnd} 窗口最小化中，执行 SW_RESTORE")
            user32.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)

        if not win32gui.IsWindowVisible(hwnd):
            print(f"[多开-置前] hwnd={hwnd} 窗口隐藏中，执行 SW_SHOW")
            user32.ShowWindow(hwnd, win32con.SW_SHOW)
            time.sleep(0.15)

        try:
            rect = win32gui.GetWindowRect(hwnd)
            x, y, r, b = rect
            w, h = r - x, b - y
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            if x >= screen_w or y >= screen_h or r <= 0 or b <= 0:
                print(f"[多开-置前] hwnd={hwnd} 窗口越出屏幕 ({x},{y})-({r},{b})，归位到左上角")
                win32gui.MoveWindow(hwnd, 0, 0, min(w, screen_w), min(h, screen_h), True)
                time.sleep(0.1)
        except Exception as e:
            print(f"[多开-置前] hwnd={hwnd} 窗口位置检查异常: {e}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if stop_signal.is_stopped:
                return False
            try:
                if win32gui.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            print(f"[多开-置前] hwnd={hwnd} 等待可见超时")
            return False

        if user32.GetForegroundWindow() != hwnd:
            fg_hwnd = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            attached = False
            try:
                if fg_tid and fg_tid != cur_tid:
                    attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
                time.sleep(0.05)
            finally:
                if attached:
                    try:
                        user32.AttachThreadInput(fg_tid, cur_tid, False)
                    except Exception:
                        pass

        time.sleep(0.3)

        is_ok = win32gui.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd)
        if is_ok:
            print(f"[多开-置前] ✓ hwnd={hwnd} 已就绪，可进行 UIA 操作")
        else:
            print(f"[多开-置前] ✗ hwnd={hwnd} 唤回失败")
        return is_ok

    except Exception as e:
        print(f"[多开-置前] hwnd={hwnd} 唤回异常: {e}")
        return False
