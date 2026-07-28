"""组装 FastAPI 应用（路由、中间件、加密、静态资源）。"""
from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    try:
        _xm_core = Path(__file__).resolve().parents[4]
        _pkg_path = _xm_core / "packages" / "python"
        if _pkg_path.is_dir():
            p = str(_pkg_path)
            if p not in sys.path:
                sys.path.insert(0, p)
    except (IndexError, OSError):
        pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.lifespan import lifespan
from app.middleware import api_key_middleware
from app.proxy import cross_service_router
from app.routes_builtin import router as builtin_router
from app.static_errors import mount_frontend_and_error_handlers
from app import constants

from src.api import (
    add_friend_api,
    agent_api,
    ai_media_api,
    chat,
    chat_monitor_api,
    chat_stats_api,
    chat_export_api,
    config_api,
    crm_api,
    crm_customer_api,
    file_api,
    friend_api,
    friend_sync_api,
    friend_backup_api,
    friend_filter_api,
    instance_api,
    knowledge_file_api,
    license_api,
    manual_compose_api,
    direct_bg_api,
    canvas_compose_api,
    moment_api,
    moment_comment_api,
    multi_open_api,
    scheduler_api,
    script_api,
    sso_api,
    stats_api,
    system,
    sse_api,
    tag_api,
    task_api,
    auto_follow_api,
    ui_bus_api,
    openapi_api,
    keyword_reply_api,
    chat_knowledge_api,
    sales_market_api,
    fulfillment_api,
    wcdb_api,
)


def create_app() -> FastAPI:
    app = FastAPI(title="XM AI Bot", lifespan=lifespan)






    try:
        from xm_py_server.encryption import setup_encryption
    except ImportError:
        print("[警告] 加密中间件源码路径不可达，尝试搜索已安装版本...")
        try:
            from xm_py_server.encryption import setup_encryption
        except ImportError:
            print("[错误] 无法加载加密中间件，API 安全层可能失效！")

            def setup_encryption(*args, **kwargs):  # type: ignore[misc]
                pass

    # 跨服务代理请求必须跳过本地加密中间件（加密由目标服务自行负责）
    # 从 CROSS_SERVICE_MAP 动态生成，确保新增服务不会遗漏
    cross_service_skip = list(constants.CROSS_SERVICE_PREFIXES)

    setup_encryption(
        app,
        skip_paths=[
            "/api/health",
            "/api/screenshots/manifest",
            "/api/v1/chat/export",
            "/api/v1/chat/export/excel",
        ],
        skip_prefixes=[
            *cross_service_skip,
            "/api/xm-bot4/screenshots",
            "/api/screenshots",
            "/api/v1/apps",
            "/api/v1/sso/detect",
            "/api/v1/sso/save",
            "/api/v1/sso/session",
            "/api/v1/sso/remove",
        ],
    )

    app.middleware("http")(api_key_middleware)

    app.include_router(agent_api.router)
    app.include_router(chat.router)
    app.include_router(chat_stats_api.router)
    app.include_router(chat_export_api.router)
    app.include_router(chat_monitor_api.router)
    app.include_router(system.router)
    app.include_router(sse_api.router)
    app.include_router(sso_api.router)
    app.include_router(config_api.router)
    app.include_router(moment_api.router)
    app.include_router(moment_comment_api.router)
    from src.api import moment_generate_api, moment_screenshot_api
    app.include_router(moment_generate_api.router)
    app.include_router(moment_screenshot_api.router)
    app.include_router(manual_compose_api.router)
    app.include_router(direct_bg_api.router)
    app.include_router(canvas_compose_api.router)
    app.include_router(license_api.router)
    app.include_router(instance_api.router)
    from src.api.instance_settings_api import router as instance_settings_router
    app.include_router(instance_settings_router)
    app.include_router(multi_open_api.router)
    app.include_router(file_api.router)
    app.include_router(ai_media_api.router)
    app.include_router(friend_api.router)
    app.include_router(friend_sync_api.router)
    app.include_router(friend_backup_api.router)
    app.include_router(friend_filter_api.router)
    app.include_router(task_api.router)
    app.include_router(auto_follow_api.router)
    app.include_router(script_api.router)
    app.include_router(crm_api.router)
    from src.api.crm_account_api import router as crm_account_router
    app.include_router(crm_account_router)
    app.include_router(crm_customer_api.router)
    app.include_router(knowledge_file_api.router)

    app.include_router(add_friend_api.router)
    app.include_router(stats_api.router)
    app.include_router(tag_api.router)
    app.include_router(scheduler_api.router)
    app.include_router(ui_bus_api.router)
    from src.api import safety_api
    app.include_router(safety_api.router)
    from src.api import dashboard_api
    app.include_router(dashboard_api.router)
    app.include_router(openapi_api.router)
    app.include_router(openapi_api.settings_router)
    app.include_router(keyword_reply_api.router)
    app.include_router(chat_knowledge_api.router)
    app.include_router(sales_market_api.router)
    app.include_router(fulfillment_api.router)
    app.include_router(wcdb_api.router)
    from src.api import plugin_market_api
    app.include_router(plugin_market_api.router)

    app.include_router(cross_service_router)
    app.include_router(builtin_router)

    # ─── 初始化 xm-sentinel 崩溃上报 & 日志推送 ───
    try:
        from xm_py_server.sentinel import init_sentinel
        from app.paths import xm_bot4_splash_app_version
        init_sentinel(
            app_key="xm-bot4",
            version=xm_bot4_splash_app_version(),
        )
    except Exception as e:
        print(f"[警告] sentinel 初始化失败（不影响主功能）: {e}")

    # ─── 挂载截图静态资源目录 ───
    from app.paths import BACKEND_ROOT
    from app.static_errors import SafeStaticFiles
    import os
    screenshots_dir = os.path.join(str(BACKEND_ROOT), "assets", "screenshots")
    app.mount(
        "/api/xm-bot4/screenshots",
        SafeStaticFiles(directory=screenshots_dir, html=False, check_dir=False),
        name="screenshots_bot4"
    )
    app.mount(
        "/api/screenshots",
        SafeStaticFiles(directory=screenshots_dir, html=False, check_dir=False),
        name="screenshots_compat"
    )

    mount_frontend_and_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app
