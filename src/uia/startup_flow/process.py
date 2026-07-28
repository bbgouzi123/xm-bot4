import time
import subprocess
import threading
from .utils import _log, _random_sleep
from .state import _enum_wechat_windows

try:
    import uiautomation as uia
except ImportError:
    uia = None

def _click_at_absolute(x: int, y: int):
    """绝对坐标点击（辅助函数）"""
    import win32api
    import ctypes
    old = win32api.GetCursorPos()
    win32api.SetCursorPos((x, y))
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)   # LEFTUP
    time.sleep(0.05)
    win32api.SetCursorPos(old)

def exit_wechat_via_tray() -> bool:
    """通过系统托盘右键菜单优雅退出微信"""
    try:
        from src.uia.retry.tray import click_wechat_tray_icon
        _log("退出", "右键点击托盘微信图标...")
        if not click_wechat_tray_icon(right_click=True):
            _log("退出", "未找到托盘微信图标")
            return False
        _random_sleep(0.5, 0.8)

        # 查找并点击"退出微信"菜单
        _found = [False]

        def _click_menu():
            if not uia: return
            try:
                import comtypes
                comtypes.CoInitialize()
                menu = uia.MenuControl(searchDepth=3)
                if menu.Exists(2, 0.5):
                    for item, _ in uia.WalkControl(menu, maxDepth=3):
                        cn = getattr(item, 'Name', '') or ''
                        if '退出' in cn and '微信' in cn:
                            try:
                                item.Click()
                                _found[0] = True
                            except Exception:
                                r = item.BoundingRectangle
                                _click_at_absolute(
                                    (r.left + r.right) // 2,
                                    (r.top + r.bottom) // 2
                                )
                                _found[0] = True
                            return
            except Exception:
                pass
            finally:
                try:
                    import gc
                    gc.collect()
                except Exception:
                    pass

        t = threading.Thread(target=_click_menu, daemon=True)
        t.start()
        t.join(timeout=5)
        return _found[0]
    except Exception:
        return False


def kill_wechat():
    """强制终止微信的所有相关进程"""
    _log("退出", "强制终止微信相关进程 (Weixin.exe / WeChat*.exe)...")
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.run(["taskkill", "/F", "/IM", "Weixin.exe", "/T"], capture_output=True, timeout=5, creationflags=_NO_WINDOW)
    except Exception:
        pass
    try:
        subprocess.run(["taskkill", "/F", "/IM", "WeChatAppEx.exe", "/T"], capture_output=True, timeout=5, creationflags=_NO_WINDOW)
    except Exception:
        pass
    try:
        subprocess.run(["taskkill", "/F", "/IM", "WeChatUpdate.exe", "/T"], capture_output=True, timeout=5, creationflags=_NO_WINDOW)
    except Exception:
        pass


    # 兜底：使用 psutil 强杀残留
    try:
        import psutil
        for p in psutil.process_iter(['name']):
            name = (p.info['name'] or '').lower()
            if 'weixin' in name or 'wechat' in name:
                try:
                    p.kill()
                except Exception:
                    pass
    except Exception:
        pass


def _get_wechat_process_count() -> int:
    """返回当前存活的微信关联进程数量"""
    try:
        import psutil
        count = 0
        for p in psutil.process_iter(['name']):
            name = (p.info['name'] or '').lower()
            if 'weixin' in name or 'wechat' in name:
                count += 1
        return count
    except Exception:
        return 0


def wait_wechat_exit(timeout: int = 15):
    """等待微信进程完全退出，而不仅是窗口关闭"""
    _log("退出", "等待微信进程池完全退出...")
    for _ in range(3):
        wins = _enum_wechat_windows()
        if not wins and _get_wechat_process_count() == 0:
            _log("退出", "✓ 微信进程已完全退出")
            return True
        time.sleep(1.0)
    
    _log("退出", "⚠ 发现存留进程，执行强杀过滤...")
    kill_wechat()
    
    for _ in range(timeout):
        if _get_wechat_process_count() == 0:
            _log("退出", "✓ 微信进程已被彻底清理")
            return True
        time.sleep(1.0)
    
    _log("退出", "⚠ 等待退出超时，忽略残留进程并继续")
    return True
