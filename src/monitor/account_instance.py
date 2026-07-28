from typing import Any
from src.uia.driver import WeChatDriver
from src.monitor.chat_monitor import ChatMonitor

class AccountInstance:
    """单个微信账号实例"""

    def __init__(self, hwnd: int, driver: WeChatDriver,
                 monitor: ChatMonitor, friend_request_monitor = None, nickname: str = "", wxid: str = ""):
        self.hwnd = hwnd
        self.driver = driver
        self.monitor = monitor
        self.friend_request_monitor = friend_request_monitor
        self.nickname = nickname
        self.wxid = wxid

    def to_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "nickname": self.nickname,
            "wxid": self.wxid,
            "connected": self.driver.is_connected(),
            "monitoring": self.monitor._running,
        }
