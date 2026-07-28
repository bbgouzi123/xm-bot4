"""
兼容占位：旧版 ``utils`` 话术模块反编译产物已移除。

业务请使用 ``src.monitor.greeting_manager.GreetingManager``。
"""

from src.monitor.greeting_manager import GreetingManager

__all__ = ["GreetingManager"]
