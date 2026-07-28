import logging
import re
import os
import json
from typing import Optional, Any

logger = logging.getLogger(__name__)

def resolve_mention_sender_wcdb(
    name: str, account_id: str,
    nicknames_to_check: list[str], respond_to_all: bool, message: str
) -> Optional[dict]:
    """
    尝试从 WCDB 数据库中解析获取被 @ 消息的具体发送人昵称。
    """
    sender_name = ""
    is_wcdb_online = False
    monitor = None
    try:
        # 1. 优先从运行状态机中提取当前主会话监听器
        session_monitor = None
        try:
            from app import state
            if hasattr(state, "monitor") and state.monitor:
                session_monitor = getattr(state.monitor, "_wcdb_session_monitor", None)
        except Exception:
            pass
            
        if session_monitor and session_monitor.is_active():
            if getattr(session_monitor, "_is_native_dll_active", False):
                monitor = getattr(session_monitor, "_monitor", None)
            else:
                monitor = getattr(session_monitor, "_msg_fallback_monitor", None)
            if monitor:
                is_wcdb_online = True

        # 2. 回退到 wcdb_monitor 全局实例扫描
        if not is_wcdb_online:
            from src.wechat_4x.wcdb_monitor import get_wcdb_monitor, _monitor_instances
            monitor = get_wcdb_monitor(account_id or "default")
            if not (monitor and monitor.is_active()):
                for m in _monitor_instances.values():
                    if m and m.is_active():
                        monitor = m
                        break
            if monitor and monitor.is_active():
                is_wcdb_online = True
    except Exception as e_wcdb_get:
        logger.debug(f"[Mention] 获取 WCDB 消息提取器异常: {e_wcdb_get}")


    if is_wcdb_online and monitor:
        try:
            username_val = name
            if not username_val.endswith("@chatroom"):
                from src.utils.contacts_cache import contacts_cache
                groups = contacts_cache.get_groups(account_id) or []
                for g in groups:
                    if g.get("name") == name:
                        username_val = g.get("wxid") or name
                        break
                        
            db_msgs = monitor.get_latest_messages(username_val, limit=15)
            for db_m in db_msgs:
                db_content = db_m["content"].strip()
                m_sender = re.match(r"^([a-zA-Z0-9_\-]+):\s*\n(.*)$", db_content, re.DOTALL)
                if m_sender:
                    sender_wxid = m_sender.group(1)
                    actual_body = m_sender.group(2).strip()
                    
                    is_match_self = False
                    for n in nicknames_to_check:
                        if n and re.search(rf'@[\s\u2005]*{re.escape(n)}', actual_body, re.IGNORECASE):
                            is_match_self = True
                            break
                    
                    is_match_all = False
                    if not is_match_self and respond_to_all:
                        for all_tag in ("所有人", "all", "All"):
                            if re.search(rf'@[\s\u2005]*{re.escape(all_tag)}', actual_body, re.IGNORECASE):
                                is_match_all = True
                                break
                                
                    if is_match_self or is_match_all:
                        # 🌟 核心防串扰：验证数据库里该条被 @ 消息的内容与当前正在处理的消息是否一致
                        def _normalize_msg(s: str) -> str:
                            cleaned = re.sub(r'@[\s\u2005\xa0]*[^\s\u2005\xa0]+', '', s)
                            cleaned = re.sub(r'[\s\u2005\xa0\u200b。，,！？!?\n\r\-:_]', '', cleaned)
                            return cleaned.strip().lower()

                        norm_db = _normalize_msg(actual_body)
                        norm_incoming = _normalize_msg(message)
                        
                        content_matched = norm_db == norm_incoming or (norm_incoming and norm_incoming in norm_db) or (norm_db and norm_db in norm_incoming)
                        if not content_matched:
                            continue
                            
                    if is_match_self:
                        from src.utils.contacts_cache import contacts_cache
                        sender_disp = ""
                        # 1. 优先从群成员缓存中检索（支持群聊中的非好友）
                        group_members = contacts_cache.get_group_members(account_id, name) or []
                        for m in group_members:
                            if m.get("wxid") == sender_wxid:
                                sender_disp = m.get("display_name") or m.get("nickname") or ""
                                break

                        # 2. 降级：从好友列表缓存检索
                        if not sender_disp:
                            def find_in_list(lst):
                                for item in lst:
                                    if item.get("wxid") == sender_wxid:
                                        return item.get("name") or item.get("remark") or item.get("nickname") or ""
                                return ""
                            sender_disp = find_in_list(contacts_cache.get_friends(account_id) or [])
                            
                            # 3. 再次降级：从本地物理文件 contacts.json 检索
                            if not sender_disp:
                                from src.crm.account_data import get_contacts_path
                                local_path = get_contacts_path(account_id)
                                if local_path and os.path.exists(local_path):
                                    try:
                                        with open(local_path, "r", encoding="utf-8") as f_contacts:
                                            data = json.load(f_contacts)
                                            if isinstance(data, list):
                                                sender_disp = find_in_list(data)
                                    except Exception:
                                        pass
                                    
                        if sender_disp:
                            sender_name = sender_disp
                            logger.info(f"[Mention] Precise sender resolved via WCDB database parsing: '{sender_name}'")
                            break
                    elif is_match_all:
                        logger.info("[Mention] Matched '@all' announcement via WCDB database parsing.")
                        return {"sender_name": "", "message": message, "is_at_all": True}
        except Exception as db_err:
            logger.debug(f"[Mention] WCDB database resolution error: {db_err}")

    if sender_name:
        return {"sender_name": sender_name, "message": message, "is_at_all": False}
    return None

