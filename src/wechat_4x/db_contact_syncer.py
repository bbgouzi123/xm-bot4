import os
import re
import sqlite3
import logging
import tempfile
from datetime import datetime
from src.utils.contacts_cache import contacts_cache

import time
import threading

logger = logging.getLogger("DbContactSyncer")

from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
from .extra_buffer_parser import parse_contact_extra_buffer, source_scene_label, build_region, clean_group_name, _SYSTEM_WXIDS, process_contact_rows, merge_group_rows
from .db_avatar_syncer import sync_avatars_from_db

_sync_locks = {}
_last_sync_timestamps = {}
_global_lock = threading.Lock()


def sync_contacts_from_db(db_path: str, hex_key: str, account_id: str):
    """
    利用解密后的 WCDB 密钥，通过纯 Python AES 引擎解密 contact.db，
    再用标准 sqlite3 批量高速同步通讯录。
    不依赖 SQLCipher DLL 或 wcdb_api.dll，兼容任意 Python 环境。
    """
    global _sync_locks, _last_sync_timestamps
    
    # 标准化 account_id 防止 key 不一致导致解密锁冷却失效
    if not account_id or account_id in ("main", "default"):
        try:
            from src.crm.account_data import get_active_account
            active_id = get_active_account()
            if active_id and active_id != "default":
                account_id = active_id
        except Exception:
            pass

    with _global_lock:
        if account_id not in _sync_locks:
            _sync_locks[account_id] = threading.Lock()
            _last_sync_timestamps[account_id] = 0.0

    # 尝试非阻塞获取锁，防并发卡死
    if not _sync_locks[account_id].acquire(blocking=False):
        logger.info(f"[WCDB协调器] 账号 {account_id} 已有通讯录同步在进行中，跳过本次触发")
        return

    now = time.time()
    # 30秒冷却期，防止高频并发同步
    if now - _last_sync_timestamps[account_id] < 30.0:
        logger.info(f"[WCDB协调器] 账号 {account_id} 距离上一次同步不足 30 秒，跳过防抖限制")
        try:
            _sync_locks[account_id].release()
        except RuntimeError:
            pass
        return

    _last_sync_timestamps[account_id] = now

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


    # 🌟 强力插入：如果配置了手动密钥和数据路径，优先使用 🌟
    if account_id:
        try:
            from src.api.instance_settings_api import load_instance_settings
            cfg = load_instance_settings(account_id)
            manual_key = cfg.get("wechat_hex_key", "").strip()
            if manual_key and len(manual_key) == 64:
                hex_key = manual_key
                logger.info(f"[WCDB协调器] 同步通讯录使用手动解密密钥: {hex_key[:6]}...{hex_key[-6:]}")
            
            manual_dir = cfg.get("wechat_data_dir", "").strip()
            if manual_dir and os.path.exists(manual_dir):
                # 防御性清洗：如果 wechat_data_dir 包含 Session 等子目录，将其规范化回 db_storage 根目录
                _cleaned_dir = os.path.abspath(manual_dir)
                if os.path.basename(_cleaned_dir).lower() in ("session", "contact", "message", "head_image", "general", "sns", "favorite", "emoticon"):
                    _cleaned_dir = os.path.dirname(_cleaned_dir)
                
                db_storage_cand = None
                if os.path.basename(_cleaned_dir).lower() == "db_storage":
                    db_storage_cand = _cleaned_dir
                else:
                    cand = os.path.join(_cleaned_dir, "db_storage")
                    if os.path.isdir(cand):
                        db_storage_cand = cand
                    else:
                        db_storage_cand = _cleaned_dir
                if db_storage_cand:
                    db_storage = db_storage_cand
        except Exception as e_cfg:
            logger.debug(f"[WCDB协调器] 读取通讯录同步手动配置失败: {e_cfg}")

    contact_db_path = ""
    _sub_candidates = ["contact", "Contact"]
    # xwechat 4.x 在登录成功后可能需要较长时间才完成 contact.db 初始化（网络登录或首次同步），重试 5 次
    _checked_candidates = []
    for _retry in range(5):
        for sub in _sub_candidates:
            candidate = os.path.join(db_storage, sub, "contact.db")
            if _retry == 0:
                _checked_candidates.append(candidate)
            if os.path.exists(candidate):
                contact_db_path = candidate
                break
        if contact_db_path:
            break
        if _retry == 0:
            logger.debug(f"[WCDB协调器] 检查路径: {_checked_candidates}")
        if _retry < 4:
            logger.info(f"[WCDB协调器] contact.db 尚未就绪，5s 后重试 (第{_retry + 1}次/最多5次)...")
            time.sleep(5)

    if not contact_db_path:
        print(f"[WCDB协调器] 未找到 contact.db 数据库文件 (db_path={db_path}，已重试5次)")
        logger.warning(f"[WCDB协调器] 未找到 contact.db，跳过通讯录同步 (检查路径: {_checked_candidates})")
        try:
            _sync_locks[account_id].release()
        except RuntimeError:
            pass
        return

    print(f"[WCDB协调器] 正在解密并同步通讯录 contact.db...")
    logger.info(f"[WCDB协调器] 开始解密 contact.db: {contact_db_path}")

    tmp_path = None
    shadow_db = None
    conn = None
    try:
        decryptor = WeChatDatabaseDecryptor(hex_key)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xmbot4_contact_") as tmp:
            tmp_path = tmp.name

        import shutil
        shadow_db = tmp_path + "_shadow"
        try:
            shutil.copy2(contact_db_path, shadow_db)
        except Exception as copy_ex:
            logger.debug(f"[WCDB协调器] contact.db 影子拷贝失败: {copy_ex}，尝试直接解密主库")
            shadow_db = contact_db_path

        success = decryptor.decrypt_database(shadow_db, tmp_path)
        if not success:
            last = decryptor.last_result
            err_msg = last.get("error") or last.get("diagnostic_status") or "未知原因"
            print(f"[WCDB协调器] contact.db 解密失败: {err_msg}，可能是由于保存的微信密钥已失效或错误。正在自动清理持久化密钥...")
            logger.error(f"[WCDB协调器] contact.db 解密失败: {err_msg}")
            try:
                from src.utils.wechat_key_store import clear_persisted_wechat_key
                clear_persisted_wechat_key(account_id)
            except Exception as e_clear:
                logger.error(f"[WCDB协调器] 清理失效密钥失败: {e_clear}")
            return

        logger.info(
            f"[WCDB协调器] 解密成功: {decryptor.last_result.get('successful_pages', 0)} 页, "
            f"状态={decryptor.last_result.get('diagnostic_status')}"
        )

        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0].lower() for r in cursor.fetchall()}

        label_dict = {}
        if "contact_label" in tables:
            try:
                cursor.execute("SELECT label_id_, label_name_ FROM contact_label")
                label_rows = cursor.fetchall()
                for lr in label_rows:
                    lid = lr[0]
                    lname = lr[1] or ""
                    cleaned_name = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9_\-\s]", "", lname).strip()
                    if cleaned_name and lid:
                        label_dict[str(lid)] = cleaned_name
            except Exception as e_lbl:
                logger.debug(f"[WCDB协调器] 读取 contact_label 失败: {e_lbl}")

        if "contact" not in tables:
            print(f"[WCDB协调器] 解密后的数据库中未找到 contact 表，可用: {tables}")
            logger.warning(f"[WCDB协调器] 无 contact 表，可用: {tables}")
            return

        # 新版字段（snake_case）：username/nick_name/alias/remark
        # 旧版字段（PascalCase）：UserName/NickName/Alias/Remark
        cursor.execute("PRAGMA table_info(contact)")
        col_names = {r["name"].lower(): r["name"] for r in cursor.fetchall()}

        def _col(lower_name):
            return col_names.get(lower_name)

        username_col = _col("username")
        nick_col = _col("nick_name") or _col("nickname")
        alias_col = _col("alias")
        remark_col = _col("remark")

        if not username_col:
            print("[WCDB协调器] contact 表中未找到 username 字段，跳过同步")
            return

        select_parts = [username_col]
        if nick_col:
            select_parts.append(f"{nick_col} AS nick_name")
        if alias_col:
            select_parts.append(f"{alias_col} AS alias")
        if remark_col:
            select_parts.append(f"{remark_col} AS remark")

        # 动态获取并提取 local_type 与 verify_flag 字段用于真好友判定，extra_buffer 用于提取个性签名等详情
        local_type_col = _col("local_type")
        verify_flag_col = _col("verify_flag")
        flag_col = _col("flag")
        ext_buf_col = _col("extra_buffer") or _col("extrabuffer")

        if local_type_col:
            select_parts.append(f"{local_type_col} AS local_type")
        else:
            select_parts.append("0 AS local_type")
        if verify_flag_col:
            select_parts.append(f"{verify_flag_col} AS verify_flag")
        else:
            select_parts.append("0 AS verify_flag")
        if flag_col:
            select_parts.append(f"{flag_col} AS flag")
        if ext_buf_col:
            select_parts.append(f"{ext_buf_col} AS extra_buffer")
        else:
            select_parts.append("NULL AS extra_buffer")

        cursor.execute(f"SELECT {', '.join(select_parts)} FROM contact")
        rows = cursor.fetchall()

        from .contact_helper import get_group_rooms, get_group_members, get_self_info

        # 读群聊辅助表 chat_room 与成员信息
        group_rows = get_group_rooms(cursor, tables)
        group_members_dict = get_group_members(cursor, tables)

        sync_time = datetime.now().isoformat()
        from src.crm.account_data import normalize_to_real_wxid
        aid = normalize_to_real_wxid(account_id or "default")

        # 🌟 完美修复：自动通过当前数据库路径推断出真实的 raw_wxid (即父目录名)
        real_wxid = None
        try:
            db_storage_dir = db_storage
            account_dir = os.path.dirname(db_storage_dir)
            raw_wxid = os.path.basename(account_dir)
            from src.utils.wechat_key_store import clean_wxid
            real_wxid = clean_wxid(raw_wxid)
        except Exception as e_path:
            logger.debug(f"[WCDB协调器] 从路径推断 wxid 异常: {e_path}")

        # 如果推断失败，兜底使用 account_id
        search_id = real_wxid if (real_wxid and not real_wxid.startswith("instance_")) else aid

        # 提取个人信息
        self_info = get_self_info(cursor, tables, username_col, alias_col, nick_col, search_id, aid)
        self_wxid = self_info["wxid"]
        self_nickname = self_info["nickname"]

        # 🌟 核心增强：一旦提取到真实的微信号和昵称，立即持久化并同步共享内存
        if self_wxid and self_nickname:
            try:
                from src.crm.account_data import _save_account_meta
                _save_account_meta(aid, self_nickname, self_wxid)
                
                if aid != self_wxid:
                    _save_account_meta(self_wxid, self_nickname, self_wxid)
                
                from src.utils.instance_manager import InstanceManagerV2
                manager = InstanceManagerV2.get_instance()
                for inst_id, inst_data in manager.get_all_instances().items():
                    if inst_id == aid or inst_data.get("wxid") == self_wxid or inst_data.get("window_handle") == manager.get_all_instances().get(aid, {}).get("window_handle"):
                        manager.update_instance(inst_id, {
                            "nickname": self_nickname,
                            "wxid": self_wxid
                        })
            except Exception as e_sync_meta:
                logger.debug(f"[WCDB协调器] 同步账号元信息与共享内存异常: {e_sync_meta}")

        friends, groups_dict = process_contact_rows(
            rows, username_col, label_dict, group_members_dict, sync_time, self_info
        )

        merge_group_rows(group_rows, groups_dict, group_members_dict, sync_time, self_info)

        groups = list(groups_dict.values())

        if friends:
            contacts_cache.set_friends(aid, friends, sync_cloud=True)
        if groups:
            contacts_cache.set_groups(aid, groups, sync_cloud=True)

        # 智能同步数据库中缓存的所有用户/联系人头像
        try:
            import threading as th
            th.Thread(
                target=sync_avatars_from_db,
                args=(db_path, hex_key),
                daemon=True,
                name=f"wcdb-avatar-{account_id}"
            ).start()
        except Exception as e_av:
            logger.warning(f"[WCDB协调器] 异步启动头像提取异常: {e_av}")

        print(f"[WCDB协调器] 成功同步 {len(friends)} 个联系人，{len(groups)} 个群聊！")
        logger.info(f"[WCDB协调器] 成功同步 {len(friends)} 好友 / {len(groups)} 群聊 → contacts_cache")

        # 广播通讯录同步完成事件给前端，触发实时刷新展示
        try:
            from src.utils.websocket_manager import ws_manager
            import asyncio
            payload = {"type": "contact_sync_completed", "data": {"account_id": aid}}
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), loop)
            else:
                loop.run_until_complete(ws_manager.broadcast(payload))
            logger.info(f"[WCDB协调器] 已成功广播 contact_sync_completed 事件 (account_id={aid})")
        except Exception as we:
            logger.debug(f"[WCDB协调器] 广播通讯录同步完成事件失败: {we}")

    except Exception as e:
        print(f"[WCDB协调器] 通讯录同步失败: {e}")
        logger.error(f"[WCDB协调器] 通讯录同步异常: {e}", exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        if shadow_db and shadow_db != contact_db_path and os.path.exists(shadow_db):
            try:
                os.unlink(shadow_db)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        
        # 释放同步锁
        try:
            _sync_locks[account_id].release()
        except RuntimeError:
            pass

