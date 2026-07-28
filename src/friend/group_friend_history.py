import os
import sqlite3
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Set, List, Dict, Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = Path.home() / ".xm-ai-bot"
_DB_PATH = _LOCAL_DIR / "group_add_friend.db"


def _get_conn():
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    # 启用 WAL 模式，避免多线程同时读写时发生数据库锁死
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """创建并初始化本地 SQLite 数据库表及唯一索引"""
    try:
        with _get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS group_friend_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_name TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    status TEXT NOT NULL,
                    added_at TEXT NOT NULL
                )
            """)
            # 删除原普通索引，建立唯一索引防止云同步合并时产生脏数据
            conn.execute("DROP INDEX IF EXISTS idx_group_nickname")
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_group_nickname_uniq 
                ON group_friend_history (group_name, nickname)
            """)
            conn.commit()
        logger.info(f"[群加历史] SQLite 数据库初始化成功: {str(_DB_PATH)}")
    except Exception as e:
        logger.error(f"[群加历史] 初始化 SQLite 数据库表结构失败: {e}")


# 模块加载时自动执行数据库初始化与建表
init_db()


def sync_from_cloud():
    """从云端服务器数据库拉取群好友加人历史，合并到本地 SQLite 数据库中（换电脑数据不丢）"""
    try:
        from src.utils.cloud_sync import get_cloud_client
        client = get_cloud_client()
        cloud_history = client.pull_group_add_friend_history()

        if cloud_history and isinstance(cloud_history, list):
            with _get_conn() as conn:
                for row in cloud_history:
                    # 使用 INSERT OR IGNORE 配合唯一索引，自动跳过重复的数据，无缝融合同步
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO group_friend_history (group_name, nickname, status, added_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (row.get("group_name"), row.get("nickname"), row.get("status"), row.get("added_at"))
                    )
                conn.commit()
            logger.info(f"[群加历史] 🔄 成功从云服务器拉取并合并了 {len(cloud_history)} 条群加好友历史记录")
    except Exception as e:
        logger.debug(f"[群加历史] 从云服务器拉取历史记录失败 (可能未登录或离线): {e}")


def push_to_cloud():
    """将本地 SQLite 数据库中群好友加人历史全量推送到云端数据库保存"""
    try:
        from src.utils.cloud_sync import get_cloud_client
        client = get_cloud_client()

        history_list = []
        with _get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT group_name, nickname, status, added_at FROM group_friend_history")
            for r in cursor.fetchall():
                history_list.append({
                    "group_name": r[0],
                    "nickname": r[1],
                    "status": r[2],
                    "added_at": r[3]
                })

        if history_list:
            client.sync_group_add_friend_history(history_list)
            logger.info(f"[群加历史] 🔄 成功将本地 {len(history_list)} 条历史记录推送到云端服务器数据库")
    except Exception as e:
        logger.debug(f"[群加历史] 推送历史记录到云服务器数据库失败: {e}")


def _async_push():
    """开启后台守护线程异步推送，防止阻塞加粉主线程和 UI 交互"""
    threading.Thread(target=push_to_cloud, daemon=True, name="group-history-push").start()


def add_history_record(group_name: str, nickname: str, status: str):
    """向本地 SQLite 写入群好友加人历史记录，并异步推送到云服务器数据库"""
    try:
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO group_friend_history (group_name, nickname, status, added_at)
                VALUES (?, ?, ?, ?)
                """,
                (group_name, nickname, status, datetime.now().isoformat())
            )
            conn.commit()
        # 异步推送到云服务器
        _async_push()
    except Exception as e:
        logger.error(f"[群加历史] 写入 SQLite 历史记录失败: {e}")


def get_processed_names(group_name: str) -> Set[str]:
    """获取该群已申请或已是好友的所有已处理成员昵称（由 SQLite 提供）"""
    try:
        with _get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT nickname FROM group_friend_history WHERE group_name = ?",
                (group_name,)
            )
            rows = cursor.fetchall()
            return {r[0] for r in rows}
    except Exception as e:
        logger.error(f"[群加历史] 从 SQLite 查询已处理名单失败: {e}")
        return set()


def get_today_added_count() -> int:
    """获取今日所有微信群累计成功申请的好友数量（由 SQLite 提供）"""
    try:
        today_prefix = datetime.now().strftime("%Y-%m-%d") + "%"
        with _get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM group_friend_history WHERE status = 'success' AND added_at LIKE ?",
                (today_prefix,)
            )
            row = cursor.fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.error(f"[群加历史] 从 SQLite 统计今日加人总量失败: {e}")
        return 0


def get_group_history_list(group_name: str, limit: int = 50) -> List[Dict[str, Any]]:
    """查询指定群聊最新的添加历史记录"""
    try:
        with _get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT nickname, status, added_at FROM group_friend_history WHERE group_name = ? ORDER BY added_at DESC LIMIT ?",
                (group_name, limit)
            )
            rows = cursor.fetchall()
            return [
                {
                    "nickname": r[0],
                    "status": r[1],
                    "added_at": r[2]
                }
                for r in rows
            ]
    except Exception as e:
        logger.error(f"[群加历史] 从 SQLite 查询历史列表失败: {e}")
        return []