def extract_target_bubble(nodes, nicknames_to_check: list[str], respond_to_all: bool, name: str, message: str = "") -> tuple:
    from src.utils.safe_uia import safe_get_name, safe_class_name
    from src.uia.message_direction import detect_is_self
    
    def _normalize_msg(s: str) -> str:
        cleaned = re.sub(r'@[\s\u2005\xa0]*[^\s\u2005\xa0]+', '', s)
        cleaned = re.sub(r'[\s\u2005\xa0\u200b。，,！？!?\n\r\-:_]', '', cleaned)
        return cleaned.strip().lower()

    norm_incoming = _normalize_msg(message) if message else ""

    # 1. Look for @self first
    for item in reversed(nodes):
        c_name = safe_get_name(item).strip()
        if not c_name or "ChatTextItemView" not in safe_class_name(item):
            continue
        if detect_is_self(item, session_name=name):
            continue
        
        last_msg_clean = c_name.replace('\u2005', ' ').replace('\u200b', '').strip()
        is_at_self = False
        for n in nicknames_to_check:
            if n and (re.search(rf'@[\s\u2005]*{re.escape(n)}', last_msg_clean, re.IGNORECASE) or re.search(rf'@[\s\u2005]*{re.escape(n)}', c_name, re.IGNORECASE)):
                is_at_self = True
                break
        
        if is_at_self:
            if norm_incoming:
                norm_db = _normalize_msg(c_name)
                content_matched = norm_db == norm_incoming or norm_incoming in norm_db or norm_db in norm_incoming
                if content_matched:
                    return item, c_name, "self"
            else:
                return item, c_name, "self"
    
    # 2. Look for @all
    if respond_to_all:
        for item in reversed(nodes):
            c_name = safe_get_name(item).strip()
            if not c_name or "ChatTextItemView" not in safe_class_name(item):
                continue
            if detect_is_self(item, session_name=name):
                continue
            
            last_msg_clean = c_name.replace('\u2005', ' ').replace('\u200b', '').strip()
            is_at_all = False
            for all_tag in ("所有人", "all", "All"):
                if re.search(rf'@[\s\u2005]*{re.escape(all_tag)}', last_msg_clean, re.IGNORECASE) or re.search(rf'@[\s\u2005]*{re.escape(all_tag)}', c_name, re.IGNORECASE):
                    is_at_all = True
                    break
            
            if is_at_all:
                if norm_incoming:
                    norm_db = _normalize_msg(c_name)
                    content_matched = norm_db == norm_incoming or norm_incoming in norm_db or norm_db in norm_incoming
                    if content_matched:
                        return item, c_name, "all"
                else:
                    return item, c_name, "all"
    
    # 3. Fallback to arbitrary @
    for item in reversed(nodes):
        c_name = safe_get_name(item).strip()
        if not c_name or "ChatTextItemView" not in safe_class_name(item):
            continue
        if detect_is_self(item, session_name=name):
            continue
        
        last_msg_clean = c_name.replace('\u2005', ' ').replace('\u200b', '').strip()
        if '@' in last_msg_clean or '@' in c_name:
            if norm_incoming:
                norm_db = _normalize_msg(c_name)
                content_matched = norm_db == norm_incoming or norm_incoming in norm_db or norm_db in norm_incoming
                if content_matched:
                    logger.info(f"[Mention] Matched fallback mention bubble via arbitrary '@' token and content matching: '{c_name[:20]}'")
                    return item, c_name, "fallback"
            else:
                logger.info(f"[Mention] Matched fallback mention bubble via arbitrary '@' token: '{c_name[:20]}'")
                return item, c_name, "fallback"
            
    return None, "", "none"
