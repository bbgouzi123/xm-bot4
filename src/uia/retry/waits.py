"""等待类工具。"""

import time
from typing import Callable

from .clicks import exists_with_timeout


def wait_for_element(find_func: Callable, timeout: float = 5.0, interval: float = 0.5):
    """等待控件出现。"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            element = find_func()
            if element and exists_with_timeout(element, 1.0):
                return element
        except Exception:
            pass
        time.sleep(interval)
    return None


def wait_for_window(title: str, timeout: float = 10.0) -> bool:
    """等待指定标题的窗口出现。"""
    import win32gui

    start = time.time()
    while time.time() - start < timeout:
        hwnd = win32gui.FindWindow(None, title)
        if hwnd:
            return True
        time.sleep(0.5)
    return False
