# wcdb_session_monitor.py
# WCDB 双引擎协调器
# 
# 职责：
#   1. 提取 WCDB 密钥并启动 WcdbMonitor。
#   2. 实时感知新消息并分类：
#      - 白名单：注入到 RPA 自动回复队列。
#      - 被拦截（黑名单）：实时向控制中心广播，毫秒级展现卡片。
#   3. 后台循环同步未读已读状态，1.5 秒内自动销毁消除已读拦截任务。
import asyncio
import hashlib
import logging
import os
import time
from typing import Optional, TYPE_CHECKING, List

if TYPE_CHECKING:
    from src.monitor.chat_monitor.message_scanner import MessageScannerLogic

logger = logging.getLogger(__name__)

ENV_WCDB_PATH = "WCDB_SESSION_DB_PATH"
ENV_WCDB_KEY = "WCDB_HEX_KEY"

# 系统账号黑名单：这些会话 ID 不参与自动回复
SYSTEM_ACCOUNTS = {
    "brandsessionholder", "brandservicesessionholder", "filehelper",
    "mphelper", "weixin", "newsapp", "weibo", "qqmail", "fmessage",
    "tmessage", "qmessage", "qqsync", "floatbottle", "lbsapp", "shakeapp",
    "medianote", "qqfriend", "readerapp", "blogapp", "facebookapp",
    "masssendapp", "meishiapp", "feedsapp", "voipapp", "cardpackage",
    "voicevoipapp", "voiceinputapp", "linkedinplugin", "softdownload",
    "appbrand", "helper_entry", "officialaccounts"
}


from .wcdb_monitor_helpers import _persist_to_instance_settings, _is_multi_open_mode

