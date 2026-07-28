"""HTTP 服务器线程与端口生命周期控制。"""
import platform
import subprocess
import threading
import traceback
import uvicorn
from xm_py_server.runtime_urls import LOOPBACK_HOST
from app import constants

# 全局状态变量
_served_app = None
# 用于跨线程通信：server_thread 启动成功后 set，失败后保留 clear 状态
_server_started = threading.Event()
# 记录服务器线程崩溃原因
_server_error = None


def register_app(app) -> None:
    """由 main 在 create_app() 之后注册，供 start_server / uvicorn 使用同一实例。"""
    global _served_app
    _served_app = app


def _get_served_app():
    if _served_app is None:
        raise RuntimeError("register_app() must be called before start_server()")
    return _served_app


def flush_cloud_before_exit(max_batches: int = 20) -> None:
    """退出前抢救性上报关键用量与事件队列（带批次上限，避免阻塞过久）"""
    try:
        from src.crm.account_data import get_active_account
        from src.utils.cloud_sync import get_cloud_client

        cloud = get_cloud_client()
        account_id = get_active_account() or "main"
        cloud.report_usage(account_id)
        flushed = cloud.flush_pending_events(max_batches=max_batches)
        print(f"[关闭] 同步后端事件抢救上报完成，补传 {flushed} 条")
    except Exception as e:
        print(f"[关闭] 同步后端抢救上报失败: {e}")


def kill_port(port: int):
    """跨平台强杀占用特定端口的进程"""
    try:
        system = platform.system()
        if system == "Windows":
            result = subprocess.run(f'netstat -aon | findstr :{port}', shell=True, capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if 'LISTENING' in line and str(port) in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    if pid and pid != "0":
                        print(f"[启动] 端口 {port} 被 PID {pid} 占用，正在强杀...")
                        subprocess.run(f'taskkill /F /T /PID {pid}', shell=True, capture_output=True)
        else:
            result = subprocess.run(f"lsof -i tcp:{port} -t", shell=True, capture_output=True, text=True)
            if result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    print(f"[启动] 端口 {port} 被 PID {pid} 占用，正在强杀...")
                    subprocess.run(f"kill -9 {pid}", shell=True, capture_output=True)
    except Exception as e:
        print(f"[启动] 清理端口 {port} 失败: {e}")


def start_server():
    """启动 HTTP 服务器（运行在 daemon 线程中，必须捕获所有异常以防静默崩溃）"""
    global _server_error
    try:
        kill_port(constants.BOT4_PORT)
        print(f"[启动] 正在绑定 HTTP 服务到 {LOOPBACK_HOST}:{constants.BOT4_PORT} ...")
        _server_started.set()
        uvicorn.run(_get_served_app(), host="0.0.0.0", port=constants.BOT4_PORT, log_level="info", log_config=None)
    except BaseException as e:
        _server_error = str(e)
        print(f"[致命] HTTP 服务器线程崩溃 ({type(e).__name__}): {e}")
        traceback.print_exc()
