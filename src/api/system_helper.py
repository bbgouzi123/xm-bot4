import os
import asyncio
from src.utils.response import ok, err
from src.utils.websocket_manager import ws_manager
from app.state import account_manager, driver as _driver

async def handle_extract_user_info(instance_id: str = None, force: bool = False):
    """执行微信用户信息（昵称/微信号/头像）提取操作，并同步实例状态"""
    if not _driver:
        return err(50000, "系统正在初始化，请稍候再试")

    # 🌟 优先执行一次微信窗口扫描同步，更新句柄并清理掉线死实例，以确保获取最新连接状态
    try:
        from fastapi.concurrency import run_in_threadpool
        from src.api.instance_helpers import do_scan_sync
        await run_in_threadpool(do_scan_sync)
    except Exception as e_scan:
        import logging
        logging.getLogger("system_helper").debug(f"[提取] 自动执行 do_scan_sync 异常: {e_scan}")

    # 优先使用指定 instance_id 绑定的 driver 实例，否则使用全局当前活跃 driver
    target_driver = _driver
    if instance_id:
        # 1. 尝试以 wxid 匹配
        matched_inst = next((inst for inst in account_manager._instances.values() if inst.wxid == instance_id or (inst.driver and getattr(inst.driver, "bot_wxid", None) == instance_id)), None)
        # 2. 如果没匹配到，去 InstanceManagerV2 查它的 window_handle，再到 account_manager._instances 里查
        if not matched_inst:
            try:
                from src.utils.instance_manager import InstanceManagerV2
                inst_data = InstanceManagerV2.get_instance().get_all_instances().get(instance_id)
                if inst_data and inst_data.get("window_handle"):
                    matched_inst = account_manager._instances.get(inst_data["window_handle"])
            except Exception:
                pass
        if matched_inst and matched_inst.driver:
            target_driver = matched_inst.driver

    if not target_driver.is_connected():
        return err(50000, "微信控制通道未就绪，请确保微信已启动且已扫码登录")

    # 如果已有昵称和微信号，说明元数据已就绪，跳过物理提取
    # 但如果指定了 force=True，则依然强制执行物理提取，以演示/确保 UIA 提取功能的可用性
    if not force and target_driver._nickname and target_driver._wxid:
        return ok({
            "nickname": target_driver._nickname,
            "wxid": target_driver._wxid,
            "skipped": True,
            "message": "信息已就绪，跳过物理提取"
        })

    from src.utils.stop_signal import stop_signal
    stop_signal.reset()

    loop = asyncio.get_event_loop()

    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
    # 如果指定了 instance_id，我们使用它作为 account_id 传给 UICommand，以便 UIBus 能够将其精准映射到指定分身窗口
    account_id = instance_id or target_driver._wxid
    if not account_id:
        from src.crm.account_data import get_active_account
        account_id = get_active_account() or 'main'

    cmd = UICommand(
        wxid=account_id, kind=UICommandKind.EXTRACT_USER_INFO,
        payload={"skip_avatar_if_exists": not force},
        priority=UICommandPriority.HIGH, timeout=120.0,
    )
    ui_bus.submit(cmd)

    finished = await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 150.0)
    if finished.status == UICommandStatus.SUCCESS:
        result = finished.result
    else:
        result = {"success": False, "error": finished.error or "提取超时"}

    # 同步信息到多开管理器 + InstanceManagerV2
    try:
        primary = account_manager.primary_instance
        if primary and primary.driver.hwnd == target_driver.hwnd:
            primary.nickname = target_driver._nickname or ""
            primary.wxid = target_driver._wxid or ""
            primary.driver._nickname = target_driver._nickname or ""
            primary.driver._wxid = target_driver._wxid or ""
            primary.driver.bot_wxid = target_driver._wxid or ""
            
        for inst in account_manager._instances.values():
            if inst.driver.hwnd == target_driver.hwnd:
                inst.nickname = target_driver._nickname or ""
                inst.wxid = target_driver._wxid or ""
                inst.driver._nickname = target_driver._nickname or ""
                inst.driver._wxid = target_driver._wxid or ""
                inst.driver.bot_wxid = target_driver._wxid or ""
                
        from src.utils.instance_manager import InstanceManagerV2
        from src.crm.account_data import make_avatar_url, set_active_account
        mgr = InstanceManagerV2.get_instance()
        synced = False
        for inst_id, inst_data in mgr.get_all_instances().items():
            if inst_data.get("window_handle") == target_driver.hwnd:
                update_data = {}
                if target_driver._nickname:
                    update_data["nickname"] = target_driver._nickname
                if target_driver._wxid:
                    update_data["wxid"] = target_driver._wxid
                    update_data["avatar"] = make_avatar_url(target_driver._wxid)
                if update_data:
                    mgr.update_instance(inst_id, update_data)
                synced = True
                break
        if target_driver._wxid or target_driver._nickname:
            set_active_account(target_driver._wxid or target_driver._nickname, target_driver._nickname)
            try:
                from src.utils.contacts_cache import contacts_cache
                loop.run_in_executor(None, contacts_cache.load_from_cloud, True)
            except Exception as sync_err:
                print(f"[同步] ⚠️ 微信实例就绪后从同步后端拉取通讯录异常: {sync_err}")
    except Exception as e:
        print(f"[同步] InstanceManagerV2 同步失败: {e}")

    try:
        status = "done" if result.get("success") else "error"
        msg = result.get("error", "") if not result.get("success") else "信息提取完成"
        asyncio.create_task(ws_manager.broadcast_json({
            "type": "uia_extract", "status": status,
            "data": result, "message": msg
        }))
    except Exception:
        pass

    if result.get("success"):
        return ok(result)
    return err(50000, result.get("error", "信息提取失败"))
