import logging
import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import app.state as app_state
from app.state import account_manager, driver, monitor
from src.utils.response import err, ok, ok_msg
from src.utils.websocket_manager import ws_manager

router = APIRouter()
_log = logging.getLogger(__name__)

@router.get("/api/multi-account/status")
async def multi_account_status():
    return ok(account_manager.get_status())

@router.post("/api/multi-account/refresh")
async def multi_account_refresh():
    results = account_manager.refresh()

    primary = account_manager.primary_instance
    if primary and primary.driver.is_connected():
        driver.hwnd = primary.driver.hwnd
        driver.root = primary.driver.root
        driver._nickname = primary.driver._nickname
        driver._wxid = primary.driver._wxid
        driver._connected = primary.driver._connected

        if primary.wxid:
            from src.crm.account_data import set_active_account
            set_active_account(primary.wxid, primary.nickname)
            
    try:
        from src.utils.instance_manager import InstanceManagerV2
        from src.crm.account_data import make_avatar_url
        mgr = InstanceManagerV2.get_instance()
        for hwnd, inst in account_manager._instances.items():
            if inst.nickname or inst.wxid:
                for inst_id, inst_data in mgr.get_all_instances().items():
                    if inst_data.get("window_handle") == hwnd:
                        update_data = {}
                        if inst.nickname:
                            update_data["nickname"] = inst.nickname
                        if inst.wxid:
                            update_data["wxid"] = inst.wxid
                            update_data["avatar"] = make_avatar_url(inst.wxid)
                        if update_data:
                            mgr.update_instance(inst_id, update_data)
                        break
    except Exception as e:
        print(f"[多开] 同步实例信息到前端异常: {e}")

    if app_state._bot_automation_running and monitor.ai_service and monitor.ai_service.is_configured():
        for inst in account_manager._instances.values():
            if not inst.monitor._running:
                inst.monitor.ai_service = monitor.ai_service
                await inst.monitor.start()
    return ok({"results": results, "status": account_manager.get_status()})

@router.post("/api/multi-account/start/{hwnd}")
async def multi_account_start(hwnd: int):
    success = await account_manager.start_instance(hwnd)
    return ok_msg("启动成功") if success else err(50000, "启动失败")

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            if await websocket.receive_text() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        _log.error(f"[WebSocket/500] 异常: {e}")
        ws_manager.disconnect(websocket)

@router.get("/api/xm-bot4/screenshots/manifest")
@router.get("/api/screenshots/manifest")
async def get_screenshots_manifest():
    from app.paths import BACKEND_ROOT
    import os

    screenshots_dir = os.path.join(str(BACKEND_ROOT), "assets", "screenshots")
    manifest = {}

    # 优先扫描本地 assets/screenshots 目录，生成文件列表
    if os.path.isdir(screenshots_dir):
        try:
            files = [
                f for f in os.listdir(screenshots_dir)
                if os.path.isfile(os.path.join(screenshots_dir, f)) and f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))
            ]
            files.sort()
            return {"bot4": files}
        except Exception as e:
            _log.warning(f"Failed to scan local screenshots directory: {e}")

    # 本地没有或扫描失败，fallback 到线上
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://xmcore.top/screenshots/manifest.json")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        _log.warning(f"Failed to fetch screenshots manifest: {e}")
    return {"bot4": []}
