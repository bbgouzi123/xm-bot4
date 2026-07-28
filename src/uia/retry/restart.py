"""微信重启与可访问性恢复流程。"""

import ctypes
import threading
import time
from typing import Optional

from .clicks import click_at_absolute
from .tray import click_wechat_tray_icon
from .window_ops import ensure_wechat_foreground


def _get_exe_path_from_hwnd(hwnd: int) -> str:
    """从窗口句柄获取进程 exe 路径（纯 Win32 API）。"""
    try:
        from ctypes import wintypes

        import win32process

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return ""
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            if ok and buf.value:
                return buf.value
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception as e:
        print(f"[重启] 获取 exe 路径异常: {e}")
    return ""


def exit_wechat_via_tray() -> bool:
    """通过托盘右键菜单退出微信（右键 -> 点击'退出微信'）。"""
    import uiautomation as uia

    print("[重启] 右键点击托盘微信图标...")
    if not click_wechat_tray_icon(right_click=True):
        print("[重启] 未找到托盘微信图标")
        return False
    time.sleep(0.8)
    try:
        exit_item = uia.MenuItemControl(Name="退出微信", searchDepth=5)
        if exit_item.Exists(3, 0.5):
            print("[重启] 找到'退出微信'菜单项，点击中...")
            try:
                exit_item.Click(simulateMove=False)
            except Exception:
                try:
                    rect = exit_item.BoundingRectangle
                    click_at_absolute((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                except Exception:
                    pass
            print("[重启] 已点击'退出微信'")
            return True
        print("[重启] 未找到'退出微信'菜单项")
    except Exception as e:
        print(f"[重启] 查找菜单项异常: {e}")
    try:
        ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
    except Exception:
        pass
    return False


def restart_wechat_for_accessibility(old_hwnd: int) -> Optional[int]:
    """完整的微信自动重启流程（激活 Qt 辅助功能）。"""
    import subprocess

    import uiautomation as uia
    import win32gui
    import win32process

    wx_exe = _get_exe_path_from_hwnd(old_hwnd)
    if not wx_exe:
        print("[重启] 无法获取微信 exe 路径")
        return None
    print(f"[重启] 微信路径: {wx_exe}")

    try:
        ctypes.windll.user32.ShowWindow(old_hwnd, 5)
        time.sleep(0.5)
    except Exception:
        pass
    wx_pid = None
    try:
        _, wx_pid = win32process.GetWindowThreadProcessId(old_hwnd)
    except Exception:
        pass

    tray_ok = exit_wechat_via_tray()
    if not tray_ok:
        print("[重启] 托盘退出失败，使用 taskkill 强制终止微信...")
        if wx_pid:
            try:
                _NO_WINDOW = subprocess.CREATE_NO_WINDOW
                subprocess.run(["taskkill", "/F", "/PID", str(wx_pid)], capture_output=True, timeout=5, creationflags=_NO_WINDOW)
                subprocess.run(["taskkill", "/F", "/IM", "Weixin.exe"], capture_output=True, timeout=5, creationflags=_NO_WINDOW)
            except Exception as e:
                print(f"[重启] taskkill 异常: {e}")

    print("[重启] 等待微信完全退出...")
    exited = False
    for _ in range(20):
        time.sleep(0.5)
        alive = []

        def chk_exit(h, _):
            try:
                from src.uia.modules.core.connect import _is_wechat_title
                if win32gui.GetClassName(h).endswith("Qt51514QWindowIcon") and _is_wechat_title(win32gui.GetWindowText(h)):
                    alive.append(h)
            except Exception:
                pass

        win32gui.EnumWindows(chk_exit, None)
        if not alive:
            exited = True
            break
    if exited:
        print("[重启] ✓ 微信已完全退出")
    else:
        print("[重启] 微信退出超时，强制杀进程...")
        try:
            subprocess.run(["taskkill", "/F", "/IM", "Weixin.exe"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(1.0)
        except Exception:
            pass
    time.sleep(1.5)

    print("[重启] 正在启动微信...")
    try:
        import os
        env = {**os.environ, "QT_ACCESSIBILITY": "1"}
        subprocess.Popen(
            [wx_exe],
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[重启] 启动微信失败: {e}")
        return None

    print("[重启] 等待微信窗口出现...")
    new_hwnd = None
    for _ in range(30):
        time.sleep(0.5)
        wins = []

        def chk_appear(h, _):
            try:
                from src.uia.modules.core.connect import _is_wechat_title
                if (
                    win32gui.GetClassName(h).endswith("Qt51514QWindowIcon")
                    and _is_wechat_title(win32gui.GetWindowText(h))
                    and win32gui.IsWindowVisible(h)
                ):
                    r = win32gui.GetWindowRect(h)
                    if (r[2] - r[0]) >= 200 and (r[3] - r[1]) >= 200:
                        wins.append(h)
            except Exception:
                pass

        win32gui.EnumWindows(chk_appear, None)
        if wins:
            new_hwnd = wins[0]
            break
    if not new_hwnd:
        print("[重启] 微信启动超时")
        return None
    print(f"[重启] 微信窗口已出现: hwnd={new_hwnd}")
    time.sleep(2.5)

    ensure_wechat_foreground(new_hwnd)
    time.sleep(0.5)

    from src.uia.startup_flow.login import smart_click_login_or_switch
    if smart_click_login_or_switch(new_hwnd):
        time.sleep(3.0)
    else:
        r = win32gui.GetWindowRect(new_hwnd)
        if (r[2] - r[0]) < 500 or (r[3] - r[1]) < 400:
            print("[重启] 检测到扫码登录界面")
            print("[重启] 请使用手机微信扫码登录，程序将自动等待...")
        else:
            print("[重启] 未检测到登录按钮（可能已自动登录）")

    print("[重启] 等待微信主界面完全加载...")
    main_hwnd = None
    for i in range(120):
        time.sleep(1.0)
        cands = []

        def chk_main(h, _):
            try:
                from src.uia.modules.core.connect import _is_wechat_title
                if (
                    win32gui.GetClassName(h).endswith("Qt51514QWindowIcon")
                    and _is_wechat_title(win32gui.GetWindowText(h))
                    and win32gui.IsWindowVisible(h)
                    and win32gui.IsWindow(h)
                ):
                    r = win32gui.GetWindowRect(h)
                    w, ht = r[2] - r[0], r[3] - r[1]
                    if w >= 500 and ht >= 400:
                        cands.append((h, w, ht))
            except Exception:
                pass

        win32gui.EnumWindows(chk_main, None)
        if cands:
            cands.sort(key=lambda x: x[1] * x[2], reverse=True)
            th, tw, tht = cands[0]
            if (i + 1) % 5 == 1:
                print(f"[重启] 候选窗口: hwnd={th} {tw}x{tht}")
            nav_ok = [False]

            def chk_nav():
                try:
                    import comtypes

                    comtypes.CoInitialize()
                    try:
                        ctypes.windll.user32.SetForegroundWindow(th)
                    except Exception:
                        pass
                    time.sleep(0.3)
                    root = uia.ControlFromHandle(th)
                    if root:
                        nav_ok[0] = root.ToolBarControl(Name="导航").Exists(3, 0.5)
                except Exception:
                    pass

            tn = threading.Thread(target=chk_nav, daemon=True)
            tn.start()
            tn.join(timeout=8)
            if nav_ok[0]:
                main_hwnd = th
                print(f"[重启] ✓ 微信主界面已就绪！hwnd={main_hwnd}")
                break
        if (i + 1) % 10 == 0:
            print(f"[重启] 仍在等待微信主界面... ({i + 1}秒)")

    return main_hwnd
