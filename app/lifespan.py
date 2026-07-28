"""Application lifespan (startup/shutdown)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.ai.factory import AIServiceFactory
from src.api import add_friend_api, chat, config_api, friend_api, moment_api, system, task_api
from src.utils.websocket_manager import ws_manager

from app import constants
import app.state as app_state
from app.state import account_manager, ai_service, driver, monitor

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    loop = asyncio.get_running_loop()
    app_state.main_loop = loop

    # === 优化：静默 Windows 上 asyncio ProactorEventLoop 特有的 WinError 10054 _call_connection_lost 异常 ===
    def silence_proactor_connection_lost(loop, context):
        exception = context.get("exception")
        message = context.get("message", "")
        if isinstance(exception, ConnectionResetError) or (exception and "[WinError 10054]" in str(exception)):
            return
        if "connection_lost" in message or "_call_connection_lost" in str(context.get("handle", "")):
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(silence_proactor_connection_lost)

    # 开启日志实时拦截与WS广播
    try:
        from src.utils.websocket_manager import ws_manager
        ws_manager.loop = asyncio.get_running_loop()
        from src.utils.stdout_logger import setup_stdout_logging
        setup_stdout_logging()
    except Exception as log_ex:
        print(f"[启动] ⚠️ 初始化实时日志拦截失败: {log_ex}")

    # 启动健康心跳定时刷新机制
    try:
        from src.utils.runtime_heartbeat import RuntimeHeartbeat
        RuntimeHeartbeat.start_heartbeat_daemon()
    except Exception as heartbeat_ex:
        print(f"[启动] ⚠️ 启动心跳定时守护失败: {heartbeat_ex}")

    # 启动屏幕右上角实时状态看板
    try:
        from src.utils.status_overlay import status_overlay
        status_overlay.start()
        status_overlay.update("未连接", "等待连接微信...")
    except Exception as overlay_ex:
        print(f"[启动] ⚠️ 启动屏幕右上角状态看板失败: {overlay_ex}")

    # === 安全地在主事件循环中启动客户 API 队列 Worker ===
    try:
        from src.api.customer_api.adapter_factory import start_queue_worker
        start_queue_worker()
    except Exception as e:
        print(f"[启动] ⚠️ 客户 API 队列 Worker 启动失败: {e}")

    # === 启动横幅 ===
    print("[启动] [BOT] xm-bot4 - 微信数字员工自动获客")

    from src.startup_state import startup_state
    import threading

    # 1. 立即标记 HTTP 就绪
    startup_state.ready = True
    startup_state.status = "基础服务已启动，正在后台初始化..."
    try:
        from src.utils.config_cache import config_cache
        config_cache.sync_in_progress = True
    except Exception:
        pass
    
    from app.lifespan_helper import background_initialization
    from app.periodic_tasks import periodic_tasks_loop

    # 2. 启动后台初始化线程
    threading.Thread(target=background_initialization, daemon=True).start()

    # 3. 启动定时同步后端快照和用量上报
    threading.Thread(target=periodic_tasks_loop, daemon=True).start()

    # 4. 启动微信白屏保活心跳守护（随机 3~10 分钟，空闲时点击任务栏图标预防白屏积累）
    try:
        from src.uia.retry.white_screen_guard import start_white_screen_guard
        start_white_screen_guard()
    except Exception as ws_ex:
        print(f"[启动] ⚠️ 启动白屏保活心跳守护失败: {ws_ex}")

    # 5. 启动微信自动更新弹窗拦截守护（每 15s 轮询，检测到"新版本"弹窗时自动点击"忽略本次更新"）
    try:
        from src.utils.wechat_update_guard import start_update_guard
        start_update_guard()
        print("[启动] ✅ 微信自动更新弹窗拦截守护已启动")
    except Exception as ug_ex:
        print(f"[启动] ⚠️ 启动微信更新弹窗拦截守护失败: {ug_ex}")

    yield

    # === 关闭阶段 ===
    print("[关闭] 停止所有监控与任务调度器...")
    try:
        from src.api.customer_api.adapter_factory import _worker_task
        if _worker_task and not _worker_task.done():
            _worker_task.cancel()
    except Exception: pass

    try:
        from src.task.scheduler import GlobalManagerRegistry
        GlobalManagerRegistry().shutdown_all()
    except Exception as e:
        print(f"[关闭] 停止注册管理器异常: {e}")

    try:
        from src.orchestrator.ui_bus import ui_bus
        ui_bus.stop(timeout=3.0)
        from src.orchestrator.alerting import get_alert_engine
        get_alert_engine().stop(timeout=2.0)
        from src.orchestrator.history_sink import get_command_history_sink
        get_command_history_sink().stop(timeout=3.0)
    except Exception: pass

    try:
        from src.crm.account_data import get_active_account
        from src.utils.cloud_sync import get_cloud_client
        cloud = get_cloud_client()
        cloud.stop_enterprise_command_poller()
        cloud.report_usage(get_active_account() or 'main')
        cloud.flush_pending_events(max_batches=20)
    except Exception: pass
        
    await account_manager.stop_all()
    if app_state.moment_scheduler: await app_state.moment_scheduler.stop()
    if app_state.moment_interaction_manager:
        try: app_state.moment_interaction_manager.stop()
        except Exception: pass
    try:
        from src.friend.webhook_pull import WebhookPullManager
        WebhookPullManager.get_instance().stop()
    except Exception: pass

    # 停止屏幕右上角实时状态看板
    try:
        from src.utils.status_overlay import status_overlay
        status_overlay.stop()
    except Exception: pass

    # 停止微信更新弹窗拦截守护
    try:
        from src.utils.wechat_update_guard import stop_update_guard
        stop_update_guard()
    except Exception: pass

    # 停止健康心跳定时刷新机制
    try:
        from src.utils.runtime_heartbeat import RuntimeHeartbeat
        RuntimeHeartbeat.stop_heartbeat_daemon()
    except Exception: pass
