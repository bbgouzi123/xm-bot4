import logging
from src.utils.response import ok, err, ok_msg

logger = logging.getLogger(__name__)

async def ensure_chat_view_impl(request, get_target_instance_fn, run_uia_fn):
    body = await request.json()
    bot_hwnd = body.get("bot_hwnd", 0)
    target_drv, _ = get_target_instance_fn(bot_hwnd)
    if not target_drv or not target_drv.is_connected():
        return err(40000, "微信未连接")

    try:
        from src.utils.stop_signal import stop_signal
        stop_signal.reset()
        def _uia_work():
            from src.utils.uia_task_runner import run_uia_task
            import time
            with run_uia_task("切换至微信聊天面板", priority=10):
                target_drv.SwitchToThisWindow()
                time.sleep(0.2)
                if target_drv._find_session_list(): return True
                chat_btn = target_drv._walk_find('ButtonControl', name="微信", max_depth=12) or target_drv._walk_find(None, name="微信", max_depth=15)
                if chat_btn:
                    from src.uia.retry import exists_with_timeout, smooth_click_at
                    if exists_with_timeout(chat_btn, 1):
                        smooth_click_at(chat_btn)
                        time.sleep(0.5)
                        return True
                return False
        if await run_uia_fn(_uia_work):
            return ok_msg("已自动切换至微信聊天面板")
        return err(50000, "未能找到微信导航图标，请手动切换")
    except Exception as e:
        logger.error(f"自动化切换聊天面板异常: {e}")
        return err(50000, f"自动化操作异常: {str(e)}")

async def leave_chat_page_impl():
    try:
        from src.utils.contacts_cache import contacts_cache
        from app.state import account_manager, monitor
        
        # 1. 遍历所有在线账号实例
        for hwnd, inst in account_manager._instances.items():
            account_id = inst.wxid if inst else "main"
            
            # 清除通讯录中的 is_takeover 状态
            friends = contacts_cache.get_friends(account_id)
            for f in friends:
                if f.get("is_takeover", False):
                    contacts_cache.update_friend(account_id, f.get("wxid"), is_takeover=False)
            
            # 清除对应 monitor 实例的 manual_interventions 避让
            if hasattr(inst, "monitor") and inst.monitor:
                inst.monitor._manual_interventions.clear()
                try:
                    partition = inst.monitor.get_account_partition(account_id)
                    if partition and hasattr(partition, "suspended_sessions"):
                        partition.suspended_sessions.clear()
                except Exception:
                    pass
                    
        # 2. 额外强制清除全局默认 monitor
        if monitor:
            monitor._manual_interventions.clear()
            try:
                partition = monitor.get_account_partition()
                if partition and hasattr(partition, "suspended_sessions"):
                    partition.suspended_sessions.clear()
            except Exception:
                pass
                
        logger.info("[API] 用户退出聊天接管页面，已自动重置清除所有人工接管与干预避让状态")
        return ok({"message": "已清除接管与干预避让状态"})
    except Exception as e:
        logger.error(f"清除接管状态失败: {e}")
        return err(50000, f"操作失败: {str(e)}")
