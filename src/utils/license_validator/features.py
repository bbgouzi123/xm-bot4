"""
功能锁与状态查询模块
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class FeaturesMixin:
    _last_degraded_log_state = False  # 缓存上次降级日志状态，避免高频刷屏

    @classmethod
    def check_features(cls) -> Dict[str, Any]:
        """功能锁查询（含版本配额与开关）"""
        # 1. 默认降级配额（兜底）
        base_features = {
            "auto_chat": True, "moments_auto": False, "mass_messaging": False,
            "data_analytics": False, "smart_acquisition": True, "community_mgmt": False,
            "ai_daily_limit": 30, "max_wechat": 1, "active_warmup": False,
            "openapi_access": False, "max_mobile": 1,
        }

        # 降级模式判断
        is_degraded_now = getattr(cls, 'is_degraded', lambda: False)()
        if is_degraded_now:
            if not getattr(cls, '_last_degraded_log_state', False):
                logger.warning("[功能锁] 处于降级模式，强制返回基础功能集")
                setattr(cls, '_last_degraded_log_state', True)
            base_features.update({"degraded": True})
            return base_features
        else:
            if getattr(cls, '_last_degraded_log_state', False):
                setattr(cls, '_last_degraded_log_state', False)
        
        # 获取订阅状态
        sub = getattr(cls, 'check_subscription', lambda: {})()
        if not sub.get("valid"):
            logger.debug("[功能锁] 订阅无效或已到期，使用试用版默认配额")
            return base_features

        # 2. 根据计划代码获取预设配额（合并模式）
        plan_features_map = {
            "trial": {"auto_chat": True, "moments_auto": True, "mass_messaging": True, "data_analytics": True, "smart_acquisition": True, "community_mgmt": True, "enterprise_dashboard": True, "ai_daily_limit": 1000, "max_wechat": 2, "max_mobile": 2, "active_warmup": True, "sales_champion_workflow": True, "custom_all_industry_agents": True, "one_on_one_training": True, "openapi_access": True},
            "basic": {"auto_chat": True, "moments_auto": True, "mass_messaging": True, "data_analytics": True, "smart_acquisition": True, "community_mgmt": False, "enterprise_dashboard": False, "ai_daily_limit": 1000, "max_wechat": 1, "max_mobile": 1, "active_warmup": False, "sales_champion_workflow": True, "openapi_access": False},
            "pro": {"auto_chat": True, "moments_auto": True, "mass_messaging": True, "data_analytics": True, "smart_acquisition": True, "community_mgmt": False, "enterprise_dashboard": False, "ai_daily_limit": 3000, "max_wechat": 3, "max_mobile": 3, "active_warmup": False, "sales_champion_workflow": True, "custom_industry_agent": True, "openapi_access": False},
            "flagship": {"auto_chat": True, "moments_auto": True, "mass_messaging": True, "data_analytics": True, "smart_acquisition": True, "community_mgmt": True, "enterprise_dashboard": True, "ai_daily_limit": 10000, "max_wechat": 10, "max_mobile": 10, "active_warmup": True, "sales_champion_workflow": True, "custom_all_industry_agents": True, "one_on_one_training": True, "openapi_access": True},
            "pkg_sales_champion_generic": {"auto_chat": True, "moments_auto": False, "mass_messaging": False, "data_analytics": False, "smart_acquisition": True, "community_mgmt": False, "enterprise_dashboard": False, "ai_daily_limit": 100, "max_wechat": 1, "max_mobile": 1, "active_warmup": False, "sales_champion_workflow": True, "openapi_access": False},
        }
        
        plan = sub.get("plan_code", "trial")
        if plan in plan_features_map:
            base_features.update(plan_features_map[plan])
        else:
            logger.warning(f"[功能锁] 未知计划代码: {plan}，回退至试用版配额")

        # 检查是否单独购买了 API 增值包 (200/月)
        try:
            from src.utils.config_cache import config_cache
            api_sub = config_cache.get("openapi_addon_subscription", {})
            if api_sub and api_sub.get("active"):
                from datetime import datetime
                exp_str = api_sub.get("expires_at", "")
                if exp_str:
                    # 兼容含有 Z 或 ISO 格式的时间
                    clean_exp = exp_str.replace("Z", "+00:00")
                    exp_dt = datetime.fromisoformat(clean_exp)
                    # 转换当前时间为带时区的格式进行比对
                    from datetime import timezone
                    now_tz = datetime.now(exp_dt.tzinfo or timezone.utc)
                    if exp_dt > now_tz:
                        base_features["openapi_access"] = True
                        logger.info("[功能锁] 成功验证独立的 API 增值包授权！")
        except Exception as e:
            logger.warning(f"[功能锁] 独立 API 增值包校验异常: {e}")

        # 3. 如果服务端返回了显式的 features 覆盖（强合并）
        features_override = sub.get("features")
        if features_override:
            if isinstance(features_override, dict):
                base_features.update(features_override)
            elif isinstance(features_override, list):
                # 兼容列表格式（有的系统用列表存储已开启的功能名）
                for f in features_override:
                    if isinstance(f, str):
                        base_features[f] = True
        
        # 4. 强制使用根节点的权威数值数据（如 max_wechat, ai_daily_limit）
        # 这些数据在 xm-user 响应中通常位于根级，优先级最高
        for key in ["max_wechat", "ai_daily_limit", "max_industries", "max_unbinds", "gift_seats_count", "max_mobile"]:
            if key in sub:
                base_features[key] = sub[key]
            
        return base_features

    @classmethod
    def get_unified_status(cls) -> Dict[str, Any]:
        """统一状态接口"""
        # check_subscription 将由 SubscriptionMixin 提供
        result = getattr(cls, 'check_subscription', lambda: {})()
        # get_machine_code 将由 MachineMixin 提供
        result["machine_code"] = getattr(cls, 'get_machine_code', lambda: "0000-0000-0000-0000")()
        result["device_id"] = result["machine_code"]
        return result
