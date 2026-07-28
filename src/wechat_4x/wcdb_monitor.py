"""
wcdb_monitor.py
WCDB 实时消息监听器（双引擎核心感知层）

通过 wcdb_api.dll（与 WeFlow 同款）打开微信 NT 加密数据库，
实时感知新消息（命名管道 <1s；轮询降级 ~1s），完整覆盖所有会话。
"""
import ctypes
import json
import logging
import time
import threading
from typing import Callable, Dict, Optional

from src.wechat_4x.wcdb_dll_loader import load_wcdb_dll

logger = logging.getLogger(__name__)

from src.wechat_4x.wcdb_diagnostics import diagnose_wcdb_init_failure as _diagnose_wcdb_init_failure


class WcdbMonitor:
    """
    WCDB 数据库实时消息监听器。
    优先使用 DLL 命名管道模式（<1s 延迟），
    不可用时自动降级为 1 秒轮询模式。
    """

    def __init__(self):
        self._dll_funcs: Optional[dict] = None
        self._handle: Optional[int] = None
        self._initialized = False
        self._running = False
        self._on_new_message: Optional[Callable[[Dict], None]] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._last_poll_max_id: Dict[str, int] = {}
        self._poll_cycle = 0
        self._lock = threading.RLock()


    def _load_dll(self) -> bool:
        if self._initialized:
            return True
        funcs = load_wcdb_dll()
        if not funcs:
            return False
        self._dll_funcs = funcs
        self._initialized = True
        return True

    def __getattr__(self, name):
        """代理 DLL 绑定的接口，兼容原有的 self._xxxx 访问方式"""
        if self._dll_funcs and name in self._dll_funcs:
            return self._dll_funcs[name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def _open_db(self, db_path: str, hex_key: str, wxid: str) -> bool:
        with self._lock:
            rc = self.wcdb_init()
            if rc != 0:
                wcdb_free = self._dll_funcs.get("wcdb_free_string") if self._dll_funcs else None
                _diagnose_wcdb_init_failure(rc, self._dll_funcs, wcdb_free)
                return False

            handle_out = ctypes.c_int64(0)
            rc = self.wcdb_open_account(db_path.encode("utf-8"), hex_key.encode("utf-8"), ctypes.byref(handle_out))
            if rc != 0:
                try:
                    import ctypes
                    err_code = ctypes.windll.kernel32.GetLastError()
                    err_msg = ctypes.FormatError(err_code)
                    logger.error(f"[WCDB监听] wcdb_open_account 失败 rc={rc}, Windows GetLastError={err_code} ({err_msg})")
                except Exception as e:
                    logger.error(f"[WCDB监听] wcdb_open_account 失败 rc={rc}, 获取 Windows 错误失败: {e}")
                return False
            self._handle = handle_out.value
            if self.wcdb_set_wxid and wxid:
                self.wcdb_set_wxid(self._handle, wxid.encode("utf-8"))
            logger.info(f"[WCDB监听] ✅ 数据库已打开 handle={self._handle}")
            return True

    def _close_db(self):
        with self._lock:
            if self._handle is not None:
                try:
                    self.wcdb_close_account(self._handle)
                except Exception:
                    pass
                self._handle = None
            try:
                self.wcdb_shutdown()
            except Exception:
                pass

    def _decode_ptr(self, ptr) -> Optional[dict]:
        if not ptr:
            return None
        try:
            raw = ctypes.string_at(ptr)
            result = json.loads(raw.decode("utf-8", errors="ignore"))
            with self._lock:
                self.wcdb_free_string(ptr)
            return result
        except Exception:
            return None

    def start(self, db_path: str, hex_key: str, wxid: str, on_new_message: Callable[[Dict], None], loop=None) -> bool:
        if self._running:
            return True
        self._on_new_message = on_new_message
        if not self._load_dll():
            return False

        # 🌟 强力插入：如果配置了手动密钥和数据路径，优先使用 🌟
        if wxid:
            try:
                from src.api.instance_settings_api import load_instance_settings
                cfg = load_instance_settings(wxid)
                manual_key = cfg.get("wechat_hex_key", "").strip()
                if manual_key and len(manual_key) == 64:
                    hex_key = manual_key
                    logger.info(f"[WCDB监听] 实时监听使用手动配置的解密密钥: {hex_key[:6]}...{hex_key[-6:]}")
                
                manual_dir = cfg.get("wechat_data_dir", "").strip()
                if manual_dir and os.path.exists(manual_dir):
                    # 如果手动配置了数据路径，直接在此路径下寻找数据库
                    db_storage_cand = None
                    if os.path.basename(manual_dir).lower() == "db_storage":
                        db_storage_cand = manual_dir
                    else:
                        cand = os.path.join(manual_dir, "db_storage")
                        if os.path.isdir(cand):
                            db_storage_cand = cand
                        else:
                            db_storage_cand = manual_dir
                    
                    if db_storage_cand:
                        from src.wechat_4x.db_match_helper import find_session_db
                        session_db = find_session_db(db_storage_cand)
                        if session_db:
                            db_path = session_db
                            logger.info(f"[WCDB监听] 实时监听成功根据手动配置定位到数据库: {db_path}")
            except Exception as e_cfg:
                logger.debug(f"[WCDB监听] 获取手动配置失败: {e_cfg}")

        if not self._open_db(db_path, hex_key.strip(), wxid):
            return False
        self._running = True

        if self.wcdb_start_monitor_pipe:
            from src.wechat_4x.wcdb_monitor_pipe import WcdbPipeMonitor
            pipe = WcdbPipeMonitor(
                self._handle, self.wcdb_start_monitor_pipe,
                self.wcdb_stop_monitor_pipe, self.wcdb_get_monitor_pipe_name,
                self.wcdb_free_string
            )
            ok = pipe.start(self._dispatch)
            if ok:
                logger.info("[WCDB监听] 命名管道模式已启动")
                return True
            logger.warning("[WCDB监听] 管道启动失败，降级轮询")

        self._init_poll_cursors()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="wcdb-poll")
        self._poll_thread.start()
        logger.info("[WCDB监听] 轮询模式已启动（1秒间隔）")
        return True

    def stop(self):
        self._running = False
        self._close_db()
        logger.info("[WCDB监听] 已停止")

    def is_active(self) -> bool:
        return self._running and self._handle is not None

    def _dispatch(self, msg: Dict):
        if self._on_new_message:
            try:
                self._on_new_message(msg)
            except Exception as e:
                logger.error(f"[WCDB监听] 消息回调异常: {e}")

    def _init_poll_cursors(self):
        if not self._handle:
            return
        try:
            out_ptr = ctypes.c_void_p(0)
            with self._lock:
                rc = self.wcdb_get_sessions(self._handle, ctypes.byref(out_ptr))
            if rc != 0 or not out_ptr.value:
                return
            data = self._decode_ptr(out_ptr)
            if not data:
                return
            sessions = data if isinstance(data, list) else data.get("sessions", [])
            for s in sessions:
                sid = s.get("session_id") or s.get("username") or s.get("talker", "")
                unread_cnt = int(s.get("unread_count") or s.get("unreadCount") or s.get("unread_cnt") or 0)
                if sid:
                    if unread_cnt > 0:
                        # 💡 【启动未读强回特性】：若启动时该会话原本存在未读消息 (unread_cnt > 0)，
                        # 我们故意将游标前置（设为当前最末消息 id 减去未读数），从而精准滑入这几条历史未读。
                        max_id = self._session_max_id(sid)
                        self._last_poll_max_id[sid] = max(0, max_id - unread_cnt)
                        logger.info(f"[WCDB监听] 发现会话 '{sid}' 存在 {unread_cnt} 条启动前未读，已重置游标至 {self._last_poll_max_id[sid]} 准备补回回复")
                    else:
                        self._last_poll_max_id[sid] = self._session_max_id(sid)
            logger.info(f"[WCDB监听] 追踪 {len(self._last_poll_max_id)} 个会话游标")
        except Exception as e:
            logger.error(f"[WCDB监听] 初始化游标失败: {e}")

    def _session_max_id(self, session_id: str) -> int:
        try:
            out_ptr = ctypes.c_void_p(0)
            with self._lock:
                rc = self.wcdb_get_messages(self._handle, session_id.encode("utf-8"), ctypes.c_int32(1), ctypes.c_int32(0), ctypes.byref(out_ptr))
            if rc != 0 or not out_ptr.value:
                return 0
            data = self._decode_ptr(out_ptr)
            if not data:
                return 0
            msgs = data if isinstance(data, list) else data.get("messages", [])
            return int(msgs[0].get("local_id") or msgs[0].get("localId") or 0) if msgs else 0
        except Exception:
            return 0

    def _poll_loop(self):
        while self._running:
            try:
                self._poll_cycle += 1
                if self._poll_cycle % 10 == 1:
                    self._refresh_sessions()
                for sid, last_id in list(self._last_poll_max_id.items()):
                    self._poll_session(sid, last_id)
            except Exception as e:
                logger.error(f"[WCDB监听] 轮询异常: {e}")
            time.sleep(1.0)

    def _refresh_sessions(self):
        try:
            out_ptr = ctypes.c_void_p(0)
            with self._lock:
                rc = self.wcdb_get_sessions(self._handle, ctypes.byref(out_ptr))
            if rc != 0 or not out_ptr.value:
                return
            data = self._decode_ptr(out_ptr)
            if not data:
                return
            sessions = data if isinstance(data, list) else data.get("sessions", [])
            for s in sessions:
                sid = s.get("session_id") or s.get("username") or s.get("talker", "")
                if sid and sid not in self._last_poll_max_id:
                    self._last_poll_max_id[sid] = self._session_max_id(sid)
        except Exception:
            pass

    def _poll_session(self, session_id: str, last_id: int):
        try:
            out_ptr = ctypes.c_void_p(0)
            with self._lock:
                rc = self.wcdb_get_messages(self._handle, session_id.encode("utf-8"), ctypes.c_int32(10), ctypes.c_int32(0), ctypes.byref(out_ptr))
            if rc != 0 or not out_ptr.value:
                return
            data = self._decode_ptr(out_ptr)
            if not data:
                return
            msgs = data if isinstance(data, list) else data.get("messages", [])
            new_msgs = [m for m in msgs if int(m.get("local_id") or m.get("localId") or 0) > last_id]
            for m in sorted(new_msgs, key=lambda x: int(x.get("local_id") or x.get("localId") or 0)):
                local_id = int(m.get("local_id") or m.get("localId") or 0)
                is_self = bool(m.get("is_sender") or m.get("IsSender") or False)
                content = str(m.get("content") or m.get("StrContent") or "")
                self._last_poll_max_id[session_id] = max(last_id, local_id)
                if is_self or not content.strip():
                    continue
                self._dispatch({
                    "session_id": session_id,
                    "content": content,
                    "is_group": "@chatroom" in session_id,
                    "is_self": False,
                    "timestamp": int(m.get("timestamp") or m.get("CreateTime") or time.time()),
                    "local_id": local_id,
                })
        except Exception as e:
            logger.debug(f"[WCDB监听] 轮询 '{session_id}' 失败: {e}")

    def get_latest_messages(self, session_id: str, limit: int = 10) -> list:
        """获取指定会话的最近消息列表"""
        if not self._handle or not self.wcdb_get_messages:
            return []
        try:
            out_ptr = ctypes.c_void_p(0)
            with self._lock:
                rc = self.wcdb_get_messages(self._handle, session_id.encode("utf-8"), ctypes.c_int32(limit), ctypes.c_int32(0), ctypes.byref(out_ptr))
            if rc != 0 or not out_ptr.value:
                return []
            data = self._decode_ptr(out_ptr)
            if not data:
                return []
            msgs = data if isinstance(data, list) else data.get("messages", [])
            result = []
            for m in msgs:
                result.append({
                    "local_id": int(m.get("local_id") or m.get("localId") or 0),
                    "is_self": bool(m.get("is_sender") or m.get("IsSender") or False),
                    "content": str(m.get("content") or m.get("StrContent") or ""),
                    "timestamp": int(m.get("timestamp") or m.get("CreateTime") or 0)
                })
            return result
        except Exception as e:
            logger.debug(f"[WCDB监听] 获取最近消息失败: {e}")
            return []


_monitor_instances: dict = {}


def get_wcdb_monitor(account_id: str) -> WcdbMonitor:
    if account_id not in _monitor_instances:
        _monitor_instances[account_id] = WcdbMonitor()
    return _monitor_instances[account_id]
