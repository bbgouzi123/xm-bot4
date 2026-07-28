"""
聊天监控控制 API
"""
from fastapi import APIRouter
from src.utils.response import ok, err, ok_msg

router = APIRouter()

def _save_monitor_state(state: str):
    try:
        from src.utils.config_cache import config_cache
        config_cache.set("monitor_auto_start", state)
    except: pass

def _load_monitor_state() -> str:
    try:
        from src.utils.config_cache import config_cache
        val = config_cache.get("monitor_auto_start")
        if val: return val
    except: pass
    return 'stopped'

@router.get("/api/monitor/status")
async def monitor_status():
    from app.routes_builtin import _bot_automation_running
    from src.utils.license_validator import LicenseValidator
    from src.utils.daily_counter import _chat_daily_counter
    from app.state import monitor, driver
    
    import asyncio
    loop = asyncio.get_running_loop()
    sub_info = await loop.run_in_executor(None, LicenseValidator.check_subscription)
    ai_limit = sub_info.get("ai_daily_limit", 30)
    
    account_id = getattr(driver, 'bot_wxid', 'main') or 'main'
    ai_used = _chat_daily_counter.get_count("auto_reply", account_id)
    
    is_running = _bot_automation_running
    
    return ok({
        "running": is_running,
        "paused": _load_monitor_state() == 'paused',
        "ai_configured": monitor.ai_service.is_configured() if monitor and monitor.ai_service else False,
        "sessions": len(monitor._initialized) if monitor else 0,
        "suspended_count": 0,
        "stats": monitor._stats.copy() if monitor else {},
        "degraded": LicenseValidator.is_degraded(),
        "quota": {
            "ai_used": ai_used,
            "ai_limit": ai_limit,
            "ai_remaining": max(0, ai_limit - ai_used) if ai_limit > 0 else -1,
            "exhausted": ai_limit > 0 and ai_used >= ai_limit,
        },
    })

@router.post("/api/monitor/start")
async def monitor_start():
    from app.routes_builtin import start_bot_automation
    res = await start_bot_automation()
    if res.get("code") in (200, 20000):
        _save_monitor_state('running')
        return ok_msg("操作成功")
    return res

@router.post("/api/monitor/stop")
async def monitor_stop():
    from app.routes_builtin import stop_bot_automation
    res = await stop_bot_automation()
    if res.get("code") in (200, 20000):
        _save_monitor_state('stopped')
        return ok_msg("操作成功")
    return res

@router.post("/api/monitor/pause")
async def monitor_pause():
    from app.state import account_manager
    for inst in account_manager._instances.values():
        if inst.monitor._running:
            inst.monitor.pause()
    _save_monitor_state('paused')
    return ok_msg("操作成功")

@router.post("/api/monitor/resume")
async def monitor_resume():
    from app.state import account_manager
    for inst in account_manager._instances.values():
        if inst.monitor._running:
            inst.monitor.resume()
    _save_monitor_state('running')
    return ok_msg("操作成功")


@router.get("/api/monitor/circuit-breaker/sessions")
async def get_circuit_breaker_sessions():
    from src.utils.uia_circuit_breaker import get_fused_sessions
    return ok(get_fused_sessions())


@router.post("/api/monitor/circuit-breaker/reset")
async def reset_circuit_breaker(payload: dict):
    from src.utils.uia_circuit_breaker import reset_session_fuse, resume_engine
    session_id = payload.get("session_id")
    clear_all = payload.get("all", False)
    if clear_all:
        resume_engine()
        return ok_msg("所有会话熔断已重置")
    elif session_id:
        reset_session_fuse(session_id)
        return ok_msg(f"会话 {session_id} 熔断已重置")
    return err("参数错误")


@router.get("/api/monitor/suspended-sessions")
async def get_suspended_sessions():
    import time
    from app.state import monitor
    if not monitor:
        return ok([])
    
    # 获取用户配置的人工回复挂起时长（分钟 * 60）
    try:
        from src.utils.rest_time import get_rest_config
        rest_cfg = get_rest_config(monitor.account_id)
        suspend_secs = int(rest_cfg.get("manual_suspend_minutes", 30)) * 60
    except Exception:
        suspend_secs = 30 * 60

    now = time.time()
    results = []
    
    # 1. 采集自自动检测的 _manual_interventions
    for name, t in list(monitor._manual_interventions.items()):
        if now - t < suspend_secs:
            results.append({
                "session_name": name,
                "suspended_at": t,
                "expire_at": t + suspend_secs,
                "duration": suspend_secs
            })
        else:
            # 顺便清理过期的
            monitor._manual_interventions.pop(name, None)
            
    # 2. 采集自 SessionManagerLogic 的 suspended_sessions（合并去重）
    try:
        partition = monitor.get_account_partition()
        for name, t in list(partition.suspended_sessions.items()):
            if now - t < suspend_secs:
                # 去重
                if not any(r["session_name"] == name for r in results):
                    results.append({
                        "session_name": name,
                        "suspended_at": t,
                        "expire_at": t + suspend_secs,
                        "duration": suspend_secs
                    })
            else:
                partition.suspended_sessions.pop(name, None)
    except Exception:
        pass

    return ok(results)


@router.post("/api/monitor/suspended-sessions/reset")
async def reset_suspended_session(payload: dict):
    from app.state import monitor
    if not monitor:
        return err("监控器未运行")
    session_name = payload.get("session_name")
    clear_all = payload.get("all", False)
    
    if clear_all:
        monitor._manual_interventions.clear()
        try:
            partition = monitor.get_account_partition()
            partition.suspended_sessions.clear()
        except Exception:
            pass
        return ok_msg("所有避让会话已恢复")
    elif session_name:
        monitor._manual_interventions.pop(session_name, None)
        try:
            partition = monitor.get_account_partition()
            partition.suspended_sessions.pop(session_name, None)
        except Exception:
            pass
        return ok_msg(f"会话 {session_name} 避让已恢复")
    return err("参数错误")


