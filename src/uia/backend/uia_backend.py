"""
UIA Backend — 现有逻辑的包装层
==============================
不修改任何现有代码，仅将现有 UIA 函数包装为 DriverBackend 接口。
微信 ≤4.1.6.x 使用此后端。

状态：待实现（阶段 0）
"""
from .base import DriverBackend
from typing import Dict, List, Tuple


class UIABackend(DriverBackend):
    """旧版微信 UIA 后端 — 包装现有代码"""

    def find_nav_toolbar(self, hwnd: int) -> Tuple[object, object]:
        # TODO: 调用 startup_flow.find_nav_toolbar(hwnd)
        raise NotImplementedError("阶段 0 待实现")

    def extract_user_info(self, hwnd: int, skip_avatar: bool = False) -> Dict:
        # TODO: 调用 driver._extract_user_info()
        raise NotImplementedError("阶段 0 待实现")

    def scan_sessions(self, hwnd: int, max_count: int = 50) -> List[Dict]:
        # TODO: 调用 driver.get_session_list()
        raise NotImplementedError("阶段 2 待实现")

    def read_messages(self, hwnd: int, count: int = 20) -> List[Dict]:
        # TODO: 调用 driver.get_messages()
        raise NotImplementedError("阶段 3 待实现")

    def click_session(self, hwnd: int, session_name: str) -> bool:
        # TODO: 调用 driver.click_session()
        raise NotImplementedError("阶段 2 待实现")

    def send_text(self, hwnd: int, text: str) -> bool:
        # TODO: 调用 driver.send_text()
        raise NotImplementedError("阶段 4 待实现")

    def is_available(self) -> bool:
        """UIA 后端始终可用（基础依赖）"""
        try:
            import uiautomation
            return True
        except ImportError:
            return False
