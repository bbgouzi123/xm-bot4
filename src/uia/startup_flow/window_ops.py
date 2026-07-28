import time
import ctypes
import win32gui
import win32con
from .utils import _log, _random_sleep

def force_focus_window(hwnd: int) -> bool:
    """
    强制将微信窗口置前（多策略唤醒前台焦点）。

    三级策略：
    1. Fast Path: SetForegroundWindow
    2. ShowWindow(SW_RESTORE) + SetForegroundWindow
    3. AttachThreadInput + Alt key 组合拳
    """
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if not win32gui.IsWindow(hwnd):
            return False

        # 已在前台
        if user32.GetForegroundWindow() == hwnd:
            return True

        # 最小化则恢复
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE

        user32.ShowWindow(hwnd, 5)       # SW_SHOW

        # Fast path
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True

        # SwitchToThisWindow
        try:
            user32.SwitchToThisWindow(hwnd, True)
            time.sleep(0.05)
            if user32.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass

        # Heavy path: AttachThreadInput 组合拳
        fg = user32.GetForegroundWindow()
        if fg and fg != hwnd:
            fg_tid = user32.GetWindowThreadProcessId(fg, None)
            cur_tid = kernel32.GetCurrentThreadId()
            attached = False
            if fg_tid != cur_tid and fg_tid != 0:
                attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
            # 模拟 Alt 按键释放焦点锁定
            user32.keybd_event(0x12, 0, 0, 0)       # VK_MENU down
            user32.keybd_event(0x12, 0, 2, 0)       # VK_MENU up
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.05)
            if attached:
                user32.AttachThreadInput(fg_tid, cur_tid, False)

        return user32.GetForegroundWindow() == hwnd
    except Exception as e:
        _log("置前", f"force_focus 异常: {e}")
        return False


def _activate_taskbar_window(hwnd: int) -> bool:
    """
    通过多种手段将微信窗口从任务栏激活到前台。

    微信新启动时，登录窗口可能被 Windows 折叠到任务栏中不可见。
    force_focus_window 处理的是已经存在的窗口重新置前，
    这里额外增加了针对「任务栏中不可见」场景的处理。

    策略：
    1. ShowWindow(SW_SHOW) + SetForegroundWindow（常规）
    2. ShowWindow(SW_RESTORE) 恢复最小化
    3. BringWindowToTop
    4. 模拟 Alt 键解锁焦点 + SetForegroundWindow
    5. SetWindowPos TOPMOST 闪现
    """
    try:
        user32 = ctypes.windll.user32

        if not win32gui.IsWindow(hwnd):
            return False

        # 1. 基础显示
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        time.sleep(0.1)
        user32.ShowWindow(hwnd, 5)   # SW_SHOW
        time.sleep(0.1)

        # 2. BringWindowToTop
        user32.BringWindowToTop(hwnd)
        time.sleep(0.05)

        # 3. SetForegroundWindow
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        if user32.GetForegroundWindow() == hwnd:
            _log("置前", f"✓ 微信窗口已激活到前台 hwnd={hwnd}")
            return True

        # 4. 模拟 Alt 键解锁 + SetForegroundWindow
        user32.keybd_event(0x12, 0, 0, 0)       # VK_MENU down
        user32.keybd_event(0x12, 0, 2, 0)       # VK_MENU up
        time.sleep(0.05)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        if user32.GetForegroundWindow() == hwnd:
            _log("置前", f"✓ 微信窗口已通过 Alt 激活 hwnd={hwnd}")
            return True

        # 5. TOPMOST 闪现策略：临时置顶再取消
        SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        time.sleep(0.1)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)

        is_fg = user32.GetForegroundWindow() == hwnd
        if is_fg:
            _log("置前", f"✓ 微信窗口已通过 TOPMOST 激活 hwnd={hwnd}")
        else:
            _log("置前", f"⚠ 尝试激活微信窗口，可能仍在任务栏 hwnd={hwnd}")
        return is_fg
    except Exception as e:
        _log("置前", f"激活窗口异常: {e}")
        return False


def simulate_wechat_show_hotkey(hwnd: int = None):
    """模拟 Ctrl + Alt + W (微信默认热键)。
    !! 重要 !! 微信热键是 Toggle 机制（显示/隐藏切换）。
    如果窗口已经是可见状态且在前台，按此键会将其“隐藏”到托盘，产生负面效果。
    """
    try:
        if hwnd:
            if win32gui.IsWindow(hwnd):
                is_visible = win32gui.IsWindowVisible(hwnd)
                # 获取窗口放置信息，判断是否最小化
                placement = win32gui.GetWindowPlacement(hwnd)
                is_minimized = placement[1] == win32con.SW_SHOWMINIMIZED
                
                # 如果窗口已经可见且不是最小化，绝对不要按热键，否则会将其隐藏
                if is_visible and not is_minimized:
                    _log("热键", "窗口已直接显示，跳过 Ctrl+Alt+W 唤醒热键")
                    return

        _log("热键", "正在发送 Ctrl+Alt+W 强制唤起微信主界面...")
        user32 = ctypes.windll.user32
        VK_CONTROL = 0x11
        VK_MENU = 0x12
        VK_W = 0x57
        KEYEVENTF_KEYUP = 0x0002
        
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_W, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_W, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def nudge_window(hwnd: int):
    """微移窗口 1 像素再恢复，触发 Qt 重绘/重建控件树"""
    try:
        rect = win32gui.GetWindowRect(hwnd)
        x, y, r, b = rect
        w, h = r - x, b - y
        win32gui.MoveWindow(hwnd, x, y, w + 1, h + 1, True)
        time.sleep(0.08)
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
    except Exception:
        pass


def _ensure_window_on_screen(hwnd: int):
    """确保微信窗口在屏幕可见区域内。

    Qt Accessibility (uiautomation 库) 在窗口完全 offscreen 时可能无法
    正确遍历控件树——即使原生 IUIAutomation COM 接口可以。
    典型场景：用户有多显示器但当前只连了一个，或上次关闭时窗口在副屏。
    """
    try:
        user32 = ctypes.windll.user32
        rect = win32gui.GetWindowRect(hwnd)
        x, y, r, b = rect
        w, h = r - x, b - y

        # 获取虚拟屏幕边界（考虑多显示器）
        vs_x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vs_y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vs_w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vs_h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        vs_right = vs_x + vs_w
        vs_bottom = vs_y + vs_h

        # 判断窗口是否基本上在屏幕外（至少 90% 的面积不可见）
        visible_left = max(x, vs_x)
        visible_top = max(y, vs_y)
        visible_right = min(r, vs_right)
        visible_bottom = min(b, vs_bottom)

        visible_w = max(0, visible_right - visible_left)
        visible_h = max(0, visible_bottom - visible_top)
        visible_area = visible_w * visible_h
        total_area = max(w * h, 1)

        if visible_area < total_area * 0.1:
            # 窗口 90%+ 在屏幕外，移到主显示器中心
            primary_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            primary_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            new_x = max(0, (primary_w - w) // 2)
            new_y = max(0, (primary_h - h) // 2)
            _log("窗口", f"⚠ 微信窗口在屏幕外 ({x},{y},{r},{b})，移回屏幕 ({new_x},{new_y})")
            win32gui.MoveWindow(hwnd, new_x, new_y, w, h, True)
            time.sleep(0.5)
            force_focus_window(hwnd)
            _random_sleep(0.3, 0.5)
    except Exception as e:
        _log("窗口", f"屏幕位置检测异常: {e}")
