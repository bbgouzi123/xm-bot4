"""
CRM 连接器与适配器工厂 (Webhook + 飞书多维表格)
"""
import logging
import json
import httpx
import asyncio
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class CRMBaseConnector:
    """CRM 连接器基类"""
    def sync_profile(self, profile_dict: dict) -> bool:
        raise NotImplementedError


class WebhookConnector(CRMBaseConnector):
    """通用 Webhook 适配器 (POST JSON 发送客户画像变动)"""
    def __init__(self, url: str):
        self.url = url

    def sync_profile(self, profile_dict: dict) -> bool:
        if not self.url:
            return False
        try:
            payload = {
                "event": "profile_updated",
                "wxid": profile_dict.get("wxid"),
                "nickname": profile_dict.get("nickname"),
                "tags": [
                    {"category": t.get("category"), "subcategory": t.get("subcategory"), "value": t.get("value")}
                    for t in profile_dict.get("tags", [])
                ],
                "conversation_summary": profile_dict.get("conversation_summary"),
                "notes": profile_dict.get("notes"),
                "last_active": profile_dict.get("last_active")
            }
            res = httpx.post(self.url, json=payload, timeout=5.0)
            if res.status_code == 200:
                logger.info(f"[CRMWebhook] Webhook 触发成功: {profile_dict.get('nickname')}")
                return True
            else:
                logger.error(f"[CRMWebhook] Webhook 状态码异常: {res.status_code}")
        except Exception as e:
            logger.error(f"[CRMWebhook] Webhook 同步异常: {e}")
        return False


class LarkBitableConnector(CRMBaseConnector):
    """飞书多维表格 Bitable 适配器"""
    def __init__(self, app_token: str, table_id: str, tenant_access_token: str = None, app_id: str = None, app_secret: str = None):
        self.app_token = app_token
        self.table_id = table_id
        self.tenant_access_token = tenant_access_token
        self.app_id = app_id
        self.app_secret = app_secret

    async def _ensure_token(self) -> str:
        if self.tenant_access_token:
            return self.tenant_access_token
        if self.app_id and self.app_secret:
            try:
                async with httpx.AsyncClient() as client:
                    res = await client.post(
                        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                        json={"app_id": self.app_id, "app_secret": self.app_secret},
                        timeout=5.0
                    )
                    if res.status_code == 200:
                        data = res.json()
                        self.tenant_access_token = data.get("tenant_access_token")
                        return self.tenant_access_token
            except Exception as e:
                logger.error(f"[LarkBitable] 获取租户 Token 失败: {e}")
        return ""

    async def sync_profile_async(self, profile_dict: dict) -> bool:
        token = await self._ensure_token()
        if not token or not self.app_token or not self.table_id:
            logger.warning("[LarkBitable] 飞书多维表格参数未配置，跳过同步")
            return False
            
        try:
            tags_str = ", ".join(f"[{t.get('subcategory')}]{t.get('value')}" for t in profile_dict.get("tags", []))
            fields = {
                "微信ID": profile_dict.get("wxid"),
                "昵称": profile_dict.get("nickname"),
                "标签画像": tags_str,
                "对话摘要": profile_dict.get("conversation_summary", "")[:1000],
                "最后活跃": profile_dict.get("last_active", ""),
            }
            
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records"
            
            search_url = f"{url}/search"
            search_query = {
                "filter": {
                    "conjunction": "and",
                    "conditions": [{"field_name": "微信ID", "operator": "is", "value": [profile_dict.get("wxid")]}]
                }
            }
            
            async with httpx.AsyncClient() as client:
                search_res = await client.post(search_url, json=search_query, headers=headers, timeout=5.0)
                record_id = None
                if search_res.status_code == 200:
                    records = search_res.json().get("data", {}).get("items", [])
                    if records:
                        record_id = records[0].get("record_id")

                if record_id:
                    put_res = await client.put(f"{url}/{record_id}", json={"fields": fields}, headers=headers, timeout=5.0)
                    success = put_res.status_code == 200
                else:
                    post_res = await client.post(url, json={"fields": fields}, headers=headers, timeout=5.0)
                    success = post_res.status_code == 200
                
                if success:
                    logger.info(f"[LarkBitable] 同步多维表格记录成功: {profile_dict.get('nickname')}")
                    return True
                else:
                    logger.error(f"[LarkBitable] 请求飞书 API 返回异常: {search_res.text if record_id else ''}")
        except Exception as e:
            logger.error(f"[LarkBitable] 同步多维表格失败: {e}")
        return False

    def sync_profile(self, profile_dict: dict) -> bool:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.sync_profile_async(profile_dict))
                return True
            else:
                return loop.run_until_complete(self.sync_profile_async(profile_dict))
        except Exception:
            try:
                new_loop = asyncio.new_event_loop()
                new_loop.run_until_complete(self.sync_profile_async(profile_dict))
                new_loop.close()
                return True
            except Exception as err:
                logger.error(f"[LarkBitable] 同步多维表格内部事件循环异常: {err}")
                return False


class CRMConnectorFactory:
    """CRM 连接器工厂"""
    _connectors = []
    
    @classmethod
    def load_connectors_from_config(cls) -> List[CRMBaseConnector]:
        cls._connectors = []
        try:
            from src.api.config_api import _load_configs
            configs = _load_configs() or {}
            crm_cfg = configs.get("crm_connectors", {})
            
            if crm_cfg.get("webhook_enabled") and crm_cfg.get("webhook_url"):
                cls._connectors.append(WebhookConnector(crm_cfg["webhook_url"]))
                logger.info("[CRMFactory] 已启用 Webhook 连接器")
                
            if crm_cfg.get("lark_enabled") and crm_cfg.get("lark_app_token") and crm_cfg.get("lark_table_id"):
                cls._connectors.append(
                    LarkBitableConnector(
                        app_token=crm_cfg["lark_app_token"],
                        table_id=crm_cfg["lark_table_id"],
                        app_id=crm_cfg.get("lark_app_id"),
                        app_secret=crm_cfg.get("lark_app_secret")
                    )
                )
                logger.info("[CRMFactory] 已启用飞书多维表格连接器")
        except Exception as e:
            logger.error(f"[CRMFactory] 加载连接器配置异常: {e}")
        return cls._connectors

    @classmethod
    def dispatch_sync(cls, profile_dict: dict):
        """分发更新"""
        connectors = cls.load_connectors_from_config()
        for conn in connectors:
            try:
                conn.sync_profile(profile_dict)
            except Exception as e:
                logger.error(f"[CRMFactory] 分发同步时连接器异常: {e}")
