from fastapi import Request
import logging
from src.utils.response import ok, err, ok_msg
from .state import router
from . import state

logger = logging.getLogger(__name__)

def _get_friend_manager():
    from src.friend.friend_manager import FriendManager
    if not hasattr(_get_friend_manager, '_instance'):
        _get_friend_manager._instance = FriendManager(driver=state._driver)
    _get_friend_manager._instance._driver = state._driver
    return _get_friend_manager._instance

@router.post("/api/friend/add")
async def add_friend(request: Request):
    if not state._driver or not state._driver.is_connected():
        return err(40000, "操作失败", {"message": "微信未连接"})

    body = await request.json()
    wxid = body.get("wxid", "").strip()
    if not wxid:
        return err(40000, "操作失败", {"message": "wxid 不能为空"})

    import asyncio
    loop = asyncio.get_event_loop()

    try:
        from src.orchestrator.ui_bus import (
            ui_bus,
            UICommand,
            UICommandKind,
            UICommandPriority,
            UICommandStatus,
        )
        from src.crm.account_data import get_active_account
        account_id = get_active_account() or ""
        cmd = UICommand(
            wxid=account_id,
            kind=UICommandKind.ADD_FRIEND,
            payload={
                "target": wxid,
                "remark": body.get("remark", ""),
                "tags": body.get("tags", ""),
                "verify_message": body.get("verifyMessage", ""),
            },
            priority=UICommandPriority.NORMAL,
            timeout=90.0,
        )
        ui_bus.submit(cmd)
        finished = await loop.run_in_executor(
            None, ui_bus.await_result, cmd.id, 120.0,
        )
        if finished.status == UICommandStatus.SUCCESS:
            return finished.result
        logger.warning(
            f"[加好友][UIBus] 失败回退: status={finished.status.value} err={finished.error}"
        )
    except Exception as e:
        logger.warning(f"[加好友][UIBus] 投递异常，回退旧路径: {e}")

    def do_add():
        mgr = _get_friend_manager()
        return mgr.add_single_friend(
            wxid=wxid,
            remark=body.get("remark", ""),
            tags=body.get("tags", ""),
            verify_message=body.get("verifyMessage", ""),
        )

    result = await loop.run_in_executor(None, do_add)
    return result

@router.get("/api/friend/remaining-count")
async def remaining_count():
    from src.utils.counter import DailyCounter
    counter = DailyCounter()
    remaining = counter.get_remaining("main", max_per_day=20)
    today = counter.get_today_count("main")
    return ok({"count": remaining, "today": today, "limit": 20})

@router.get("/api/friend/add-logs")
async def add_logs(limit: int = 50):
    mgr = _get_friend_manager()
    return ok(mgr.get_add_logs(limit=limit))

@router.post("/api/friend/import")
async def import_friends(request: Request):
    body = await request.json()
    friends = body.get("friends", [])
    if not friends:
        return err(40000, "名单为空")
    mgr = _get_friend_manager()
    return mgr.import_friends(friends)

@router.post("/api/friend/list/export")
async def export_friends():
    mgr = _get_friend_manager()
    data = mgr.get_friend_list(limit=9999)
    return ok({"data": data})

@router.post("/api/friend/sync-by-api")
async def sync_friends_by_api():
    return ok({"synced": 0})

@router.delete("/api/friend/list/{wxid}")
async def delete_friend(wxid: str):
    mgr = _get_friend_manager()
    result = mgr.delete_friend(wxid)
    return ok({"wxid": wxid}) if result else err(40400, "好友不存在")
