"""
聊天记录会话存证与 Excel 导出 API
"""
import os
import shutil
import tempfile
import time
import sqlite3
import logging
import hashlib
import re
import urllib.parse
from io import BytesIO
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from src.utils.response import err
from src.utils.wechat_key_store import get_persisted_wechat_key
from src.utils.wcdb_helpers import match_db_storage_by_key
from src.utils.wechat_decrypt import WeChatDatabaseDecryptor

logger = logging.getLogger("ChatExportApi")
router = APIRouter()

TYPE_LABELS = {3: "[图片消息]", 34: "[语音消息]", 43: "[视频消息]", 47: "[表情消息]", 49: "[小程序/文件/链接消息]"}

def process_msg_content(c_val) -> str:
    if c_val is None: return ""
    if isinstance(c_val, bytes):
        if c_val.startswith(b'\x28\xb5\x2f\xfd'):
            try:
                import zstandard as zstd
                c_val = zstd.ZstdDecompressor().decompress(c_val).decode('utf-8', errors='ignore')
            except Exception:
                try: c_val = c_val.decode('utf-8', errors='ignore')
                except Exception: pass
        else:
            try: c_val = c_val.decode('utf-8', errors='ignore')
            except Exception: pass
    else:
        c_val = str(c_val)
    if c_val.strip().startswith("<msg>"):
        if "<voicemsg" in c_val: return "[语音消息]"
        elif "<emoji" in c_val: return "[表情符号]"
        elif "<img" in c_val: return "[图片消息]"
        elif "<videomsg" in c_val: return "[视频消息]"
        elif "<appmsg" in c_val: return "[链接/卡片消息]"
        else: return "[媒体/文件消息]"
    elif c_val.strip().startswith("<?xml"):
        return "[系统消息：撤回了一条消息]" if "revokemsg" in c_val else "[系统消息]"
    c_val = re.sub(r'^wxid_[a-zA-Z0-9_-]+:\s*\n', '', c_val)
    if isinstance(c_val, str) and (c_val.startswith("b'") or c_val.startswith('b"')):
        return "[媒体/加密消息数据]"
    return c_val

def fetch_messages_base(bot_wxid: str, talker: str, limit: int):
    from app.state import account_manager
    target_inst = next((inst for inst in account_manager._instances.values() if inst.wxid == bot_wxid or getattr(inst.driver, "bot_wxid", "") == bot_wxid), None)
    if not target_inst and account_manager._instances:
        target_inst = list(account_manager._instances.values())[0]
    target_wxid = bot_wxid

    hex_key = None
    if target_inst:
        wcdb_mon = getattr(target_inst.monitor, "_wcdb_session_monitor", None)
        hex_key = getattr(wcdb_mon, "_hex_key", None) if wcdb_mon else None

    if not hex_key:
        KEYS_FILE_PATH = os.path.expanduser("~/.xm-ai-bot/wechat_keys.json")
        if os.path.exists(KEYS_FILE_PATH):
            try:
                with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                    kd = json.load(f)
                from src.utils.wechat_key_store import clean_wxid
                hex_key = kd.get(clean_wxid(target_wxid)) or kd.get(target_wxid) or kd.get("last_key")
            except Exception: pass

    if not hex_key:
        hex_key = get_persisted_wechat_key(target_wxid)
    if not hex_key: raise ValueError("无法获取该账号的微信密钥")
    db_dir = match_db_storage_by_key(hex_key)
    if not db_dir: raise ValueError("本地未匹配到微信数据库存储目录")
    message_db_path = os.path.join(db_dir, "message", "message_0.db")
    contact_db_path = os.path.join(db_dir, "contact", "contact.db")
    if not os.path.exists(contact_db_path): contact_db_path = os.path.join(db_dir, "Contact", "contact.db")
    if not os.path.exists(message_db_path): raise ValueError("未找到消息数据库文件")

    temp_dir = tempfile.mkdtemp(prefix="xm_export_")
    tmp_msg_dec = os.path.join(temp_dir, "msg_dec.db")
    tmp_contact_dec = os.path.join(temp_dir, "contact_dec.db")
    try:
        decryptor = WeChatDatabaseDecryptor(hex_key)
        tmp_raw_msg = os.path.join(temp_dir, "msg_raw.db")
        shutil.copy2(message_db_path, tmp_raw_msg)
        if not decryptor.decrypt_database(tmp_raw_msg, tmp_msg_dec): raise ValueError("影子解密消息数据库失败")
        has_contact = False
        if os.path.exists(contact_db_path):
            try:
                tmp_raw_ct = os.path.join(temp_dir, "ct_raw.db")
                shutil.copy2(contact_db_path, tmp_raw_ct)
                if decryptor.decrypt_database(tmp_raw_ct, tmp_contact_dec): has_contact = True
            except Exception: pass

        conn = sqlite3.connect(tmp_msg_dec)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Name2Id'")
        has_name2id = cursor.fetchone() is not None
        raw_msgs = []

        if not has_name2id:
            cursor.execute("SELECT CreateTime, Message, IsSender, Type FROM message WHERE StrTalker = ? ORDER BY CreateTime DESC LIMIT ?;", (talker, limit))
            for r in cursor.fetchall():
                c_raw = r["Message"]
                if c_raw and (isinstance(c_raw, bytes) and c_raw.startswith(b'\xb5') or (isinstance(c_raw, str) and "xb5" in c_raw)):
                    logger.warning(f"[Live Capture 3.x] type: {type(c_raw).__name__} | Repr: {repr(c_raw)}")
                raw_msgs.append({"time": r["CreateTime"], "content": process_msg_content(c_raw), "is_self": r["IsSender"] == 1, "type": r["Type"]})
        else:
            cursor.execute("SELECT rowid, user_name FROM Name2Id")
            rowid_to_wxid = {row["rowid"]: row["user_name"] for row in cursor.fetchall()}
            t_name = f"Msg_{hashlib.md5(talker.encode('utf-8')).hexdigest()}"
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?;", (t_name,))
            if cursor.fetchone() is not None:
                cursor.execute(f"SELECT create_time, message_content, real_sender_id, local_type FROM {t_name} ORDER BY local_id DESC LIMIT ?;", (limit,))
                for r in cursor.fetchall():
                    sender_wxid = rowid_to_wxid.get(r["real_sender_id"], "")
                    c_raw = r["message_content"]
                    if c_raw and (isinstance(c_raw, bytes) and c_raw.startswith(b'\xb5') or (isinstance(c_raw, str) and "xb5" in c_raw)):
                        logger.warning(f"[Live Capture 4.x] type: {type(c_raw).__name__} | Repr: {repr(c_raw)}")
                        if isinstance(c_raw, bytes): logger.warning(f"  Hex: {c_raw.hex()}")
                    raw_msgs.append({"time": r["create_time"], "content": process_msg_content(c_raw), "is_self": sender_wxid == target_wxid, "type": r["local_type"]})

        raw_msgs = list(reversed(raw_msgs))
        talker_name, bot_name = talker, target_wxid
        if has_contact:
            try:
                cursor.execute("ATTACH DATABASE ? AS contact_db;", (tmp_contact_dec,))
                cursor.execute("SELECT remark, nick_name FROM contact_db.contact WHERE username = ?;", (talker,))
                r = cursor.fetchone()
                if r: talker_name = r["remark"] or r["nick_name"] or talker
                cursor.execute("SELECT remark, nick_name FROM contact_db.contact WHERE username = ?;", (target_wxid,))
                rb = cursor.fetchone()
                if rb: bot_name = rb["remark"] or rb["nick_name"] or target_wxid
                cursor.execute("DETACH DATABASE contact_db;")
            except Exception as e: logger.error(f"备注获取失败: {e}")
        conn.close()
        return raw_msgs, talker_name, bot_name, target_wxid
    finally:
        try: shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception: pass

