"""
窗口管理工具函数
"""
import ctypes
import time
try:
    import win32gui
except ImportError:
    win32gui = None

def try_bring_wechat_window_to_front():
    """通过模拟微信系统级热键 Ctrl+Alt+W 尝试唤出微信"""
    try:
        user32 = ctypes.windll.user32
        VK_CONTROL = 17
        VK_MENU = 18
        VK_W = 87
        KEYEVENTF_KEYUP = 2

        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_W, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_W, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        return True
    except:
        return False


def force_focus_window(hwnd):
    """
    强制将窗口置顶，绕过 Windows 的焦点窃取保护。
    结合了常规切换与 Heavy Path（模拟 Alt 键、AttachThreadInput 和取消前台锁定超时）的组合拳。
    """
    if not hwnd:
        return False
    
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 如果已经是当前前台窗口，直接返回 True
        if user32.GetForegroundWindow() == hwnd:
            return True

        # 如果窗口隐藏/不可见，优先通过点击托盘图标唤回，其次通过快捷键呼出
        if not user32.IsWindowVisible(hwnd):
            try:
                from src.uia.retry.tray import click_wechat_tray_icon
                if click_wechat_tray_icon():
                    time.sleep(0.8)
            except Exception:
                pass
            if not user32.IsWindowVisible(hwnd):
                try_bring_wechat_window_to_front()
                time.sleep(0.5)

        # 还原或显示窗口
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW

        # 尝试标准置顶
        if user32.SetForegroundWindow(hwnd):
            return True

        user32.SwitchToThisWindow(hwnd, True)
        if user32.GetForegroundWindow() == hwnd:
            return True

        # 如果标准 API 失败，使用重型“置顶组合拳”
        foreground_window = user32.GetForegroundWindow()
        if foreground_window == hwnd:
            return True

        foreground_thread_id = user32.GetWindowThreadProcessId(foreground_window, None)
        current_thread_id = kernel32.GetCurrentThreadId()
        attached = False

        if foreground_thread_id != current_thread_id and foreground_window != 0:
            try:
                # 附加输入线程
                attached = user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)
                
                # 临时消除前台锁定超时限制
                SPI_SETFOREGROUNDLOCKTIMEOUT = 8193
                SPIF_SENDWININICHANGE = 2
                SPIF_UPDATEINIFILE = 1
                old_timeout = ctypes.c_uint32()
                
                user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(old_timeout), 0)
                user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDWININICHANGE | SPIF_UPDATEINIFILE)
                
                # 模拟按下/弹起 Alt 键，突破切换权限限制
                ALT_KEY = 18
                KEYEVENTF_KEYUP = 2
                user32.keybd_event(ALT_KEY, 0, 0, 0)
                user32.keybd_event(ALT_KEY, 0, KEYEVENTF_KEYUP, 0)
                
                # 再次执行置顶
                user32.SetForegroundWindow(hwnd)
                user32.SwitchToThisWindow(hwnd, True)
                time.sleep(0.05)
                result = user32.GetForegroundWindow() == hwnd
                
                # 还原超时设置
                user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(old_timeout.value), SPIF_SENDWININICHANGE | SPIF_UPDATEINIFILE)
                
                # 解绑输入线程
                if attached:
                    user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)
                
                return result
            except:
                if attached:
                    try:
                        user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)
                    except:
                        pass
    except Exception:
        pass
    
    return False


def tile_all_wechat_windows(hwnds: list, compact: bool = False):
    """一键平铺布局：在所有显示屏上按顺序横向排列窗口。

    Args:
        hwnds:   待排列的窗口句柄列表
        compact: True 表示紧凑模式（屏幕偏紧时自动合定），
                 False 表示标准模式（宽松布局）
    """
    if not hwnds:
        return

    import os
    import win32api
    import win32con
    import win32gui
    import win32process

    try:
        # 1. 获取所有显示器的工作区信息，并从左往右排序
        monitors = []
        for monitor in win32api.EnumDisplayMonitors():
            info = win32api.GetMonitorInfo(monitor[0])
            monitors.append(info["Work"])  # (left, top, right, bottom)
        monitors.sort(key=lambda x: x[0])
        num_monitors = len(monitors)

        pid = os.getpid()
        import psutil
        
        # 2. 区分微信窗口与本程序窗口，并将微信窗口按进程创建时间升序排序（保证老微信在前，新多开微信排在后面）
        wechat_hwnds = []
        main_hwnds = []
        
        for hwnd in hwnds:
            if not win32gui.IsWindow(hwnd):
                continue
            try:
                _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                if win_pid == pid:
                    main_hwnds.append(hwnd)
                else:
                    wechat_hwnds.append(hwnd)
            except Exception:
                wechat_hwnds.append(hwnd)
                
        def get_process_create_time(h):
            try:
                _, win_pid = win32process.GetWindowThreadProcessId(h)
                return psutil.Process(win_pid).create_time()
            except Exception:
                return 0
                
        wechat_hwnds.sort(key=get_process_create_time)
        
        # 紧凑模式：每个微信压缩至 500px，避免叠放
        WECHAT_WIDTH = 500 if compact else 1000
        MAIN_WIDTH   = 700 if compact else 900

        window_configs = []
        for hwnd in wechat_hwnds:
            window_configs.append((hwnd, WECHAT_WIDTH))
        for hwnd in main_hwnds:
            window_configs.append((hwnd, MAIN_WIDTH))

        # 3. 挨个屏幕横向排列，不占满屏幕宽度，高度刚好是屏幕高度
        monitor_idx = 0
        current_x = monitors[0][0]

        for hwnd, width in window_configs:
            m_left, m_top, m_right, m_bottom = monitors[monitor_idx]
            m_height = m_bottom - m_top

            # 如果当前屏幕剩余宽度不够，且还有下一个屏幕，则移至下一个屏幕
            if current_x + width > m_right and monitor_idx + 1 < num_monitors:
                monitor_idx += 1
                m_left, m_top, m_right, m_bottom = monitors[monitor_idx]
                m_height = m_bottom - m_top
                current_x = m_left

            # 再次检查，如果超出当前屏幕右边界，则重置回屏幕左侧（发生叠放）
            if current_x + width > m_right:
                current_x = m_left

            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, current_x, m_top, width, m_height, win32con.SWP_SHOWWINDOW)
            except Exception as e:
                print(f"[window_utils] 移动窗口并设置Z-order失败: {e}")

            current_x += width

    except Exception as e:
        print(f"[window_utils] 一键物理排列异常: {e}")


