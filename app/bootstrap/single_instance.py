import platform

_global_mutex_handle = None
_local_mutex_handle = None

def check_single_instance() -> bool:
    """
    通过 Windows 命名互斥体判断是否已有活跃的主实例在运行。
    返回 True 表示没有其他实例（当前是唯一主实例）；
    返回 False 表示已有其他实例在运行。
    """
    if platform.system() != "Windows":
        return True
    
    import ctypes
    mutex_name = 'Global\\xm_bot4_rpa_mutex_v1'
    kernel32 = ctypes.windll.kernel32
    
    try:
        mutex_handle = kernel32.CreateMutexW(None, False, mutex_name)
        last_error = kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183
        if last_error == 183:
            if mutex_handle:
                kernel32.CloseHandle(mutex_handle)
            return False
        global _global_mutex_handle
        _global_mutex_handle = mutex_handle
        return True
    except Exception:
        # 如果 Global 创建失败（受无管理员权限下的命名空间限制），降级尝试 Local 域互斥体
        try:
            local_name = 'Local\\xm_bot4_rpa_mutex_v1'
            mutex_handle = kernel32.CreateMutexW(None, False, local_name)
            if kernel32.GetLastError() == 183:
                if mutex_handle:
                    kernel32.CloseHandle(mutex_handle)
                return False
            global _local_mutex_handle
            _local_mutex_handle = mutex_handle
            return True
        except Exception:
            return True
