import os
import ctypes
import struct
from ctypes import wintypes

def copy_file_to_clipboard(file_path: str):
    """将文件路径复制到剪贴板（CF_HDROP 格式，可直接粘贴到微信）"""
    CF_HDROP = 15
    GHND = 0x0042

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # 显式声明类型，避免 64 位 Python 大地址溢出错误
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE

    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p

    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL

    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    abs_path = os.path.abspath(file_path)
    file_str = abs_path + '\0\0'
    file_bytes = file_str.encode('utf-16-le')

    header = struct.pack('IiiII', 20, 0, 0, 0, 1)
    data = header + file_bytes

    hGlobal = kernel32.GlobalAlloc(GHND, len(data))
    if not hGlobal:
        raise RuntimeError("GlobalAlloc 失败")

    pGlobal = kernel32.GlobalLock(hGlobal)
    if not pGlobal:
        kernel32.GlobalFree(hGlobal)
        raise RuntimeError("GlobalLock 失败")

    ctypes.memmove(pGlobal, data, len(data))
    kernel32.GlobalUnlock(hGlobal)

    if not user32.OpenClipboard(0):
        kernel32.GlobalFree(hGlobal)
        raise RuntimeError("OpenClipboard 失败")

    user32.EmptyClipboard()
    user32.SetClipboardData(CF_HDROP, hGlobal)
    user32.CloseClipboard()
