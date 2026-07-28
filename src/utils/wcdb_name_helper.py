import os
import sqlite3
import tempfile
import logging

logger = logging.getLogger(__name__)
_wcdb_name_cache = {}
_wcdb_wxid_cache = {}

def get_name_from_wcdb(account_id: str, username: str) -> str:
    """
    通过解密本地 wechat contact.db 数据库实时单条反查指定 username 的备注或昵称。
    使用 auto_detect_db_path 精准寻址，彻底自愈刚启动时控制中心第一秒展示原始微信号的 Bug。
    """
    if not account_id or not username:
        return ""
        
    global _wcdb_name_cache
    cache_key = f"{account_id}:{username}"
    if cache_key in _wcdb_name_cache:
        return _wcdb_name_cache[cache_key]
        
    try:
        from src.utils.wechat_key_store import get_persisted_wechat_key
        hex_key = get_persisted_wechat_key(account_id)
        if not hex_key:
            try:
                from src.api.instance_settings_api import load_instance_settings
                cfg = load_instance_settings(account_id)
                hex_key = cfg.get("wechat_hex_key", "").strip()
            except Exception:
                pass
                
        if not hex_key:
            return ""
            
        # 利用成熟 of auto_detect_db_path 探测主数据库 session.db 的路径，100% 契合 4.x 的哈希目录
        from src.wechat_4x.db_match_helper import auto_detect_db_path
        db_path = auto_detect_db_path(hex_key, account_id)
        if not db_path:
            return ""
            
        # 关联得出 db_storage 目录，并查找 contact.db
        db_storage = os.path.dirname(os.path.dirname(db_path))
        contact_db = ""
        for sub in ["contact", "Contact"]:
            candidate_db = os.path.join(db_storage, sub, "contact.db")
            if os.path.exists(candidate_db):
                contact_db = candidate_db
                break
                
        if not contact_db:
            return ""
            
        from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
        
        decryptor = WeChatDatabaseDecryptor(hex_key)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xm_unread_name_") as tmp:
            tmp_path = tmp.name
            
        try:
            if decryptor.decrypt_database(contact_db, tmp_path):
                conn = sqlite3.connect(tmp_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("PRAGMA table_info(contact)")
                col_names = {r["name"].lower(): r["name"] for r in cursor.fetchall()}
                usr_col = col_names.get("username")
                nick_col = col_names.get("nick_name") or col_names.get("nickname")
                remark_col = col_names.get("remark")
                
                if usr_col and nick_col:
                    q_cols = [usr_col, nick_col]
                    if remark_col:
                        q_cols.append(remark_col)
                    cursor.execute(f"SELECT {', '.join(q_cols)} FROM contact WHERE {usr_col}=?", (username,))
                    row = cursor.fetchone()
                    if row:
                        val = (row[remark_col] if remark_col and row[remark_col] else row[nick_col]) or ""
                        val = val.strip()
                        if val:
                            _wcdb_name_cache[cache_key] = val
                            conn.close()
                            return val
                conn.close()
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
    except Exception as e_wcdb:
        logger.debug(f"[未读自愈] 通过 contact.db 单条反查昵称异常: {e_wcdb}")
        
    return ""

def find_wxid_from_wcdb(account_id: str, name: str) -> str:
    """
    根据给定的好友昵称、备注或微信号，去 contact.db 中实时解密反查其对应的真实微信 ID。
    """
    if not account_id or not name:
        return ""
        
    global _wcdb_wxid_cache
    cache_key = f"{account_id}:{name}"
    if cache_key in _wcdb_wxid_cache:
        return _wcdb_wxid_cache[cache_key]
        
    try:
        from src.utils.wechat_key_store import get_persisted_wechat_key
        hex_key = get_persisted_wechat_key(account_id)
        if not hex_key:
            try:
                from src.api.instance_settings_api import load_instance_settings
                cfg = load_instance_settings(account_id)
                hex_key = cfg.get("wechat_hex_key", "").strip()
            except Exception:
                pass
                
        if not hex_key:
            return ""
            
        from src.wechat_4x.db_match_helper import auto_detect_db_path
        db_path = auto_detect_db_path(hex_key, account_id)
        if not db_path:
            return ""
            
        db_storage = os.path.dirname(os.path.dirname(db_path))
        contact_db = ""
        for sub in ["contact", "Contact"]:
            candidate_db = os.path.join(db_storage, sub, "contact.db")
            if os.path.exists(candidate_db):
                contact_db = candidate_db
                break
                
        if not contact_db:
            return ""
            
        from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
        
        decryptor = WeChatDatabaseDecryptor(hex_key)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xm_avatar_wxid_") as tmp:
            tmp_path = tmp.name
            
        try:
            if decryptor.decrypt_database(contact_db, tmp_path):
                conn = sqlite3.connect(tmp_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("PRAGMA table_info(contact)")
                col_names = {r["name"].lower(): r["name"] for r in cursor.fetchall()}
                usr_col = col_names.get("username")
                nick_col = col_names.get("nick_name") or col_names.get("nickname")
                remark_col = col_names.get("remark")
                
                if usr_col and nick_col:
                    q = f"SELECT {usr_col} FROM contact WHERE {usr_col}=? OR {nick_col}=?"
                    params = [name, name]
                    if remark_col:
                        q += f" OR {remark_col}=?"
                        params.append(name)
                        
                    cursor.execute(q, params)
                    row = cursor.fetchone()
                    if row:
                        val = row[usr_col] or ""
                        if val:
                            _wcdb_wxid_cache[cache_key] = val
                            conn.close()
                            return val
                conn.close()
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
    except Exception as ex:
        logger.debug(f"[头像自愈] 数据库反查 WXID 异常: {ex}")
        
    return ""

get_wxid_from_wcdb = find_wxid_from_wcdb
