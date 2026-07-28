"""
系统/连接/实例 API
"""
import os
import threading
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Request
from src.utils.response import ok, err, ok_msg
from src.utils.websocket_manager import ws_manager

router = APIRouter()

_driver = None
# 微信连接状态锁，防止并发触发
_wechat_connect_lock = threading.Lock()
_wechat_connected = False


import ctypes
class _SysPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

# _global_do_extract is deprecated, logic moved to UIBus handler

def init(driver):
    global _driver
    _driver = driver


def _do_wechat_connect():
    global _wechat_connected
    try:
        from src.utils.wechat_connector import do_wechat_connect as exec_connect
        result = exec_connect()
        if result.get("success"):
            _wechat_connected = True
        return result
    finally:
        try:
            import gc
            gc.collect()
        except Exception:
            pass


@router.post("/api/wechat/connect")
async def wechat_connect():
    """前端登录后显式触发微信连接

    此 API 替代了原来在 lifespan 中自动执行的微信连接逻辑。
    只有用户登录进入主页后，前端才会调用此接口。
    """
    global _wechat_connected

    if _wechat_connected and _driver and _driver.is_connected():
        return ok({"already_connected": True, "nickname": _driver._nickname or ""})

    if not _wechat_connect_lock.acquire(blocking=False):
        return ok({"pending": True, "message": "微信正在连接中..."})

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do_wechat_connect)
        return ok(result)
    finally:
        _wechat_connect_lock.release()


@router.get("/api/public/uia-probe")
async def uia_probe():
    """全局探测器，用于 UI 分析和调试：监听全局长按"""
    import os, time, asyncio
    os.environ["QT_ACCESSIBILITY"] = "1"
    try:
        import ctypes
        ctypes.windll.kernel32.SetEnvironmentVariableW("QT_ACCESSIBILITY", "1")
        import comtypes
        comtypes.CoInitialize()
    except Exception: pass
    
    result_lines = []
    try:
        import uiautomation as uia
        import ctypes
        POINT = _SysPoint
            
        print("[PROBE] 等待鼠标左键长按 3 秒...")
        start_time = time.time()
        timeout = 15.0
        press_start = 0
        is_pressed = False
        target_control = None
        
        while time.time() - start_time < timeout:
            state = ctypes.windll.user32.GetAsyncKeyState(0x01)
            if (state & 0x8000) != 0:
                if not is_pressed:
                    is_pressed = True
                    press_start = time.time()
                elif time.time() - press_start >= 3.0:
                    pt = POINT()
                    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                    target_control = uia.ControlFromPoint(pt.x, pt.y)
                    break
            else:
                is_pressed = False
            await asyncio.sleep(0.05)
            
        if not target_control:
            return ok({"dump": "探测超时，未检测到长按。"})
            
        result_lines.append(f"[PROBE] 坐标 ({pt.x}, {pt.y}) 探测成功！")
        result_lines.append(f"元素名: '{target_control.Name}' 类名: {target_control.ClassName} 类型: {target_control.ControlTypeName}")
        parent = target_control.GetParentControl()
        if parent:
            result_lines.append(f"父元素名: '{parent.Name}' 类名: {parent.ClassName} 类型: {parent.ControlTypeName}")
                
    except Exception as e:
        result_lines.append(f"执行异常: {str(e)}")
    finally:
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        
    return ok({"dump": "\n".join(result_lines)})

@router.get("/api/health")
async def health_check():
    """健康检查：前端用来判断后端是否完全就绪（lifespan 初始化完成）"""
    from src.startup_state import startup_state
    return {
        "ready": startup_state.ready,
        "init_complete": startup_state.init_complete,
        "status": startup_state.status
    }


@router.get("/api/user/current")
async def current_user():
    if _driver:
        return ok(_driver.get_current_user())
    return ok({"nickname": "未连接"})


class ExtractUserInfoRequest(BaseModel):
    instance_id: Optional[str] = None
    force: Optional[bool] = False


@router.post("/api/user/extract-info")
async def extract_user_info(req: Optional[ExtractUserInfoRequest] = None):
    """显式触发微信用户信息提取（昵称+微信号+头像）"""
    from src.api.system_helper import handle_extract_user_info
    instance_id = req.instance_id if req else None
    force = req.force if req else False
    return await handle_extract_user_info(instance_id, force=force)


