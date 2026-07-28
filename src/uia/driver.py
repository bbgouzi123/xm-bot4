"""
微信 UIA 驱动 — WeChatDriver
基于 uiautomation / pywinauto 控制微信 4.1.x 窗口

核心能力：
  - 窗口发现与连接
  - 账号信息提取
  - 会话列表扫描
  - 消息读取与发送
  - 联系人列表获取
"""
import os
import random

# 在引入 UI 组件或执行操作前，强制开启 Qt 辅助功能可读性（等价于设置环境变量 QT_ACCESSIBILITY=1）
os.environ["QT_ACCESSIBILITY"] = "1"
try:
    import ctypes
    ctypes.windll.kernel32.SetEnvironmentVariableW("QT_ACCESSIBILITY", "1")
    # 【DPI 感知声明】防止 150% 等高缩放下坐标偏移（如果 main.py 已设置过则会静默跳过）
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
except Exception:
    pass

import time
import random
import re
import hashlib
import json
import logging
import threading
from typing import Optional, List, Dict
from pathlib import Path

try:
    import win32gui
    import win32con
    import uiautomation as uia
except ImportError:
    win32gui = None
    win32con = None
    uia = None
try:
    import pyperclip
except ImportError:
    pyperclip = None

from .elements import WxClass, WxName, RESOLUTION_PARAMS
from .session import parse_session_name
from .message import parse_message

logger = logging.getLogger(__name__)


# ==================== 辅助函数 ====================

def random_delay(lo: float = 0.2, hi: float = 0.5):
    time.sleep(random.uniform(lo, hi))


def try_click(ctrl, max_retries: int = 3, delay: float = 0.3):
    """安全点击控件（统一重定向到 retry 包，带防双击与多模降级保护）"""
    from src.uia.retry import try_click as safe_try_click
    return safe_try_click(ctrl, max_retries=max_retries, delay=delay)


def exists_with_timeout(ctrl, timeout: float = 2) -> bool:
    try:
        return ctrl.Exists(timeout, 0.5)
    except Exception:
        return False


def is_shift_pressed() -> bool:
    try:
        return ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000 != 0
    except Exception:
        return False


def click_at_absolute(x: int, y: int):
    """绝对坐标点击。自动穿透隐私保护遮罩。"""
    from src.uia.retry.clicks import physical_click
    physical_click(x, y)
    time.sleep(0.1)


if win32gui is None:
    class WeChatDriver:
        """非 Windows 平台下的 Dummy 微信 UIA 驱动占位"""
        def __init__(self, *args, **kwargs):
            pass
        
        def claim_redpacket(self, session_id: str, is_group: bool) -> bool:
            return False
else:
    from .modules.core import WeChatCoreMixin
    from .modules.navigation import WeChatNavigationMixin
    from .modules.search import WeChatSearchMixin
    from .modules.messager import WeChatMessagerMixin
    from .modules.contacts import WeChatContactsMixin
    from .modules.moments import WeChatMomentsMixin
    from .modules.warmup import WeChatWarmupMixin
    from .tag_sync.chat_scene import ChatSceneMixin

    class WeChatDriver(
        WeChatCoreMixin,
        WeChatNavigationMixin,
        WeChatSearchMixin,
        WeChatMessagerMixin,
        WeChatContactsMixin,
        WeChatMomentsMixin,
        WeChatWarmupMixin,
        ChatSceneMixin
    ):
        """
        微信 4.1.x UIA 驱动（Facade 模式）
        为了解决功能臃肿，代码已按照职责划分到 src/uia/modules/ 下面的 Mixin 中。
        此处作为外观模式，汇集所有挂载能力向外暴露统一 API。
        """
        
        def claim_redpacket(self, session_id: str, is_group: bool) -> bool:
            """自动抢红包方法"""
            from .modules.redpacket import claim_redpacket_impl
            return claim_redpacket_impl(self, session_id, is_group)
