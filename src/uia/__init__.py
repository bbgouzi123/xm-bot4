"""UIA 模块"""
try:
    import win32gui
    import win32process
    import uiautomation
    from .driver import WeChatDriver
except ImportError:
    WeChatDriver = None

from .session import parse_session_name
from .message import parse_message
