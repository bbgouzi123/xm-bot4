import sqlite3
import logging
import os
from datetime import datetime
from src.crm.account_data import get_account_data_dir

logger = logging.getLogger(__name__)

class MessageDatabase:
    """账号隔离的自动回复指纹与会话持久化数据库"""
    
    def __init__(self, account_id: str):
        self.account_id = account_id
        db_dir = get_account_data_dir(account_id)
        self.db_path = os.path.join(db_dir, "session_state.db")
        self.init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init_db(self):
        try:
            with self._get_conn() as conn:
                # 存储已处理/已忽略的消息指纹
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS processed_fingerprints (
                        session_name TEXT NOT NULL,
                        fingerprint TEXT PRIMARY KEY,
                        last_message TEXT,
                        unread_count INTEGER,
                        processed_at TEXT NOT NULL
                    )
                """)
                # 索引优化
                conn.execute("CREATE INDEX IF NOT EXISTS idx_session_name ON processed_fingerprints (session_name)")
                
                # 存储最后一次回复时间，以便重启后依然能保留冷却限制
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_reply_state (
                        session_name TEXT PRIMARY KEY,
                        last_reply_time REAL NOT NULL,
                        last_fingerprint TEXT
                    )
                """)
                conn.commit()
            logger.info(f"[消息DB] 账号 '{self.account_id}' 数据库初始化成功: {self.db_path}")
        except Exception as e:
            logger.error(f"[消息DB] 账号 '{self.account_id}' 初始化数据库失败: {e}", exc_info=True)

    def is_fingerprint_exists(self, fingerprint: str) -> bool:
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM processed_fingerprints WHERE fingerprint = ?", (fingerprint,))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"[消息DB] 查询指纹失败: {e}")
            return False

    def add_fingerprint(self, session_name: str, fingerprint: str, last_message: str, unread_count: int):
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO processed_fingerprints (session_name, fingerprint, last_message, unread_count, processed_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (session_name, fingerprint, last_message, unread_count, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"[消息DB] 插入指纹失败: {e}")

    def delete_fingerprint(self, session_name: str, fingerprint: str):
        try:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM processed_fingerprints WHERE fingerprint = ?", (fingerprint,))
                conn.commit()
        except Exception as e:
            logger.error(f"[消息DB] 删除指纹失败: {e}")

    def delete_session_fingerprints(self, session_name: str):
        try:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM processed_fingerprints WHERE session_name = ?", (session_name,))
                conn.commit()
            logger.info(f"[消息DB] 已成功删除会话 '{session_name}' 的所有历史消息指纹")
        except Exception as e:
            logger.error(f"[消息DB] 删除会话指纹失败: {e}")

    def get_last_reply_time(self, session_name: str) -> float:
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT last_reply_time FROM session_reply_state WHERE session_name = ?", (session_name,))
                row = cursor.fetchone()
                return row[0] if row else 0.0
        except Exception as e:
            logger.error(f"[消息DB] 获取最后回复时间失败: {e}")
            return 0.0

    def update_reply_state(self, session_name: str, last_reply_time: float, last_fingerprint: str = None):
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO session_reply_state (session_name, last_reply_time, last_fingerprint)
                    VALUES (?, ?, ?)
                """, (session_name, last_reply_time, last_fingerprint))
                conn.commit()
        except Exception as e:
            logger.error(f"[消息DB] 更新回复状态失败: {e}")

    def load_all_reply_states(self) -> dict:
        states = {}
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT session_name, last_reply_time FROM session_reply_state")
                for row in cursor.fetchall():
                    states[row[0]] = row[1]
        except Exception as e:
            logger.error(f"[消息DB] 加载所有回复状态失败: {e}")
        return states

    def load_recent_fingerprints(self, session_name: str, limit: int = 200) -> set:
        fps = set()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT fingerprint FROM processed_fingerprints 
                    WHERE session_name = ? 
                    ORDER BY processed_at DESC LIMIT ?
                """, (session_name, limit))
                for row in cursor.fetchall():
                    fps.add(row[0])
        except Exception as e:
            logger.error(f"[消息DB] 加载最近指纹失败: {e}")
        return fps


class PersistentFingerprintSet(set):
    """具有本地数据库写入和同步删除能力的持久化 Set"""
    def __init__(self, seq, monitor, session_name):
        super().__init__(seq)
        self.monitor = monitor
        self.session_name = session_name

    def add(self, item):
        if item not in self:
            super().add(item)
            try:
                self.monitor.db.add_fingerprint(self.session_name, item, "", 0)
            except Exception as e:
                logger.error(f"[PersistentFingerprintSet] 异步写入 DB 失败: {e}")
                
            # 🌟 优化限制：当内存中去重指纹数超过 200 条时，主动淘汰最旧的一条，并从 SQLite 数据库同步删除，防范无上限越积越大
            if len(self) > 200:
                try:
                    oldest_item = list(self)[0]
                    super().discard(oldest_item)
                    self.monitor.db.delete_fingerprint(self.session_name, oldest_item)
                except Exception as clean_ex:
                    logger.debug(f"[PersistentFingerprintSet] 截断清理历史指纹异常: {clean_ex}")

    def discard(self, item):
        if item in self:
            super().discard(item)
            try:
                self.monitor.db.delete_fingerprint(self.session_name, item)
            except Exception as e:
                logger.error(f"[PersistentFingerprintSet] 异步删除 DB 失败: {e}")


class FingerprintsDict(dict):
    """支持惰性读取和持久化写入的指纹字典"""
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    def __getitem__(self, key):
        if key not in self:
            try:
                fps = self.monitor.db.load_recent_fingerprints(key)
            except Exception:
                fps = set()
            super().__setitem__(key, PersistentFingerprintSet(fps, self.monitor, key))
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if not isinstance(value, PersistentFingerprintSet):
            value = PersistentFingerprintSet(value, self.monitor, key)
        super().__setitem__(key, value)

    def setdefault(self, key, default=None):
        if key not in self:
            try:
                fps = self.monitor.db.load_recent_fingerprints(key)
            except Exception:
                fps = set()
            self[key] = PersistentFingerprintSet(fps, self.monitor, key)
        return self[key]

    def pop(self, key, default=None):
        res = super().pop(key, None)
        try:
            self.monitor.db.delete_session_fingerprints(key)
        except Exception as e:
            logger.error(f"[FingerprintsDict] pop 时从 DB 删除会话 '{key}' 指纹失败: {e}")
        return res if res is not None else default

    def __delitem__(self, key):
        if key in self:
            super().__delitem__(key)
        try:
            self.monitor.db.delete_session_fingerprints(key)
        except Exception as e:
            logger.error(f"[FingerprintsDict] __delitem__ 时从 DB 删除会话 '{key}' 指纹失败: {e}")


class LastReplyTimeDict(dict):
    """支持实时将回复时间戳落盘至 SQLite 的字典"""
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        try:
            self.monitor.db.update_reply_state(key, value)
        except Exception as e:
            logger.error(f"[LastReplyTimeDict] 更新最后回复状态至 DB 失败: {e}")
