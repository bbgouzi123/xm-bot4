import os
import aiohttp
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class FeishuNotifier:
    """飞书机器人风控告警通知 (支持脱机后报警)"""
    
    def __init__(self):
        # 备份环境变量作为兜底
        self._env_webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")

    def get_webhook_url(self) -> str:
        """动态加载用户配置的飞书 Webhook 地址，若未开启或未填写则降级到环境变量"""
        try:
            from src.api.config_api.base_config import _load_configs
            configs = _load_configs()
            fs = configs.get("alert_feishu_settings", {})
            if fs.get("enabled", False) and fs.get("webhook_url", "").strip():
                return fs.get("webhook_url", "").strip()
        except Exception:
            pass
        return self._env_webhook_url
        
    async def send_text(self, text: str) -> bool:
        """发送纯文本异常警报"""
        url = self.get_webhook_url()
        if not url:
            logger.warning("未配置飞书 Webhook URL，跳过同步后端文本报警")
            return False
            
        payload = {
            "msg_type": "text",
            "content": {"text": text}
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"飞书短信推送异常: {e}")
            return False

    async def send_alert_card(self, title: str, content: str, level: str = "error") -> bool:
        """发送富文本互动卡片，醒目提示代运营操作员"""
        url = self.get_webhook_url()
        if not url:
            return False
            
        # 根据系统异常级别配色
        color_map = {
            "error": "red",
            "fatal": "red",
            "warning": "orange",
            "info": "blue"
        }
        color = color_map.get(level, "blue")
        
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title
                    },
                    "template": color
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "content": content,
                            "tag": "lark_md"
                        }
                    }
                ]
            }
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"飞书卡片推送异常: {e}")
            return False

# 导出单例对象
feishu_notifier = FeishuNotifier()
