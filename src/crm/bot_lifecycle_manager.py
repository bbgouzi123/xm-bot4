"""
微信状态变更后，各业务模块的生命周期重置与自动化任务的安全点火。
"""
import logging
import asyncio
import app.state as app_state
from app.state import monitor, account_manager

logger = logging.getLogger(__name__)


def notify_bot_data_scope_changed():
    """接管微信变更后：配置/通讯录/获客/排期等按 bot_wxid 从同步后端重拉，避免 SSO 账号维度串数据。"""
    # 1. 切换账号必须立即清空内存，防止串数据（此为数据隔离安全红线，在主线程同步执行）
    try:
        from src.utils.contacts_cache import contacts_cache
        contacts_cache.clear_memory_cache()
    except Exception as e:
        logger.warning(f"[多账号] 同步清空通讯录缓存异常: {e}")

    # 2. 耗时的云端重拉与监控器重启操作，移入后台守护线程，彻底释放 FastAPI 主事件循环，防止断网时请求整体超时卡死
    def _bg_sync_and_restart():
        logger.info("[多账号] 🚀 已拉起后台异步重载线程 (bg-notify-bot-data-scope-changed)")
        try:
            from src.utils.config_cache import config_cache
            config_cache.load_from_cloud(clear_before_load=True) # 切换账号必须清空并重拉配置，防止串号与旧缓存残留
        except Exception as e:
            logger.debug(f"[多账号] 后台重拉配置缓存跳过/失败: {e}")
        try:
            from src.utils.contacts_cache import contacts_cache
            contacts_cache.load_from_cloud()
        except Exception as e:
            logger.debug(f"[多账号] 后台重拉通讯录缓存跳过/失败: {e}")
        try:
            from src.friend.friend_queue import reload_from_cloud_for_active_bot
            reload_from_cloud_for_active_bot()
        except Exception as e:
            logger.debug(f"[多账号] 后台重置好友队列跳过/失败: {e}")
        try:
            from src.crm.moment_planner_service import reload_schedules_from_cloud_for_active_bot
            reload_schedules_from_cloud_for_active_bot()
        except Exception as e:
            logger.debug(f"[多账号] 后台重置朋友圈排期跳过/失败: {e}")
        # 账号就绪后，通过安全重启机制，彻底释放以脏 ID 运行 of 旧监控协程，并以真实微信号重新加载运行
        try:
            try:
                from src.utils.config_cache import config_cache
                saved = config_cache.get("monitor_auto_start") or "stopped"
                # 引入本地配置的双重决策机制：若本地配置中 bot_auto_start 为 True，则一律视同为需恢复开启运行
                from src.api.config_api import _load_configs
                local_configs = _load_configs()
                if local_configs.get("bot_auto_start", False):
                    saved = "running"
            except Exception:
                saved = "stopped"

            # 强制将从云端拉取到的最新 AI 配置覆盖注入到 AI 服务对象中，根除 cold-start 时因同步延迟导致 AI 服务未配置的毒瘤
            try:
                from src.api.config_api import _load_configs
                from src.ai.factory import AIServiceFactory
                configs = _load_configs()
                if configs:
                    new_service = AIServiceFactory.create_from_full_config(configs)
                    if new_service and new_service.is_configured():
                        app_state.ai_service = new_service
                        app_state.monitor.ai_service = new_service
                        if account_manager:
                            for inst in account_manager._instances.values():
                                if inst.monitor:
                                    inst.monitor.ai_service = new_service
                            logger.info("[多账号] 微信账号就绪，最新 AI 服务配置已成功加载并应用")
            except Exception as ai_ex:
                logger.warning(f"[多账号] 尝试在账号就绪后重新注入 AI 配置异常: {ai_ex}")

            async def _restart_monitor(mon_obj):
                try:
                    # 先彻底注销停止旧协程，确保释放旧账号绑定的状态
                    await mon_obj.stop()
                    if saved in ('running', 'paused'):
                        # start 内部会自动调用 reset_session_caches 清空会话指纹与未读历史
                        await mon_obj.start()
                        logger.info(f"[多账号] 成功重启并恢复监控器 (wxid={mon_obj.account_id})")
                    else:
                        # 即使自动化未开启，也应该在后台拉起 WCDB 只读连接以备其它模块（如联系人同步、未读感知）使用
                        asyncio.create_task(mon_obj._start_wcdb_engine())
                except Exception as restart_ex:
                    logger.error(f"[多账号] 重启监控器异常: {restart_ex}")

            def _safe_restart(mon_obj):
                if app_state.main_loop and app_state.main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(_restart_monitor(mon_obj), app_state.main_loop)
                else:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(_restart_monitor(mon_obj))
                        else:
                            loop.run_until_complete(_restart_monitor(mon_obj))
                    except RuntimeError:
                        new_loop = asyncio.new_event_loop()
                        new_loop.run_until_complete(_restart_monitor(mon_obj))

            from app.routes_builtin import _bot_automation_running, _start_bot_core
            if saved in ('running', 'paused') and not _bot_automation_running:
                logger.info("[多账号] 检测到账号已就绪且上次自动回复为开启，但全局核心尚未运行，触发自动恢复点火...")
                if app_state.main_loop and app_state.main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(_start_bot_core(), app_state.main_loop)
                else:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(_start_bot_core())
                        else:
                            loop.run_until_complete(_start_bot_core())
                    except RuntimeError:
                        new_loop = asyncio.new_event_loop()
                        new_loop.run_until_complete(_start_bot_core())
            else:
                # 全局 monitor 在多开架构中作为兼容对象，不应被启动运行以防产生双重回复。
                # 若它意外处于运行状态，则在此处予以安全停止。
                if monitor:
                    if monitor.is_running():
                        logger.info("[多账号] 检测到全局兼容层 monitor 处于运行状态，正在将其停止...")
                        if app_state.main_loop and app_state.main_loop.is_running():
                            asyncio.run_coroutine_threadsafe(monitor.stop(), app_state.main_loop)
                if account_manager:
                    for inst in account_manager._instances.values():
                        if inst.monitor:
                            _safe_restart(inst.monitor)
        except Exception as e:
            logger.warning(f"[多账号] 重置并安全重启监控器失败: {e}")
        # 清空授权状态缓存，使之能立刻反应最新绑定状态
        try:
            from src.utils.license_validator import LicenseValidator
            LicenseValidator.clear_subscription_cache()
        except Exception as e:
            logger.warning(f"[多账号] 清空订阅缓存失败: {e}")

    import threading
    threading.Thread(
        target=_bg_sync_and_restart,
        daemon=True,
        name="bg-notify-bot-data-scope-changed"
    ).start()

