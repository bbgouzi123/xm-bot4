from fastapi import Request, BackgroundTasks
import time
import logging
from src.utils.response import ok, err, ok_msg
from .state import router
from . import state

logger = logging.getLogger(__name__)

_sync_status = {
    "syncing": False,
    "total": 0,
    "new": 0,
    "errors": []
}

def _get_active_account_id_for_sync() -> str:
    try:
        from src.crm.account_data import get_active_account
        return get_active_account() or "default"
    except Exception:
        return "default"

def _get_resume_snapshot(account_id: str = "") -> dict:
    from src.utils.contact_sync_checkpoint import ContactSyncCheckpointStore
    store = ContactSyncCheckpointStore()
    target_account = account_id or _get_active_account_id_for_sync()
    records = store.list_records(target_account)
    snapshot = ContactSyncCheckpointStore.summarize(records)
    snapshot["account_id"] = target_account
    snapshot["updated_at"] = int(time.time())
    return snapshot

@router.get("/api/contacts")
async def list_contacts():
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    account_id = get_active_account() or "default"
    result = contacts_cache.get_friends(account_id)
    return ok(result)

@router.delete("/api/contacts/{wxid}")
@router.delete("/api/contacts")
async def delete_contact(
    wxid: str = None, 
    name: str = None, 
    category: str = "联系人"
):
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    account_id = get_active_account() or "default"
    
    removed = 0
    if wxid and wxid.strip():
        removed = contacts_cache.remove_friend(account_id, wxid)
        if removed > 0:
            try:
                from src.crm.profile_manager import ProfileManager
                pm = ProfileManager()
                pm.delete_profile(wxid)
            except Exception as e:
                logger.warning(f"Failed to clean up CRM profile for {wxid}: {e}")
    elif name and name.strip():
        # Retrieve target wxids to delete their profiles before deletion match
        try:
            friends = contacts_cache.get_friends(account_id)
            matched_wxids = []
            for f in friends:
                f_name = (f.get("name") or "").strip()
                f_display = (f.get("display_name") or "").strip()
                f_remark = (f.get("remark") or "").strip()
                f_cat = (f.get("category") or "联系人").strip() or "联系人"
                if (f_name == name.strip() or f_display == name.strip() or f_remark == name.strip()) and (f_cat == category or category in ("联系人", "群聊", "")):
                    w = f.get("wxid")
                    if w:
                        matched_wxids.append(w)
        except Exception:
            matched_wxids = []
            
        removed = contacts_cache.remove_friends_by_match(
            account_id, [], [{"name": name, "category": category}]
        )
        if removed > 0 and matched_wxids:
            try:
                from src.crm.profile_manager import ProfileManager
                pm = ProfileManager()
                for w in matched_wxids:
                    pm.delete_profile(w)
            except Exception as e:
                logger.warning(f"Failed to clean up matched CRM profiles: {e}")
    
    if removed > 0:
        return ok_msg(f"已删除联系人 {wxid or name}")
    return err(40400, f"未找到联系人 {wxid or name}")

@router.post("/api/contacts/batch-delete")
async def batch_delete_contacts(request: Request):
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    body = await request.json()
    wxids = body.get("wxids", [])
    items = body.get("items", [])
    if not isinstance(wxids, list) or len(wxids) == 0:
        wxids = []
    if not isinstance(items, list) or len(items) == 0:
        items = []
    if len(wxids) == 0 and len(items) == 0:
        return err(40000, "wxids/items 不能为空")

    account_id = get_active_account() or "default"
    
    # Pre-collect matched wxids for items to delete their CRM profiles
    matched_wxids = list(wxids)
    if items:
        try:
            friends = contacts_cache.get_friends(account_id)
            for item in items:
                t_name = (item.get("name") or "").strip()
                t_cat = (item.get("category") or "联系人").strip() or "联系人"
                for f in friends:
                    f_name = (f.get("name") or "").strip()
                    f_display = (f.get("display_name") or "").strip()
                    f_remark = (f.get("remark") or "").strip()
                    f_cat = (f.get("category") or "联系人").strip() or "联系人"
                    if (f_name == t_name or f_display == t_name or f_remark == t_name) and (f_cat == t_cat or t_cat in ("联系人", "群聊", "")):
                        w = f.get("wxid")
                        if w and w not in matched_wxids:
                            matched_wxids.append(w)
        except Exception:
            pass

    removed = contacts_cache.remove_friends_by_match(account_id, wxids, items)
    if removed > 0 and matched_wxids:
        try:
            from src.crm.profile_manager import ProfileManager
            pm = ProfileManager()
            for w in matched_wxids:
                if w and str(w).strip():
                    pm.delete_profile(str(w).strip())
        except Exception as e:
            logger.warning(f"Failed to clean up batch CRM profiles: {e}")
            
    return ok({
        "requested": len(wxids) + len(items),
        "removed": removed,
        "message": f"已删除 {removed} 个联系人"
    })

