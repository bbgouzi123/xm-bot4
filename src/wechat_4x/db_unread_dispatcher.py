import logging
from typing import Any

logger = logging.getLogger("DbUnreadDispatcher")

async def process_and_dispatch_unreads(
    current_unreads: dict,
    scanner,
    account_id: str,
    last_unread_counts: dict,
    last_active_summaries: dict,
):
    """统一处理获取到的会话未读列表并广播/注入回复队列"""
    from src.utils.contacts_cache import contacts_cache
    from src.utils.websocket_manager import ws_manager
    from src.wechat_4x.db_unread_syncer_helper import process_unread_session

    all_friends = contacts_cache.get_friends(account_id) or []
    all_groups = contacts_cache.get_groups(account_id) or []
    
    wxid_to_name = {}
    for f in all_friends:
        n = f.get('name') or f.get('remark') or f.get('nickname') or ''
        if f.get('wxid'): wxid_to_name[f['wxid']] = n
        if f.get('alias'): wxid_to_name[f['alias']] = n

    group_wxid_to_name = {}
    for g in all_groups:
        n = g.get('name') or ''
        if g.get('wxid'): group_wxid_to_name[g['wxid']] = n
        if g.get('alias'): group_wxid_to_name[g['alias']] = n

    try:
        reply_cfg, friend_excludes, group_excludes = scanner._prepare_reply_filters(account_id)
    except Exception as ex:
        logger.warning(f"[未读同步] 获取回复配置失败: {ex}")
        reply_cfg, friend_excludes, group_excludes = {}, [], []

    from src.api.config_api import _load_configs
    from src.api.instance_settings_api import load_instance_settings
    configs = _load_configs() or {}
    inst_settings = load_instance_settings(account_id) or {}
    auto_reply_enabled = configs.get("auto_reply_enabled", True) and inst_settings.get("auto_reply_enabled", True)

    active_username = None
    try:
        import app.state as app_state
        active_username = getattr(app_state, 'active_chat_wxid', None)
    except Exception:
        pass

    if auto_reply_enabled:
        for username, info in current_unreads.items():
            u_prev = last_unread_counts.get(username, 0)
            u_now = info.get("unread_count", 0)
            if u_now > 0 and u_prev == 0:
                is_group = username.endswith("@chatroom")
                name = group_wxid_to_name.get(username) if is_group else wxid_to_name.get(username)
                logger.info(f"[未读同步] 会话 '{name or username}' (wxid={username}) 未读数自愈重置")
                if hasattr(scanner, "db"):
                    scanner.db.delete_session_fingerprints(username)
                    if name:
                        scanner.db.delete_session_fingerprints(name)
                scanner._fingerprints.pop(username, None)
                if name:
                    scanner._fingerprints.pop(name, None)
                if hasattr(scanner, '_broadcasted_whitelist_fps'):
                    session_fps = getattr(scanner, '_session_broadcasted_fps', {}).pop(username, set())
                    for old_fp in session_fps:
                        scanner._broadcasted_whitelist_fps.discard(old_fp)

            process_unread_session(
                username=username,
                info=info,
                scanner=scanner,
                account_id=account_id,
                active_username=active_username,
                wxid_to_name=wxid_to_name,
                group_wxid_to_name=group_wxid_to_name,
                reply_cfg=reply_cfg,
                friend_excludes=friend_excludes,
                group_excludes=group_excludes,
                last_active_summaries=last_active_summaries,
                last_unread_counts=last_unread_counts,
            )

    if hasattr(ws_manager, "task_cache"):
        keys_to_remove = []
        for task_id, cached in list(ws_manager.task_cache.items()):
            if task_id.startswith("whitelist_"):
                friend_name = cached.get("data", {}).get("friend_name", "")
                is_still_unread = False
                for u_name, u_info in current_unreads.items():
                    u_disp = group_wxid_to_name.get(u_name) if u_name.endswith("@chatroom") else wxid_to_name.get(u_name)
                    if task_id == f"whitelist_{u_name}" or u_disp == friend_name or u_name == friend_name:
                        is_still_unread = True
                        break
                if not is_still_unread:
                    keys_to_remove.append((task_id, friend_name))
        
        for task_id, friend_name in keys_to_remove:
            ws_manager.task_cache.pop(task_id, None)
            await ws_manager.broadcast_task_update(
                task_id=task_id,
                task_type="自动回复",
                status="completed",
                progress=100,
                total=100,
                message="未读状态已消除",
                friend_name=friend_name
            )

    last_unread_counts.clear()
    for u, info in current_unreads.items():
        last_unread_counts[u] = info["unread_count"]
