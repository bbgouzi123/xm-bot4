import os
import time
import logging
import asyncio
import hashlib
import sqlite3
import tempfile
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class SessionDbUnreadSyncer:
    """
    解密读取 session.db 自动同步微信未读数到控制中心的引擎。
    作为独立运行的异步后台循环，与 UIA 监控协调。
    """
    def __init__(self, scanner, account_id: str, db_path: str, hex_key: str):
        self._scanner = scanner
        self._account_id = account_id
        self._db_path = db_path
        self._hex_key = hex_key
        self._started = False
        self._task = None
        # 🌟 用于过滤无修改同步以节省磁盘解密 IO 的指纹缓存
        self._last_mtime = 0.0
        self._last_size = 0
        self._last_mtime_wal = 0.0
        self._last_size_wal = 0
        # 🌟 用于跟踪活跃会话（未读数=0）在影子拷贝数据库中的最新消息摘要
        self._last_active_summaries: Dict[str, str] = {}
        self._last_decrypt_failed = False
        self._last_unread_counts: Dict[str, int] = {}
        self._first_sync = True

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        if self._started:
            return
        self._started = True
        
        target_loop = loop
        if not target_loop:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        if target_loop:
            is_loop_thread = False
            try:
                if asyncio.get_running_loop() is target_loop:
                    is_loop_thread = True
            except RuntimeError:
                pass

            if is_loop_thread:
                self._task = target_loop.create_task(self._loop())
            else:
                self._task = asyncio.run_coroutine_threadsafe(self._loop(), target_loop)
        else:
            self._task = asyncio.create_task(self._loop())
        print(f"[未读同步] 已启动未读数据库同步引擎 ({self._account_id})")
        logger.info(f"[未读同步] 已启动未读数据库同步引擎 ({self._account_id})")

    def stop(self):
        self._started = False
        if self._task:
            try:
                self._task.cancel()
            except Exception as e:
                logger.debug(f"[未读同步] 取消任务异常: {e}")
            self._task = None
        print(f"[未读同步] 已停止 ({self._account_id})")
        logger.info(f"[未读同步] 已停止 ({self._account_id})")

    async def _loop(self):
        # 延迟一下等待 WcdbMonitor 完成最初初始化
        await asyncio.sleep(2)
        while self._started:
            try:
                await self._sync_unread_once()
            except Exception as e:
                logger.error(f"[未读同步] 同步出错: {e}", exc_info=True)
            
            # 🌟 频率自适应：若 DLL 活跃则 1.0 秒轮询（无 I/O），若降级解密则 1.5 秒轮询以减轻磁盘负载
            wcdb_mon = getattr(self._scanner, "_wcdb_session_monitor", None)
            if wcdb_mon and wcdb_mon.is_active() and hasattr(wcdb_mon._monitor, "_handle") and wcdb_mon._monitor._handle:
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(1.5)

    async def _sync_unread_once(self):
        if not self._db_path or not self._hex_key or not os.path.exists(self._db_path):
            return

        # 🌟 [竞态修复] 若 account_id 在当年构造时为 default（WCDB 先于 set_active_account 启动），
        # 尝试实时修正为真实账号 ID，保证白名单读取安全
        if not self._account_id or self._account_id == "default":
            try:
                real = self._scanner.account_id
                if not real or real == "default":
                    from src.crm.account_data import get_active_account
                    real = get_active_account()
                if real and real != "default":
                    self._account_id = real
                    logger.info(f"[\u672a\u8bfb\u540c\u6b65] account_id \u5df2\u52a8\u6001\u4fee\u6b63\u4e3a: {self._account_id}")
                else:
                    logger.debug("[\u672a\u8bfb\u540c\u6b65] account_id \u4ecd\u4e3a default\uff0c\u8df3\u8fc7\u672c\u8f6e\u540c\u6b65")
                    return
            except Exception:
                return

        # 🌟 强力优化：如果 DLL 引擎活跃，直接从 DLL 内存提取会话未读数，实现毫秒级响应，避免所有磁盘拷贝与解密 IO
        wcdb_mon = getattr(self._scanner, "_wcdb_session_monitor", None)
        if wcdb_mon and wcdb_mon.is_active() and hasattr(wcdb_mon._monitor, "_handle") and wcdb_mon._monitor._handle:
            try:
                from .db_session_helper import get_latest_sessions_from_db
                parsed_sessions = get_latest_sessions_from_db(wcdb_mon, limit=100)
                if parsed_sessions is not None:
                    current_unreads = {}
                    SYSTEM_ACCOUNTS = {"brandsessionholder", "brandservicesessionholder", "filehelper", "mphelper", "weixin", "newsapp", "weibo", "qqmail", "fmessage", "tmessage", "qmessage", "qqsync", "floatbottle", "lbsapp", "shakeapp", "medianote", "qqfriend", "readerapp", "blogapp", "facebookapp", "masssendapp", "meishiapp", "feedsapp", "voipapp", "cardpackage", "voicevoipapp", "voiceinputapp", "linkedinplugin", "softdownload", "appbrand", "helper_entry", "officialaccounts"}
                    for s in parsed_sessions:
                        username = s.get("wxid") or ""
                        if not username or username in SYSTEM_ACCOUNTS or username.startswith("gh_"):
                            continue
                        current_unreads[username] = {
                            "unread_count": s.get("unread", 0),
                            "summary": s.get("lastMessage", ""),
                            "last_timestamp": 0
                        }
                    
                    if self._first_sync:
                        for u, info in current_unreads.items():
                            self._last_active_summaries[u] = info["summary"]
                            # 对启动时即为未读的消息，将其上一次未读数初始化为 0，确保在随后的轮询中可被正常拉起回复
                            if info.get("unread_count", 0) > 0:
                                self._last_unread_counts[u] = 0
                            else:
                                self._last_unread_counts[u] = info.get("unread_count", 0)
                        self._first_sync = False
                        logger.info(f"[未读同步-DLL] 已成功初始化会话摘要基准采样，共记录 {len(self._last_active_summaries)} 个会话")
                        # DLL路径：首次同步仅采样基准，直接 return 不 dispatch（避免首次就触发回复）
                        return

                    from .db_unread_dispatcher import process_and_dispatch_unreads
                    await process_and_dispatch_unreads(
                        current_unreads=current_unreads,
                        scanner=self._scanner,
                        account_id=self._account_id,
                        last_unread_counts=self._last_unread_counts,
                        last_active_summaries=self._last_active_summaries,
                    )
                    return
            except Exception as dll_ex:
                logger.debug(f"[未读同步-DLL] 内存获取失败，回退至影子拷贝: {dll_ex}")

        # 🌟 极速指纹拦截器：检测 session.db 与 WAL 属性。如果没有任何变化，说明未读状态未更新，直接返回，避免无意义的频繁解密
        try:
            stat_db = os.stat(self._db_path)
            mtime_db = stat_db.st_mtime
            size_db = stat_db.st_size

            wal_path = self._db_path + "-wal"
            mtime_wal, size_wal = 0.0, 0
            if os.path.exists(wal_path):
                stat_wal = os.stat(wal_path)
                mtime_wal = stat_wal.st_mtime
                size_wal = stat_wal.st_size

            if (mtime_db == self._last_mtime and size_db == self._last_size and
                mtime_wal == self._last_mtime_wal and size_wal == self._last_size_wal):
                return
        except Exception:
            mtime_db, size_db, mtime_wal, size_wal = 0.0, 0, 0.0, 0

        from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
        import shutil
        decryptor = WeChatDatabaseDecryptor(self._hex_key)
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xm_session_sync_") as tmp:
            tmp_path = tmp.name

        shadow_db = None
        try:
            loop = asyncio.get_running_loop()
            
            # 1. 影子拷贝以避免微信数据库文件的独占锁定及冲突
            shadow_db = tmp_path + "_shadow"
            try:
                await loop.run_in_executor(
                    None, lambda: shutil.copy2(self._db_path, shadow_db)
                )
            except Exception as copy_ex:
                logger.debug(f"[未读同步] 影子拷贝失败: {copy_ex}，尝试直接解密主库")
                shadow_db = self._db_path

            success = await loop.run_in_executor(
                None, lambda: decryptor.decrypt_database(shadow_db, tmp_path)
            )
            if not success:
                if not self._last_decrypt_failed:
                    logger.error(f"[未读同步] 解密数据库失败: {decryptor.last_result.get('error') or '未知错误'}")
                    self._last_decrypt_failed = True
                return

            if self._last_decrypt_failed:
                logger.info("[未读同步] 数据库解密已恢复正常")
                self._last_decrypt_failed = False

            conn = sqlite3.connect(tmp_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            SYSTEM_ACCOUNTS = {"brandsessionholder", "brandservicesessionholder", "filehelper", "mphelper", "weixin", "newsapp", "weibo", "qqmail", "fmessage", "tmessage", "qmessage", "qqsync", "floatbottle", "lbsapp", "shakeapp", "medianote", "qqfriend", "readerapp", "blogapp", "facebookapp", "masssendapp", "meishiapp", "feedsapp", "voipapp", "cardpackage", "voicevoipapp", "voiceinputapp", "linkedinplugin", "softdownload", "appbrand", "helper_entry", "officialaccounts"}

            # 🌟 首次同步时，执行一次全量会话的基准摘要采样，防止将启动前的历史已读消息误触发回复
            if self._first_sync:
                try:
                    cursor.execute("SELECT username, summary, unread_count FROM SessionTable WHERE is_hidden = 0")
                    for r in cursor.fetchall():
                        u = r["username"] or ""
                        if u and u not in SYSTEM_ACCOUNTS and not u.startswith("gh_"):
                            self._last_active_summaries[u] = r["summary"] or ""
                            # 对启动时即为未读的消息，将其上一次未读数初始化为 0，确保在第一轮扫描中可被正常拉起回复
                            u_cnt = r["unread_count"] or 0
                            if u_cnt > 0:
                                self._last_unread_counts[u] = 0
                            else:
                                self._last_unread_counts[u] = u_cnt
                    self._first_sync = False
                    logger.info(f"[未读同步] 已成功初始化会话摘要基准采样，共记录 {len(self._last_active_summaries)} 个会话")
                except Exception as base_ex:
                    logger.error(f"[未读同步] 初始化基准采样异常: {base_ex}")

            active_username = None
            try:
                import app.state as app_state
                active_username = getattr(app_state, 'active_chat_wxid', None)
            except Exception:
                pass

            # 🌟 优化 SQL：不仅拉取未读消息、当前活跃窗口，还拉取过去 5 分钟内活跃过的会话（支持手机端秒点已读同步穿透）
            min_ts = int(time.time()) - 300
            if active_username:
                sql = "SELECT username, unread_count, status, summary, last_timestamp FROM SessionTable WHERE (unread_count > 0 OR (status & 4096) != 0 OR username = ? OR last_timestamp > ?) AND is_hidden = 0"
                params = (active_username, min_ts)
            else:
                sql = "SELECT username, unread_count, status, summary, last_timestamp FROM SessionTable WHERE (unread_count > 0 OR (status & 4096) != 0 OR last_timestamp > ?) AND is_hidden = 0"
                params = (min_ts,)

            cursor.execute(sql, params)
            rows = cursor.fetchall()

            current_unreads = {}
            for r in rows:
                username = r["username"] or ""
                if not username or username in SYSTEM_ACCOUNTS or username.startswith("gh_"):
                    continue
                
                db_unread = r["unread_count"] or 0
                status_val = r["status"] or 0
                is_manual_unread = (status_val & 4096) != 0
                
                eff_unread = db_unread
                if eff_unread == 0 and is_manual_unread:
                    eff_unread = 1

                current_unreads[username] = {
                    "unread_count": eff_unread,
                    "summary": r["summary"] or "",
                    "last_timestamp": r["last_timestamp"] or 0
                }
            conn.close()

            from .db_unread_dispatcher import process_and_dispatch_unreads
            await process_and_dispatch_unreads(
                current_unreads=current_unreads,
                scanner=self._scanner,
                account_id=self._account_id,
                last_unread_counts=self._last_unread_counts,
                last_active_summaries=self._last_active_summaries,
            )

            # 只有在完整处理成功后才更新指纹，以防解密失败导致卡死在旧状态
            self._last_mtime = mtime_db
            self._last_size = size_db
            self._last_mtime_wal = mtime_wal
            self._last_size_wal = size_wal

        finally:
            if shadow_db and shadow_db != self._db_path and os.path.exists(shadow_db):
                try:
                    os.unlink(shadow_db)
                except Exception:
                    pass
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
