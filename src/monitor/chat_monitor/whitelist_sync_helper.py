import logging
import asyncio
import os
import time
from src.utils.contacts_cache import contacts_cache
from .message_scanner import check_friend_in_list, check_group_in_list

logger = logging.getLogger(__name__)

async def try_sync_group_whitelist(engine, name: str, clean_name: str, group_list: list, account_id: str) -> bool:
    """群聊白名单未命中时，尝试进行实时数据库同步校验，防止新加入群同步延迟"""
    is_valid_wxid = account_id and account_id != "default" and not account_id.startswith("wx_")
    now = time.time()
    last_sync = getattr(engine, "_last_global_whitelist_sync_time", 0.0)
    
    if not is_valid_wxid or (now - last_sync <= 300.0):
        return False

    engine._last_global_whitelist_sync_time = now
    logger.info(f"[前置拦截·白名单热修复] 群聊 '{name}' 未命中白名单，且符合全局同步冷却，触发实时同步校验...")
    
    try:
        from src.wechat_4x.db_contact_syncer import sync_contacts_from_db
        from src.wechat_4x.db_match_helper import auto_detect_db_path
        from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor

        db_path = ""
        hex_key = ""
        wcdb_monitor = getattr(engine, "_wcdb_session_monitor", None)
        if wcdb_monitor:
            db_path = getattr(wcdb_monitor, "_db_path", "")
            hex_key = getattr(wcdb_monitor, "_hex_key", "")

        if not hex_key:
            from src.utils.wechat_key_store import get_persisted_wechat_key
            hex_key = get_persisted_wechat_key(account_id) or os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
        if not hex_key:
            hex_key = get_wcdb_key_extractor().get_key(timeout_s=2.0) or ""
        if not db_path and hex_key:
            db_path = os.environ.get("WCDB_SESSION_DB_PATH", "") or auto_detect_db_path(hex_key, account_id) or ""

        if db_path and hex_key:
            db_storage_dir = os.path.dirname(os.path.dirname(db_path))
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, sync_contacts_from_db, db_storage_dir, hex_key, account_id)

            all_groups = contacts_cache.get_groups(account_id)
            g_wxid = ""
            for g in all_groups:
                g_w = (g.get("wxid") or "").strip()
                g_n = (g.get("name") or "").strip()
                if g_w == name.strip() or g_w == clean_name:
                    g_wxid = g_w
                    break
                if g_n in (name.strip(), clean_name):
                    g_wxid = g_w
                    break
            
            return check_group_in_list(name, g_wxid, group_list, account_id=account_id) or check_group_in_list(clean_name, g_wxid, group_list, account_id=account_id)
    except Exception as e_sync:
        logger.error(f"[前置拦截·白名单热修复] 触发同步异常: {e_sync}")
        
    return False

async def try_sync_friend_whitelist(engine, name: str, friend_list: list, account_id: str) -> bool:
    """好友白名单未命中时，尝试进行实时数据库同步校验，防止新好友同步延迟"""
    is_valid_wxid = account_id and account_id != "default" and not account_id.startswith("wx_")
    now = time.time()
    last_sync = getattr(engine, "_last_global_whitelist_sync_time", 0.0)
    
    if not is_valid_wxid or (now - last_sync <= 300.0):
        return False

    engine._last_global_whitelist_sync_time = now
    logger.info(f"[前置拦截·白名单热修复] 好友 '{name}' 未命中白名单，且符合全局同步冷却，触发实时同步校验...")
    
    try:
        from src.wechat_4x.db_contact_syncer import sync_contacts_from_db
        from src.wechat_4x.db_match_helper import auto_detect_db_path
        from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor

        db_path = ""
        hex_key = ""
        if not hex_key:
            from src.utils.wechat_key_store import get_persisted_wechat_key
            hex_key = get_persisted_wechat_key(account_id) or os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
        if not hex_key:
            hex_key = get_wcdb_key_extractor().get_key(timeout_s=2.0) or ""
        if not db_path and hex_key:
            db_path = os.environ.get("WCDB_SESSION_DB_PATH", "") or auto_detect_db_path(hex_key, account_id) or ""

        if db_path and hex_key:
            db_storage_dir = os.path.dirname(os.path.dirname(db_path))
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, sync_contacts_from_db, db_storage_dir, hex_key, account_id)

            all_friends = contacts_cache.get_friends(account_id)
            f_wxid = next(
                ((f.get('wxid') or '').strip() for f in all_friends
                 if (f.get('wxid') or '').strip() == name.strip()
                 or (f.get('name') or '').strip() == name.strip()
                 or (f.get('remark') or '').strip() == name.strip()),
                ""
            )
            
            return check_friend_in_list(name, f_wxid, friend_list, account_id=account_id)
    except Exception as e_sync:
        logger.error(f"[前置拦截·白名单热修复] 触发同步异常: {e_sync}")
        
    return False
