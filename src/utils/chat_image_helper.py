import os
import re
import sqlite3
import logging

logger = logging.getLogger(__name__)

def resolve_wxid_from_cache_or_db(account_id: str, name: str) -> str:
    """
    优先从 contacts_cache 中匹配好友/群聊的 wxid，以防频繁解密 contact.db，极其高效。
    """
    if not name:
        return ""
    try:
        from src.utils.contacts_cache import contacts_cache
        # 1. 查好友
        friends = contacts_cache.get_friends(account_id) or []
        for f in friends:
            if f.get("name") == name or f.get("alias") == name or f.get("wxid") == name:
                return f.get("wxid") or ""
        # 2. 查群聊
        groups = contacts_cache.get_groups(account_id) or []
        for g in groups:
            if g.get("name") == name or g.get("wxid") == name:
                return g.get("wxid") or ""
    except Exception:
        pass
        
    # 3. 兜底走数据库反查
    try:
        from src.utils.wcdb_name_helper import get_wxid_from_wcdb
        return get_wxid_from_wcdb(account_id, name)
    except Exception:
        return ""


def resolve_chat_images_in_history(talker_wxid: str, account_id: str, uia_messages: list) -> list:
    """
    匹配 UIA 消息历史中的 [图片] 占位符，并从解密 SQLite 数据库中提取图片 XML 还原为图片静态链接。
    支持微信 3.x 与 4.x 双版本数据库架构。
    """
    # 查找所有的 [图片] 消息索引
    img_msg_indices = []
    for idx, msg in enumerate(uia_messages):
        if len(msg) > 1 and msg[1] == "[图片]":
            img_msg_indices.append(idx)
            
    if not img_msg_indices:
        return uia_messages
        
    try:
        from src.utils.wechat_key_store import get_persisted_wechat_key
        hex_key = get_persisted_wechat_key(account_id)
        if not hex_key:
            return uia_messages
            
        from src.wechat_4x.db_match_helper import auto_detect_db_path
        db_path = auto_detect_db_path(hex_key, account_id)
        if not db_path:
            return uia_messages
            
        db_storage_dir = os.path.dirname(os.path.dirname(db_path))
        decrypted_db = os.path.join(db_storage_dir, "message", "temp_msg_monitor", "message_shadow_dec.db")
        if not os.path.exists(decrypted_db):
            # 兜底查找 sync 路径的 db
            decrypted_db = os.path.join(db_storage_dir, "message", "temp_msg_monitor", "message_shadow_dec_sync.db")
            if not os.path.exists(decrypted_db):
                return uia_messages
                
        conn = sqlite3.connect(decrypted_db)
        cursor = conn.cursor()
        db_images = []
        try:
            # 检查是 3.x (有 message 表) 还是 4.x (分表)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='message'")
            if cursor.fetchone():
                # 3.x
                cursor.execute(
                    "SELECT StrContent FROM message WHERE StrTalker = ? AND Type = 3 AND StrContent IS NOT NULL AND StrContent != '' ORDER BY CreateTime DESC LIMIT ?",
                    (talker_wxid, len(img_msg_indices) * 2 + 5)
                )
                db_images = [row[0] for row in cursor.fetchall()]
            else:
                # 4.x
                import hashlib
                target_table = f"Msg_{hashlib.md5(talker_wxid.encode('utf-8')).hexdigest()}"
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (target_table,))
                if cursor.fetchone():
                    cursor.execute(
                        f"SELECT message_content FROM {target_table} WHERE local_type = 3 AND message_content IS NOT NULL AND message_content != '' ORDER BY local_id DESC LIMIT ?",
                        (len(img_msg_indices) * 2 + 5,)
                    )
                    for row in cursor.fetchall():
                        content = row[0]
                        if isinstance(content, bytes):
                            try:
                                if content.startswith(b'\x28\xb5\x2f\xfd'):
                                    import zstandard as zstd
                                    dctx = zstd.ZstdDecompressor()
                                    content = dctx.decompress(content)
                                content = content.decode('utf-8', errors='ignore')
                            except Exception:
                                try:
                                    content = content.decode('utf-8', errors='ignore')
                                except Exception:
                                    content = ""
                        else:
                            content = str(content or "")
                        if content:
                            db_images.append(content)
        finally:
            conn.close()
            
        if not db_images:
            return uia_messages
            
        from src.wechat_4x.dat_decryptor import try_decrypt_wechat_dat
        
        db_idx = 0
        # 从最新到最旧匹配 (UIA 最底下的图片是最新的，对应 DB 第一个最新的记录)
        for idx in reversed(img_msg_indices):
            if db_idx < len(db_images):
                content_xml = db_images[db_idx]
                db_idx += 1
                try:
                    decrypted_url = try_decrypt_wechat_dat(content_xml, db_path, account_id)
                    if decrypted_url:
                        # 替换 uia_messages 中对应元素的 content 值为解密后的 URL
                        uia_messages[idx] = list(uia_messages[idx])  # 确保是可修改的 list 结构
                        uia_messages[idx][1] = decrypted_url
                        logger.info(f"[图片自愈] 成功将 UIA 历史图片 #{idx} 解密还原为: {decrypted_url}")
                except Exception as e_dec:
                    logger.debug(f"[图片自愈] 解密图片失败: {e_dec}")
                    
    except Exception as e:
        logger.error(f"[图片自愈] 执行异常: {e}")
        
    return uia_messages