@router.get("/api/v1/chat/export", response_class=HTMLResponse)
async def export_chat_history(
    bot_wxid: str = Query(..., description="微信机器人实例 wxid"),
    talker: str = Query(..., description="好友或群聊 session_id"),
    limit: int = Query(500, description="导出聊天记录的上限条数")
):
    try:
        raw_msgs, talker_name, bot_name, target_wxid = fetch_messages_base(bot_wxid, talker, limit)
        chat_rows_html = []
        for msg in raw_msgs:
            m_time, m_type, m_content, is_self = msg["time"], msg["type"], msg["content"], msg["is_self"]
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m_time))
            if m_type == 10000:
                chat_rows_html.append(f'<div class="system-msg"><span>{m_content}</span></div>')
            else:
                role_class = "self" if is_self else "other"
                avatar_url = f"/api/avatar/{target_wxid if is_self else talker}"
                sender_display = bot_name if is_self else talker_name
                display_content = m_content if m_type == 1 else TYPE_LABELS.get(m_type, "[其他消息]")
                display_content_str = str(display_content).strip()
                if display_content_str.startswith("<"): display_content_str = "[其他网页/小程序内容]"
                import html
                display_content_str = html.escape(display_content_str)
                chat_rows_html.append(f"""
                <div class="msg-row {role_class}">
                    <img class="avatar" src="{avatar_url}" onerror="this.style.display='none'" />
                    <div class="msg-box">
                        <span class="sender-name">{sender_display}</span>
                        <div class="bubble">{display_content_str}</div>
                        <span class="msg-time">{time_str}</span>
                    </div>
                </div>
                """)

        export_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        css_style = (
            "body { background-color:#f3f4f6; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; margin:0; padding:12px; display:flex; justify-content:center; }\n"
            ".chat-container { width:100%; max-width:650px; background:#fff; border-radius:16px; box-shadow:0 10px 30px rgba(0,0,0,0.08); display:flex; flex-direction:column; overflow:hidden; }\n"
            ".chat-header { padding:18px 24px; background:#f8fafc; border-bottom:1px solid #e2e8f0; display:flex; justify-content:space-between; align-items:center; }\n"
            ".chat-header-title { font-size:16px; font-weight:700; color:#1e293b; }\n"
            ".chat-header-meta { font-size:12px; color:#64748b; display:flex; align-items:center; gap:10px; }\n"
            ".btn-excel-icon { text-decoration:none; color:#10b981; display:inline-flex; align-items:center; justify-content:center; padding:4px; border:1px solid #10b981; border-radius:4px; background:rgba(16,185,129,0.06); transition:all 0.2s; margin-left:6px; }\n"
            ".btn-excel-icon:hover { background:#10b981; color:white; transform:scale(1.05); }\n"
            ".chat-body { padding:24px; background:#f8fafc; min-height:500px; max-height:80vh; overflow-y:auto; }\n"
            ".msg-row { display:flex; margin-bottom:20px; align-items:flex-start; }\n"
            ".msg-row.self { flex-direction:row-reverse; }\n"
            ".avatar { width:38px; height:38px; border-radius:6px; object-fit:cover; margin:0 12px; background:#cbd5e1; border:1px solid #e2e8f0; }\n"
            ".msg-box { max-width:72%; display:flex; flex-direction:column; }\n"
            ".msg-row.self .msg-box { align-items:flex-end; }\n"
            ".sender-name { font-size:11px; font-weight:500; color:#64748b; margin-bottom:5px; }\n"
            ".bubble { padding:10px 14px; border-radius:8px; font-size:14px; line-height:1.5; word-break:break-all; white-space:pre-wrap; }\n"
            ".msg-row.other .bubble { background:#ffffff; color:#0f172a; border:1px solid #e2e8f0; box-shadow:0 1px 2px rgba(0,0,0,0.02); border-top-left-radius:2px; }\n"
            ".msg-row.self .bubble { background:#22c55e; color:#ffffff; border-top-right-radius:2px; box-shadow:0 1px 2px rgba(0,0,0,0.05); }\n"
            ".msg-time { font-size:10px; color:#94a3b8; margin-top:6px; }\n"
            ".system-msg { text-align:center; margin:20px 0; display:flex; justify-content:center; }\n"
            ".system-msg span { background:#e2e8f0; color:#475569; padding:5px 10px; border-radius:6px; font-size:11px; max-width:80%; word-break:break-all; }"
        )
        final_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>聊天记录会话存证 - {talker_name}</title><style>{css_style}</style>
