import time
from fastapi import APIRouter, Request
from app.state import account_manager
from src.crm.account_data import get_active_account
from src.utils.response import err, ok, ok_msg
from src.utils.websocket_manager import ws_manager

router = APIRouter()

@router.post("/api/manual-takeover/takeover")
async def manual_takeover(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    wxid = data.get("wxid")
    session_id = data.get("session_id")
    if not session_id:
        return err(40000, "参数缺少 session_id")
    
    if not wxid:
        wxid = get_active_account()
    
    inst = account_manager.get_instance_by_wxid(wxid)
    if not inst:
        return err(40000, f"未找到微信号为 {wxid} 的运行实例")
    
    if not hasattr(inst.monitor, "_human_takeover_sessions"):
        inst.monitor._human_takeover_sessions = set()
    inst.monitor._human_takeover_sessions.add(session_id)
    
    inst.monitor._manual_interventions[session_id] = time.time()
    
    try:
        await ws_manager.broadcast_json({
            "type": "manual_takeover_status",
            "data": {
                "wxid": wxid,
                "session_id": session_id,
                "takeover": True
            }
        })
    except Exception:
        pass
    
    return ok_msg(f"会话 {session_id} 已成功进入人工接管状态")


@router.post("/api/manual-takeover/resume")
async def manual_resume(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    wxid = data.get("wxid")
    session_id = data.get("session_id")
    if not session_id:
        return err(40000, "参数缺少 session_id")
    
    if not wxid:
        wxid = get_active_account()
        
    inst = account_manager.get_instance_by_wxid(wxid)
    if not inst:
        return err(40000, f"未找到微信号为 {wxid} 的运行实例")
        
    if hasattr(inst.monitor, "_human_takeover_sessions"):
        inst.monitor._human_takeover_sessions.discard(session_id)
    
    inst.monitor._manual_interventions.pop(session_id, None)
    
    try:
        await ws_manager.broadcast_json({
            "type": "manual_takeover_status",
            "data": {
                "wxid": wxid,
                "session_id": session_id,
                "takeover": False
            }
        })
    except Exception:
        pass
        
    return ok_msg(f"会话 {session_id} 已恢复自动回复")


@router.get("/api/manual-takeover/status")
async def get_takeover_status(wxid: str = None):
    if not wxid:
        wxid = get_active_account()
        
    inst = account_manager.get_instance_by_wxid(wxid)
    if not inst:
        return ok({"sessions": []})
        
    sessions = []
    if hasattr(inst.monitor, "_human_takeover_sessions"):
        sessions = list(inst.monitor._human_takeover_sessions)
    return ok({"sessions": sessions})
