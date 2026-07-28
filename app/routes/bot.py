import logging
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
import app.state as app_state
from app.state import account_manager, driver, monitor
from src.api import config_api
from src.utils.response import err, ok, ok_msg

router = APIRouter()
_log = logging.getLogger(__name__)

async def _start_bot_core() -> bool:
    """内部：实际启动各项后台任务（不含授权检查，由调用方负责）。"""
    app_state._bot_automation_running = True

    if monitor.ai_service and monitor.ai_service.is_configured():
        for inst in account_manager._instances.values():
            inst.monitor.ai_service = monitor.ai_service
            if inst.hwnd:
                try:
                    from src.uia.retry.window_ops import ensure_wechat_foreground
                    ensure_wechat_foreground(inst.hwnd)
                except Exception as e:
                    _log.warning(f"[启动] 自检唤醒微信窗口 (hwnd={inst.hwnd}) 异常: {e}")
        await account_manager.start_all()
    else:
        app_state._bot_automation_running = False
        return False

    try:
        from src.api.config_api import _load_configs
        configs = _load_configs()
        auto_accept = configs.get("friend_request_settings", {}).get("auto_accept", False)
        if auto_accept and hasattr(config_api, "_friend_request_monitor") and getattr(config_api, "_friend_request_monitor"):
            if not config_api._friend_request_monitor.is_running():
                config_api._friend_request_monitor.start()
                _log.info("[启动] 检查到好友自动通过已开启，拉起监控器")
    except Exception as e:
        _log.warning(f"[启动] 尝试恢复好友自动通过监控异常: {e}")

    try:
        from src.api.config_api import _load_configs, _save_configs
        configs = _load_configs()
        configs["bot_auto_start"] = True
        _save_configs(configs)
        
        from src.utils.config_cache import config_cache
        config_cache.set("monitor_auto_start", "running")
    except Exception:
        pass

    try:
        from src.utils.websocket_manager import ws_manager
        await ws_manager.broadcast_json({"type": "bot_status", "data": {"running": True}})
    except Exception:
        pass

    return True


@router.post("/api/system/bot/start")
async def start_bot_automation():
    if app_state._bot_automation_running:
        return ok_msg("已经处于运行状态")
    
    from src.utils.license_validator import LicenseValidator
    from src.utils.daily_counter import DailyCounter
    
    features = await run_in_threadpool(LicenseValidator.check_features)
    if not features.get("auto_chat", False):
        return err(40301, "当前版本不支持自动回复功能，请升级套餐")

    sub = await run_in_threadpool(LicenseValidator.check_subscription)
    if sub.get("status") in ("trial_expired", "expired"):
        return err(40302, "体验版已到期！部分功能已受限，请选择版本升级。")
        
    ai_limit = features.get("ai_daily_limit", 30)
    if ai_limit > 0:
        account_id = getattr(driver, 'bot_wxid', 'main') if driver else 'main'
        current_count = DailyCounter().get_count("auto_reply", account_id)
        if current_count >= ai_limit:
            return err(40303, f"今日 AI 自动聊天额度（{ai_limit}次）已用完，请升级套餐")
    
    try:
        wxid = getattr(driver, 'bot_wxid', None) if driver else None
        if wxid:
            LicenseValidator.bind_wechat(wxid)
    except Exception as e:
        _log.debug(f"微信号绑定失败（不影响启动）: {e}")
    
    success = await _start_bot_core()
    if not success:
        return err(40000, "启动失败：AI 服务未配置，请先在系统设置中配置 AI 服务 API Key")
        
    return ok_msg("自动聊天已开启")


@router.post("/api/system/bot/stop")
async def stop_bot_automation():
    if not app_state._bot_automation_running:
        return ok_msg("已经处于停止状态")
        
    app_state._bot_automation_running = False
    
    await account_manager.stop_all()
    
    if hasattr(config_api, "_friend_request_monitor") and getattr(config_api, "_friend_request_monitor"):
        try:
            if config_api._friend_request_monitor.is_running():
                config_api._friend_request_monitor.stop()
        except:
            pass
            
    try:
        from src.api.config_api import _load_configs, _save_configs
        configs = _load_configs()
        configs["bot_auto_start"] = False
        _save_configs(configs)
        
        from src.utils.config_cache import config_cache
        config_cache.set("monitor_auto_start", "stopped")
    except Exception:
        pass
        
    try:
        from src.utils.websocket_manager import ws_manager
        await ws_manager.broadcast_json({"type": "bot_status", "data": {"running": False}})
    except Exception:
        pass
        
    return ok_msg("自动聊天已关闭")

@router.get("/api/system/bot/status")
async def get_bot_status():
    return ok({"running": app_state._bot_automation_running})
