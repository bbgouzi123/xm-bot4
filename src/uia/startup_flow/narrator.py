"""辅助功能激活器 — 确保微信构建完整的 UIA 无障碍树。"""
import subprocess
import time
import os
import threading
import win32gui
import win32con
from .utils import _log
from .volume_helper import get_system_mute_state, set_system_mute_state, is_wechat_uia_active

_active_strategy = None       # "narrator" | "magnifier" | "uia_client" | None
_uia_client_stop = None       # threading.Event
_uia_client_thread = None

_narrator_ref_lock = threading.Lock()
_narrator_ref_count = 0

_original_mute_state = False
_system_muted_by_us = False

def _is_process_running(name: str) -> bool:
    try:
        import psutil
        return any(p.info['name'] and p.info['name'].lower() == name.lower() for p in psutil.process_iter(['name']))
    except:
        return False

def start_narrator(force_physical: bool = False, allow_upgrade: bool = False, source: str = ""):
    """分层激活辅助功能客户端，确保 Qt 构建 UIA 树

    Args:
        force_physical: 强制升级为物理讲述人
        allow_upgrade: 允许 3.5s 后自动探测升级
        source: 调用来源标识，仅用于日志区分（如 'preheat', 'login', 'launcher'）
    """
    global _active_strategy, _uia_client_stop, _uia_client_thread, _narrator_ref_count
    with _narrator_ref_lock:
        _narrator_ref_count += 1
        source_tag = f" [{source}]" if source else ""
        _log("UIA", f"start_narrator(){source_tag} 引用增加 -> {_narrator_ref_count}")
        if _narrator_ref_count > 1 and not force_physical:
            return

        _active_strategy = None
        try:
            import ctypes
            ctypes.windll.user32.SystemParametersInfoW(0x0047, 1, None, 3)
            _log("UIA", "✓ SPI 屏幕阅读器标志已设置")
        except:
            pass

        if not force_physical:
            try:
                _log("UIA", "优先启动 Python UIA 事件订阅客户端...")
                _uia_client_stop = threading.Event()
                _uia_client_thread = threading.Thread(
                    target=_uia_event_client_loop, args=(_uia_client_stop,), daemon=True
                )
                _uia_client_thread.start()
                _active_strategy = "uia_client"
                
                # 自动探测升级：如果允许升级且 3.5 秒后微信 UIA 无障碍树依然没有激活，自动升级启动物理讲述人以彻底击碎 UIA 初始化超时
                if allow_upgrade:
                    def auto_upgrade_detector():
                        time.sleep(3.5)
                        if _active_strategy == "uia_client" and not is_wechat_uia_active():
                            _log("UIA", "⚠️ 探测到微信 UIA 节点树尚未激活，正在自动升级启动物理讲述人...")
                            start_narrator(force_physical=True)
                    threading.Thread(target=auto_upgrade_detector, daemon=True).start()
                return
            except Exception as e:
                _log("UIA", f"启动 Python UIA 事件客户端失败: {e}")
                return

        _log("UIA", "强制升级启动物理讲述人...")
        if _uia_client_stop:
            _uia_client_stop.set()
        if _uia_client_thread:
            _uia_client_thread.join(timeout=1.0)
        _uia_client_stop = _uia_client_thread = None

        if _try_launch_narrator():
            _active_strategy = "narrator"
            return

        if _try_launch_magnifier():
            _active_strategy = "magnifier"

def stop_narrator(force_cleanup: bool = False):
    """关闭辅助功能客户端并清理，还原系统音量"""
    global _active_strategy, _uia_client_stop, _uia_client_thread, _narrator_ref_count, _system_muted_by_us
    with _narrator_ref_lock:
        if force_cleanup:
            _narrator_ref_count = 0
            _log("UIA", "stop_narrator() 强制清理并停止讲述人")
        else:
            _narrator_ref_count = max(0, _narrator_ref_count - 1)
            _log("UIA", f"stop_narrator() 引用减少 -> {_narrator_ref_count}")
            if _narrator_ref_count > 0:
                return

        _log("UIA", "✓ 讲述人辅助功能客户端已被停止并清理")
        _active_strategy = None

        # 无论之前激活的是哪个策略，在真正停用清理时，均彻底无条件关闭可能残留的 Narrator 和 Magnify 进程
        _stop_process("Narrator")
        _stop_process("Magnify")

        # 同时停用可能在后台运行的 Python UIA 模拟订阅客户端
        if _uia_client_stop:
            _uia_client_stop.set()
        if _uia_client_thread:
            _uia_client_thread.join(timeout=2.0)
        _uia_client_stop = _uia_client_thread = None

        # 还原系统静音状态，保证流程结束后无条件开启音量
        try:
            set_system_mute_state(False)
            _log("UIA", "✓ 讲述人已关闭，系统音量恢复开启")
        except Exception as e_vol:
            _log("UIA", f"开启系统音量异常: {e_vol}")
        _system_muted_by_us = False

def _find_executable(name: str) -> str | None:
    import shutil
    if shutil.which(name): return name
    for p in [rf"C:\Windows\sysnative\{name}", rf"C:\Windows\System32\{name}"]:
        if os.path.exists(p): return p
    return None

