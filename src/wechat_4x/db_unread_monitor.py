"""
db_unread_monitor.py
纯 Python 影子拷贝 WCDB 未读消息监测器（免 DLL 降级组件）

当 wcdb_api.dll 加载失败或失效时，使用本组件通过影子拷贝并解密 session.db 来检测未读消息，
完全不注入微信内存、不触碰微信风控，实现 100% 绿色的未读感知。
"""
import os
import shutil
import sqlite3
import logging
import threading
import time
from typing import Callable, Dict, Optional
from src.utils.wechat_decrypt import WeChatDatabaseDecryptor

logger = logging.getLogger("DbUnreadMonitor")


class SessionDbFallbackMonitor:
    """
    基于影子拷贝与纯 Python 解密的 session.db 未读监控器
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._db_path = ""
        self._hex_key = ""
        self._on_new_message: Optional[Callable[[dict], None]] = None
        self._last_unread_counts: Dict[str, int] = {}
        self._last_summaries: Dict[str, str] = {}
        self._last_mtime = 0.0
        self._last_size = 0
        self._last_mtime_wal = 0.0
        self._last_size_wal = 0
        self._first_poll_after_baseline = True

    def start(self, db_path: str, hex_key: str, on_new_message: Callable[[dict], None]) -> bool:
        if self._running:
            return True

        if not db_path or not os.path.exists(db_path):
            logger.error("[Python未读监听] 数据库路径不存在: %s", db_path)
            return False

        self._db_path = db_path
        self._hex_key = hex_key
        self._on_new_message = on_new_message
        self._running = True
        self._last_unread_counts.clear()
        self._last_summaries.clear()
        self._last_mtime = 0.0
        self._last_size = 0
        self._last_mtime_wal = 0.0
        self._last_size_wal = 0
        self._first_poll_after_baseline = True

        self._thread = threading.Thread(target=self._run_loop, name="WcdbFallbackUnreadMonitor", daemon=True)
        self._thread.start()
        logger.info("[Python未读监听] 降级监听线程已成功启动 db=%s", db_path)
        return True

    def stop(self):
        self._running = False
        if self._thread:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
            self._thread = None
        logger.info("[Python未读监听] 降级监听线程已停止")

    def is_active(self) -> bool:
        return self._running

    def _run_loop(self):
        # 建立缓存的临时文件夹
        tmp_dir = os.path.join(os.path.dirname(self._db_path), "temp_monitor")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
        except Exception:
            tmp_dir = os.environ.get("TEMP", "C:\\Windows\\Temp")

        shadow_db = os.path.join(tmp_dir, "session_shadow.db")
        decrypted_db = os.path.join(tmp_dir, "session_shadow_dec.db")

        # 首次运行先做一次状态基准采样，防止刚启动时把已存在的未读消息重复触发回复
        self._poll_once(shadow_db, decrypted_db, is_baseline=True)

        while self._running:
            try:
                time.sleep(1.0)  # 将 0.1 秒的高频轮询调整为 1.0 秒，保障降级模式下磁盘与 CPU 的平稳，杜绝高频 I/O 拥堵导致的延迟
                if not self._running:
                    break
                self._poll_once(shadow_db, decrypted_db, is_baseline=False)
            except Exception as e:
                logger.error("[Python未读监听] 轮询异常: %s", e, exc_info=True)

        # 退出清理
        for f in [shadow_db, decrypted_db]:
            if os.path.exists(f):
                try:
                    os.unlink(f)
                except Exception:
                    pass

    def _poll_once(self, shadow_db: str, decrypted_db: str, is_baseline: bool = False):
        if not os.path.exists(self._db_path):
            return

        # 同时检查主库 session.db 和 WAL 日志 session.db-wal，以杜绝 SQLite 延迟落盘带来的感知滞后
        mtime_db, size_db = 0.0, 0
        mtime_wal, size_wal = 0.0, 0
        try:
            stat_db = os.stat(self._db_path)
            mtime_db = stat_db.st_mtime
            size_db = stat_db.st_size

            wal_path = self._db_path + "-wal"
            if os.path.exists(wal_path):
                stat_wal = os.stat(wal_path)
                mtime_wal = stat_wal.st_mtime
                size_wal = stat_wal.st_size

            # 若主库与 WAL 指纹均未变动，则直接跳过，零 IO 与解密负载
            is_unchanged = (
                not is_baseline and
                not self._first_poll_after_baseline and
                mtime_db == self._last_mtime and
                size_db == self._last_size and
                mtime_wal == self._last_mtime_wal and
                size_wal == self._last_size_wal
            )
            if is_unchanged:
                return
        except Exception:
            pass

        # 1. 影子拷贝
        try:
            shutil.copy2(self._db_path, shadow_db)
        except Exception as e:
            logger.debug("[Python未读监听] 影子拷贝失败 (微信可能正独占锁定): %s", e)
            return

        # 2. 解密数据库
        conn = None
        try:
            decryptor = WeChatDatabaseDecryptor(self._hex_key)
            if not decryptor.decrypt_database(shadow_db, decrypted_db):
                logger.debug("[Python未读监听] 解密 session_shadow 失败，密钥可能不匹配")
                return

            # 3. 读取未读消息数与最新摘要
            conn = sqlite3.connect(decrypted_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 自适应未读数字段（一般是 unread_count 或 unreadCount）
            cursor.execute("PRAGMA table_info(SessionTable)")
            columns = {row["name"].lower(): row["name"] for row in cursor.fetchall()}
            unread_col = columns.get("unread_count") or columns.get("unreadcount") or "unread_count"
            summary_col = columns.get("summary") or "summary"
            status_col = columns.get("status") or "status"

            cursor.execute(f"SELECT username, {unread_col} AS unread_val, {status_col} AS status_val, {summary_col} AS sum_val FROM SessionTable")
            rows = cursor.fetchall()

            for r in rows:
                username = r["username"] or ""
                if not username:
                    continue

                unread_val = int(r["unread_val"] or 0)
                status_val = int(r["status_val"] or 0)
                is_manual_unread = (status_val & 4096) != 0
                if unread_val == 0 and is_manual_unread:
                    unread_val = 1
                sum_val = str(r["sum_val"] or "").strip()

                # 如果是首次基准采样，仅做状态同步，不触发新消息回调
                if is_baseline:
                    # 💡 【启动未读强回特性】：若启动时该会话已有未读消息 (unread_val > 0)，
                    # 我们在 baseline 中故意将其前置未读数记为 0。这样在启动后的第一次轮询中，
                    # 就会因为 unread_val > prev_unread (0) 立即触发新消息回复，补回历史未读。
                    if unread_val > 0:
                        self._last_unread_counts[username] = 0
                    else:
                        self._last_unread_counts[username] = unread_val
                    self._last_summaries[username] = sum_val
                    continue

                prev_unread = self._last_unread_counts.get(username, 0)
                prev_summary = self._last_summaries.get(username, "")

                # 核心唤醒条件：
                # 1. 当前未读数 > 0 且发生了增加
                # 2. 或者当前未读数 > 0 且最近消息内容发生了变动
                is_new_message = False
                if unread_val > 0:
                    if unread_val > prev_unread or sum_val != prev_summary:
                        is_new_message = True

                # 更新状态
                self._last_unread_counts[username] = unread_val
                self._last_summaries[username] = sum_val

                if is_new_message and self._on_new_message:
                    # 模拟 WCDB monitor 结构触发回调
                    msg_payload = {
                        "session_id": username,
                        "content": sum_val or "[新消息]",
                        "is_group": username.endswith("@chatroom"),
                        "is_self": False,
                    }
                    logger.info("[Python未读监听] 🔔 检测到新消息 [%s]: %s", username, sum_val[:40])
                    try:
                        self._on_new_message(msg_payload)
                    except Exception as callback_err:
                        logger.error("[Python未读监听] 消息回调投递失败: %s", callback_err)

            # 在处理完并更新状态后，保存最新的文件及 WAL 属性以用于下一次轮询拦截
            self._last_mtime = mtime_db
            self._last_size = size_db
            self._last_mtime_wal = mtime_wal
            self._last_size_wal = size_wal
            if not is_baseline:
                self._first_poll_after_baseline = False

        except Exception as ex:
            logger.debug("[Python未读监听] 读取影子数据库出错: %s", ex)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            # 临时清理已解密的文件以防泄露
            if os.path.exists(decrypted_db):
                try:
                    os.unlink(decrypted_db)
                except Exception:
                    pass
            if os.path.exists(shadow_db):
                try:
                    os.unlink(shadow_db)
                except Exception:
                    pass
