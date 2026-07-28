import os
import logging
try:
    import winreg
except ImportError:
    winreg = None
import ctypes
import subprocess
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Win32 API 常量
PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
SystemHandleInformation = 16
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
DUPLICATE_CLOSE_SOURCE = 0x0001

# 结构体定义
class SYSTEM_HANDLE_TABLE_ENTRY_INFO(ctypes.Structure):
    _fields_ = [
        ("UniqueProcessId", wintypes.USHORT),
        ("CreatorBackTraceIndex", wintypes.USHORT),
        ("ObjectTypeIndex", ctypes.c_ubyte),
        ("HandleAttributes", ctypes.c_ubyte),
        ("HandleValue", wintypes.USHORT),
        ("Object", ctypes.c_void_p),
        ("GrantedAccess", wintypes.ULONG),
    ]

class SYSTEM_HANDLE_INFORMATION_STRUCT(ctypes.Structure):
    _fields_ = [
        ("NumberOfHandles", ctypes.c_ulong),
        ("Handles", SYSTEM_HANDLE_TABLE_ENTRY_INFO * 1),
    ]

def get_wechat_path() -> str:
    """从进程、注册表及兜底路径智能深度查找微信安装路径"""
    # 策略 0：尝试从当前正在运行的微信进程直接提取路径
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                name = proc.info.get('name')
                if name and name.lower() in ('wechat.exe', 'weixin.exe'):
                    exe_path = proc.info.get('exe')
                    if exe_path and os.path.exists(exe_path):
                        return exe_path
            except Exception:
                pass
    except Exception:
        pass

    # 策略 1：读取注册表 HKEY_CURRENT_USER\Software\Tencent\Weixin
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin")
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        weixin_path = os.path.join(path, "Weixin.exe")
        wechat_path = os.path.join(path, "WeChat.exe")
        if os.path.exists(weixin_path):
            return weixin_path
        if os.path.exists(wechat_path):
            return wechat_path
    except Exception:
        pass

    # 策略 2：读取注册表 HKEY_CURRENT_USER\Software\Tencent\WeChat
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat")
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        weixin_path = os.path.join(path, "Weixin.exe")
        wechat_path = os.path.join(path, "WeChat.exe")
        if os.path.exists(weixin_path):
            return weixin_path
        if os.path.exists(wechat_path):
            return wechat_path
    except Exception:
        pass

    # 策略 3：读取注册表 HKEY_LOCAL_MACHINE 和 HKCU 的 Weixin & WeChat 路径 (含 WOW6432Node)
    for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub_path in (
            r"Software\Tencent\Weixin",
            r"Software\Tencent\WeChat",
            r"Software\Wow6432Node\Tencent\Weixin",
            r"Software\Wow6432Node\Tencent\WeChat"
        ):
            try:
                key = winreg.OpenKey(root_key, sub_path)
                path, _ = winreg.QueryValueEx(key, "InstallPath")
                weixin_path = os.path.join(path, "Weixin.exe")
                wechat_path = os.path.join(path, "WeChat.exe")
                if os.path.exists(weixin_path):
                    return weixin_path
                if os.path.exists(wechat_path):
                    return wechat_path
            except Exception:
                pass

    # 策略 4：从 Uninstall 注册表反查安装位置 (含 HKCU 和 HKLM)
    for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for unreg_key in [
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Weixin",
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\WeChat",
            r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Weixin",
            r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\WeChat",
        ]:
            try:
                with winreg.OpenKey(root_key, unreg_key, 0, winreg.KEY_READ) as key:
                    loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                    if loc:
                        for name in ("Weixin.exe", "WeChat.exe"):
                            reg_exe = os.path.join(loc, name)
                            if os.path.exists(reg_exe):
                                return reg_exe
            except Exception:
                pass

    # 策略 5：兜底常见路径（扩充覆盖 D/E/F 盘 + 用户目录 + Weixin & WeChat 文件夹）
    user_home = os.path.expanduser("~")
    for p in [
        r"D:\Program Files\Tencent\Weixin\Weixin.exe",
        r"C:\Program Files\Tencent\Weixin\Weixin.exe",
        r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
        r"D:\Weixin\Weixin.exe",
        r"E:\Program Files\Tencent\Weixin\Weixin.exe",
        r"F:\Program Files\Tencent\Weixin\Weixin.exe",
        r"D:\Program Files\Weixin\Weixin.exe",
        os.path.join(user_home, r"AppData\Local\Programs\Tencent\Weixin\Weixin.exe"),
        os.path.join(user_home, r"AppData\Roaming\Tencent\Weixin\Weixin.exe"),
        r"C:\Program Files\Tencent\WeChat\Weixin.exe",
        r"C:\Program Files\Tencent\WeChat\WeChat.exe",
        r"C:\Program Files (x86)\Tencent\WeChat\Weixin.exe",
        r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
    ]:
        if os.path.exists(p):
            return p

    # 没找到则返回 None，而非无法执行的 "Weixin.exe"
    return None

