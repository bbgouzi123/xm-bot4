import logging
import os
import threading
from typing import List, Dict, Optional, Callable
from src.uia.driver import WeChatDriver
from .db_reader import WeChatDbReader
from .ws_listener import WeChatWsListener
from .dat_decryptor import try_decrypt_wechat_dat

logger = logging.getLogger("WeChat4xDriver")

class WeChat4xDriver(WeChatDriver):
    """
    WeChat4xDriver handles Windows WeChat 4.1.7 (NT architecture) messaging.
    
    Uses:
    - Scheme A (Decrypting WAL SQLCipher DB Reader) or
    - Scheme B (Real-time Frida Hook via WebSockets)
    for high-performance read and listen operations.
    
    Delegates all writing/sending and control operations (e.g. ChatWith, SendMsg, SendFiles)
    directly to the parent WeChatDriver UIA automation class to reuse the verified UIA pipeline.
    """
    def __init__(self, scheme: str = "A", 
                 db_path: Optional[str] = None, 
                 key_hex: Optional[str] = None, 
                 ws_port: int = 9001):
        super().__init__()
        self.scheme = scheme.upper()
        self.db_path = db_path
        self.key_hex = key_hex
        self.ws_port = ws_port
        
        self.db_reader: Optional[WeChatDbReader] = None
        self.ws_listener: Optional[WeChatWsListener] = None
        
        # Buffer to cache incoming messages received via background reader/listener thread
        self.msg_buffer: List[Dict] = []
        self.buffer_lock = threading.Lock()
        self._listener_callback: Optional[Callable[[Dict], None]] = None

    def initialize_4x(self, callback: Optional[Callable[[Dict], None]] = None):
        """Initialize the requested reading scheme (A or B) in a background thread."""
        self._listener_callback = callback
        
        if self.scheme == "A":
            if not self.db_path or not self.key_hex:
                logger.error("[WeChat4xDriver] Missing db_path or key_hex for Scheme A.")
                return False
            
            self.db_reader = WeChatDbReader(self.db_path, self.key_hex)
            if self.db_reader.connect():
                # Spin off database poller thread
                t = threading.Thread(
                    target=self.db_reader.poll_new_messages,
                    args=(self._on_message_received,),
                    daemon=True
                )
                t.start()
                logger.info("[WeChat4xDriver] Scheme A (DB Poller) reader thread started.")
                return True
            else:
                logger.error("[WeChat4xDriver] Failed to connect DB Reader. Reverting schemes...")
                return False
                
        elif self.scheme == "B":
            self.ws_listener = WeChatWsListener(port=self.ws_port)
            self.ws_listener.start(self._on_message_received)
            logger.info(f"[WeChat4xDriver] Scheme B (Frida WS Listener) started on port {self.ws_port}.")
            return True
            
        else:
            logger.error(f"[WeChat4xDriver] Unsupported scheme config: {self.scheme}")
            return False

    def _on_message_received(self, msg: Dict):
        """Internal callback invoked when a new message is fetched or received."""
        # 如果是图片消息，且包含 xml，尝试进行本地加密 DAT 文件解密
        if msg.get("msg_type") == 3 or (msg.get("content") and "<msg>" in str(msg.get("content"))):
            try:
                decrypted_url = try_decrypt_wechat_dat(str(msg.get("content", "")), self.db_path, self._wxid)
                if decrypted_url:
                    msg["content"] = decrypted_url
                    logger.info(f"[WeChat4xDriver] 已成功将接收到的加密图片 DAT 解密为: {decrypted_url}")
            except Exception as decrypt_ex:
                logger.debug(f"[WeChat4xDriver] 自动解密 DAT 图片异常: {decrypt_ex}")

        # 1. Forward directly to the registered active driver callback if present
        if self._listener_callback:
            try:
                self._listener_callback(msg)
            except Exception as e:
                logger.error(f"[WeChat4xDriver] Registered callback invocation error: {e}")
        
        # 2. Append to general message buffer for pull-based requests
        with self.buffer_lock:
            self.msg_buffer.append(msg)
            # Keep buffer size bounded
            if len(self.msg_buffer) > 1000:
                self.msg_buffer.pop(0)

