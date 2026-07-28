from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseCustomerAdapter(ABC):
    """客户 API 适配器基类"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    async def send_message(self, target: str, text: str) -> bool:
        """发送消息上报"""
        pass

    @abstractmethod
    async def notify_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """事件通知：新好友通过、跟单完成等"""
        pass
