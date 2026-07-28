"""
db_message_monitor.py
纯 Python 影子拷贝 WCDB 历史消息监测器（免 DLL 降级组件）

支持微信 4.x 多分片 message_N.db 扫描 —— 群聊分表 Msg_<md5(wxid)> 可能落在任意分片中。
"""
import os
import shutil
import sqlite3
import logging
import threading
import time
from typing import Optional
from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
from src.utils.chat_history import ChatHistoryManager
from src.uia.message_direction_helper import mark_message_direction

logger = logging.getLogger("DbMessageMonitor")


class MessageDbFallbackMonitor:
    """基于影子拷贝与纯 Python 异步解密的 message_N.db 消息同步器（支持微信 4.x 多分片）"""

    def __init__(self, account_id: str):
        self.account_id = account_id
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._db_path = ""       # message_0.db 路径（用于增量轮询）
        self._msg_dir = ""       # message/ 目录（用于多分片遍历）
        self._hex_key = ""
        self._last_mtime = 0.0
        self._last_size = 0
        self._last_wal_mtime = 0.0
        self._last_wal_size = 0
        self._last_local_id = 0
        self._query_lock = threading.Lock()  # 🔒 查询专用锁，防止并发查询竞争内存

    def start(self, session_db_path: str, hex_key: str) -> bool:
        if self._running:
            return True
        db_storage_dir = os.path.dirname(os.path.dirname(session_db_path))
        msg_dir = os.path.join(db_storage_dir, "message")
        msg_db = os.path.join(msg_dir, "message_0.db")
        if not os.path.exists(msg_db):
            logger.warning("[Python消息监听] 未找到 message_0.db: %s", msg_db)
            return False
        self._db_path = msg_db
        self._msg_dir = msg_dir
        self._hex_key = hex_key
        self._running = True
        self._last_mtime = 0.0
        self._last_size = 0
        self._last_local_id = 0
        self._thread = threading.Thread(target=self._run_loop, name="WcdbFallbackMessageMonitor", daemon=True)
        self._thread.start()
        logger.info("[Python消息监听] 后台同步线程启动 db=%s", msg_db)
        return True

    def stop(self):
        self._running = False
        if self._thread:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
            self._thread = None
        logger.info("[Python消息监听] 后台同步线程已停止")

    def _run_loop(self):
        tmp_dir = os.path.join(os.path.dirname(self._db_path), "temp_msg_monitor")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
        except Exception:
            tmp_dir = os.environ.get("TEMP", "C:\\Windows\\Temp")
        shadow_db = os.path.join(tmp_dir, "message_shadow.db")
        decrypted_db = os.path.join(tmp_dir, "message_shadow_dec.db")
        self._sync_once(shadow_db, decrypted_db, is_baseline=True)
        while self._running:
            try:
                time.sleep(5.0)
                if not self._running:
                    break
                self._sync_once(shadow_db, decrypted_db, is_baseline=False)
            except Exception as e:
                logger.error("[Python消息监听] 消息同步异常: %s", e)
        for f in [shadow_db, decrypted_db]:
            if os.path.exists(f):
                try:
                    os.unlink(f)
                except Exception:
                    pass

    def _sync_once(self, shadow_db: str, decrypted_db: str, is_baseline: bool = False):
        if not os.path.exists(self._db_path):
            return
        try:
            stat = os.stat(self._db_path)
            mtime, size = stat.st_mtime, stat.st_size
            
            # 🌟 WAL 变更监控：结合 WAL 文件的修改时间和大小共同作脏检查，应对未 checkpoint 的数据库事务变化
            wal_path = self._db_path + "-wal"
            wal_mtime, wal_size = 0.0, 0
            if os.path.exists(wal_path):
                try:
                    wal_stat = os.stat(wal_path)
                    wal_mtime, wal_size = wal_stat.st_mtime, wal_stat.st_size
                except Exception:
                    pass
            
            if (mtime == self._last_mtime and size == self._last_size and 
                wal_mtime == self._last_wal_mtime and wal_size == self._last_wal_size):
                return
                
            shutil.copy2(self._db_path, shadow_db)
            
            # 🌟 影子拷贝 WAL 文件：使 SQLCipher 打开临时影子库时能自动应用 WAL 日志重做，以获取最实时数据
            shadow_wal = shadow_db + "-wal"
            if os.path.exists(wal_path):
                try:
                    shutil.copy2(wal_path, shadow_wal)
                except Exception as wal_ex:
                    logger.debug(f"[Python消息监听] 复制后台 WAL 失败: {wal_ex}")
                    
            decryptor = WeChatDatabaseDecryptor(self._hex_key)
            if not decryptor.decrypt_database(shadow_db, decrypted_db):
                logger.debug("[Python消息监听] 解密 message_shadow.db 失败")
                return
            conn = sqlite3.connect(decrypted_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='message'")
                if cursor.fetchone():
                    # 微信 3.x 单大表增量同步
                    if is_baseline:
                        cursor.execute("SELECT MAX(localId) as max_id FROM message")
                        row = cursor.fetchone()
                        if row and row["max_id"]:
                            self._last_local_id = int(row["max_id"])
                    else:
                        self._sync_3x_increment(cursor)
                else:
                    if is_baseline:
                        logger.info("[Python消息监听] 检测到微信 4.x 分表架构，decrypted_db 已建立可供历史查询")
            finally:
                conn.close()
            self._last_mtime = mtime
            self._last_size = size
            self._last_wal_mtime = wal_mtime
            self._last_wal_size = wal_size
        except Exception as ex:
            logger.debug("[Python消息监听] 同步出错: %s", ex)
        finally:
            for f in [shadow_db, shadow_db + "-wal"]:
                if os.path.exists(f):
                    try:
                        os.unlink(f)
                    except Exception:
                        pass

    def _sync_3x_increment(self, cursor):
        """微信 3.x 单表新消息增量同步到 ChatHistoryManager"""
        from src.utils.contacts_cache import contacts_cache
        cursor.execute("SELECT localId, CreateTime, StrTalker, StrContent, IsSender FROM message WHERE localId > ? ORDER BY localId ASC", (self._last_local_id,))
        rows = cursor.fetchall()
        if not rows: return
        history_mgr = ChatHistoryManager(self.account_id)
        groups = contacts_cache.get_groups(self.account_id) or []
        friends = contacts_cache.get_friends(self.account_id) or []
        wxid_to_name = {x["wxid"]: x.get("name") or x.get("remark") or "" for pool in (groups, friends) for x in pool if x.get("wxid")}
        for r in rows:
            local_id = int(r["localId"])
            self._last_local_id = max(self._last_local_id, local_id)
            talker, content, is_self = r["StrTalker"] or "", r["StrContent"] or "", bool(r["IsSender"] == 1)
            if not talker or not content.strip(): continue
            s_name = wxid_to_name.get(talker, talker)
            mark_message_direction(content, is_self, s_name)
            try: history_mgr.add_message(s_name, "我" if is_self else s_name, "assistant" if is_self else "user", content)
            except Exception: pass

    def _decrypt_shard(self, shard_path: str, decrypted_path: str) -> bool:
        """对单个分片执行影子拷贝 + 解密"""
        try:
            shadow_tmp = decrypted_path + ".tmp"
            shutil.copy2(shard_path, shadow_tmp)
            wal_path, shadow_wal = shard_path + "-wal", shadow_tmp + "-wal"
            if os.path.exists(wal_path):
                try: shutil.copy2(wal_path, shadow_wal)
                except Exception: pass
            ok = WeChatDatabaseDecryptor(self._hex_key).decrypt_database(shadow_tmp, decrypted_path)
            for f in [shadow_tmp, shadow_wal]:
                if os.path.exists(f):
                    try: os.unlink(f)
                    except Exception: pass
            return ok
        except Exception as e:
            logger.debug(f"[消息DB诊断] 解密分片 {shard_path} 失败: {e}")
            return False

    def _query_shard_db(self, dec_db: str, target_table: str, talker_wxid: str, limit: int, dctx) -> Optional[list]:
        """在已解密的 DB 文件里查目标 talker 消息。返回列表或 None（分表不存在时）"""
        conn_s = None
        try:
            conn_s = sqlite3.connect(dec_db)
            conn_s.row_factory = sqlite3.Row
            cur = conn_s.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Name2Id'")
            if cur.fetchone():
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (target_table,))
                if not cur.fetchone(): return None
                cur.execute("SELECT rowid, user_name FROM Name2Id")
                name2id = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute(f"SELECT local_id, create_time, real_sender_id, message_content FROM {target_table} ORDER BY local_id DESC LIMIT ?", (limit,))
                rows = cur.fetchall()
                result = []
                for r in reversed(rows):
                    wxid_val = name2id.get(r["real_sender_id"] or 0, "")
                    is_self, content = (wxid_val == self.account_id), r["message_content"]
                    if isinstance(content, bytes):
                        try:
                            if content.startswith(b'\x28\xb5\x2f\xfd') and dctx:
                                content = dctx.decompress(content).decode('utf-8', errors='ignore')
                            else:
                                content = content.decode('utf-8', errors='ignore')
                        except Exception:
                            content = content.decode('utf-8', errors='ignore')
                    else:
                        content = str(content or "")
                    result.append({"local_id": int(r["local_id"] or 0), "is_self": is_self, "content": content, "timestamp": int(r["create_time"] or 0)})
                return result
            else:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='message'")
                if not cur.fetchone(): return None
                cur.execute("SELECT localId, CreateTime, StrTalker, StrContent, IsSender FROM message WHERE StrTalker=? ORDER BY localId DESC LIMIT ?", (talker_wxid, limit))
                rows = cur.fetchall()
                return [{"local_id": int(r["localId"] or 0), "is_self": bool(r["IsSender"] == 1), "content": str(r["StrContent"] or ""), "timestamp": int(r["CreateTime"] or 0)} for r in reversed(rows)]
        except Exception as e:
            logger.debug("[消息DB诊断] 查询 %s 异常: %s", dec_db, e)
            return None
        finally:
            if conn_s:
                try: conn_s.close()
                except Exception: pass

    def get_latest_messages(self, talker_wxid: str, limit: int = 10) -> list:
        import hashlib
        tmp_dir = os.path.join(os.path.dirname(self._db_path), "temp_msg_monitor")
        try: os.makedirs(tmp_dir, exist_ok=True)
        except Exception: pass
        target_table = f"Msg_{hashlib.md5(talker_wxid.encode('utf-8')).hexdigest()}"
        msg_dir = self._msg_dir or os.path.dirname(self._db_path)
        try:
            import zstandard as zstd
            dctx = zstd.ZstdDecompressor()
        except ImportError:
            dctx = None
        shard_files = []
        idx = 0
        while True:
            shard = os.path.join(msg_dir, f"message_{idx}.db")
            if not os.path.exists(shard): break
            shard_files.append(shard)
            idx += 1
        if not shard_files: shard_files = [self._db_path]
        logger.info("[Python消息监听] 开始扫描 %d 个分片查 talker=%s 分表=%s", len(shard_files), talker_wxid, target_table)
        with self._query_lock:
            q_dec = os.path.join(tmp_dir, "query_dec.db")
            if shard_files and self._decrypt_shard(shard_files[0], q_dec):
                quick = self._query_shard_db(q_dec, target_table, talker_wxid, limit, dctx)
                try: os.unlink(q_dec)
                except Exception: pass
                if quick is not None:
                    logger.info("[Python消息监听] ✅ 在 message_0.db 找到 talker=%s 消息 %d 条", talker_wxid, len(quick))
                    return quick
            for shard_path in shard_files[1:]:
                if self._decrypt_shard(shard_path, q_dec):
                    result = self._query_shard_db(q_dec, target_table, talker_wxid, limit, dctx)
                    try: os.unlink(q_dec)
                    except Exception: pass
                    if result is not None:
                        logger.info("[Python消息监听] ✅ 在分片 %s 找到 talker=%s 消息 %d 条", os.path.basename(shard_path), talker_wxid, len(result))
                        return result
            logger.warning("[Python消息监听] ⚠️ 所有 %d 个分片均未找到 talker='%s' 分表 %s", len(shard_files), talker_wxid, target_table)
            return []

