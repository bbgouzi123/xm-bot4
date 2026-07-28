"""键盘状态检测。"""

import ctypes


def is_shift_pressed() -> bool:
    """检测是否按下 Shift（用于紧急终止任务）。"""
    try:
        import win32api
        import win32con

        state = win32api.GetAsyncKeyState(win32con.VK_SHIFT)
        return bool(state & 0x8000)
    except Exception:
        return False


def is_escape_pressed() -> bool:
    """检测是否按下 ESC（用于长任务暂停）。"""
    try:
        import win32api
        import win32con

        state = win32api.GetAsyncKeyState(win32con.VK_ESCAPE)
        return bool(state & 0x8000)
    except Exception:
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
        except Exception:
            return False
