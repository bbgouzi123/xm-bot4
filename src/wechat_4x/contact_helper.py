import os
import json
import logging

logger = logging.getLogger("ContactHelper")

def get_group_rooms(cursor, tables) -> list:
    """读群聊辅助表 chat_room"""
    group_rows = []
    if "chat_room" in tables:
        try:
            cursor.execute("PRAGMA table_info(chat_room)")
            cr_cols = {r["name"].lower(): r["name"] for r in cursor.fetchall()}
            cr_name_col = cr_cols.get("chatroom_name") or cr_cols.get("chatroomname") or cr_cols.get("name") or cr_cols.get("username")
            cr_disp_col = cr_cols.get("nick_name") or cr_cols.get("nickname") or cr_cols.get("display_name")
            if cr_name_col:
                parts2 = [cr_name_col]
                if cr_disp_col:
                    parts2.append(f"{cr_disp_col} AS disp_name")
                cursor.execute(f"SELECT {', '.join(parts2)} FROM chat_room")
                group_rows = cursor.fetchall()
        except Exception as eg:
            logger.debug(f"[WCDB协调器] 读取 chat_room 失败(可忽略): {eg}")
    return group_rows


def get_group_members(cursor, tables) -> dict:
    """读群聊成员信息"""
    group_members_dict = {}
    if "chatroom_member" in tables and "name2id" in tables:
        try:
            cursor.execute("""
                SELECT 
                    cr.username AS room_wxid,
                    m.username AS member_wxid,
                    c.nick_name AS member_nickname,
                    c.remark AS member_remark
                FROM chatroom_member cm
                JOIN name2id cr ON cm.room_id = cr.rowid
                JOIN name2id m ON cm.member_id = m.rowid
                LEFT JOIN contact c ON m.username = c.username
            """)
            member_rows = cursor.fetchall()
            for mr in member_rows:
                r_wxid = mr[0] or ""
                m_wxid = mr[1] or ""
                m_nick = mr[2] or ""
                m_remark = mr[3] or ""
                if not r_wxid or not m_wxid:
                    continue
                if r_wxid not in group_members_dict:
                    group_members_dict[r_wxid] = []
                group_members_dict[r_wxid].append({
                    "wxid": m_wxid,
                    "nickname": m_nick,
                    "remark": m_remark
                })
        except Exception as e_mem:
            logger.debug(f"[WCDB协调器] 读取群成员失败: {e_mem}")
    return group_members_dict


def get_self_info(cursor, tables, username_col, alias_col, nick_col, search_id, aid) -> dict:
    """自动解析真实 self_wxid/nickname/alias"""
    self_wxid = search_id
    self_nickname = ""
    self_alias = search_id
    if "contact" in tables:
        try:
            db_usr_col = username_col or "username"
            db_ali_col = alias_col or "alias"
            db_nick_col = nick_col or "nick_name"
            cursor.execute(
                f"SELECT {db_usr_col}, {db_nick_col}, {db_ali_col} FROM contact WHERE {db_usr_col}=? OR {db_ali_col}=?",
                (search_id, search_id)
            )
            r_self = cursor.fetchone()
            if r_self:
                if r_self[0]:
                    self_wxid = r_self[0]
                if r_self[1]:
                    self_nickname = r_self[1]
                if r_self[2]:
                    self_alias = r_self[2]
        except Exception as e_self:
            logger.debug(f"[WCDB协调器] 自动解析真实 self_wxid/nickname/alias 失败: {e_self}")

    if not self_nickname:
        try:
            meta_path = os.path.expanduser(f"~/.xm-ai-bot/accounts/{aid}/account_meta.json")
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    self_nickname = meta.get("nickname") or ""
        except Exception:
            pass

    return {
        "wxid": self_wxid,
        "nickname": self_nickname,
        "alias": self_alias
    }

def extract_group_members_from_wcdb(group_name: str, hex_key: str) -> list:
    """从微信解密数据库中高速同步提取指定群名的成员列表"""
    import os
    import shutil
    import tempfile
    import sqlite3
    from src.utils.wcdb_helpers import match_db_storage_by_key, detect_db_path
    from src.utils.wechat_decrypt import WeChatDatabaseDecryptor

    db_storage = match_db_storage_by_key(hex_key)
    if not db_storage:
        fallback_path = detect_db_path()
        if fallback_path:
            db_storage = os.path.dirname(os.path.dirname(fallback_path))

    contact_db_path = None
    if db_storage:
        for sub in ["contact", "Contact"]:
            candidate = os.path.join(db_storage, sub, "contact.db")
            if os.path.exists(candidate):
                contact_db_path = candidate
                break

    if not contact_db_path:
        return None

    decryptor = WeChatDatabaseDecryptor(hex_key)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xmbot4_export_") as tmp:
        tmp_path = tmp.name
    shadow_db = tmp_path + "_shadow"
    
    conn = None
    try:
        shutil.copy2(contact_db_path, shadow_db)
        success = decryptor.decrypt_database(shadow_db, tmp_path)
        if success:
            conn = sqlite3.connect(tmp_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT username FROM contact WHERE (nick_name=? OR remark=?) AND username LIKE '%@chatroom'",
                (group_name, group_name)
            )
            r_room = cursor.fetchone()
            if r_room:
                room_wxid = r_room[0]
                cursor.execute("""
                    SELECT 
                        m.username AS member_wxid,
                        c.nick_name AS member_nickname,
                        c.remark AS member_remark
                    FROM chatroom_member cm
                    JOIN name2id cr ON cm.room_id = cr.rowid
                    JOIN name2id m ON cm.member_id = m.rowid
                    LEFT JOIN contact c ON m.username = c.username
                    WHERE cr.username = ?
                """, (room_wxid,))
                member_rows = cursor.fetchall()
                if member_rows:
                    db_members = []
                    for mr in member_rows:
                        m_wxid = mr[0] or ""
                        m_nick = mr[1] or ""
                        m_remark = mr[2] or ""
                        disp = m_remark or m_nick or m_wxid
                        db_members.append({
                            "group_name": group_name,
                            "nickname": m_nick,
                            "display_name": disp,
                            "wxid": m_wxid,
                            "username": m_wxid,
                        })
                    return db_members
    except Exception as e:
        logger.warning(f"微信解密数据库极速提取群成员失败: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        for p_del in [shadow_db, tmp_path]:
            if p_del and os.path.exists(p_del):
                try:
                    os.unlink(p_del)
                except Exception:
                    pass
    return None
