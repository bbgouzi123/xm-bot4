import time
import random

def _log(tag: str, msg: str):
    """统一日志格式"""
    print(f"[{tag}] {msg}")


def _random_sleep(lo: float = 0.3, hi: float = 0.7):
    """反风控随机延迟"""
    time.sleep(random.uniform(lo, hi))


def is_wechat_main_window(hwnd: int) -> bool:
    """通过快速类名初筛与 UIA 探测确认一个微信窗口句柄是否是已登录的主界面窗口"""
    import win32gui
    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return False
        
    cls = win32gui.GetClassName(hwnd)
    cls_lower = cls.lower()
    title = win32gui.GetWindowText(hwnd).strip()
    
    # 🌟 过滤无标题的后台/阴影/IPC 辅助窗口，确保绑定的是用户可见的真实主界面窗口
    if not title:
        return False
        
    # 1. 快速类名初筛：杜绝 UIA 超时误判
    if "wechatloginwndforpc" in cls_lower or "loginwnd" in cls_lower:
        return False
    if "wechatmainwndforpc" in cls_lower:
        # WeChatMainWndForPC 代表微信原生主界面窗口，100% 是已登录主界面
        return True
        
    # 2. 针对 Qt 等新型窗口类名，降级到 UIA 特征探测
    import uiautomation as uia
    import comtypes
    
    bind_result = [False]
    def _test_thread():
        try:
            comtypes.CoInitialize()
            root = uia.ControlFromHandle(hwnd)
            if root:
                nav = root.ToolBarControl(AutomationId="main_tabbar")
                if nav.Exists(0.2, 0):
                    bind_result[0] = True
                    return
                nav_name = root.ToolBarControl(Name="导航")
                if nav_name.Exists(0.2, 0):
                    bind_result[0] = True
                    return
        except Exception:
            pass
            
    import threading
    t = threading.Thread(target=_test_thread, daemon=True)
    t.start()
    t.join(timeout=1.5)
    return bind_result[0]