def minimize_other_wechat_windows(target_hwnd: int, instances: dict) -> list:
    """临时最小化除 target_hwnd 外的所有微信窗口"""
    minimized = []
    for hwnd, inst in instances.items():
        if hwnd != target_hwnd and inst.driver.is_connected():
            try:
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
                minimized.append(hwnd)
            except:
                pass
    if minimized:
        time.sleep(0.3)
    return minimized


def restore_wechat_windows(hwnds: list):
    """恢复先前最小化的微信窗口"""
    for hwnd in hwnds:
        try:
            ctypes.windll.user32.ShowWindow(hwnd, 9)
        except:
            pass


def handle_tile_and_restore(all_hwnds: list, is_tiled_state: bool, saved_positions: dict):
    """
    具体的窗口平铺与还原业务逻辑。
    """
    import win32gui
    import win32con
    
    if is_tiled_state:
        # 还原位置
        restored_count = 0
        for hwnd in all_hwnds:
            if hwnd in saved_positions and win32gui.IsWindow(hwnd):
                try:
                    left, top, width, height = saved_positions[hwnd]
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, left, top, width, height, win32con.SWP_SHOWWINDOW)
                    restored_count += 1
                except Exception:
                    pass
        saved_positions.clear()
        return "restore", restored_count
    else:
        # 平铺前保存位置
        saved_positions.clear()
        for hwnd in all_hwnds:
            if win32gui.IsWindow(hwnd):
                try:
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                    width = right - left
                    height = bottom - top
                    saved_positions[hwnd] = (left, top, width, height)
                except Exception:
                    pass

        try:
            tile_all_wechat_windows(all_hwnds)
        except Exception as e:
            saved_positions.clear()
            raise e

        return "tile", len(all_hwnds)


def auto_tile_wechat_windows(new_count: int) -> list:
    """多开后自动平铺所有微信窗口，避免重叠"""
    import os
    import win32gui
    import win32process

    details = []
    try:
        time.sleep(4)
        wechat_hwnds = []

        def enum_cb(hwnd, _):
            try:
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd)
                from src.uia.modules.core.connect import _is_wechat_title
                is_wechat = _is_wechat_title(title)
                if is_wechat and win32gui.IsWindowVisible(hwnd):
                    r = win32gui.GetWindowRect(hwnd)
                    w = r[2] - r[0]
                    h = r[3] - r[1]
                    # 仅平铺已经完全登录的主界面窗口 (宽 >= 500 且 高 >= 400)，排除较小的登录窗口
                    if w >= 500 and h >= 400:
                        from src.uia.startup_flow.utils import is_wechat_main_window
                        if is_wechat_main_window(hwnd):
                            wechat_hwnds.append(hwnd)
            except Exception:
                pass

        win32gui.EnumWindows(enum_cb, None)

        # 确保本程序的主窗口也参与排版，跟在最后一个微信的后面
        main_hwnd = 0
        try:
            pid = os.getpid()
            def enum_main_cb(hwnd, _):
                nonlocal main_hwnd
                if win32gui.IsWindowVisible(hwnd):
                    _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if win_pid == pid:
                        title = win32gui.GetWindowText(hwnd)
                        if "xm-bot4" in title:
                            main_hwnd = hwnd
                            return False
                return True
            win32gui.EnumWindows(enum_main_cb, None)
        except Exception:
            pass

        if main_hwnd and main_hwnd not in wechat_hwnds:
            wechat_hwnds.append(main_hwnd)

        if len(wechat_hwnds) <= 1:
            details.append("仅检测到 1 个窗口，无需平铺")
            return details

        tile_all_wechat_windows(wechat_hwnds)
        details.append(f"✅ 已将 {len(wechat_hwnds)} 个窗口平铺到所有显示器")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[多开] 自动平铺异常: {e}")
        details.append(f"⚠ 自动平铺异常: {e}")

    return details


def is_wechat_name(cn: str) -> bool:
    """统一判断名称是否包含微信/WeChat/Weixin等关键字，忽略大小写"""
    if not cn:
        return False
    lower_cn = cn.lower()
    return "微信" in cn or "wechat" in lower_cn or "weixin" in lower_cn