@router.get("/api/user/platform")
async def platform_user(request: Request):
    """获取 XM-User 平台用户信息（需 JWT 认证）"""
    xm_user = getattr(request.state, "xm_user", None)
    if xm_user:
        return ok({
            "authenticated": True,
            "user_id": xm_user.user_id,
            "tenant_id": xm_user.tenant_id,
            "app_id": xm_user.app_id,
            "role": xm_user.role,
        })
    return ok({"authenticated": False, "user_id": None})


@router.get("/api/connection/status")
async def connection_status():
    connected = _driver.is_connected() if _driver else False
    return ok({"connected": connected, "status": "connected" if connected else "disconnected"})


@router.post("/api/reconnect")
async def reconnect():
    return ok_msg("操作成功") if _driver and _driver.connect() else err(50000, "操作失败")



@router.post("/api/init/multi")
async def init_multi():
    if _driver:
        _driver.connect()
    return ok({"instances": 1})


@router.post("/api/system/wechat41/auto_config")
async def auto_config():
    return ok_msg("操作成功")


@router.post("/api/system/narrator/stop")
async def stop_narrator():
    return ok_msg("操作成功")


@router.post("/api/system/settings/voice-to-text/enable")
async def enable_voice_to_text():
    """自动化打开微信设置，开启语音自动转文字开关"""
    if not _driver:
        return {"success": False, "reason": "WeChatDriver 未初始化"}
    import asyncio
    from src.utils.stop_signal import stop_signal
    stop_signal.reset()
    loop = asyncio.get_event_loop()
    
    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
    from src.crm.account_data import get_active_account
    account_id = get_active_account() or 'main'

    cmd = UICommand(
        wxid=account_id, kind=UICommandKind.ENABLE_VOICE_TO_TEXT,
        payload={},
        priority=UICommandPriority.HIGH, timeout=120.0,
    )
    ui_bus.submit(cmd)

    finished = await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 150.0)
    if finished.status == UICommandStatus.SUCCESS:
        return finished.result
    else:
        return {"success": False, "reason": finished.error or "操作超时"}


@router.get("/api/system/local-ips")
async def get_system_local_ips():
    """获取本机的局域网 IP 列表"""
    import socket
    ips = []
    try:
        # 1. 尝试通过 UDP socket 探测（最准）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and primary_ip != "127.0.0.1":
            ips.append(primary_ip)
    except Exception:
        pass

    try:
        # 2. 备用方式获取所有网卡 IP
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip != "127.0.0.1" and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    # 如果还是空的，加个 127.0.0.1 兜底
    if not ips:
        ips.append("127.0.0.1")

    return ok(ips)


@router.get("/api/system/status-overlay")
async def get_status_overlay():
    """获取屏幕右上角实时状态看板启用状态"""
    from src.utils.status_overlay import status_overlay
    return ok({"enabled": status_overlay.hwnd is not None})


@router.post("/api/system/status-overlay/toggle")
async def toggle_status_overlay(enabled: bool):
    """开启或关闭屏幕右上角实时状态看板"""
    from src.utils.status_overlay import status_overlay
    if enabled:
        status_overlay.start()
        status_overlay.update("就绪", "等待系统指令...")
    else:
        status_overlay.stop()
    return ok({"enabled": status_overlay.hwnd is not None})


@router.get("/api/system/diag_driver")
async def diag_driver():
    global _driver
    from app.state import account_manager
    instances_info = []
    for hwnd, inst in account_manager._instances.items():
        instances_info.append({
            "hwnd": hwnd,
            "nickname": inst.nickname,
            "wxid": inst.wxid,
            "connected": inst.driver.is_connected() if inst.driver else False,
            "drv_connected": inst.driver._connected if inst.driver else False,
            "drv_hwnd": inst.driver.hwnd if inst.driver else None,
        })
    
    return ok({
        "driver": {
            "connected": _driver.is_connected() if _driver else False,
            "drv_connected": _driver._connected if _driver else False,
            "hwnd": _driver.hwnd if _driver else None,
            "nickname": _driver._nickname if _driver else None,
            "wxid": _driver._wxid if _driver else None,
        },
        "instances": instances_info
    })




