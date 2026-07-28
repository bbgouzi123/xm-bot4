import logging
from typing import Tuple

logger = logging.getLogger(__name__)

def execute_enterprise_command(mixin, cmd_type: str, payload: dict) -> Tuple[bool, dict]:
    """
    执行单条企业命令。
    """
    try:
        if cmd_type == "pause_all":
            return _cmd_pause_all(mixin)
        elif cmd_type == "resume_all":
            return _cmd_resume_all(mixin)
        elif cmd_type == "force_sync":
            return _cmd_force_sync(mixin)
        elif cmd_type == "set_daily_limit":
            return _cmd_set_daily_limit(mixin, payload)
        elif cmd_type == "suspend_auto_reply":
            return _cmd_suspend_auto_reply(mixin)
        elif cmd_type == "update_config":
            return _cmd_update_config(mixin, payload)
        else:
            logger.warning(f"[企业命令] 未知命令类型: {cmd_type}")
            return False, {"error": f"未知命令类型: {cmd_type}"}
    except Exception as e:
        return False, {"error": str(e)}

def _cmd_pause_all(mixin) -> Tuple[bool, dict]:
    """暂停所有自动化任务"""
    try:
        from src.utils.stop_signal import stop_signal
        stop_signal.request_stop("企业管控平台远程下发暂停指令")
        mixin.report_audit_event("enterprise_pause_all", risk_level="warning")
        return True, {"message": "所有任务已暂停"}
    except Exception as e:
        return False, {"error": str(e)}

def _cmd_resume_all(mixin) -> Tuple[bool, dict]:
    """恢复所有自动化任务"""
    try:
        from src.utils.stop_signal import stop_signal
        stop_signal.reset()
        mixin.report_audit_event("enterprise_resume_all")
        return True, {"message": "所有任务已恢复"}
    except Exception as e:
        return False, {"error": str(e)}

def _cmd_force_sync(mixin) -> Tuple[bool, dict]:
    """强制同步数据到同步后端"""
    try:
        from src.crm.profile_manager import ProfileManager
        ProfileManager().sync_all_to_cloud()
        from src.crm.account_data import get_active_account
        mixin.report_usage(get_active_account() or "main")
        mixin.report_audit_event("enterprise_force_sync")
        return True, {"message": "数据已强制同步"}
    except Exception as e:
        return False, {"error": str(e)}

def _cmd_set_daily_limit(mixin, payload: dict) -> Tuple[bool, dict]:
    """设置每日加好友上限"""
    try:
        limit = payload.get("limit", 30)
        from src.api.config_api import _load_configs, _save_configs
        configs = _load_configs() or {}
        configs["add_friend_daily_limit"] = limit
        _save_configs(configs)
        mixin.report_audit_event(
            "enterprise_set_daily_limit",
            detail={"limit": limit},
        )
        return True, {"message": f"每日加好友上限已设为 {limit}"}
    except Exception as e:
        return False, {"error": str(e)}

def _cmd_suspend_auto_reply(mixin) -> Tuple[bool, dict]:
    """暂停自动回复"""
    try:
        from src.api.config_api import _load_configs, _save_configs
        configs = _load_configs() or {}
        configs["auto_reply_enabled"] = False
        _save_configs(configs)
        mixin.report_audit_event(
            "enterprise_suspend_auto_reply",
            risk_level="warning",
        )
        return True, {"message": "自动回复已暂停"}
    except Exception as e:
        return False, {"error": str(e)}

def _cmd_update_config(mixin, payload: dict) -> Tuple[bool, dict]:
    """远程更新配置"""
    try:
        from src.api.config_api import _load_configs, _save_configs
        configs = _load_configs() or {}
        update_keys = payload.get("config", {})
        configs.update(update_keys)
        _save_configs(configs)

        # 强制更新本地内存配置缓存，确保行业配置等共享 KV 在运行期立即生效，无需重启
        try:
            from src.utils.config_cache import config_cache
            config_cache.load_from_cloud(clear_before_load=True)
            logger.info("[企业命令] 成功自动重载云端配置缓存")
        except Exception as load_err:
            logger.warning(f"[企业命令] 自动重载云端配置缓存失败: {load_err}")

        # 通过 SSE 广播配置更新系统通知给前端客户端
        try:
            from src.utils.sse_manager import sse_manager
            sse_manager.notify(
                "config_updated", 
                f"企业已远程同步最新的规则与话术，更新了 {len(update_keys)} 个配置项，最新口径已即时生效。"
            )
        except Exception as sse_err:
            logger.warning(f"[企业命令] 推送配置更新 SSE 通知失败: {sse_err}")

        mixin.report_audit_event(
            "enterprise_update_config",
            detail={"keys": list(update_keys.keys())},
        )
        return True, {"message": f"已更新 {len(update_keys)} 个配置项"}
    except Exception as e:
        return False, {"error": str(e)}
