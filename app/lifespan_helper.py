import sys
from pathlib import Path
import threading
import time as _time
import logging

from app.state import account_manager, ai_service, driver, monitor
import app.state as app_state
from src.startup_state import startup_state
from src.ai.factory import AIServiceFactory
from src.api import add_friend_api, chat, config_api, friend_api, moment_api, system, task_api

_last_coze_activate_date = None

def background_initialization():
    """在后台线程执行重型初始化任务，不阻塞 FastAPI 端口开放"""
    _time.sleep(2.0)
    try:
        # === 1. API 模块初始化 (引用注入) ===
        chat.init(driver, monitor)
        system.init(driver)
        config_api.init(driver, ai_service)
        moment_api.init(driver)
        friend_api.init(driver)
        task_api.init(driver)
        add_friend_api.init(driver)

        # === 2. 加载 AI 配置 ===
        startup_state.status = "加载 AI 配置..."
        try:
            from src.api.config_api import _load_configs
            configs = _load_configs()
            if configs:
                new_service = AIServiceFactory.create_from_full_config(configs)
                if new_service and new_service.is_configured():
                    ai_service.update_config(configs.get('external_api_settings', {}))
                    monitor.ai_service = new_service
                    config_api.init(driver, new_service)
                    if app_state.moment_interaction_manager:
                        app_state.moment_interaction_manager.ai_service = new_service
            
            # === 2.1 Coze 自动登录白嫖积分 ===
            if configs and configs.get("coze_auto_login") and configs.get("coze_cookie"):
                def _async_coze_login():
                    try:
                        import asyncio
                        from src.utils.coze_auth_helper import auto_activate_coze
                        res = asyncio.run(auto_activate_coze(configs.get("coze_cookie")))
                        if res and res.get("success"):
                            from datetime import datetime
                            global _last_coze_activate_date
                            _last_coze_activate_date = datetime.now().strftime("%Y-%m-%d")
                            print(f"[启动] [Coze 自动激活] 成功，已标记激活日期为: {_last_coze_activate_date}")
                    except Exception as e:
                        print(f"[启动] [Coze 自动激活] 失败: {e}")
                threading.Thread(target=_async_coze_login, name="async-coze-login", daemon=True).start()
        except Exception: pass

        # === 3. 同步后端与缓存同步 ===
        startup_state.status = "正在同步同步后端配置..."
        try:
            from src.utils.cloud_sync import get_cloud_client
            cloud = get_cloud_client()
            
            is_cloud_alive = cloud.initial_sync()
            cloud.start_background_sync()
            
            from src.utils.contacts_cache import contacts_cache
            from src.crm.account_data import get_active_account
            active_acc = get_active_account() or "main"
            contacts_cache._load_local_snapshot(active_acc)
            
            def _async_cloud_init_load():
                has_cloud_configs = False
                try:
                    from src.utils.config_cache import config_cache
                    if config_cache.load_from_cloud():
                        has_cloud_configs = True
                except Exception as ce:
                    print(f"[启动] ⚠️ 异步从同步后端加载配置缓存异常: {ce}")
                
                if has_cloud_configs:
                    try:
                        from src.api.config_api import _load_configs, _save_configs, _reload_ai_service
                        configs = _load_configs()
                        if configs:
                            _save_configs(configs)  # 写入本地 config_*.json
                            _reload_ai_service()    # 热加载最新的 AI 服务 (Coze 等)
                            print("[启动] ✅ 成功从云端恢复 AI 配置并已热重载")
                    except Exception as re:
                        print(f"[启动] ⚠️ 异步重载云端 AI 配置失败: {re}")
                
                if is_cloud_alive:
                    try:
                        contacts_cache.load_from_cloud(force=True)
                    except Exception as c_err:
                        print(f"[启动] ⚠️ 异步从同步后端加载通讯录异常: {c_err}")
            
            threading.Thread(
                target=_async_cloud_init_load,
                name="async-cloud-init-load",
                daemon=True
            ).start()

            if is_cloud_alive:
                try:
                    cloud.start_enterprise_command_poller(interval=5)
                except Exception as e:
                    print(f"[启动] ⚠️ 企业命令轮询启动失败: {e}")
            else:
                print("[启动] ⚠️ 同步后端服务初始不可达，已进入离线就绪模式，后台同步守护将持续尝试连接。")
        except Exception as e:
            print(f"[启动] ⚠️ 同步服务初始化异常: {e}")

        startup_state.status = "同步定价知识..."
        try:
            from src.utils.pricing_sync import start_background_sync as start_pricing_bg_sync
            start_pricing_bg_sync()
        except Exception: pass

        # === 5. UIBus handler 注册与启动 ===
        try:
            from app.ui_bus_handlers import register_ui_bus_handlers
            from src.orchestrator.ui_bus import ui_bus
            from src.orchestrator.history_sink import get_command_history_sink
            from src.orchestrator.alerting import get_alert_engine
            from src.utils.websocket_manager import ws_manager
            import asyncio

            register_ui_bus_handlers()

            def ui_bus_ws_broadcast(payload: dict):
                try:
                    from app.state import main_loop
                    if main_loop and main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), main_loop)
                    else:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(ws_manager.broadcast(payload))
                        else:
                            loop.run_until_complete(ws_manager.broadcast(payload))
                except Exception as ws_ex:
                    print(f"[UIBus WS] 广播事件失败: {ws_ex}")

            ui_bus.set_ws_broadcaster(ui_bus_ws_broadcast)

            history_sink = get_command_history_sink()
            history_sink.start()
            ui_bus.set_command_sink(history_sink)

            alert_engine = get_alert_engine()
            alert_engine.set_ws_broadcaster(ui_bus_ws_broadcast)

            def alert_stats_provider(window_minutes: int) -> dict:
                try:
                    from src.api.ui_bus_api import _aggregate_stats
                    from datetime import datetime, timedelta, timezone
                    since_dt = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
                    since_str = since_dt.isoformat()
                    items = history_sink.list_history(since=since_str, limit=1000)
                    stats = _aggregate_stats(items, source="local")
                    if "success_rate" in stats:
                        stats["success_rate"] = stats["success_rate"] * 100.0
                    if "by_account" in stats:
                        for acc in stats["by_account"]:
                            if "success_rate" in acc:
                                acc["success_rate"] = acc["success_rate"] * 100.0
                    return stats
                except Exception as st_ex:
                    print(f"[Alert Stats] 获取统计数据异常: {st_ex}")
                    return {}

            alert_engine.set_stats_provider(alert_stats_provider)

            def alert_self_notifier(evt):
                try:
                    from src.orchestrator.ui_bus import UICommand, UICommandKind, UICommandPriority
                    from src.crm.account_data import get_active_account
                    text = alert_engine.format_wechat_text(evt)
                    active_wxid = get_active_account() or "default"
                    cmd = UICommand(
                        wxid=active_wxid,
                        kind=UICommandKind.SEND_MESSAGE,
                        priority=UICommandPriority.URGENT,
                        payload={
                            "target": "filehelper",
                            "text": text,
                        }
                    )
                    ui_bus.submit(cmd)
                except Exception as sn_ex:
                    print(f"[Alert Self Notifier] 微信自通知失败: {sn_ex}")

            alert_engine.set_self_notifier(alert_self_notifier)
            alert_engine.start()

            print("[启动] ✅ UIBus & 告警引擎已成功启动，事件与日志落盘链路就绪")
        except Exception as e:
            print(f"[启动] ⚠️ UIBus / 告警引擎初始化失败: {e}")

        # === 6. 其他组件挂载 ===
        startup_state.status = "挂载核心组件..."
        try:
            from src.monitor.friend_request_monitor import FriendRequestMonitor
            from src.monitor.moment_scheduler import MomentScheduler
            from src.monitor.moment_interaction_manager import MomentInteractionManager
            from src.utils.license_validator import LicenseValidator
            from src.utils.hotkey_manager import init_global_hotkeys
            from src.utils.status_overlay import status_overlay
            import asyncio as _asyncio
            
            try:
                status_overlay.start()
                status_overlay.update("就绪", "等待系统指令...")
            except Exception as hud_err:
                print(f"[启动] ⚠️ 实时状态看板启动失败: {hud_err}")

            LicenseValidator.start_periodic_verify()
            init_global_hotkeys()

            try:
                from silent_narrator import SilentNarrator
                SilentNarrator.activate()
                print("[启动] ✅ SilentNarrator 已成功激活，无障碍屏幕阅读器模拟启动")
            except Exception as narrator_ex:
                print(f"[启动] ⚠️ 激活 SilentNarrator 失败: {narrator_ex}")
            
            from src.utils.stop_signal import stop_signal
            stop_signal.start_listener()
            
            app_state.moment_scheduler = MomentScheduler(driver, check_interval=60)
            _asyncio.run_coroutine_threadsafe(
                app_state.moment_scheduler.start(), app_state.main_loop
            )
            app_state.moment_interaction_manager = MomentInteractionManager(driver, monitor.ai_service)
            from src.friend.webhook_pull import WebhookPullManager
            WebhookPullManager.get_instance().start()
        except Exception: pass

        try:
            from src.task.auto_follow_daemon import ensure_daemon_started
            ensure_daemon_started()
        except Exception as e:
            print(f"[启动] 恢复 SDR 跟单守护失败: {e}")

        # === 8. 启动微信密钥后台自动监听截获线程 ===
        try:
            from src.wechat_4x.wechat_hook_controller import WeChatHookController
            controller = WeChatHookController()
            controller.start_auto_key_monitor()
            print("[启动] ✅ 微信数据库密钥后台静默监听线程已成功启动")
        except Exception as e_monitor:
            print(f"[启动] ⚠️ 启动微信密钥自动监听异常: {e_monitor}")

        startup_state.status = "就绪"
        startup_state.init_complete = True
        print("[启动] ✅ 后台初始化完成")

        # === 7. 恢复上次运行状态（闪退保护）===
        try:
            import asyncio as _asyncio
            from src.api.config_api import _load_configs
            configs = _load_configs()

            async def _auto_restore_bot():
                for _wait_round in range(7200):
                    # 【性能与时序优化】首次探测微信延迟 15 秒执行
                    # 确保系统刚打开时的黄金前 15 秒全部让渡给前端界面极速载入和渲染。
                    # 微信连接与 WCDB 初始化在主界面安全呈现后再于后台静默处理，避免启动卡顿。
                    if _wait_round == 0:
                        await _asyncio.sleep(15)
                    else:
                        await _asyncio.sleep(5)

                    # 🌟 [强力门控] 如果平台用户尚未登录（比如卡在登录页），则不做任何微信连接或自动恢复探测
                    from src.utils.auth_session import has_active_platform_session
                    if not has_active_platform_session():
                        continue

                    instances = list(account_manager._instances.values())
                    any_connected = any(inst.driver.is_connected() for inst in instances)
                    any_ready = any(inst.driver.is_connected() and inst.wxid for inst in instances)
                    
                    if not (any_connected and any_ready):
                        try:
                            from fastapi.concurrency import run_in_threadpool
                            if _wait_round == 0 or (_wait_round + 1) % 12 == 0:
                                print(f"[启动] 🔄 检测到尚未连接或就绪的微信实例，正在尝试自动探测与绑定 (轮询第 {_wait_round + 1} 次)...")
                            await run_in_threadpool(account_manager.discover_and_connect)
                            instances = list(account_manager._instances.values())
                            any_connected = any(inst.driver.is_connected() for inst in instances)
                            any_ready = any(inst.driver.is_connected() and inst.wxid for inst in instances)
                        except Exception as e_disc:
                            print(f"[启动] 自动恢复微信连接 - 自动发现窗口异常: {e_disc}")

                    if any_connected and any_ready:
                        from app.routes_builtin import _start_bot_core, _bot_automation_running
                        if configs.get("bot_auto_start", False) and not _bot_automation_running:
                            print("[启动] 🔄 检测到上次自动聊天为开启状态，正在自动恢复...")
                            try:
                                from src.utils.license_validator import LicenseValidator
                                from fastapi.concurrency import run_in_threadpool
                                sub = await run_in_threadpool(LicenseValidator.check_subscription)
                                features = await run_in_threadpool(LicenseValidator.check_features)
                                
                                if sub.get("status") in ("trial_expired", "expired") or not features.get("auto_chat", False):
                                    print("[启动] ⚠️ 自动恢复失败：许可证已过期，或当前套餐不支持自动回复功能")
                                    configs["bot_auto_start"] = False
                                    try:
                                        from src.api.config_api import _save_configs
                                        _save_configs(configs)
                                    except Exception:
                                        pass
                                else:
                                    ok = await _start_bot_core()
                                    if ok:
                                        print("[启动] ✅ 自动聊天已自动恢复（闪退保护）")
                                    else:
                                        print("[启动] ⚠️ 自动聊天恢复失败（AI 服务未配置）")
                            except Exception as e_check:
                                print(f"[启动] ⚠️ 自动恢复授权校验时抛出异常: {e_check}")
                        
                        auto_accept = configs.get("friend_request_settings", {}).get("auto_accept", False)
                        if auto_accept:
                            try:
                                from src.api.config_api import _reload_friend_request_monitor
                                _reload_friend_request_monitor(configs)
                                print("[启动] ✅ 好友自动通过已自动恢复")
                            except Exception as e:
                                print(f"[启动] ⚠️ 自动恢复好友自动通过监控异常: {e}")
                        
                        try:
                            from src.utils.moment_config import get_moment_settings
                            from src.crm.account_data import get_active_account
                            account_id = get_active_account()
                            moment_settings = get_moment_settings(account_id)
                            if moment_settings.get("enabled", False):
                                if app_state.moment_interaction_manager and not app_state.moment_interaction_manager._running:
                                    app_state.moment_interaction_manager.start()
                                    print("[启动] ✅ 朋友圈自动点赞评论巡回已自动恢复")
                        except Exception as e:
                            print(f"[启动] ⚠️ 自动恢复朋友圈点赞评论巡回异常: {e}")
                        
                        return
                print("[启动] ⚠️ 等待超时：10 小时内微信未连接，自动化状态未能自动恢复")

            _asyncio.run_coroutine_threadsafe(
                _auto_restore_bot(), app_state.main_loop
            )
        except Exception as _restore_err:
            print(f"[启动] ⚠️ 恢复上次运行状态失败: {_restore_err}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[启动] ❌ 后台初始化崩溃: {e}")
        startup_state.status = f"初始化失败: {e}"


