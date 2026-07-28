import logging
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class PluginMarketMixin:
    """WeChatDBManager 的履约插件市场、购买历史、提现管理混入类 (Mixin)"""

    def get_market_plugins(self) -> List[Dict[str, Any]]:
        """获取所有已注册的插件列表"""
        return getattr(self, "_market_plugins", [])

    def add_market_plugin(self, plugin_data: Dict[str, Any]) -> Dict[str, Any]:
        """发布并添加一个新的插件（支持安全状态的初始设定）"""
        plugins = getattr(self, "_market_plugins", [])
        plugins.insert(0, plugin_data)
        self._persist_snapshot()
        return plugin_data

    def delete_market_plugin(self, plugin_id: str) -> bool:
        """删除某个插件"""
        plugins = getattr(self, "_market_plugins", [])
        for idx, item in enumerate(plugins):
            if item.get("id") == plugin_id:
                plugins.pop(idx)
                self._persist_snapshot()
                return True
        return False

    def get_plugin_purchases(self) -> List[Dict[str, Any]]:
        """获取全量的购买历史"""
        return getattr(self, "_plugin_purchases", [])

    def add_plugin_purchase(self, purchase_data: Dict[str, Any]) -> Dict[str, Any]:
        """新增一条购买记录，并同步激活用户的履约能力"""
        purchases = getattr(self, "_plugin_purchases", [])
        purchases.insert(0, purchase_data)
        
        # 自动将所购买插件的代码转化为用户本地的 fulfillment capabilities
        plugins = getattr(self, "_market_plugins", [])
        target_plugin = next((p for p in plugins if p.get("id") == purchase_data.get("plugin_id")), None)
        
        if target_plugin:
            # 引入 db_task_mixin 中能力注册的逻辑
            capabilities = getattr(self, "_fulfillment_capabilities", [])
            existing = next((c for c in capabilities if c.get("key") == target_plugin.get("key")), None)
            
            new_cap = {
                "key": target_plugin.get("key"),
                "name": target_plugin.get("name"),
                "safety_level": target_plugin.get("safety_level", 3),  # 优先采用插件本身的安全级别，默认3
                "enabled": True,
                "is_custom": True,
                "config": {
                    "intent_keywords": target_plugin.get("intent_keywords", ""),
                    "cmd_template": target_plugin.get("cmd_template", ""),
                    "code_content": target_plugin.get("code_content", ""), # 注入源代码供查看与修改
                    "purchased": True,
                    "plugin_id": target_plugin.get("id"),
                    **(target_plugin.get("config") or {}) # 动态合并插件默认配置，如 fallback_video 等
                }
            }
            if existing:
                existing.update(new_cap)
            else:
                capabilities.append(new_cap)
                
        self._persist_snapshot()
        return purchase_data

    def get_withdrawal_records(self) -> List[Dict[str, Any]]:
        """获取所有提现申请历史"""
        return getattr(self, "_withdrawal_records", [])

    def add_withdrawal_record(self, record_data: Dict[str, Any]) -> Dict[str, Any]:
        """提交新的提现申请"""
        records = getattr(self, "_withdrawal_records", [])
        records.insert(0, record_data)
        self._persist_snapshot()
        return record_data