def format_db_message_content(content: str, db_path: str | None, wxid: str | None = None) -> str:
    """
    格式化数据库里读取出来的原始消息内容。
    如果是图片 XML，自动解密并转换为前端可用的静态链接，其余多媒体转为友好占位符。
    """
    if not content:
        return ""
        
    s_clean = content.strip()
    # 判断是否为 XML 格式
    if s_clean.startswith("<msg") or s_clean.startswith("<?xml") or "<appmsg" in s_clean or "<img" in s_clean:
        # 1. 尝试判定是否为图片
        if "md5=" in s_clean or 'type="3"' in s_clean or "<img" in s_clean:
            try:
                from src.wechat_4x.dat_decryptor import try_decrypt_wechat_dat
                decrypted_url = try_decrypt_wechat_dat(s_clean, db_path, wxid)
                if decrypted_url:
                    return decrypted_url
            except Exception:
                pass
            return "[图片]"
            
        # 2. 检查是否为名片
        if "nickname=" in s_clean and "username=" in s_clean:
            return "[名片]"
            
        # 3. 检查是否为视频
        if "<videomsg" in s_clean or 'type="43"' in s_clean:
            return "[视频]"
            
        # 4. 检查是否为语音
        if "<voicemsg" in s_clean or 'type="34"' in s_clean:
            return "[语音]"
            
        # 5. 检查是否为文件或链接 (AppMsg)
        if "<appmsg" in s_clean:
            # 尝试提取标题
            title_match = re.search(r"<title>(.*?)</title>", s_clean)
            title = title_match.group(1) if title_match else ""
            if title:
                # 区分文件和普通链接
                if "<type>6</type>" in s_clean:
                    return f"[文件] {title}"
                else:
                    return f"[链接] {title}"
            return "[链接/文件]"
            
        return "[多媒体消息]"
        
    return content


def get_chat_history_from_db_clean(bot_wxid: str, talker_wxid: str, limit: int, nickname: str, friend_name: str, chat_type: str) -> list:
    """
    直接从数据库读取聊天历史，格式化并解密其中的图片，不经过任何 UIA 操作，性能极高。
    """
    db_msgs = []
    hex_key = os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
    
    # 1. 尝试从 DLL 数据库连接读取
    try:
        from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
        monitor = get_wcdb_monitor(bot_wxid)
        if monitor and monitor.is_active():
            db_msgs = monitor.get_latest_messages(talker_wxid, limit=limit)
    except Exception as e:
        logger.debug(f"[DB历史] DLL 数据库读取异常: {e}")
        
    # 2. 尝试从 Python 影子拷贝库读取
    if not db_msgs and hex_key:
        try:
            from src.wechat_4x.db_message_monitor import MessageDbFallbackMonitor
            temp_sync = MessageDbFallbackMonitor(bot_wxid)
            session_db = os.environ.get("WCDB_SESSION_DB_PATH", "")
            if not session_db:
                from src.wechat_4x.db_match_helper import auto_detect_db_path
                session_db = auto_detect_db_path(hex_key, bot_wxid)
            if session_db and os.path.exists(session_db):
                temp_sync._db_path = os.path.join(os.path.dirname(os.path.dirname(session_db)), "message", "message_0.db")
                temp_sync._msg_dir = os.path.join(os.path.dirname(os.path.dirname(session_db)), "message")
                temp_sync._hex_key = hex_key
                db_msgs = temp_sync.get_latest_messages(talker_wxid, limit=limit)
        except Exception as e:
            logger.debug(f"[DB历史] 影子库读取异常: {e}")
            
    if not db_msgs:
        return []
        
    # 按时间由旧到新排序（因为前端显示是从上往下，最新的在最底下）
    db_msgs = sorted(db_msgs, key=lambda x: x.get("timestamp", 0))
    
    # 找到 db_path 供 try_decrypt_wechat_dat 使用
    db_path = None
    if hex_key:
        from src.wechat_4x.db_match_helper import auto_detect_db_path
        db_path = auto_detect_db_path(hex_key, bot_wxid)
        
    formatted = []
    for i, m in enumerate(db_msgs):
        is_self = m.get("is_self", False)
        content_raw = m.get("content", "")
        
        # 格式化并解密内容
        content = format_db_message_content(content_raw, db_path, bot_wxid)
        
        sender = nickname if is_self else friend_name
        is_group = (chat_type == "group")
        
        formatted.append({
            "id": i,
            "content": content,
            "isSelf": is_self,
            "sender": sender,
            "isGroup": is_group,
            "isTimeMessage": False
        })
        
    return formatted


