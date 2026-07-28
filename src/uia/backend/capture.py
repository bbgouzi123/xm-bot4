"""
PrintWindow 截图工具
====================
提供不依赖 UIA 的窗口截图能力。
已在微信 4.1.8.29 上验证通过。

状态：待实现（阶段 0）
"""
# import ctypes
# import numpy as np
# from ctypes import windll
# import win32gui, win32ui


def capture_window(hwnd: int):
    """对指定窗口进行 PrintWindow 截图

    Args:
        hwnd: 窗口句柄

    Returns:
        numpy.ndarray (H, W, 3) BGR 格式

    TODO 阶段 0 实现:
        1. GetWindowRect 获取窗口尺寸
        2. CreateCompatibleDC / CreateCompatibleBitmap
        3. PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT=3)
        4. GetBitmapBits → numpy array
        5. BGRA → BGR 转换
    """
    raise NotImplementedError("阶段 0 待实现")


def capture_region(hwnd: int, x: int, y: int, w: int, h: int):
    """截取窗口指定区域

    Args:
        hwnd: 窗口句柄
        x, y: 区域左上角（相对于窗口）
        w, h: 区域宽高

    Returns:
        numpy.ndarray (h, w, 3) BGR 格式

    TODO 阶段 0 实现:
        1. capture_window() 获取全窗口截图
        2. numpy 切片裁剪
    """
    raise NotImplementedError("阶段 0 待实现")
