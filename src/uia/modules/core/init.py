"""WeChatDriver 核心状态字段初始化。"""
import threading
from pathlib import Path
from typing import List, Optional


class WeChatCoreInitMixin:
    def __init__(self):
        self.driver = self
        self.hwnd: Optional[int] = None
        self._root = None          # uiautomation 根控件
        self.resolution = "1080p"
        self._nickname = ""
        self._wxid = ""
        self._connected = False
        self._lock = threading.Lock()
        self._contacts_cache: List[dict] = []
        self._contacts_file = Path.home() / ".xm-ai-bot" / "contacts.json"

    @property
    def root(self):
        import win32gui
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            return None
        # 核心防御：若微信主窗口处于挂起卡死状态，拒绝执行 ControlFromHandle 以防调用线程卡死
        import ctypes
        if ctypes.windll.user32.IsHungAppWindow(self.hwnd):
            import logging
            logging.getLogger("WeChatDriver").warning(f"[UIA] 检测到微信主窗口 hwnd={self.hwnd} 处于挂起卡死(Hung)状态，主动拦截 root 访问以防线程卡死")
            return None
            
        # 核心修正：COM 跨套间/跨线程调用安全保护。
        # 总是根据当前线程 of COM 套间动态实例化一个 Control 对象，其开销极小且绝对安全。
        import uiautomation as uia
        import time
        import comtypes
        for i in range(5):
            try:
                try:
                    comtypes.CoInitialize()
                except Exception:
                    pass
                return uia.ControlFromHandle(self.hwnd)
            except Exception as e:
                if i < 4:
                    print(f"[UIA] ControlFromHandle(hwnd={self.hwnd}) 遇到临时 COM 异常: {e}，正在进行第 {i+1} 次重试...")
                    time.sleep(0.2)
                else:
                    print(f"[UIA] ControlFromHandle(hwnd={self.hwnd}) 失败: {e}")
                    return None

    @root.setter
    def root(self, value):
        self._root = value
