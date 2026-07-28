"""检查微信进程是否有 QT_ACCESSIBILITY 环境变量"""
import ctypes
import ctypes.wintypes as wt

# 微信 PID
TARGET_PID = 9736

# 使用 WMI 查询进程环境
import subprocess
result = subprocess.run(
    ['powershell', '-c', f'(Get-Process -Id {TARGET_PID}).StartInfo.EnvironmentVariables | Out-String'],
    capture_output=True, text=True
)
print("PowerShell 方式:")
print(result.stdout[:500] if result.stdout else "(空)")

# 检查微信版本
import win32process, win32api
import os
for pid in [9736]:
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if h:
            buf = ctypes.create_unicode_buffer(512)
            size = wt.DWORD(512)
            ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            exe = buf.value
            print(f"\n微信路径: {exe}")
            if os.path.exists(exe):
                info = win32api.GetFileVersionInfo(exe, '\\')
                ms = info['FileVersionMS']
                ls = info['FileVersionLS']
                ver = f"{ms>>16}.{ms&0xFFFF}.{ls>>16}.{ls&0xFFFF}"
                print(f"微信版本: {ver}")
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception as e:
        print(f"版本检测异常: {e}")
