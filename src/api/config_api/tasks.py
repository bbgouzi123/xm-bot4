from fastapi import Request
from src.utils.response import ok, err
from .state import router
from . import state

def _get_mass_sending_core():
    if state._mass_sending_core is None:
        from src.task.mass_sending_core import MassSendingCore
        state._mass_sending_core = MassSendingCore(state._driver)
    else:
        state._mass_sending_core.driver = state._driver
    return state._mass_sending_core

@router.get("/api/tasks/logs")
async def task_logs():
    try:
        from src.task.scheduler import UnifiedScheduler
        scheduler = UnifiedScheduler()
        return ok(scheduler.get_all_tasks())
    except Exception:
        return ok([])

@router.get("/api/tasks/status")
async def task_status():
    try:
        from src.task.scheduler import UnifiedScheduler
        scheduler = UnifiedScheduler()
        return ok({"features": scheduler.get_features_status()})
    except Exception:
        return ok({"features": {}})

@router.post("/api/tasks/auto-follow")
async def create_auto_follow(request: Request):
    if not state._driver or not state._driver.is_connected():
        return err(40000, "微信未连接")
    body = await request.json()
    from src.monitor.auto_follow import AutoFollowTask
    follow = AutoFollowTask(state._driver, state._ai_service)
    return follow.create_task(body)

@router.post("/api/tasks/auto-follow/batch")
async def create_batch_auto_follow(request: Request):
    if not state._driver or not state._driver.is_connected():
        return err(40000, "微信未连接")
    body = await request.json()
    from src.monitor.auto_follow import AutoFollowTask
    follow = AutoFollowTask(state._driver, state._ai_service)
    return follow.create_batch_tasks(body)

@router.get("/api/tasks/auto-follow")
async def list_auto_follow():
    from src.monitor.auto_follow import AutoFollowTask
    follow = AutoFollowTask(state._driver, state._ai_service)
    return follow.get_tasks_by_account("main")

@router.post("/api/tasks/auto-follow/cancel")
async def cancel_auto_follow(request: Request):
    body = await request.json()
    task_id = body.get("task_id", "")
    from src.monitor.auto_follow import AutoFollowTask
    follow = AutoFollowTask(state._driver, state._ai_service)
    return follow.cancel_task(task_id)

@router.post("/api/tasks/auto-follow/pause")
async def pause_auto_follow(request: Request):
    body = await request.json()
    task_id = body.get("task_id", "")
    from src.monitor.auto_follow import AutoFollowTask
    follow = AutoFollowTask(state._driver, state._ai_service)
    return follow.pause_task(task_id)

@router.post("/api/tasks/auto-follow/resume")
async def resume_auto_follow(request: Request):
    body = await request.json()
    task_id = body.get("task_id", "")
    from src.monitor.auto_follow import AutoFollowTask
    follow = AutoFollowTask(state._driver, state._ai_service)
    return follow.resume_task(task_id)

def _get_active_driver():
    try:
        from app.state import account_manager as am
        from src.crm.account_data import get_active_account
        active_wxid = get_active_account()
        if am:
            if active_wxid and active_wxid != "default":
                inst = am.get_instance_by_wxid(active_wxid)
                if inst and inst.driver:
                    return inst.driver
            if am.primary_driver:
                return am.primary_driver
            if am._instances:
                first_inst = next(iter(am._instances.values()), None)
                if first_inst and first_inst.driver:
                    return first_inst.driver
    except Exception:
        pass
    from . import state
    return state._driver


@router.post("/api/tasks/friend-request/toggle")
async def friend_request_toggle(request: Request):
    body = await request.json()
    action = body.get("action", "toggle")
    
    driver = _get_active_driver()
    if driver and not driver.is_connected():
        try:
            driver.connect()
        except Exception:
            pass

    if not driver or not driver.is_connected():
        from src.crm.account_data import get_active_account
        active_wxid = get_active_account()
        if not active_wxid or active_wxid == "default":
            return err(40000, "微信未连接")


        
    if action not in ("stop", "pause"):
        from src.utils.license_validator import LicenseValidator
        import asyncio
        loop = asyncio.get_running_loop()
        sub = await loop.run_in_executor(None, LicenseValidator.check_subscription)
        if sub.get("status") in ("trial_expired", "expired"):
            return err(40302, "体验版已到期！部分功能已受限，请选择版本升级。")

        features = await loop.run_in_executor(None, LicenseValidator.check_features)
        ai_limit = features.get("ai_daily_limit", 30)
        if ai_limit > 0:
            from src.utils.daily_counter import DailyCounter
            account_id = getattr(state._driver, 'bot_wxid', 'main') if state._driver else 'main'
            current_count = DailyCounter().get_count("auto_reply", account_id)
            if current_count >= ai_limit:
                return err(40303, f"今日 AI 额度（{ai_limit}次）已用完，无法开启，请升级套餐")

    # 获取当前激活微信账号实例的 monitor
    active_monitor = None
    try:
        from app.state import account_manager as am
        from src.crm.account_data import get_active_account
        active_wxid = get_active_account()
        if am and active_wxid:
            inst = am.get_instance_by_wxid(active_wxid)
            if inst:
                if inst.friend_request_monitor is None:
                    from src.monitor.friend_request_monitor import FriendRequestMonitor
                    inst.friend_request_monitor = FriendRequestMonitor(inst.driver, state._ai_service)
                active_monitor = inst.friend_request_monitor
    except Exception:
        pass

    if active_monitor is None:
        if state._friend_request_monitor is None:
            from src.monitor.friend_request_monitor import FriendRequestMonitor
            state._friend_request_monitor = FriendRequestMonitor(state._driver, state._ai_service)
        active_monitor = state._friend_request_monitor

    
    from .base_config import _load_configs, _save_configs
    configs = _load_configs()
    if "friend_request_settings" not in configs:
        configs["friend_request_settings"] = {}

    if action == "stop" or (active_monitor.is_running() and action == "toggle"):
        active_monitor.stop()
        # 同时关闭所有实例的好友自动通过监控以保持一致
        try:
            from app.state import account_manager as am
            if am:
                for inst in am._instances.values():
                    if inst.friend_request_monitor and inst.friend_request_monitor.is_running():
                        inst.friend_request_monitor.stop()
        except Exception:
            pass
        configs["friend_request_settings"]["auto_accept"] = False
        _save_configs(configs)
        return ok({"status": "stopped", "message": "已停止新朋友监控"})
    else:
        active_monitor.start()
        # 同时开启所有实例的好友自动通过监控以保持一致
        try:
            from app.state import account_manager as am
            if am:
                for inst in am._instances.values():
                    if inst.friend_request_monitor and not inst.friend_request_monitor.is_running():
                        inst.friend_request_monitor.start()
        except Exception:
            pass
        configs["friend_request_settings"]["auto_accept"] = True
        _save_configs(configs)
        return ok({"status": "running", "message": "已启动新朋友监控"})