@router.post("/api/contacts/cleanup")
async def cleanup_contacts():
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    account_id = get_active_account() or "default"
    removed = contacts_cache.cleanup_synthetic_duplicates(account_id)
    return ok({"removed": removed, "message": f"已清理 {removed} 个重复/占位联系人"})

@router.get("/api/contacts/tags")
async def list_tags():
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    account_id = get_active_account() or "default"
    tags = contacts_cache.get_contact_tags(account_id)
    return ok(tags)

@router.get("/api/contacts/groups")
async def list_groups():
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    account_id = get_active_account() or "default"
    groups = contacts_cache.get_groups(account_id)
    return ok(groups)

@router.get("/api/contacts/group_tags")
async def list_group_tags():
    return ok([])

@router.post("/api/contact/sync")
async def sync_contacts(background_tasks: BackgroundTasks):
    if not state._driver or not state._driver.is_connected():
        return err(40000, "微信未连接")

    if _sync_status["syncing"]:
        return ok({"msg": "同步任务已经在运行中"})

    _sync_status["syncing"] = True
    _sync_status["total"] = 0
    _sync_status["new"] = 0
    _sync_status["errors"] = []

    import asyncio
    main_loop = asyncio.get_running_loop()

    def do_sync():
        try:
            from src.uia.contacts import ContactSync
            from src.utils.uia_lock import UIATaskPriority
            from src.utils.uia_task_runner import run_uia_task_func
            syncer = ContactSync(state._driver)

            def progress_cb(event, data):
                if event in ("progress", "completed", "contact_added"):
                    _sync_status["total"] = data.get("total", 0)
                    _sync_status["new"] = data.get("new", 0)

                # 1. 实时更新自动化物理锁的文本进度与详情
                try:
                    from src.uia.input_guard import uia_lock
                    if event == "contact_added":
                        contact = data.get("contact", {})
                        c_name = contact.get("display_name") or contact.get("name") or "未知"
                        uia_lock.update_status(f"正在同步: {c_name} (新增 {data.get('new', 0)} 人 / 共 {data.get('total', 0)} 人)")
                    elif event == "resumed":
                        uia_lock.update_status(f"正在续跑同步: 已同步 {data.get('total', 0)} 人")
                    elif event == "completed":
                        uia_lock.update_status(f"同步完成 (共同步 {data.get('total', 0)} 人)")
                except Exception as ex:
                    logger.debug(f"[Sync] 物理锁遮罩状态推送异常: {ex}")

                # 2. 广播 WebSocket 状态以便前端组件（如 Progress 卡片）渲染
                try:
                    from src.utils.websocket_manager import ws_manager
                    payload = dict(data)
                    payload["status"] = event
                    asyncio.run_coroutine_threadsafe(
                        ws_manager.broadcast({"type": "contact_progress", "data": payload}), main_loop
                    )
                except Exception as ex:
                    logger.debug(f"[Sync] 广播 WebSocket 状态异常: {ex}")
            
            def _run_sync_job():
                res_contacts_local = syncer.sync_all(
                    callback=progress_cb,
                    already_locked=True,
                )
                res_tags_local = syncer.sync_tags(already_locked=True)
                return res_contacts_local, res_tags_local

            res_contacts, res_tags = run_uia_task_func(
                _run_sync_job,
                task_name="通讯录同步面板任务",
                priority=UIATaskPriority.HIGH,
                timeout=180,
                pause_background_tasks=True,
                use_physical_lock=True,
            )
            
            _sync_status["errors"] = res_contacts.get("errors", []) + res_tags.get("errors", [])
        finally:
            _sync_status["syncing"] = False

    background_tasks.add_task(do_sync)
    return ok({"msg": "已启动后台同步"})

@router.get("/api/contact/sync/status")
async def sync_contacts_status():
    payload = dict(_sync_status)
    payload["resume"] = _get_resume_snapshot()
    return ok(payload)

@router.get("/api/contact/sync/checkpoints")
async def sync_contacts_checkpoints(include_all: bool = False):
    from src.utils.contact_sync_checkpoint import ContactSyncCheckpointStore
    store = ContactSyncCheckpointStore()
    account_id = _get_active_account_id_for_sync()
    data = store.list_records(None if include_all else account_id)
    snapshot = ContactSyncCheckpointStore.summarize(data)
    return ok({
        "account_id": account_id,
        "scope": "all" if include_all else "active_account",
        "items": data,
        "total": len(data),
        "resume": snapshot,
    })

@router.post("/api/contact/sync/checkpoints/clear")
async def clear_sync_checkpoints(request: Request):
    from src.utils.contact_sync_checkpoint import ContactSyncCheckpointStore
    body = await request.json()
    account_id = _get_active_account_id_for_sync()
    if not account_id:
        return err(40000, "未找到活跃账号，无法清理记录")

    store = ContactSyncCheckpointStore()
    cleared = store.clear_by_prefix(f"{account_id}::")
    logger.info(f"[Checkpoint] 用户手动触发清理: 账号={account_id}, 清理数量={cleared}")

    return ok({
        "account_id": account_id,
        "cleared": cleared,
        "scope": "account_all",
        "message": "已清除该账号下所有同步续跑记录"
    })
