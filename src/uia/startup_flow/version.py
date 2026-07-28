import os
from .utils import _log

# UIA 兼容版本上限: 4.1.7.x 已验证可用（4.1.7.59 实测通过）
# 4.1.8.29 已确认封死 Qt Accessibility 控件树
_WECHAT_MAX_COMPATIBLE = (4, 1, 7, 99)

def detect_wechat_version(exe_path: str = None) -> tuple:
    """
    检测微信 exe 的文件版本号。
    通过 Windows API GetFileVersionInfo 读取版本资源。
    Returns: (major, minor, patch, build), 失败返回 (0, 0, 0, 0)
    """
    if not exe_path:
        from src.utils.wechat_launcher import get_wechat_path
        exe_path = get_wechat_path()
    if not exe_path or not os.path.exists(exe_path):
        return (0, 0, 0, 0)
    try:
        import win32api as _api
        info = _api.GetFileVersionInfo(exe_path, "\\")
        ms = info['FileVersionMS']
        ls = info['FileVersionLS']
        return (
            (ms >> 16) & 0xFFFF,
            ms & 0xFFFF,
            (ls >> 16) & 0xFFFF,
            ls & 0xFFFF,
        )
    except Exception:
        pass
    return (0, 0, 0, 0)


def is_wechat_version_compatible(version: tuple) -> bool:
    """判断微信版本是否在 UIA 兼容范围内"""
    if version == (0, 0, 0, 0):
        return True
    return version <= _WECHAT_MAX_COMPATIBLE


def format_version(v: tuple) -> str:
    return '.'.join(str(x) for x in v)
