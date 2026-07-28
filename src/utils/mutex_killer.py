"""
微信 Mutex 精准暗杀器 — 重构精简版（限制单文件 300 行）
"""
import os
import ctypes
import ctypes.wintypes as wt
import logging
import struct
from .mutex_isolation_helper import ensure_data_isolation, build_isolated_env, apply_filesave_path, restore_filesave_path
from .mutex_killer_utils import (
    _get_wechat_pids,
    _query_handle_name,
    WECHAT_MUTEX_KEYWORDS,
    SYSTEM_HANDLE_TABLE_ENTRY_INFO,
    SystemHandleInformation,
    ntdll,
    kernel32,
)

logger = logging.getLogger(__name__)

# ==================== Windows API 常量 ====================
PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
DUPLICATE_CLOSE_SOURCE = 0x1
DUPLICATE_SAME_ACCESS = 0x2
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
STATUS_BUFFER_TOO_SMALL = 0xC0000023

# ==================== 核心功能 ====================

def kill_wechat_mutex() -> dict:
    """精准暗杀微信互斥锁，打通多开隔离通道"""
    result = {
        "success": False,
        "killed_count": 0,
        "wechat_pids": [],
        "details": [],
        "error": None,
    }

    h_temp = None
    try:
        try:
            import uuid
            temp_name = f"Global\\XM_Bot4_Temp_Mutex_{uuid.uuid4().hex}"
            h_temp = kernel32.CreateMutexW(None, False, temp_name)
        except Exception as e_temp:
            logger.debug(f"[Mutex] 创建临时 Mutex 失败: {e_temp}")

        wechat_pids = _get_wechat_pids()
        result["wechat_pids"] = wechat_pids

        if not wechat_pids:
            result["details"].append("未检测到运行中的微信进程，无需暗杀 Mutex")
            result["success"] = True
            return result

        result["details"].append(f"发现 {len(wechat_pids)} 个微信进程: {wechat_pids}")
        wechat_pid_set = set(wechat_pids)

        # 枚举系统句柄表，初始分配 16MB，不够则翻倍重试
        buf_size = 16 * 1024 * 1024
        max_buf_size = 256 * 1024 * 1024

        while buf_size <= max_buf_size:
            buf = ctypes.create_string_buffer(buf_size)
            ret_len = ctypes.c_ulong(0)
            status = ntdll.NtQuerySystemInformation(
                SystemHandleInformation,
                buf,
                buf_size,
                ctypes.byref(ret_len),
            )
            if status == STATUS_INFO_LENGTH_MISMATCH or status == STATUS_BUFFER_TOO_SMALL:
                buf_size *= 2
                continue
            if status < 0:
                result["error"] = f"NtQuerySystemInformation 失败: 0x{status & 0xFFFFFFFF:08X}"
                return result
            break
        else:
            result["error"] = "系统句柄表过大，无法枚举"
            return result

        handle_count = struct.unpack_from('I', buf.raw, 0)[0]
        result["details"].append(f"系统句柄总数: {handle_count}")

        entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO)
        header_size = ctypes.sizeof(ctypes.c_ulong)

        my_pid = os.getpid()
        my_pid_16 = my_pid & 0xffff
        mutex_type_index = None
        if h_temp:
            for i in range(handle_count):
                offset = header_size + i * entry_size
                if offset + entry_size > len(buf):
                    break
                entry_data = buf.raw[offset:offset + entry_size]
                pid = struct.unpack_from('H', entry_data, 0)[0]
                if pid == my_pid_16:
                    handle_value = struct.unpack_from('H', entry_data, 6)[0]
                    if handle_value == (h_temp & 0xFFFF):
                        mutex_type_index = entry_data[4]
                        result["details"].append(f"动态获取到 Mutex 的 ObjectTypeIndex: {mutex_type_index}")
                        break

        if mutex_type_index is None:
            logger.warning("[Mutex] 无法定位 Mutex ObjectTypeIndex，安全拦截退出以防止句柄无差别扫描挂起")
            result["details"].append("无法定位 Mutex ObjectTypeIndex，安全拦截退出")
            result["success"] = True  # 标记为 True 以作为安全的良性退出，避免阻断后续启动流程
            return result

        wechat_pid_16_map = {w_pid & 0xffff: w_pid for w_pid in wechat_pids}
        killed = 0
        my_process = kernel32.GetCurrentProcess()

        # 遍历所有句柄，查找属于微信进程的 Mutex
        for i in range(handle_count):
            offset = header_size + i * entry_size
            if offset + entry_size > len(buf):
                break

            entry_data = buf.raw[offset:offset + entry_size]
            pid_16 = struct.unpack_from('H', entry_data, 0)[0]

            if pid_16 not in wechat_pid_16_map:
                continue

            object_type_index = entry_data[4]
            if mutex_type_index is not None and object_type_index != mutex_type_index:
                continue

            actual_pid = wechat_pid_16_map[pid_16]
            handle_value = struct.unpack_from('H', entry_data, 6)[0]

            target_process = kernel32.OpenProcess(
                PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION,
                False,
                actual_pid,
            )
            if not target_process:
                continue

            try:
                dup_handle = wt.HANDLE(0)
                ok = kernel32.DuplicateHandle(
                    target_process,
                    handle_value,
                    my_process,
                    ctypes.byref(dup_handle),
                    0,
                    False,
                    DUPLICATE_SAME_ACCESS,
                )
                if not ok or not dup_handle.value:
                    continue

                try:
                    name = _query_handle_name(dup_handle.value)
                    if name:
                        is_wechat_mutex = any(
                            keyword in name for keyword in WECHAT_MUTEX_KEYWORDS
                        )
                        if is_wechat_mutex:
                            result["details"].append(
                                f"[命中] PID={actual_pid} Handle=0x{handle_value:X} Name={name}"
                            )

                            close_dup = wt.HANDLE(0)
                            close_ok = kernel32.DuplicateHandle(
                                target_process,
                                handle_value,
                                my_process,
                                ctypes.byref(close_dup),
                                0,
                                False,
                                DUPLICATE_CLOSE_SOURCE,
                            )
                            if close_ok:
                                killed += 1
                                result["details"].append(
                                    f"[暗杀成功] PID={actual_pid} 的 Mutex 已被摧毁"
                                )
                                if close_dup.value:
                                    kernel32.CloseHandle(close_dup.value)
                            else:
                                result["details"].append(
                                    f"[暗杀失败] PID={actual_pid} DuplicateHandle 关闭源句柄失败"
                                )
                finally:
                    kernel32.CloseHandle(dup_handle.value)
            finally:
                kernel32.CloseHandle(target_process)

        result["killed_count"] = killed
        result["success"] = True
        if killed > 0:
            result["details"].append(f"共暗杀 {killed} 个 Mutex，微信多开通道已打通")
        else:
            result["details"].append(
                "未找到微信 Mutex 句柄。可能微信版本使用了不同的 Mutex 名称，"
                "或微信尚未完成初始化。将回退到传统并发启动方案。"
            )

    except Exception as e:
        import traceback
        result["error"] = f"Mutex 暗杀异常: {e}"
        result["details"].append(traceback.format_exc())
    finally:
        if h_temp:
            try:
                kernel32.CloseHandle(h_temp)
            except Exception:
                pass

    return result
