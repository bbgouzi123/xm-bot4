import os
import ctypes
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def get_dll_path(dll_name: str) -> str:
    import sys
    env_path = os.environ.get("WCDB_DLL_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        for base in (os.path.dirname(sys.executable), meipass):
            p = os.path.join(base, dll_name)
            if os.path.exists(p):
                return p

    here = os.path.dirname(os.path.abspath(__file__))
    product_dir = os.path.dirname(os.path.dirname(os.path.dirname(here)))

    candidates = [
        os.path.join(here, "..", "assets", dll_name),
        os.path.join(product_dir, "wx", "WeFlow", "resources", dll_name),
        os.path.join(here, "..", "resources", dll_name),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

def load_wcdb_dll() -> Optional[dict]:
    """加载并配置 wcdb_api.dll，返回绑定的接口字典"""
    api_path = get_dll_path("wcdb_api.dll")
    if api_path and os.path.exists(api_path):
        dll_dir = os.path.dirname(os.path.abspath(api_path))
        if dll_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = dll_dir + os.path.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(dll_dir)
            except Exception:
                pass

    for dep in ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "WCDB.dll"]:
        dep_path = get_dll_path(dep)
        if os.path.exists(dep_path):
            try:
                ctypes.CDLL(dep_path)
            except Exception:
                pass

    api_path = get_dll_path("wcdb_api.dll")
    if not os.path.exists(api_path):
        logger.error(f"[WCDB监听] wcdb_api.dll 未找到: {api_path}")
        return None
    try:
        dll = ctypes.CDLL(api_path)
    except Exception as e:
        try:
            err_code = ctypes.windll.kernel32.GetLastError()
            err_msg = ctypes.FormatError(err_code)
            logger.error(f"[WCDB监听] 加载失败: {e}, Windows GetLastError={err_code} ({err_msg})")
        except Exception as ex:
            logger.error(f"[WCDB监听] 加载失败: {e} (获取错误码异常: {ex})")
        return None

    try:
        wcdb_init = dll.wcdb_init
        wcdb_init.restype = ctypes.c_int32
        wcdb_shutdown = dll.wcdb_shutdown
        wcdb_shutdown.restype = ctypes.c_int32
        wcdb_open_account = dll.wcdb_open_account
        wcdb_open_account.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64)]
        wcdb_open_account.restype = ctypes.c_int32
        wcdb_close_account = dll.wcdb_close_account
        wcdb_close_account.argtypes = [ctypes.c_int64]
        wcdb_close_account.restype = ctypes.c_int32
        wcdb_free_string = dll.wcdb_free_string
        wcdb_free_string.argtypes = [ctypes.c_void_p]
        wcdb_free_string.restype = None
        wcdb_get_sessions = dll.wcdb_get_sessions
        wcdb_get_sessions.argtypes = [ctypes.c_int64, ctypes.POINTER(ctypes.c_void_p)]
        wcdb_get_sessions.restype = ctypes.c_int32
        wcdb_get_messages = dll.wcdb_get_messages
        wcdb_get_messages.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_int32, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
        wcdb_get_messages.restype = ctypes.c_int32
        
        try:
            wcdb_set_wxid = dll.wcdb_set_my_wxid
            wcdb_set_wxid.argtypes = [ctypes.c_int64, ctypes.c_char_p]
            wcdb_set_wxid.restype = ctypes.c_int32
        except Exception:
            wcdb_set_wxid = None
            
        try:
            wcdb_start_monitor_pipe = dll.wcdb_start_monitor_pipe
            wcdb_start_monitor_pipe.argtypes = [ctypes.c_int64]
            wcdb_start_monitor_pipe.restype = ctypes.c_int32
            wcdb_stop_monitor_pipe = dll.wcdb_stop_monitor_pipe
            wcdb_stop_monitor_pipe.argtypes = [ctypes.c_int64]
            wcdb_stop_monitor_pipe.restype = ctypes.c_int32
            wcdb_get_monitor_pipe_name = dll.wcdb_get_monitor_pipe_name
            wcdb_get_monitor_pipe_name.argtypes = [ctypes.c_int64, ctypes.POINTER(ctypes.c_void_p)]
            wcdb_get_monitor_pipe_name.restype = ctypes.c_int32
        except Exception:
            wcdb_start_monitor_pipe = None
            wcdb_stop_monitor_pipe = None
            wcdb_get_monitor_pipe_name = None
            logger.info("[WCDB监听] 命名管道 API 不可用，使用轮询降级")

        # 可选：内部诊断日志接口（不影响主流程）
        try:
            wcdb_get_logs = dll.wcdb_get_logs
            wcdb_get_logs.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            wcdb_get_logs.restype = ctypes.c_int32
        except Exception:
            wcdb_get_logs = None

        logger.info(f"[WCDB监听] DLL 加载成功: {api_path}")
        return {
            "wcdb_init": wcdb_init,
            "wcdb_shutdown": wcdb_shutdown,
            "wcdb_open_account": wcdb_open_account,
            "wcdb_close_account": wcdb_close_account,
            "wcdb_free_string": wcdb_free_string,
            "wcdb_get_sessions": wcdb_get_sessions,
            "wcdb_get_messages": wcdb_get_messages,
            "wcdb_set_wxid": wcdb_set_wxid,
            "wcdb_start_monitor_pipe": wcdb_start_monitor_pipe,
            "wcdb_stop_monitor_pipe": wcdb_stop_monitor_pipe,
            "wcdb_get_monitor_pipe_name": wcdb_get_monitor_pipe_name,
            "wcdb_get_logs": wcdb_get_logs,
        }
    except Exception as e:
        logger.error(f"[WCDB监听] 函数绑定失败: {e}")
        return None