def _try_launch_narrator() -> bool:
    global _original_mute_state, _system_muted_by_us
    
    # 强制先清理残留的可能已僵死的物理讲述人进程，确保全新拉起
    try:
        _stop_process("Narrator")
    except:
        pass

    exe = _find_executable("Narrator.exe")
    if not exe: return False
    
    # 物理讲述人启动前：一律先开启系统静音防噪
    try:
        _original_mute_state = get_system_mute_state()
        set_system_mute_state(True)
        _system_muted_by_us = True
        _log("UIA", "✓ 已对系统实施临时静音以屏蔽讲述人声音")
    except:
        pass

    try:
        subprocess.Popen([exe], creationflags=subprocess.CREATE_NO_WINDOW | 8, close_fds=True)
        time.sleep(1.2)
        _minimize_accessibility_window("讲述人", "Narrator")
        return True
    except:
        try:
            import ctypes
            if ctypes.windll.shell32.ShellExecuteW(None, None, exe, None, None, 6) > 32:
                time.sleep(1.5)
                _minimize_accessibility_window("讲述人", "Narrator")
                return True
        except: pass
    return False

def _try_launch_magnifier() -> bool:
    global _original_mute_state, _system_muted_by_us
    
    # 强制先清理残留的可能已缩放的放大镜进程
    try:
        _stop_process("Magnify")
    except:
        pass

    exe = _find_executable("Magnify.exe")
    if not exe: return False
    
    try:
        _original_mute_state = get_system_mute_state()
        set_system_mute_state(True)
        _system_muted_by_us = True
    except:
        pass

    try:
        subprocess.Popen([exe], creationflags=subprocess.CREATE_NO_WINDOW | 8, close_fds=True)
        time.sleep(1.0)
        _minimize_accessibility_window("放大镜", "Magnifier")
        return True
    except:
        try:
            import ctypes
            if ctypes.windll.shell32.ShellExecuteW(None, None, exe, None, None, 6) > 32:
                time.sleep(1.2)
                _minimize_accessibility_window("放大镜", "Magnifier")
                return True
        except: pass
    return False

def _minimize_accessibility_window(cn_name: str, en_name: str):
    try:
        def minimize(hwnd, _):
            title = win32gui.GetWindowText(hwnd)
            if cn_name in title or en_name in title:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        win32gui.EnumWindows(minimize, None)
    except:
        pass

def _uia_event_client_loop(stop_event: threading.Event):
    try:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        uia_obj = comtypes.CoCreateInstance(
            comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}"), interface=mod.IUIAutomation
        )
        class FocusHandler(comtypes.COMObject):
            _com_interfaces_ = [mod.IUIAutomationFocusChangedEventHandler]
            def HandleFocusChangedEvent(self, sender): return 0
        handler = FocusHandler()
        uia_obj.AddFocusChangedEventHandler(None, handler)
        _log("UIA", "✓ 已注册 UIA FocusChangedEventHandler（模拟激活）")
        _actively_probe_wechat_tree(uia_obj, mod)

        import ctypes as ct
        from ctypes import wintypes
        msg = wintypes.MSG()
        probe_counter = 0
        while not stop_event.is_set():
            while ct.windll.user32.PeekMessageW(ct.byref(msg), None, 0, 0, 1):
                ct.windll.user32.TranslateMessage(ct.byref(msg))
                ct.windll.user32.DispatchMessageW(ct.byref(msg))
            probe_counter += 1
            if probe_counter <= 100 and probe_counter % 30 == 0:
                _actively_probe_wechat_tree(uia_obj, mod)
            stop_event.wait(0.1)
        try: uia_obj.RemoveFocusChangedEventHandler(handler)
        except: pass
    except Exception as e:
        _log("UIA", f"UIA 事件客户端异常: {e}，降级为轮询探查...")
        _uia_probe_fallback(stop_event)

def _actively_probe_wechat_tree(uia_obj, mod):
    try:
        import ctypes as ct
        hwnds = []
        win32gui.EnumWindows(lambda h, _: hwnds.append(h) or True if win32gui.GetClassName(h).endswith(("WeChatMainWndForPC", "Qt51514QWindowIcon")) else True, None)
        for hwnd in hwnds:
            elem = uia_obj.ElementFromHandle(ct.c_int(hwnd))
            if elem:
                try: _ = elem.CurrentName
                except: pass
    except: pass

def _uia_probe_fallback(stop_event: threading.Event):
    try:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread(debug=False):
            while not stop_event.is_set():
                try:
                    root = auto.GetRootControl()
                    for child in root.GetChildren():
                        if stop_event.is_set(): break
                        try: _ = child.Name
                        except: pass
                except: pass
                stop_event.wait(2.0)
    except: pass

def _stop_process(name: str):
    try:
        def close_win(hwnd, _):
            title = win32gui.GetWindowText(hwnd)
            cname = name.replace(".exe", "")
            if cname in title or cname.lower() in title.lower():
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        win32gui.EnumWindows(close_win, None)
        time.sleep(0.5)
    except: pass
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", f"{name}.exe"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
    except: pass
