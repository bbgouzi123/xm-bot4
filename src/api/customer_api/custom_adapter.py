import httpx
import logging
import asyncio
from typing import Dict, Any
from .base_adapter import BaseCustomerAdapter

logger = logging.getLogger(__name__)

class CustomCustomerAdapter(BaseCustomerAdapter):
    """自定义 KV 映射与表单/JSON格式适配器"""
    
    async def send_message(self, target: str, text: str) -> bool:
        payload = {
            "target": target,
            "text": text
        }
        return await self.notify_event("send_message", payload)

    async def notify_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        url = self.config.get("url")
        if not url:
            logger.warning("[CustomAdapter] 未配置推送 URL")
            return False
            
        mapping = self.config.get("field_mapping", {})
        headers = self.config.get("headers", {})
        
        mapped_payload = {}
        # 映射 event_type 关键字
        event_key = mapping.get("event_type", "event_type")
        mapped_payload[event_key] = event_type
        
        for k, v in payload.items():
            mapped_key = mapping.get(k, k)
            mapped_payload[mapped_key] = v
            
        is_urlencoded = self.config.get("format") == "urlencoded"
        
        async with httpx.AsyncClient(timeout=10) as client:
            for attempt in range(3):
                try:
                    if is_urlencoded:
                        resp = await client.post(url, headers=headers, data=mapped_payload)
                    else:
                        resp = await client.post(url, headers=headers, json=mapped_payload)
                    if resp.status_code in (200, 201):
                        logger.info(f"[CustomAdapter] 事件 {event_type} 推送成功")
                        return True
                    else:
                        logger.warning(f"[CustomAdapter] 推送失败，HTTP 状态码: {resp.status_code}，重试中...")
                except Exception as e:
                    logger.warning(f"[CustomAdapter] 自定义推送异常: {e}，重试中...")
                await asyncio.sleep(1.0)
        logger.error(f"[CustomAdapter] 事件 {event_type} 推送彻底失败")
        return False