</head>
<body>
    <div class="chat-container">
        <div class="chat-header">
            <span class="chat-header-title">微信对话存证报告 - {talker_name}</span>
            <span class="chat-header-meta">
                导出时间: {export_time}
                <a href="/api/v1/chat/export/excel?bot_wxid={bot_wxid}&talker={talker}&limit={limit}" class="btn-excel-icon" title="导出为 Excel 格式">
                    <svg viewBox="0 0 24 24" style="width:14px; height:14px; fill:none; stroke:currentColor; stroke-width:2.5; stroke-linecap:round; stroke-linejoin:round; display:block;">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="7 10 12 15 17 10"></polyline>
                        <line x1="12" y1="15" x2="12" y2="3"></line>
                    </svg>
                </a>
            </span>
        </div>
        <div class="chat-body">{"".join(chat_rows_html)}</div>
    </div>
</body>
</html>"""
        return HTMLResponse(content=final_html, status_code=200)
    except Exception as e:
        logger.error(f"导出 HTML 异常: {e}", exc_info=True)
        return HTMLResponse(content=f"<h3>系统异常: {str(e)}</h3>", status_code=500)

@router.get("/api/v1/chat/export/excel")
async def export_chat_history_excel(
    bot_wxid: str = Query(..., description="微信机器人实例 wxid"),
    talker: str = Query(..., description="好友或群聊 session_id"),
    limit: int = Query(500, description="导出聊天记录的上限条数")
):
    try:
        try: import openpyxl
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])
            import openpyxl
        raw_msgs, talker_name, bot_name, target_wxid = fetch_messages_base(bot_wxid, talker, limit)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "会话记录存证"
        ws.append(["发信人头像 (API 链接)", "发信人名称", "聊天内容", "发送时间"])
        for msg in raw_msgs:
            m_time, m_type, m_content, is_self = msg["time"], msg["type"], msg["content"], msg["is_self"]
            sender_name = bot_name if is_self else talker_name
            sender_avatar_url = f"/api/avatar/{target_wxid if is_self else talker}"
            display_content_str = str(m_content if m_type == 1 else TYPE_LABELS.get(m_type, "[其他消息]")).strip()
            if display_content_str.startswith("<"): display_content_str = "[其他内容]"
            ws.append([sender_avatar_url, sender_name, display_content_str, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m_time))])
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 60)
        stream = BytesIO()
        wb.save(stream)
        stream.seek(0)
        fn_encoded = urllib.parse.quote(f"chat_history_{talker_name}.xlsx".encode('utf-8'))
        return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn_encoded}"})
    except Exception as e:
        logger.error(f"导出 Excel 异常: {e}", exc_info=True)
        return StreamingResponse(BytesIO(b"error"), media_type="text/plain")
