import os
import urllib.parse
import logging
import asyncio
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from app.state import account_manager, driver
from src.crm.account_data import get_account_data_dir, ACCOUNTS_DIR, get_active_account, make_avatar_url
from src.utils.response import err, ok
from src.utils.wechat_key_store import clean_wxid

router = APIRouter()
_log = logging.getLogger(__name__)

_DEFAULT_AVATAR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="#e2e8f0"/><circle cx="50" cy="40" r="16" fill="#94a3b8"/><path d="M22 80c0-15.5 12.5-28 28-28s28 12.5 28 28v2H22v-2z" fill="#94a3b8"/></svg>"""

SYS_NAME_MAP = {
    "文件传输助手": "filehelper",
    "公众号": "newsapp",
    "服务号": "brandsessionholder",
    "订阅号": "newsapp",
    "微信团队": "weixin",
    "新的朋友": "fmessage"
}

_KNOWN_SYSTEM_WXIDS = {
    "filehelper", "newsapp", "fmessage", "weixin", "brandsessionholder",
    "brandservicesessionholder", "notifymessage", "medianote", "floatbottle",
    "qqmail", "tmessage", "helper_entry", "weibo", "systemnotify",
    "notification_messages", "opencustomerservicemsg", "userexperience_alarm",
    "qmessage", "qqsync"
}

@router.get("/api/avatar/{wxid}")
async def get_avatar(wxid: str):
    wxid = urllib.parse.unquote(wxid)
    wxid = clean_wxid(wxid) or wxid
    
    normalized_wxid = wxid.strip().lower()
    if normalized_wxid in SYS_NAME_MAP:
        normalized_wxid = SYS_NAME_MAP[normalized_wxid]
        
    # 快速拦截：系统账号与公众号统一返回通用默认图标，免去昂贵的磁盘/数据库查找
    if normalized_wxid in _KNOWN_SYSTEM_WXIDS or normalized_wxid.startswith("gh_"):
        return Response(
            content=_DEFAULT_AVATAR_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=604800, immutable"}  # 缓存 7 天
        )

    avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
    if os.path.exists(avatar_path):
        return FileResponse(avatar_path, headers={"Cache-Control": "no-cache"})
        
    target_wxid = None
    try:
        from src.utils.contacts_cache.cache import ContactsCache
        cache = ContactsCache()
        
        active_id = get_active_account() or "main"
        account_ids = {active_id}
        try:
            for inst in account_manager._instances.values():
                if inst.wxid:
                    account_ids.add(inst.wxid)
        except Exception:
            pass
            
        candidate_wxids = []
        for acct in account_ids:
            for f in cache.get_friends(acct):
                if wxid in (f.get("wxid"), f.get("name"), f.get("nickname"), f.get("remark")):
                    c_wxid = f.get("wxid")
                    if c_wxid and c_wxid not in candidate_wxids:
                        candidate_wxids.append(c_wxid)
                    
            for g in cache.get_groups(acct):
                if wxid in (g.get("wxid"), g.get("name"), g.get("nickname"), g.get("remark")):
                    c_wxid = g.get("wxid")
                    if c_wxid and c_wxid not in candidate_wxids:
                        candidate_wxids.append(c_wxid)

        if candidate_wxids:
            if len(candidate_wxids) == 1:
                target_wxid = candidate_wxids[0]
            else:
                import app.state as app_state
                active_mapped = app_state.name_to_active_wxid.get(wxid)
                if active_mapped and active_mapped in candidate_wxids:
                    target_wxid = active_mapped
                
                if not target_wxid:
                    newest_mtime = 0.0
                    best_candidate = None
                    for c_wxid in candidate_wxids:
                        c_path = os.path.join(ACCOUNTS_DIR, f"{c_wxid}.png")
                        if os.path.exists(c_path):
                            try:
                                mtime = os.path.getmtime(c_path)
                                if mtime > newest_mtime:
                                    newest_mtime = mtime
                                    best_candidate = c_wxid
                            except Exception:
                                pass
                    target_wxid = best_candidate or candidate_wxids[0]

        if not target_wxid:
            try:
                from src.utils.wcdb_name_helper import find_wxid_from_wcdb
                for acct in account_ids:
                    db_wxid = find_wxid_from_wcdb(acct, wxid)
                    if db_wxid:
                        target_wxid = db_wxid
                        break
            except Exception:
                pass

        if not target_wxid:
            import re
            search_names = set(n.strip() for n in re.split(r'[、,，]', wxid) if n.strip())
            if len(search_names) >= 2:
                best_group_wxid = None
                best_match_count = 0
                for acct in account_ids:
                    for g in cache.get_groups(acct):
                        members = g.get("members", [])
                        if not members:
                            continue
                        match_count = 0
                        for m in members:
                            m_name = m.get("remark") or m.get("nickname") or m.get("wxid")
                            if m_name in search_names:
                                match_count += 1
                        if match_count >= 2 and match_count > best_match_count:
                            best_match_count = match_count
                            best_group_wxid = g.get("wxid")
                if best_group_wxid:
                    target_wxid = best_group_wxid
                    
        if target_wxid:
            resolved_path = os.path.join(ACCOUNTS_DIR, f"{target_wxid}.png")
            if os.path.exists(resolved_path):
                return FileResponse(resolved_path, headers={"Cache-Control": "no-cache"})
            
            # 若解析后的 wxid 是公众号/服务号或系统账号，但本地无 PNG 头像，则直接返回通用默认图标
            normalized_target = target_wxid.strip().lower()
            if normalized_target in _KNOWN_SYSTEM_WXIDS or normalized_target.startswith("gh_"):
                return Response(
                    content=_DEFAULT_AVATAR_SVG,
                    media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=604800, immutable"}
                )
    except Exception as e:
        _log.warning(f"[AvatarAPI] 匹配昵称/备注头像时出错: {e}")
        
    # 统一兜底：返回唯一的默认占位图标，防止 404 报错并直接进行长期缓存
    return Response(
        content=_DEFAULT_AVATAR_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=604800, immutable"}  # 统一缓存 7 天
    )

@router.post("/api/avatar/sync")
async def sync_avatar(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
        
    instance_id = body.get("instance_id")
    target_driver = driver
    
    target_inst = None
    if instance_id:
        if instance_id in account_manager._instances:
            target_inst = account_manager._instances[instance_id]
        elif isinstance(instance_id, str) and instance_id.isdigit() and int(instance_id) in account_manager._instances:
            target_inst = account_manager._instances[int(instance_id)]
        else:
            for inst in account_manager._instances.values():
                if inst.wxid == instance_id or (inst.driver and getattr(inst.driver, "_wxid", None) == instance_id):
                    target_inst = inst
                    break

    if target_inst and getattr(target_inst, "driver", None):
        target_driver = target_inst.driver
        if not target_driver.is_connected():
            try:
                hwnd = body.get("hwnd") or target_inst.hwnd
                if hwnd:
                    target_driver.connect_to_wechat(hwnd)
            except Exception:
                pass

    target_wxid = ""
    if instance_id:
        target_wxid = instance_id
    elif target_driver and getattr(target_driver, "_wxid", None):
        target_wxid = target_driver._wxid

    hex_key = get_persisted_wechat_key(target_wxid) if target_wxid else None
    if not hex_key:
        hex_key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
    if not hex_key:
        try:
            api_dir = os.path.dirname(os.path.abspath(__file__))
            product_dir = os.path.dirname(os.path.dirname(os.path.dirname(api_dir)))
            env_path = os.path.join(product_dir, '.env')
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip().startswith("WECHAT_4X_KEY_HEX="):
                            val = line.split("=", 1)[1].strip()
                            if len(val) == 64:
                                hex_key = val
                                break
        except Exception:
            pass

    if hex_key:
        try:
            from src.api.wcdb_api import _match_db_storage_by_key, _detect_db_path
            db_storage = _match_db_storage_by_key(hex_key)
            if not db_storage:
                fallback_path = _detect_db_path()
                if fallback_path:
                    db_storage = os.path.dirname(os.path.dirname(fallback_path))
            
            if db_storage:
                from src.wechat_4x.db_contact_syncer import sync_avatars_from_db
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, sync_avatars_from_db, db_storage, hex_key)
                
                target_wxid = target_driver._wxid if (target_driver and target_driver._wxid) else ""
                if not target_wxid:
                    target_wxid = get_active_account()

                if target_wxid and target_wxid != "default":
                    try:
                        from src.utils.instance_manager import InstanceManagerV2
                        mgr = InstanceManagerV2.get_instance()
                        avatar_url = make_avatar_url(target_wxid)
                        
                        for inst_id, inst_data in mgr.get_all_instances().items():
                            if (inst_id == instance_id or 
                                (target_driver and inst_data.get("window_handle") == target_driver.hwnd) or
                                (target_wxid and inst_data.get("wxid") == target_wxid)):
                                update_data = {"avatar": avatar_url}
                                if target_wxid:
                                    update_data["wxid"] = target_wxid
                                mgr.update_instance(inst_id, update_data)
                                break
                    except Exception as e_mgr:
                        _log.warning(f"更新 InstanceManagerV2 实例头像失败: {e_mgr}")
                
                return ok({"success": True, "message": "头像已通过高能通道快速同步"})
        except Exception as e_wcdb:
            _log.warning(f"高能通道头像提取异常，降级使用 UIA 流程: {e_wcdb}")

    if not target_driver or not target_driver.is_connected():
        return err(40000, "关联的微信窗口因最小化或系统原因处于非联机(幽灵)状态，请确保其窗口可见后重试")
    
    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus

    cmd = UICommand(
        wxid=target_driver._wxid or "",
        kind=UICommandKind.FETCH_AVATAR,
        payload={},
        priority=UICommandPriority.HIGH,
        timeout=120.0,
    )
    ui_bus.submit(cmd)

    loop = asyncio.get_running_loop()
    finished = await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 150.0)
    if finished.status == UICommandStatus.SUCCESS:
        result = finished.result
    else:
        result = {"success": False, "error": finished.error or "头像同步超时"}

    if result.get("success") and target_driver._wxid:
        try:
            from src.utils.instance_manager import InstanceManagerV2
            mgr = InstanceManagerV2.get_instance()
            avatar_url = make_avatar_url(target_driver._wxid)
            
            for inst_id, inst_data in mgr.get_all_instances().items():
                if (inst_id == instance_id or 
                    inst_data.get("window_handle") == target_driver.hwnd or
                    (target_driver._wxid and inst_data.get("wxid") == target_driver._wxid) or 
                    (target_driver._nickname and inst_data.get("nickname") == target_driver._nickname)):
                    
                    update_data = {"avatar": avatar_url}
                    if target_driver._nickname:
                        update_data["nickname"] = target_driver._nickname
                    if target_driver._wxid:
                        update_data["wxid"] = target_driver._wxid
                    mgr.update_instance(inst_id, update_data)
                    break
        except Exception:
            pass

    return result

def get_persisted_wechat_key(wxid: str) -> str | None:
    from src.utils.wechat_key_store import get_persisted_wechat_key as _store_get
    try:
        return _store_get(wxid)
    except Exception:
        return None