def kill_wechat_mutex():
    """遍历所有微信进程，找到互斥体句柄并强行关闭，从而解除单开限制"""
    try:
        import psutil
    except ImportError:
        logger.warning("未安装 psutil，采用系统任务列表查询PID")
        return False

    pids = []
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] and proc.info['name'].lower() in ('wechat.exe', 'weixin.exe'):
            pids.append(proc.info['pid'])

    if not pids:
        logger.info("[多开锁定] 当前未检测到运行中的微信进程，无需清理锁。")
        return True

    # 载入 API
    ntdll = ctypes.windll.ntdll
    kernel32 = ctypes.windll.kernel32

    # 查询所有句柄信息
    buf_size = 1024 * 1024
    buf = ctypes.create_string_buffer(buf_size)
    
    while True:
        res = ntdll.NtQuerySystemInformation(
            SystemHandleInformation,
            buf,
            buf_size,
            None
        )
        if res == STATUS_INFO_LENGTH_MISMATCH:
            buf_size *= 2
            buf = ctypes.create_string_buffer(buf_size)
        elif res == 0:
            break
        else:
            logger.error(f"[多开锁定] 查询系统句柄失败: {hex(res)}")
            return False

    # 解析句柄信息
    num_handles = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[0]
    # 手动计算 Handles 数组偏移量以防 32/64 位对齐差异
    entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO)
    offset = ctypes.sizeof(ctypes.c_ulong)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        # 64位对齐补丁
        offset = 8

    logger.info(f"[多开锁定] 开始扫描系统 {num_handles} 个句柄...")

    closed_count = 0
    for i in range(num_handles):
        entry_ptr = ctypes.addressof(buf) + offset + (i * entry_size)
        entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO.from_address(entry_ptr)
        
        if entry.UniqueProcessId in pids:
            # 过滤出可能是 Mutex (Object Type Index 很多时候在 Windows 10/11 上为 38 或 40 左右，这里通过名称二次判断)
            h_process = kernel32.OpenProcess(
                PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION,
                False,
                entry.UniqueProcessId
            )
            if not h_process:
                continue

            try:
                # 复制句柄到当前进程以查询名称并关闭
                h_target = wintypes.HANDLE()
                dup_res = kernel32.DuplicateHandle(
                    h_process,
                    entry.HandleValue,
                    kernel32.GetCurrentProcess(),
                    ctypes.byref(h_target),
                    0,
                    False,
                    0
                )
                
                if dup_res:
                    # 查询句柄对象名称
                    name_buf = ctypes.create_string_buffer(512)
                    # 使用 NtQueryObject 获取对象名
                    res_obj = ntdll.NtQueryObject(
                        h_target,
                        1,  # ObjectNameInformation
                        name_buf,
                        512,
                        None
                    )
                    
                    if res_obj == 0:
                        # 转换并解析名称，通常是以 \Sessions\1\BaseNamedObjects\_WeChat_App_Instance 开头
                        name_str = name_buf.raw[8:].decode('utf-16le', errors='ignore')
                        if "_WeChat_App_Instance" in name_str or "WeChat_SingleInstance" in name_str:
                            logger.info(f"[多开锁定] 发现微信单开锁句柄: {name_str.strip()}, 句柄值={entry.HandleValue}")
                            
                            # 强行关闭源进程中的句柄 (使用 DUPLICATE_CLOSE_SOURCE)
                            h_close = wintypes.HANDLE()
                            kernel32.DuplicateHandle(
                                h_process,
                                entry.HandleValue,
                                kernel32.GetCurrentProcess(),
                                ctypes.byref(h_close),
                                0,
                                False,
                                DUPLICATE_CLOSE_SOURCE
                            )
                            kernel32.CloseHandle(h_close)
                            closed_count += 1
                            
                    kernel32.CloseHandle(h_target)
            finally:
                kernel32.CloseHandle(h_process)

    logger.info(f"[多开锁定] 清理完成，共关闭了 {closed_count} 个互斥体锁。")
    return True
