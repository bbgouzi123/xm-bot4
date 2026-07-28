import os
import json
import time
import logging
import threading

logger = logging.getLogger("RuntimeHeartbeat")

class RuntimeHeartbeat:
    """Worker 运行心跳保活写入器"""
    _thread = None
    _stop_event = threading.Event()

    @staticmethod
    def get_heartbeat_path() -> str:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        state_dir = os.path.join(appdata, "xm-bot4", "state")
        os.makedirs(state_dir, exist_ok=True)
        return os.path.join(state_dir, "runtime_heartbeat.json")

    @classmethod
    def write_heartbeat(cls):
        """将当前进程的 PID 和最新时间戳写入心跳文件"""
        try:
            heartbeat_path = cls.get_heartbeat_path()
            heartbeat_data = {
                "pid": os.getpid(),
                "timestamp": int(time.time()),
                "status": "running"
            }
            with open(heartbeat_path, "w", encoding="utf-8") as f:
                json.dump(heartbeat_data, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"[心跳] 写入心跳文件失败: {e}")

    @classmethod
    def start_heartbeat_daemon(cls):
        """开启后台心跳写入守护线程（每 5 秒刷新一次）"""
        if cls._thread and cls._thread.is_alive():
            return

        cls._stop_event.clear()
        
        def _loop():
            logger.info("[心跳] 启动 Worker 状态心跳定时守护线程")
            while not cls._stop_event.is_set():
                cls.write_heartbeat()
                time.sleep(5.0)

        cls._thread = threading.Thread(target=_loop, name="runtime-heartbeat-daemon", daemon=True)
        cls._thread.start()

    @classmethod
    def stop_heartbeat_daemon(cls):
        """停止心跳写入守护"""
        cls._stop_event.set()
        if cls._thread:
            cls._thread.join(timeout=2.0)
            logger.info("[心跳] 已停止 Worker 状态心跳守护")