# Image decryption logic moved to dat_decryptor.py


    # ==================== Overriding Read/Query APIs ====================

    def get_all_messages(self, parse_file: bool = False,
                        context_count: int = 20,
                        session_name: str = "",
                        scroll_to_bottom: bool = False) -> List:
        """
        Override get_all_messages to return messages fetched from db/hook pipeline.
        Falls back to standard UIA implementation only if background listener fails or is inactive.
        """
        is_current_foreground = False
        try:
            import app.state as app_state
            active_name = getattr(app_state, 'active_chat_name', None)
            active_wxid = getattr(app_state, 'active_chat_wxid', None)
            if session_name:
                is_current_foreground = (session_name == active_name or session_name == active_wxid)
        except Exception:
            pass

        # 🚀 数据库优先读取方案：如果不需要物理文件解析 (如截图/翻译)，且 WCDB 双引擎在线，
        # 则无条件以数据库读取数据为准，彻底杜绝 UIA 遍历与物理采色！
        if not parse_file:
            from src.crm.account_data import get_active_account
            active_acct = get_active_account()
            if active_acct and active_acct != 'default':
                is_db_online = False
                db_msgs = []
                target_wxid = session_name
                try:
                    from src.utils.contacts_cache import contacts_cache
                    friends = contacts_cache.get_friends(active_acct)
                    for f in friends:
                        if f.get("name") == session_name or f.get("remark") == session_name:
                            target_wxid = f.get("wxid")
                            break
                    if target_wxid == session_name:
                        groups = contacts_cache.get_groups(active_acct)
                        for g in groups:
                            if g.get("name") == session_name:
                                target_wxid = g.get("wxid")
                                break
                except Exception as e_cache:
                    logger.debug(f"[WeChat4xDriver] 从缓存查找 '{session_name}' 的 wxid 失败: {e_cache}")

                try:
                    import app.state as app_state
                    session_monitor = getattr(app_state.monitor, "_wcdb_session_monitor", None)
                    if session_monitor and session_monitor.is_active():
                        is_db_online = True
                        db_msgs = session_monitor.get_latest_messages(target_wxid, limit=context_count)
                except Exception:
                    pass

                if not db_msgs:
                    try:
                        from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
                        monitor = get_wcdb_monitor(active_acct)
                        if monitor and monitor.is_active():
                            is_db_online = True
                            db_msgs = monitor.get_latest_messages(target_wxid, limit=context_count)
                    except Exception:
                        pass

                if is_db_online and db_msgs:
                    formatted = []
                    for m in reversed(db_msgs):
                        sender = "自己" if m["is_self"] else session_name
                        formatted.append((sender, m["content"]))
                    logger.info(f"[WeChat4xDriver] 🚀 读取数据库消息为准 => 会话='{session_name}' ({target_wxid})，获取 {len(formatted)} 条记录")
                    return formatted

        # 如果需要解析媒体文件（如截图图片/翻译语音），或者微信正处于该会话前台，且数据库未就绪，使用 UIA 扫描以执行实时的图片控件截图或语音翻译
        if parse_file or is_current_foreground:
            uia_msgs = super().get_all_messages(parse_file, context_count, session_name, scroll_to_bottom)
            if uia_msgs:
                return uia_msgs

        # Filter message buffer matches by sender/session
        matched_msgs = []
        with self.buffer_lock:
            for msg in self.msg_buffer:
                if not session_name or msg.get("sender_wxid") == session_name:
                    sender = "自己" if msg.get("is_self") else session_name
                    matched_msgs.append((sender, msg.get("content", "")))
        
        if matched_msgs:
            return matched_msgs[-context_count:]
            
        # Fallback to parent UIA parsing logic if no records exist in buffer
        logger.debug("[WeChat4xDriver] Message buffer empty. Falling back to parent UIA scanner.")
        return super().get_all_messages(parse_file, context_count, session_name, scroll_to_bottom)

    def shutdown_4x(self):
        """Gracefully release all decryption sockets and listener ports."""
        if self.db_reader:
            self.db_reader.stop()
        if self.ws_listener:
            self.ws_listener.stop()
        logger.info("[WeChat4xDriver] Clean driver shutdown completed.")
