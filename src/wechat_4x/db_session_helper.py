"""db_session_helper.py - WCDB 数据库会话读取助手"""
import os
import ctypes
import hashlib
import sqlite3
import datetime
import tempfile
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_failed_warnings = set()

def _debug_log(msg: str):
    logger.debug(msg)

def get_latest_sessions_from_db(mon, limit: int = 50) -> Optional[List[dict]]:
    """
    从数据库获取最新会话列表。
    - 优先尝试通过 active monitor (DLL) 内存连接直接获取（毫秒级，零磁盘开销）
    - 若 DLL 不可用，降级为解密 `session.db` 进行 SQLite 直接查询（零物理干扰，零抢焦点）
    """
    _debug_log(f"开始获取会话，mon={mon}, limit={limit}")
    logger.info(f"[DEBUG_SESSION] 开始获取会话，mon={mon}, limit={limit}")
    # 自适应解包
    wcdb_mon = mon
    from src.wechat_4x.wcdb_session_monitor import WcdbSessionMonitor
    
    if mon and not isinstance(mon, WcdbSessionMonitor):
        if hasattr(mon, "monitor") and mon.monitor:
            wcdb_mon = getattr(mon.monitor, "_wcdb_session_monitor", None)

    # 1. 尝试从 DLL 内存查询
    if wcdb_mon and wcdb_mon.is_active() and hasattr(wcdb_mon._monitor, "_handle") and wcdb_mon._monitor._handle:
        try:
            _debug_log(f"尝试 DLL 获取会话, handle={wcdb_mon._monitor._handle}")
            logger.info(f"[DEBUG_SESSION] 尝试 DLL 获取会话, handle={wcdb_mon._monitor._handle}")
            out_ptr = ctypes.c_void_p(0)
            rc = wcdb_mon._monitor._wcdb_get_sessions(wcdb_mon._monitor._handle, ctypes.byref(out_ptr))
            _debug_log(f"DLL get_sessions 返回 rc={rc}, ptr={out_ptr.value}")
            logger.info(f"[DEBUG_SESSION] DLL get_sessions 返回 rc={rc}, ptr={out_ptr.value}")
            if rc == 0 and out_ptr.value:
                data = wcdb_mon._monitor._decode_ptr(out_ptr)
                _debug_log(f"DLL 解码结果: {data is not None}, 长度={len(data) if isinstance(data, list) else 'dict'}")
                logger.info(f"[DEBUG_SESSION] DLL 解码结果: {data is not None}, 长度={len(data) if isinstance(data, list) else 'dict'}")
                if data:
                    raw_sessions = data if isinstance(data, list) else data.get("sessions", [])
                    res = _parse_wcdb_sessions(mon, raw_sessions, limit)
                    _debug_log(f"DLL 解析后会话数={len(res)}")
                    logger.info(f"[DEBUG_SESSION] DLL 解析后会话数={len(res)}")
                    return res
        except Exception as e:
            _debug_log(f"通过 DLL 获取会话失败: {e}")
            logger.error(f"[WCDB会话助手] 通过 DLL 获取会话失败: {e}")

    # 2. 降级：解密读取本地 sqlite 文件
    db_path = getattr(wcdb_mon, "_db_path", None) if wcdb_mon else None
    hex_key = getattr(wcdb_mon, "_hex_key", None) if wcdb_mon else None
    
    # 提取微信 ID
    wxid = None
    if wcdb_mon:
        wxid = getattr(wcdb_mon, "_wxid", None) or getattr(wcdb_mon, "_account_id", None)
    if not wxid and mon:
        wxid = getattr(mon, "wxid", None) or getattr(mon, "account_id", None)

    _debug_log(f"准备解密本地 SQLite. db_path={db_path}, hex_key={hex_key is not None}, wxid={wxid}")
    logger.info(f"[DEBUG_SESSION] 准备解密本地 SQLite. db_path={db_path}, hex_key={hex_key is not None}, wxid={wxid}")

    if not db_path or not hex_key:
        if wxid and wxid != "default":
            from src.utils.wechat_key_store import get_persisted_wechat_key
            from .db_match_helper import auto_detect_db_path
            hex_key = get_persisted_wechat_key(wxid)
            if hex_key:
                expected = wxid if (wxid and not wxid.startswith("account_")) else None
                db_path = auto_detect_db_path(hex_key, expected)
                _debug_log(f"自动探测 db_path={db_path}, hex_key={hex_key is not None}")
                logger.info(f"[DEBUG_SESSION] 自动探测 db_path={db_path}, hex_key={hex_key is not None}")

    if not db_path or not hex_key or not os.path.exists(db_path):
        _debug_log(f"无法定位 db_path 或 hex_key (wxid={wxid})，降级失败. db_path={db_path}, hex_key_exists={hex_key is not None}")
        if wxid not in _failed_warnings:
            logger.warning(f"[WCDB会话助手] 无法定位 db_path 或 hex_key (wxid={wxid})，降级失败（该警告针对此微信号仅提示一次，后续将静默降级为 UIA 扫描）")
            _failed_warnings.add(wxid)
        else:
            logger.debug(f"[WCDB会话助手] 无法定位 db_path 或 hex_key (wxid={wxid})，已静默降级")
        return None

    try:
        from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
        
        decryptor = WeChatDatabaseDecryptor(hex_key)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xm_session_fetch_") as tmp:
            tmp_path = tmp.name

        try:
            _debug_log(f"开始解密数据库. db_path={db_path}, tmp_path={tmp_path}")
            logger.info(f"[DEBUG_SESSION] 开始解密数据库. db_path={db_path}, tmp_path={tmp_path}")
            if not decryptor.decrypt_database(db_path, tmp_path):
                _debug_log("解密数据库失败")
                logger.error("[DEBUG_SESSION] 解密数据库失败")
                return None
            
            _debug_log("解密成功，开始读取 sqlite")
            logger.info("[DEBUG_SESSION] 解密成功，开始读取 sqlite")
            conn = sqlite3.connect(tmp_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # 动态检查 SessionTable 的列进行自适应查询，防止微信数据库字段修改导致解密读取崩溃
            cursor.execute("PRAGMA table_info(SessionTable)")
            columns = [row[1] for row in cursor.fetchall()]
            
            select_cols = []
            # 1. username
            if "username" in columns:
                select_cols.append("username")
            else:
                select_cols.append("'' AS username")
                
            # 2. unread_count
            unread_col = next((c for c in columns if c.lower() in ("unread_count", "unreadcount", "unread")), None)
            if unread_col:
                select_cols.append(f"{unread_col} AS unread_count")
            else:
                select_cols.append("0 AS unread_count")
                
            # 3. summary
            summary_col = next((c for c in columns if c.lower() in ("summary", "digest", "msg_content", "last_msg")), None)
            if summary_col:
                select_cols.append(f"{summary_col} AS summary")
            else:
                select_cols.append("'' AS summary")
                
            # 4. last_timestamp
            ts_col = next((c for c in columns if c.lower() in ("last_timestamp", "lasttimestamp", "update_time", "timestamp")), None)
            if ts_col:
                select_cols.append(f"{ts_col} AS last_timestamp")
            else:
                select_cols.append("0 AS last_timestamp")
                
            # 5. is_pinned
            pin_col = next((c for c in columns if c.lower() in ("is_pinned", "ispinned", "placed_to_top", "is_placed_to_top")), None)
            if pin_col:
                select_cols.append(f"{pin_col} AS is_pinned")
            else:
                select_cols.append("0 AS is_pinned")
                
            # 6. is_muted
            mute_col = next((c for c in columns if c.lower() in ("is_muted", "ismuted", "silence", "is_silence")), None)
            if mute_col:
                select_cols.append(f"{mute_col} AS is_muted")
            else:
                select_cols.append("0 AS is_muted")
            
            # 7. status
            status_col = next((c for c in columns if c.lower() == "status"), None)
            if status_col:
                select_cols.append(f"{status_col} AS status")
            else:
                select_cols.append("0 AS status")
            
            where_clause = "WHERE is_hidden = 0" if "is_hidden" in columns else ""
            if where_clause:
                where_clause += " AND username NOT LIKE 'gh_%'"
            else:
                where_clause = "WHERE username NOT LIKE 'gh_%'"
            query = f"SELECT {', '.join(select_cols)} FROM SessionTable {where_clause} ORDER BY last_timestamp DESC LIMIT ?"
            
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
            raw_sessions = []
            for r in rows:
                db_unread = r["unread_count"] or 0
                status_val = r["status"] or 0
                is_manual_unread = (status_val & 4096) != 0
                if db_unread == 0 and is_manual_unread:
                    db_unread = 1
                raw_sessions.append({
                    "username": r["username"] or "",
                    "unread_count": db_unread,
                    "summary": r["summary"] or "",
                    "last_timestamp": r["last_timestamp"] or 0,
                    "is_pinned": r["is_pinned"] or 0,
                    "is_muted": r["is_muted"] or 0,
                })
            _debug_log(f"SQLite 查询到 raw_sessions 数量={len(raw_sessions)}")
            logger.info(f"[DEBUG_SESSION] SQLite 查询到 raw_sessions 数量={len(raw_sessions)}")
            conn.close()
            res = _parse_wcdb_sessions(mon, raw_sessions, limit)
            _debug_log(f"SQLite 解析后会话数={len(res)}")
            logger.info(f"[DEBUG_SESSION] SQLite 解析后会话数={len(res)}")
            return res
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        import traceback
        _debug_log(f"降级解密读取会话异常: {e}\n{traceback.format_exc()}")
        logger.error(f"[WCDB会话助手] 降级解密读取会话失败: {e}", exc_info=True)
        return None

def _parse_wcdb_sessions(mon, raw_sessions: List[dict], limit: int) -> List[dict]:
    from src.utils.contacts_cache import contacts_cache
    account_id = "default"
    if mon:
        account_id = getattr(mon, "_account_id", None) or getattr(mon, "wxid", None) or getattr(mon, "account_id", None) or "default"

    all_friends = contacts_cache.get_friends(account_id) or []
    all_groups = contacts_cache.get_groups(account_id) or []
    _debug_log(f"开始解析 raw_sessions, 数量={len(raw_sessions)}, account_id={account_id}, friends={len(all_friends)}, groups={len(all_groups)}")
    logger.info(f"[DEBUG_SESSION] 开始解析 raw_sessions, 数量={len(raw_sessions)}, account_id={account_id}, friends={len(all_friends)}, groups={len(all_groups)}")
    
    wxid_to_name = {}
    group_wxid_to_name = {}
    for f in all_friends:
        wxid = f.get('wxid')
        if wxid: wxid_to_name[wxid] = f.get('name') or f.get('remark') or ""
    for g in all_groups:
        wxid = g.get('wxid')
        if wxid: group_wxid_to_name[wxid] = g.get('name') or ""

    # 内置账号名字定义
    system_names = {
        "filehelper": "文件传输助手",
        "newsapp": "公众号",
        "brandsessionholder": "服务号",
        "brandservicesessionholder": "服务号",
        "weixin": "微信团队",
        "fmessage": "新的朋友",
    }

    parsed_list = []
    _debug_log(f"raw_sessions 前3个为: {[s.get('username') for s in raw_sessions[:3]]}")
    for s in raw_sessions:
        username = s.get("username") or s.get("session_id") or s.get("talker", "")
        if not username:
            continue
        
        # 过滤不需要在聊天接管显示的后台或垃圾账号
        if username.startswith("gh_") and username not in system_names:
            continue
        
        is_group = "@chatroom" in username
        
        # 解析名字
        name = ""
        if username in system_names:
            name = system_names[username]
        elif is_group:
            name = group_wxid_to_name.get(username) or username
        else:
            name = wxid_to_name.get(username) or username
            
        # 计算 id
        try:
            session_id = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:8], 16)
        except Exception:
            session_id = 0

        # 消息预览摘要
        summary = s.get("summary") or s.get("digest") or s.get("last_msg_content") or ""
        
        # 未读数
        unread = int(s.get("unread_count") or s.get("unread") or 0)
        
        # 置顶/免打扰
        is_pinned = bool(s.get("is_pinned") or s.get("pinned") or False)
        is_muted = bool(s.get("is_muted") or s.get("muted") or False)
        
        # 时间格式化
        ts = int(s.get("last_timestamp") or s.get("update_time") or s.get("time") or 0)
        last_time_str = _format_timestamp(ts)
        
        # 判断公众号
        from src.uia.session import SYSTEM_ACCOUNTS
        is_official = username in ('公众号', '服务号') or username in SYSTEM_ACCOUNTS or username.startswith("gh_")
        
        parsed_list.append({
            "id": session_id,
            "name": name,
            "wxid": username,
            "lastTime": last_time_str,
            "lastMessage": summary,
            "unread": unread,
            "isGroup": is_group,
            "isPinned": is_pinned,
            "isMuted": is_muted,
            "isAt": False,
            "isOfficial": is_official,
            "avatar": "",
        })
        
        if len(parsed_list) >= limit:
            break
            
    return parsed_list

def _format_timestamp(ts: int) -> str:
    if not ts:
        return ""
    try:
        if ts > 1000000000000:
            ts = ts // 1000
        dt = datetime.datetime.fromtimestamp(ts)
        now = datetime.datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        elif dt.date() == (now - datetime.timedelta(days=1)).date():
            return "昨天"
        elif now.year == dt.year:
            return dt.strftime("%m-%d")
        else:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""
