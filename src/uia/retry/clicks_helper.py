import time
import random
import threading
import ctypes
import win32api
import win32gui
import win32process
from .clicks import physical_click, random_delay, _get_shield_ctx

def try_right_click(element, max_retries: int = 3, delay: float = 0.3) -> bool:
    """安全右键点击，带重试。"""
    for i in range(max_retries):
        try:
            if element and element.Exists(2):
                with _get_shield_ctx():
                    element.RightClick()
                time.sleep(delay)
                return True
        except Exception:
            if i < max_retries - 1:
                time.sleep(0.5)
    return False

def exists_with_timeout(element, timeout: float = 3.0) -> bool:
    """用线程超时检测控件是否存在（防止 UIA 调用无限挂起）。"""
    result = [False]

    def check():
        try:
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread(debug=False):
                result[0] = element.Exists(timeout)
        except Exception:
            pass

    t = threading.Thread(target=check, daemon=True)
    t.start()
    t.join(timeout=timeout + 1.0)
    return result[0]

def smooth_click_at(element, offset_x: int = 0, offset_y: int = 0):
    """平滑点击（点击后恢复鼠标位置）。"""
    from src.uia.input_guard import uia_lock
    uia_lock.check_interrupt()

    try:
        # 获取目标窗口句柄并校验是否为前台窗口
        try:
            hwnd = element.NativeWindowHandle
            if hwnd:
                fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
                if fg_hwnd != hwnd:
                    _, target_pid = win32process.GetWindowThreadProcessId(hwnd)
                    _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                    if target_pid != fg_pid:
                        print(f"[重试] ⚠️ 目标窗口未处于前台（目标hwnd={hwnd}, 当前前台={fg_hwnd}），尝试自动置前...")
                        from src.uia.retry.window_ops import ensure_wechat_foreground
                        if ensure_wechat_foreground(hwnd):
                            time.sleep(0.20)
                            fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
                            if fg_hwnd != hwnd:
                                _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                                if target_pid != fg_pid:
                                    print(f"[重试] 强行置前校验仍未通过，跳过平滑物理点击")
                                    return
                        else:
                            print(f"[重试] 置前失败，跳过平滑物理点击以防误触")
                            return
        except Exception as check_e:
            print(f"[重试] 校验目标窗口前台状态异常: {check_e}")

        rect = element.BoundingRectangle
        x = rect.left + offset_x if offset_x else (rect.left + rect.right) // 2
        y = rect.top + offset_y if offset_y else (rect.top + rect.bottom) // 2

        physical_click(x, y, settle=0.05, restore_cursor=True)
        random_delay(0.2, 0.4)
    except Exception as e:
        print(f"[重试] 平滑点击失败: {e}")

def click_at_absolute(x: int, y: int):
    """绝对坐标点击（用于精确操作）。自动穿透隐私保护遮罩。"""
    try:
        physical_click(x, y, settle=random.uniform(0.1, 0.2))
        random_delay(0.1, 0.2)
    except Exception as e:
        print(f"[重试] 绝对坐标点击失败 ({x}, {y}): {e}")
