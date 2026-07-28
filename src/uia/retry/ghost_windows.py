"""
安全唤醒与清理微信内部辅助小窗口
从 window_ops 剥离以对齐单文件 300 行质量红线限制
"""
import ctypes
import time
from collections import defaultdict
import win32gui
import win32process
import win32con
from .tray import click_wechat_tray_icon

def close_wechat_ghost_windows() -> bool:
    """安全唤醒微信主窗口（避免误关主窗口）"""
    user32 = ctypes.windll.user32
    all_windows = []

    def enum_callback(hwnd, _):
        try:
            from src.uia.modules.core.connect import _is_wechat_title
            title = win32gui.GetWindowText(hwnd)
            if _is_wechat_title(title) and win32gui.GetClassName(hwnd).endswith("Qt51514QWindowIcon"):
                r = win32gui.GetWindowRect(hwnd)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                all_windows.append((hwnd, r[2] - r[0], r[3] - r[1], win32gui.IsWindowVisible(hwnd), pid))
        except Exception:
            pass

    win32gui.EnumWindows(enum_callback, None)
    if not all_windows:
        print("[启动] 未检测到微信窗口，跳过清理")
        return False

    pid_groups = defaultdict(list)
    for hwnd, w, h, visible, pid in all_windows:
        pid_groups[pid].append((hwnd, w, h, visible))

    small_windows, normal_windows = [], []
    for item in all_windows:
        if item[1] >= 500 and item[2] >= 400:
            normal_windows.append(item)
        else:
            small_windows.append(item)
    if not small_windows and all(v for _, _, _, v, _ in normal_windows):
        return False

    _tray_clicked = False
    tray_once_for_aux_with_main = False

    for hwnd, w, h, _, pid in small_windows:
        windows_in_process = pid_groups[pid]
        has_normal = any(ww >= 500 and hh >= 400 for _, ww, hh, _ in windows_in_process)
        if len(windows_in_process) == 1:
            print(f"[启动] 微信窗口尺寸异常小: hwnd={hwnd} {w}x{h}（唯一窗口，尝试唤回）")
            if not _tray_clicked:
                click_wechat_tray_icon()
                _tray_clicked = True
        elif has_normal:
            print(f"[启动] 发现微信内部辅助小窗口 (已忽略): hwnd={hwnd} {w}x{h}")
            tray_once_for_aux_with_main = True
        else:
            print(f"[启动] 小窗口处理: hwnd={hwnd} {w}x{h}")
            if not _tray_clicked:
                click_wechat_tray_icon()
                _tray_clicked = True

    if tray_once_for_aux_with_main and not _tray_clicked:
        print("[启动] 模拟点击任务栏托盘「微信」图标一次（用于主窗口置顶/识别）…")
        click_wechat_tray_icon()
        _tray_clicked = True
        time.sleep(0.5)

    restored = False
    for hwnd, w, h, visible, _ in normal_windows:
        if visible:
            continue
        print(f"[启动] 唤回隐藏的主窗口: hwnd={hwnd} {w}x{h}")
        if _tray_clicked:
            time.sleep(0.5)
            if win32gui.IsWindowVisible(hwnd):
                restored = True
                continue
        
        if not _tray_clicked:
            if click_wechat_tray_icon():
                _tray_clicked = True
                restored = True
                continue
        
        print("[启动] 托盘未成功唤回，回退 ShowWindow + 白屏修复")
        try:
            from .wechat_healer import fix_white_screen_after_show
            user32.ShowWindow(hwnd, win32con.SW_SHOW)
            time.sleep(0.2)
            user32.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            fix_white_screen_after_show(hwnd)
            restored = True
        except Exception as e:
            print(f"[启动] 唤回失败: {e}")

    if not normal_windows and not restored:
        print("[启动] 未找到正常尺寸窗口")

    verify_results = []

    def verify_callback(hwnd, _):
        try:
            from src.uia.modules.core.connect import _is_wechat_title
            title = win32gui.GetWindowText(hwnd)
            if (
                _is_wechat_title(title)
                and win32gui.GetClassName(hwnd).endswith("Qt51514QWindowIcon")
                and win32gui.IsWindowVisible(hwnd)
            ):
                r = win32gui.GetWindowRect(hwnd)
                verify_results.append((hwnd, r[2] - r[0], r[3] - r[1]))
        except Exception:
            pass

    win32gui.EnumWindows(verify_callback, None)
    has_valid = any(w >= 500 and h >= 400 for _, w, h in verify_results)
    print("[启动] [OK] 微信主窗口已就绪" if has_valid else f"[启动] [WARN] 未检测到有效主窗口，当前可见窗口: {verify_results}")
    return has_valid
