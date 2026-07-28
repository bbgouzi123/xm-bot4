"""DPI 缩放相关工具。"""

import ctypes
from typing import Optional

_dpi_scale_cache: Optional[float] = None


def get_dpi_scale() -> float:
    """获取当前系统的 DPI 缩放比例（如 150% 返回 1.5）。"""
    global _dpi_scale_cache
    if _dpi_scale_cache is not None:
        return _dpi_scale_cache

    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        _dpi_scale_cache = dpi / 96.0
    except (AttributeError, OSError):
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
            _dpi_scale_cache = dpi / 96.0
        except Exception:
            _dpi_scale_cache = 1.0

    return _dpi_scale_cache
