"""
DriverBackend 抽象基类
======================
定义 UIA Backend 和 OCR Backend 的统一接口。
所有后端必须实现这些方法，确保 WeChatDriver 可以无缝切换。
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Tuple


class DriverBackend(ABC):
    """微信驱动后端抽象基类"""

    @abstractmethod
    def find_nav_toolbar(self, hwnd: int) -> Tuple[object, object]:
        """查找导航栏

        Args:
            hwnd: 微信主窗口句柄

        Returns:
            (root_control, nav_toolbar) — UIA 模式返回控件对象，
            OCR 模式返回 (None, region_rect)
        """
        ...

    @abstractmethod
    def extract_user_info(self, hwnd: int, skip_avatar: bool = False) -> Dict:
        """提取当前登录用户信息

        Returns:
            {
                "nickname": str,
                "wxid": str,
                "avatar_path": str or None,
            }
        """
        ...

    @abstractmethod
    def scan_sessions(self, hwnd: int, max_count: int = 50) -> List[Dict]:
        """扫描会话列表

        Returns:
            [{"name": str, "last_msg": str, "time": str, "unread": int}, ...]
        """
        ...

    @abstractmethod
    def read_messages(self, hwnd: int, count: int = 20) -> List[Dict]:
        """读取当前聊天窗口的消息

        Returns:
            [{"sender": str, "content": str, "time": str, "type": str}, ...]
        """
        ...

    @abstractmethod
    def click_session(self, hwnd: int, session_name: str) -> bool:
        """点击指定会话

        Returns:
            True=成功, False=未找到
        """
        ...

    @abstractmethod
    def send_text(self, hwnd: int, text: str) -> bool:
        """在当前聊天窗口发送文本消息

        Returns:
            True=成功
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """检查该后端是否可用（依赖是否满足）"""
        ...
