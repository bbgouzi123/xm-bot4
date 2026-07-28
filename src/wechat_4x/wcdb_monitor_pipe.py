"""
wcdb_monitor_pipe.py
WCDB 命名管道监听模式（主要感知通道）

当 wcdb_api.dll 支持 wcdb_start_monitor_pipe 时启用此模式，
延迟 < 1 秒，优先于轮询模式运行。
"""
import json
import logging
import socket
import threading
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class WcdbPipeMonitor:
    """基于 DLL 命名管道的实时消息监听器"""

    def __init__(
        self,
        handle: int,
        wcdb_start_monitor_pipe,
        wcdb_stop_monitor_pipe,
        wcdb_get_monitor_pipe_name,
        wcdb_free_string,
        koffi_decode=None
    ):
        self._handle = handle
        self._start_fn = wcdb_start_monitor_pipe
        self._stop_fn = wcdb_stop_monitor_pipe
        self._get_pipe_name_fn = wcdb_get_monitor_pipe_name
        self._free_str_fn = wcdb_free_string
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self, on_event: Callable[[Dict], None]) -> bool:
        """启动命名管道监听，返回是否成功"""
        import ctypes
        rc = self._start_fn(self._handle)
        if rc != 0:
            logger.warning(f"[管道监听] wcdb_start_monitor_pipe 失败 rc={rc}")
            return False

        pipe_name = "\\\\.\\pipe\\weflow_monitor"
        if self._get_pipe_name_fn:
            try:
                ptr = ctypes.c_void_p(0)
                rc2 = self._get_pipe_name_fn(self._handle, ctypes.byref(ptr))
                if rc2 == 0 and ptr.value:
                    raw = ctypes.string_at(ptr.value)
                    pipe_name = raw.decode("utf-8", errors="ignore")
                    self._free_str_fn(ptr)
            except Exception:
                pass

        logger.info(f"[管道监听] 管道名称: {pipe_name}")
        self._running = True
        self._thread = threading.Thread(
            target=self._pipe_loop,
            args=(pipe_name, on_event),
            daemon=True,
            name="wcdb-pipe-reader"
        )
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._stop_fn:
            try:
                self._stop_fn(self._handle)
            except Exception:
                pass

    def _pipe_loop(self, pipe_name: str, on_event: Callable[[Dict], None]):
        """命名管道读取主循环，支持断线重连"""
        RECONNECT_DELAY = 3.0
        while self._running:
            try:
                sock = socket.socket(socket.AF_UNIX)
                sock.connect(pipe_name)
                logger.info("[管道监听] 已连接，开始接收实时事件...")
                buf = b""
                while self._running:
                    try:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        lines = buf.split(b"\n")
                        buf = lines[-1]
                        for line in lines[:-1]:
                            line = line.strip()
                            if line:
                                _dispatch_pipe_event(line.decode("utf-8", errors="ignore"), on_event)
                    except Exception:
                        break
                sock.close()
            except Exception as e:
                logger.debug(f"[管道监听] 连接失败: {e}，{RECONNECT_DELAY}s 后重连...")
            if self._running:
                time.sleep(RECONNECT_DELAY)


def _dispatch_pipe_event(json_str: str, on_event: Callable[[Dict], None]):
    """解析管道 JSON 事件并转化为标准格式调用回调"""
    try:
        evt = json.loads(json_str)
        action = evt.get("action", "")
        if action not in ("new_message", "update", "message"):
            return

        session_id = (evt.get("session_id") or evt.get("talker") or evt.get("StrTalker") or "")
        content = evt.get("content") or evt.get("StrContent") or ""
        is_self = bool(evt.get("is_sender") or evt.get("IsSender") or False)
        timestamp = int(evt.get("timestamp") or evt.get("CreateTime") or time.time())
        local_id = int(evt.get("local_id") or evt.get("localId") or 0)
        is_group = "@chatroom" in (session_id or "")

        if not session_id or not content or is_self:
            return

        on_event({
            "session_id": session_id,
            "content": content,
            "is_group": is_group,
            "is_self": is_self,
            "timestamp": timestamp,
            "local_id": local_id,
        })
    except Exception as e:
        logger.debug(f"[管道监听] 事件解析失败: {e}")