async def get_chat_messages_uia_fallback(target_drv, session_name: str, parse_file: bool, context_count: int, lock: bool, run_uia_fn) -> dict:
    """
    UIA 物理扫描聊天记录的兜底实现。
    """
    try:
        def _uia_work():
            def _core():
                non_chat_sessions = {
                    "公众号", "订阅号消息", "服务号", "服务通知", "微信支付", 
                    "腾讯新闻", "微信游戏", "微信支付商家助手", "小程序助手"
                }
                from src.uia.session import session_type_cache
                is_official = (session_name in non_chat_sessions) or (session_type_cache.get_type(session_name) == "official_account")
                if is_official:
                    logger.info(f"[API] 检测到会话 '{session_name}' 为公众号/系统非聊天会话，直接返回，避免 UIA 乱操作")
                    return {"messages": [], "chatType": "official_account"}

                if not lock:
                    edit_msg = target_drv._get_edit_control(session_name)
                    if not (edit_msg and edit_msg.Exists(0.2)):
                        logger.debug(f"[API] 后台轮询：微信当前未处于会话 '{session_name}'，为防打扰自动避让")
                        return {"messages": [], "chatType": "unknown"}

                if not target_drv.ChatWith(session_name, lock_input=lock, foreground=lock):
                    return {"messages": [], "chatType": "unknown"}
                chat_type = target_drv.get_chat_window_type(session_name)
                if chat_type == "official_account":
                    return {"messages": [], "chatType": "official_account"}
                raw_messages = target_drv.get_all_messages(parse_file, context_count, session_name)
                
                try:
                    active_wxid = (getattr(target_drv, "_wxid", None) or "")
                    talker_wxid = resolve_wxid_from_cache_or_db(active_wxid, session_name)
                    if talker_wxid:
                        # 延迟导入，防止循环依赖
                        from src.utils.chat_image_helper import resolve_chat_images_in_history
                        raw_messages = resolve_chat_images_in_history(talker_wxid, active_wxid, raw_messages)
                except Exception as ex_resolve:
                    logger.debug(f"[图片自愈] 执行历史图片解密映射异常: {ex_resolve}")
                formatted = []
                for i, msg in enumerate(raw_messages):
                    sender = msg[0] if len(msg) > 0 else ""
                    content = msg[1] if len(msg) > 1 else ""
                    nickname = target_drv._nickname or "我"
                    is_self = sender == nickname or sender == "我" or (chat_type == "file_transfer")
                    formatted.append({
                        "id": i, "content": content, "isSelf": is_self,
                        "sender": sender, "isGroup": chat_type == "group", "isTimeMessage": False,
                    })
                return {"messages": formatted, "chatType": chat_type}

            if lock:
                from src.uia.input_guard import uia_lock
                with uia_lock(f"正在接管与【{session_name}】的会话"):
                    uia_lock.update_status("正在激活微信并切换至会话...")
                    res = _core()
                    uia_lock.update_status("会话接管成功")
                    return res
            else:
                return _core()

        return await run_uia_fn(_uia_work)
    except Exception as e:
        logger.error(f"获取聊天记录兜底失败: {e}")
        return {"messages": [], "chatType": "unknown"}
