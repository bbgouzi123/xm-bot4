import os
import sqlite3
import tempfile
import logging
from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
from src.crm.account_data import ACCOUNTS_DIR

logger = logging.getLogger("DbAvatarSyncer")

def sync_avatars_from_db(db_path: str, hex_key: str):
    """
    从 head_image.db 中提取所有联系人的头像并高速保存到 ACCOUNTS_DIR。
    """
    path = os.path.abspath(db_path)
    # 防御性解析真正的 db_storage 目录，无论传入的是文件、子目录还是根目录
    if os.path.isfile(path):
        parent1 = os.path.dirname(path)
        if os.path.basename(parent1).lower() in ("session", "contact", "message", "head_image", "general", "sns", "favorite", "emoticon"):
            db_storage = os.path.dirname(parent1)
        else:
            db_storage = parent1
    else:
        if os.path.basename(path).lower() in ("session", "contact", "message", "head_image", "general", "sns", "favorite", "emoticon"):
            db_storage = os.path.dirname(path)
        else:
            db_storage = path


    head_image_db_path = ""
    candidates = [
        os.path.join(db_storage, "head_image", "head_image.db"),
        os.path.join(db_storage, "head_image.db"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            head_image_db_path = candidate
            break

    if not head_image_db_path:
        # Check sibling directories as fallback
        parent_dir = os.path.dirname(db_storage)
        candidate_sibling = os.path.join(parent_dir, "head_image", "head_image.db")
        if os.path.exists(candidate_sibling):
            head_image_db_path = candidate_sibling

    if not head_image_db_path:
        logger.warning("[WCDB协调器] 未找到 head_image.db，跳过头像数据库同步")
        return

    logger.info(f"[WCDB协调器] 开始解密 head_image.db: {head_image_db_path}")

    tmp_path = None
    conn = None
    try:
        decryptor = WeChatDatabaseDecryptor(hex_key)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xmbot4_head_") as tmp:
            tmp_path = tmp.name

        success = decryptor.decrypt_database(head_image_db_path, tmp_path)
        if not success:
            last = decryptor.last_result
            err_msg = last.get("error") or last.get("diagnostic_status") or "未知原因"
            logger.error(f"[WCDB协调器] head_image.db 解密失败: {err_msg}")
            return

        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0].lower() for r in cursor.fetchall()}

        if "head_image" not in tables:
            logger.warning("[WCDB协调器] head_image 表不存在，跳过头像提取")
            return

        cursor.execute("SELECT username, image_buffer FROM head_image")
        rows = cursor.fetchall()

        os.makedirs(ACCOUNTS_DIR, exist_ok=True)

        count = 0
        for row in rows:
            username = row["username"]
            image_buffer = row["image_buffer"]
            if not username or not image_buffer:
                continue
            
            # 解析二进制数据
            try:
                if isinstance(image_buffer, memoryview):
                    img_bytes = image_buffer.tobytes()
                elif isinstance(image_buffer, str):
                    if image_buffer.lower().startswith("ffd8") or image_buffer.lower().startswith("89504e47"):
                        img_bytes = bytes.fromhex(image_buffer)
                    else:
                        try:
                            import base64
                            img_bytes = base64.b64decode(image_buffer)
                        except Exception:
                            img_bytes = image_buffer.encode('utf-8')
                else:
                    img_bytes = image_buffer

                if not img_bytes:
                    continue

                avatar_path = os.path.join(ACCOUNTS_DIR, f"{username}.png")
                # 只有当文件不存在或者大小不一致时才写盘以优化 I/O 效率
                if not os.path.exists(avatar_path) or os.path.getsize(avatar_path) != len(img_bytes):
                    with open(avatar_path, "wb") as f:
                        f.write(img_bytes)
                    count += 1
            except Exception as e_write:
                logger.debug(f"[WCDB协调器] 写入头像文件失败 {username}: {e_write}")

        logger.info(f"[WCDB协调器] 成功从 head_image.db 同步了 {count} 个头像文件")

    except Exception as e:
        logger.error(f"[WCDB协调器] 头像同步异常: {e}", exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
