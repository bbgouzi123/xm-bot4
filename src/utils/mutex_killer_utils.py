"""
微信 Mutex 精准暗杀器 — 底层数据类型与工具函数
"""
import os
import ctypes
import ctypes.wintypes as wt
import logging
import struct
from typing import List, Optional

logger = logging.getLogger(__name__)

# ==================== Windows API 常量 ====================
SystemHandleInformation = 16
ObjectNameInformation = 1
ObjectTypeInformation = 2

# 微信 Mutex 名称关键字
WECHAT_MUTEX_KEYWORDS = [
    "_WeChat_App_Instance_Identity_Mutex_Name",
    "WeChat_GlobalConfig_Multi_Process_Mutex",
    "_Weixin_App_Instance_Identity_Mutex_Name",
    "Weixin_GlobalConfig_Multi_Process_Mutex",
]

# 微信进程名
WECHAT_PROCESS_NAMES = [
    "wechat.exe", "weixin.exe",
    "WeChatApp.exe", "WeixinApp.exe",
]

# ==================== ctypes 结构体 ====================
ntdll = ctypes.windll.ntdll
kernel32 = ctypes.windll.kernel32

class SYSTEM_HANDLE_TABLE_ENTRY_INFO(ctypes.Structure):
    _fields_ = [
        ("UniqueProcessId", ctypes.c_ushort),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectTypeIndex", ctypes.c_byte),
        ("HandleAttributes", ctypes.c_byte),
        ("HandleValue", ctypes.c_ushort),
        ("Object", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
    ]

class SYSTEM_HANDLE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("NumberOfHandles", ctypes.c_ulong),
        ("Handles", SYSTEM_HANDLE_TABLE_ENTRY_INFO * 1),
    ]

class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_wchar_p),
    ]

class OBJECT_NAME_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Name", UNICODE_STRING),
    ]

# ==================== 辅助函数 ====================

def _get_wechat_pids() -> List[int]:
    """获取所有微信进程的 PID"""
    pids = []
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name'].lower()
                if name in WECHAT_PROCESS_NAMES:
                    pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        import subprocess
        for proc_name in WECHAT_PROCESS_NAMES:
            try:
                output = subprocess.check_output(
                    f'tasklist /FI "IMAGENAME eq {proc_name}" /FO CSV /NH',
                    shell=True, text=True, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                for line in output.strip().split('\n'):
                    if line and proc_name.lower() in line.lower():
                        parts = line.strip('"').split('","')
                        if len(parts) >= 2:
                            try:
                                pids.append(int(parts[1]))
                            except ValueError:
                                pass
            except Exception:
                pass
    return list(set(pids))

def _query_handle_name(handle: int) -> Optional[str]:
    """查询内核句柄的对象名称，在独立线程中执行以防 NtQueryObject 挂起"""
    import threading
    name_res = [None]
    
    def _worker():
        try:
            buf_size = 4096
            buf = ctypes.create_string_buffer(buf_size)
            ret_len = ctypes.c_ulong(0)
            status = ntdll.NtQueryObject(
                handle,
                ObjectNameInformation,
                buf,
                buf_size,
                ctypes.byref(ret_len),
            )
            if status == 0:
                name_info = ctypes.cast(buf, ctypes.POINTER(OBJECT_NAME_INFORMATION))
                name_str = name_info.contents.Name
                if name_str.Length > 0 and name_str.Buffer:
                    name_res[0] = name_str.Buffer
        except Exception:
            pass
            
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=0.1) # 100ms 超时防挂起
    return name_res[0]
