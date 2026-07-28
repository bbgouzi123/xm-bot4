import httpx
import logging
import hmac
import hashlib
import time
import asyncio
import json
from typing import Dict, Any
from .base_adapter import BaseCustomerAdapter

logger = logging.getLogger(__name__)

class StandardCustomerAdapter(BaseCustomerAdapter):
    """标准 JSON 投递适配器"""
    
    async def send_message(self, target: str, text: str) -> bool:
        payload = {
            "event": "send_message",
            "target": target,
            "text": text,
            "timestamp": int(time.time())
        }
        return await self.notify_event("send_message", payload)

    _url_warned = False

    async def notify_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        url = self.config.get("url")
        if not url:
            if not StandardCustomerAdapter._url_warned:
                logger.warning("[StandardAdapter] 未配置推送 URL")
                StandardCustomerAdapter._url_warned = True
            return False
            
        secret = self.config.get("secret", "")
        headers = {"Content-Type": "application/json"}
        
        # 封装并签名
        body = {
            "event_type": event_type,
            "payload": payload,
            "timestamp": int(time.time())
        }
        body_str = json.dumps(body, ensure_ascii=False)
        
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                body_str.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            headers["X-XM-Signature"] = signature
            
        async with httpx.AsyncClient(timeout=10) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(url, headers=headers, content=body_str)
                    if resp.status_code == 200:
                        logger.info(f"[StandardAdapter] 事件 {event_type} 推送成功")
                        return True
                    else:
                        logger.warning(f"[StandardAdapter] 推送失败，HTTP 状态码: {resp.status_code}，重试中...")
                except Exception as e:
                    logger.warning(f"[StandardAdapter] 推送异常: {e}，重试中...")
                await asyncio.sleep(1.0)
        logger.error(f"[StandardAdapter] 事件 {event_type} 推送彻底失败")
        return False
