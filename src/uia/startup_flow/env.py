import os
import ctypes
from .utils import _log

def check_qt_accessibility_injected() -> bool:
    """检查注册表中 QT_ACCESSIBILITY=1 是否已持久化（HKCU 或 HKLM 任一即可）"""
    import winreg
    for hive, path in [
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ]:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_QUERY_VALUE)
            val, _ = winreg.QueryValueEx(key, "QT_ACCESSIBILITY")
            winreg.CloseKey(key)
            if val == "1":
                return True
        except Exception:
            pass
    return False


def inject_qt_accessibility():
    """将 QT_ACCESSIBILITY=1 写入注册表（HKCU + HKLM 双写）+ 广播 + 当前进程，并注入系统 ScreenReader 标志"""
    os.environ["QT_ACCESSIBILITY"] = "1"
    try:
        ctypes.windll.kernel32.SetEnvironmentVariableW("QT_ACCESSIBILITY", "1")
    except Exception:
        pass
    import winreg
    written = False
    # 写入 HKCU（用户级）
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Environment",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "QT_ACCESSIBILITY", 0, winreg.REG_SZ, "1")
        winreg.CloseKey(key)
        written = True
    except Exception:
        pass
    # 写入 HKLM（系统级，确保 WeChat 多进程子进程也能读到）
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "QT_ACCESSIBILITY", 0, winreg.REG_SZ, "1")
        winreg.CloseKey(key)
        written = True
    except Exception:
        pass
        
    # 写入 HKCU Control Panel\Accessibility 的 Blind Access 和 Screen Reader，辅助无障碍激活
    try:
        acc_key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Control Panel\Accessibility",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(acc_key, "Blind Access", 0, winreg.REG_SZ, "1")
        winreg.SetValueEx(acc_key, "Screen Reader", 0, winreg.REG_SZ, "1")
        winreg.CloseKey(acc_key)
        _log("环境", "无障碍辅助标志已写入注册表(Blind Access=1, Screen Reader=1)")
    except Exception as e:
        _log("环境", f"⚠ 写入无障碍辅助注册表标志失败: {e}")

    if written:
        # 广播变更，通知所有窗口环境变量已改变（使用非阻塞的 SendNotifyMessageW 替代 SendMessageTimeoutW 避免挂起）
        try:
            ctypes.windll.user32.SendNotifyMessageW(0xFFFF, 0x001A, 0, "Environment")
        except Exception:
            pass
        _log("环境", "QT_ACCESSIBILITY=1 已写入注册表（HKCU + HKLM 双写）")
    else:
        _log("环境", "⚠ QT_ACCESSIBILITY 注册表写入失败")

def ensure_tray_always_show_all_icons():
    """设置 Win10/Win11 注册表: 始终在通知区域显示所有图标"""
    try:
        import platform
        # 获取内部版本号，Win11 的 build number >= 22000
        try:
            build_num = int(platform.version().split('.')[2])
        except Exception:
            build_num = 0
            
        if build_num >= 22000:
            _log("环境", "检测到 Windows 11，忽略托盘全局显示强制设置（Win11 不受此注册表影响，且重启外壳会中断 UIA 连结）")
            return

        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
        
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
        except Exception:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            
        try:
            val, _ = winreg.QueryValueEx(key, "EnableAutoTray")
        except Exception:
            val = None
            
        if val != 0:
            winreg.SetValueEx(key, "EnableAutoTray", 0, winreg.REG_DWORD, 0)
            _log("环境", "已修改注册表：开启始终显示所有托盘图标 (EnableAutoTray=0)")
            
            # 通知 Explorer 重新加载设置（这通常不足以立即生效，但最好加上）
            import ctypes
            try:
                ctypes.windll.user32.SendNotifyMessageW(0xFFFF, 0x001A, 0, "TraySettings")
            except Exception:
                pass
            
            # 优雅地重启 Explorer 以使设置立即生效
            # 注意: Explorer 重启瞬间任务栏会消失，由于我们是数字员工自动化系统，初次配置时这点可以接受
            import subprocess
            import time
            _log("环境", "正在重启 Explorer 以应用托盘设置...")
            subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            # 稍作等待以确保进程退出并释放资源
            time.sleep(1.0)
            # 使用 Popen 后台重启，不等待
            subprocess.Popen(["explorer.exe"], creationflags=subprocess.CREATE_NO_WINDOW | 0x00000008)
            _log("环境", "Explorer 重启完成，托盘图标将全部显示。")
            
        winreg.CloseKey(key)
    except Exception as e:
        _log("环境", f"设置托盘图标显示异常: {e}")