class WcdbSessionMonitor:
    # WCDB 双引擎协调器实例（每个 ChatMonitor 实例对应一个）。
    def __init__(self, scanner: "MessageScannerLogic"):
        self._scanner = scanner
        self._monitor = None
        self._key_extractor = None
        self._started = False
        self._account_id: Optional[str] = None
        self._unread_syncer = None
        self._wxid: Optional[str] = None
        self._loop = None
        self._is_native_dll_active: bool = False  # True=DLL激活; False=Python降级

    def start(self, asyncio_loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
        if self._started:
            return True

        self._loop = asyncio_loop
        if not self._loop:
            try: self._loop = asyncio.get_event_loop()
            except Exception: pass

        try:
            from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
            from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
        except ImportError as e:
            logger.warning(f"[WCDB协调器] 模块导入失败，WCDB 双引擎不可用: {e}")
            return False

        scanner = self._scanner
        self._account_id = scanner.account_id
        # 若此时账号 ID 仍是 default（UIA 未连接），尝试从全局活跃账号补偿
        if not self._account_id or self._account_id == "default":
            try:
                from src.crm.account_data import get_active_account
                _fallback = get_active_account()
                if _fallback and _fallback != "default":
                    self._account_id = _fallback
                    logger.info(f"[WCDB协调器] start() 时 account_id 修正为活跃账号: {self._account_id}")
            except Exception: pass
        
        self._wxid = getattr(scanner.driver, 'bot_wxid', None) or getattr(scanner.driver, '_wxid', None) or ""
        if not self._wxid:
            try:
                from src.utils.instance_manager import InstanceManagerV2
                _aid = InstanceManagerV2.get_instance().get_active_instance_id()
                if _aid and _aid.startswith("wxid_"): self._wxid = _aid
            except Exception: pass
        if not self._wxid:
            try:
                from app.state import account_manager as am
                _pi = getattr(am, "primary_instance", None)
                if _pi and _pi.wxid: self._wxid = _pi.wxid
            except Exception: pass

        from src.utils.wechat_key_store import get_persisted_wechat_key, persist_wechat_key
        hex_key = get_persisted_wechat_key(self._wxid)
        if not hex_key and not _is_multi_open_mode():
            _fb = get_persisted_wechat_key()
            if _fb and len(_fb) == 64:
                from src.utils.wechat_key_store import verify_wechat_key
                if verify_wechat_key(_fb, self._wxid):
                    logger.info(f"[WCDB协调器] 单账号模式：成功校验全局密钥，自动执行账号({self._wxid})绑定")
                    persist_wechat_key(_fb, self._wxid)
                    hex_key = _fb
        elif not hex_key:
            logger.info(f"[WCDB协调器] 多开模式，禁用 last_key fallback，等待 Hook 实时提取 (wxid={self._wxid})")

        if not hex_key:
            from src.wechat_4x.wcdb_monitor_helpers import resolve_target_pid
            target_pid = resolve_target_pid(self._wxid)
            if target_pid:
                logger.info(f"[WCDB协调器] 成功定位到 PID={target_pid}")

            # 🌟 [关键防护] 检查目标 PID 是否正在被 auto_get_key 独占扫码登录。
            # 如果是，代表正在扫码阶段，此时 wcdb_key_extractor 进来只会产生干扰或超时卸载 Hook。
            # 这里应立即退让跳过，待登录完成后由主流程自动更新上线。
            try:
                import sys
                _excl_pids3 = getattr(sys, "_xm_bot4_exclusive_pids", set())
                if target_pid and target_pid in _excl_pids3:
                    logger.info(f"[WCDB协调器] PID={target_pid} 正被 auto_get_key 独占，本次退让跳过")
                    return False
            except Exception:
                pass

            self._key_extractor = get_wcdb_key_extractor()
            hex_key = self._key_extractor.get_key(timeout_s=20.0, pid=target_pid) or ""
            if hex_key:
                persist_wechat_key(hex_key, self._wxid)
            else:
                logger.warning("[WCDB协调器] 密钥提取失败，WCDB 监听不启动。")
                return False

        from .db_match_helper import auto_detect_db_path
        expected_wx = self._wxid if (self._wxid and not self._wxid.startswith("account_") and self._wxid != "default") else None
        db_path = auto_detect_db_path(hex_key, expected_wx)
        if not db_path:
            env_path = os.environ.get(ENV_WCDB_PATH, "")
            if env_path and os.path.exists(env_path):
                from src.utils.wechat_key_store import clean_wxid
                clean_curr = clean_wxid(self._wxid)
                if clean_curr and clean_curr in clean_wxid(env_path):
                    db_path = env_path
                    logger.info(f"[WCDB协调器] 使用环境变量 db 路径: {db_path}")
                else:
                    logger.warning(f"[WCDB协调器] 拦截不匹配账号的环境变量残留路径: {env_path}")
        if not db_path or not os.path.exists(db_path):
            logger.info("[WCDB协调器] 未找到 session.db，WCDB 监听不启动。")
            try:
                from src.utils.wechat_key_store import clear_persisted_wechat_key
                clear_persisted_wechat_key(self._wxid)
            except Exception as e_clear:
                logger.error(f"[WCDB协调器] 清理可能错绑的失效密钥失败: {e_clear}")
            return False

        self._db_path = db_path
        self._hex_key = hex_key

        # 3. 启动监听
        monitor = get_wcdb_monitor(self._account_id)
        self._monitor = monitor
        ok = monitor.start(
            db_path=db_path,
            hex_key=hex_key,
            wxid=self._wxid,
            on_new_message=self._on_wcdb_message,
            loop=asyncio_loop
        )

        if not ok:
            logger.info("[WCDB协调器] DLL 双引擎不可用，尝试启动纯 Python 影子拷贝监听器...")
            try:
                from .db_unread_monitor import SessionDbFallbackMonitor
                fallback = SessionDbFallbackMonitor()
                if fallback.start(db_path, hex_key, self._on_wcdb_message):
                    self._monitor = fallback
                    ok = True
                    try:
                        from .db_message_monitor import MessageDbFallbackMonitor
                        self._msg_fallback_monitor = MessageDbFallbackMonitor(self._account_id)
                        self._msg_fallback_monitor.start(db_path, hex_key)
                    except Exception as me:
                        logger.error(f"[WCDB协调器] 启动备用消息历史监控器失败: {me}")
            except Exception as fe:
                logger.error("[WCDB协调器] 启动备用未读监测器异常: %s", fe)
        else:
            self._is_native_dll_active = True

        if ok:
            self._started = True
            try:
                from .db_unread_syncer import SessionDbUnreadSyncer
                self._unread_syncer = SessionDbUnreadSyncer(scanner, self._account_id, db_path, hex_key)
                self._unread_syncer.start(asyncio_loop)
            except Exception as se:
                logger.error(f"[WCDB协调器] 启动未读同步引擎异常: {se}")
            try:
                from .db_contact_syncer import sync_contacts_from_db
                db_storage_dir = os.path.dirname(os.path.dirname(db_path))
                import threading
                threading.Thread(
                    target=sync_contacts_from_db,
                    args=(db_storage_dir, hex_key, self._account_id),
                    daemon=True,
                    name=f"wcdb-startup-sync-{self._account_id}"
                ).start()
            except Exception as e:
                logger.warning(f"[WCDB协调器] 异步启动数据库通讯录同步失败: {e}")

            # WCDB 启动成功后，回写 db_path 和密钥到 instance_settings，使弹窗能正确回显
            if self._wxid:
                _persist_to_instance_settings(self._wxid, db_path, hex_key)

        return ok

    def stop(self):
        if hasattr(self, "_msg_fallback_monitor") and self._msg_fallback_monitor:
            try: self._msg_fallback_monitor.stop()
            except Exception: pass
            self._msg_fallback_monitor = None
        if self._unread_syncer:
            try: self._unread_syncer.stop()
            except Exception: pass
            self._unread_syncer = None
        if self._monitor:
            self._monitor.stop()
        self._started = False

    def is_native_active(self) -> bool:
        return self._started and self._is_native_dll_active and self._monitor is not None and self._monitor.is_active()

    def is_active(self) -> bool:
        return self._started and self._monitor is not None and self._monitor.is_active()

    def _on_wcdb_message(self, msg: dict):
        # WcdbMonitor 发现新消息时的回调（在独立线程中调用）。
        # 分流处理：允许自动回复的消息注入队列，被拦截的消息实时向控制中心广播。
        session_id = msg.get("session_id", "")
        content = msg.get("content", "")
        is_group = msg.get("is_group", False)
        is_self = msg.get("is_self", False)

        # 🌟 [双通道竞争修复] DLL 降级时 SessionDbFallbackMonitor 与 db_unread_syncer 并存；
        # syncer 具备更完善的历史回扫和 @所有人 检测，本回调在 syncer 活跃时仅广播拦截状态。
        _unread_syncer_active = self._unread_syncer is not None and getattr(self._unread_syncer, '_started', False)

        if is_self or not session_id or not content.strip() or session_id in SYSTEM_ACCOUNTS or session_id.startswith("gh_"):
            return

        live_account_id = self._account_id
        if not live_account_id or live_account_id == "default":
            try:
                from src.crm.account_data import get_active_account
                _real = get_active_account()
                if _real and _real != "default":
                    live_account_id = _real
                    self._account_id = live_account_id
            except Exception:
                pass
        if not live_account_id or live_account_id == "default":
            return

        # 💡 如果用户关闭了自动聊天（auto_reply_enabled == False），直接跳过处理与分发，防止误显卡片与卡5%进度
        try:
            from src.api.config_api import _load_configs
            from src.api.instance_settings_api import load_instance_settings
            if not (_load_configs() or {}).get("auto_reply_enabled", True):
                return
            if not (load_instance_settings(live_account_id) or {}).get("auto_reply_enabled", True):
                return
        except Exception:
            pass

        from src.wechat_4x.wcdb_monitor_helpers import resolve_display_name
        name = resolve_display_name(live_account_id, session_id, is_group)
        if not name:
            return
        from src.monitor.chat_monitor.message_scanner import MessageScannerLogic
        if name in getattr(MessageScannerLogic, "SYSTEM_SESSIONS", set()):
            return

        logger.info(f"[WCDB双引擎] 🔔 检测到新消息 [{name}]: {content[:60]}")

        is_blocked = False
        try:
            from src.wechat_4x.wcdb_monitor_helpers import check_is_blocked
            is_blocked, _ = check_is_blocked(
                self._scanner, live_account_id, name, session_id, is_group, content, self._loop
            )
        except Exception as filter_ex:
            logger.warning(f"[WCDB协调器] 过滤判定异常: {filter_ex}")

        try:
            loop = self._loop or (asyncio.get_event_loop() if not self._loop else None)
            if is_blocked:
                logger.info(f"[WCDB双引擎] 🚫 会话 '{name}' (wxid={session_id}) 被拦截，仅广播不回复")
                if loop and loop.is_running():
                    from src.wechat_4x.wcdb_monitor_helpers import broadcast_blocked_session
                    asyncio.run_coroutine_threadsafe(broadcast_blocked_session(self._scanner, name, is_group, session_id, content), loop)
                else:
                    self._scanner._update_overlay_and_broadcast_whitelist(name, is_group=is_group, wxid=session_id)
                    from src.utils.websocket_manager import ws_manager
                    task_key = f"whitelist_{session_id}"
                    if hasattr(ws_manager, "task_cache") and task_key in ws_manager.task_cache:
                        ws_manager.task_cache[task_key]["data"]["incoming_msg"] = content
            elif _unread_syncer_active:
                logger.debug(f"[WCDB双引擎] 会话 '{name}' 入队由 db_unread_syncer 负责，本回调跳过注入")
                if loop and loop.is_running():
                    from src.utils.websocket_manager import ws_manager
                    asyncio.run_coroutine_threadsafe(
                        ws_manager.broadcast_task_update(
                            task_id=f"auto_reply_{session_id or name}", task_type="自动回复",
                            status="running", progress=5, total=100, message="收到新消息，正在处理...",
                            friend_name=name, friend_wxid=session_id, incoming_msg=content, is_group=is_group
                        ), loop
                    )
            else:
                from src.wechat_4x.wcdb_monitor_helpers import inject_to_reply_queue, make_fingerprint
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        inject_to_reply_queue(self._scanner, name, content, is_group, wxid=session_id), loop
                    )
                else:
                    fp = make_fingerprint(name, content)
                    self._scanner._enqueue_to_reply_buffer(
                        name=name, last_msg=content, is_group=is_group,
                        user_name=name, is_at=False, fp=fp, wxid=session_id
                    )
        except Exception as e:
            logger.error(f"[WCDB协调器] 消息分发分流失败: {e}", exc_info=True)

    def get_latest_sessions_from_db(self, limit: int = 50) -> Optional[List[dict]]:
        from .db_session_helper import get_latest_sessions_from_db as get_sessions
        return get_sessions(self, limit)

    def get_latest_messages(self, session_id: str, limit: int = 10) -> list:
        try:
            m = self._monitor
            if m and hasattr(m, "get_latest_messages"):
                res = m.get_latest_messages(session_id, limit)
                if res: return res
            fb = getattr(self, "_msg_fallback_monitor", None)
            if fb: return fb.get_latest_messages(session_id, limit)
        except Exception as e:
            logger.debug(f"[WCDB消息诊断] 获取最近消息异常: {e}")
        return []