@router.get("/api/tasks/friend-request/logs")
async def friend_request_logs():
    active_monitor = None
    try:
        from app.state import account_manager as am
        from src.crm.account_data import get_active_account
        active_wxid = get_active_account()
        if am and active_wxid:
            inst = am.get_instance_by_wxid(active_wxid)
            if inst:
                if inst.friend_request_monitor is None:
                    from src.monitor.friend_request_monitor import FriendRequestMonitor
                    inst.friend_request_monitor = FriendRequestMonitor(inst.driver, state._ai_service)
                active_monitor = inst.friend_request_monitor
    except Exception:
        pass

    if active_monitor is None:
        if state._friend_request_monitor is None:
            from src.monitor.friend_request_monitor import FriendRequestMonitor
            state._friend_request_monitor = FriendRequestMonitor(state._driver, state._ai_service)
        active_monitor = state._friend_request_monitor
    return active_monitor.get_logs()

@router.post("/api/tasks/mass-sending")
async def create_mass_send(request: Request):
    if not state._driver or not state._driver.is_connected():
        return err(40000, "微信未连接")
        
    from src.utils.license_validator import LicenseValidator
    import asyncio
    loop = asyncio.get_running_loop()
    features = await loop.run_in_executor(None, LicenseValidator.check_features)
    if not features.get("mass_messaging", False):
        return err(40301, "当前版本不支持智能群发，请升级套餐")
        
    body = await request.json()
    targets = body.get("targets", [])
    msg_type = body.get("msg_type", "text")
    content = body.get("content", "")
    files = body.get("files", [])

    media_urls = body.get("media_urls", "")
    if not media_urls and files:
        if isinstance(files, list):
            media_urls = ",".join(str(f) for f in files if f)
        elif isinstance(files, str):
            media_urls = files

    schedule_time = body.get("schedule_time", "")
    
    if not targets:
        return err(40000, "目标列表不可为空")
        
    if not content and not media_urls:
        return err(40000, "内容或多媒体文件不可为空")
        
    core = _get_mass_sending_core()
    task_ids = core.submit_task(targets, msg_type, content, media_urls=media_urls, schedule_time=schedule_time)
    return ok({"task_ids": task_ids})

@router.get("/api/tasks/mass-sending")
async def list_mass_send():
    core = _get_mass_sending_core()
    return core.get_all_tasks()

@router.post("/api/tasks/mass-sending/{task_id}/cancel")
async def cancel_mass_send(task_id: str):
    core = _get_mass_sending_core()
    success = core.cancel_task(task_id)
    return ok({"task_id": task_id}) if success else err(50000, "取消失败")

@router.post("/api/tasks/mass-sending/cancel-all")
async def cancel_all_mass_send():
    core = _get_mass_sending_core()
    count = core.cancel_all()
    return ok({"cancelled": count})

@router.post("/api/tasks/mass-sending/{task_id}/pause")
async def pause_mass_send(task_id: str):
    core = _get_mass_sending_core()
    success = core.pause_task(task_id)
    return ok({"task_id": task_id}) if success else err(50000, "暂停失败")

@router.post("/api/tasks/mass-sending/{task_id}/resume")
async def resume_mass_send(task_id: str):
    core = _get_mass_sending_core()
    success = core.resume_task(task_id)
    return ok({"task_id": task_id}) if success else err(50000, "恢复失败")

@router.post("/api/tasks/auto-follow/{task_id}/cancel")
async def cancel_auto_follow_by_id(task_id: str):
    return ok({"task_id": task_id})

@router.post("/api/tasks/auto-follow/{task_id}/pause")
async def pause_auto_follow_by_id(task_id: str):
    return ok({"task_id": task_id, "status": "paused"})

@router.post("/api/tasks/auto-follow/{task_id}/resume")
async def resume_auto_follow_by_id(task_id: str):
    return ok({"task_id": task_id, "status": "running"})

@router.post("/api/tasks/auto-follow/batch")
async def batch_auto_follow(request: Request):
    return ok({"created": 0})
