"""微信任务栏按钮操作"""

import ctypes
import time
import win32api
from .tray import safe_com_init
from .tray_utils import _is_win11
from src.utils.safe_uia import safe_walk_control

def click_wechat_taskbar_button(hwnd: int = 0) -> bool:
    """点击 Windows 任务栏上的微信运行按钮（非托盘图标），将被遮挡的微信窗口置顶。

    核心增强：当微信有多个窗口（主窗口+通讯录管理器等）时，
    点击任务栏按钮会弹出多窗口预览缩略图，此时自动检测并点击第一个预览来激活主窗口。
    """
    user32 = ctypes.windll.user32
    win11 = _is_win11()

    def _inner_search() -> bool:
        try:
            import uiautomation as auto
            with safe_com_init():
                taskbar = auto.PaneControl(ClassName="Shell_TrayWnd")
                if not taskbar.Exists(1, 0.5):
                    return False

                screen_w = user32.GetSystemMetrics(0)
                right_zone_start = screen_w * 2 // 3

                cnt = 0
                for ctrl, _ in safe_walk_control(taskbar, max_depth=8):
                    cnt += 1
                    if cnt > 500:
                        break
                    cn = getattr(ctrl, "Name", "") or ""
                    ct = getattr(ctrl, "ControlTypeName", "") or ""
                    from src.utils.window_utils import is_wechat_name
                    if is_wechat_name(cn) and ct in ("ButtonControl", "ListItemControl"):
                        if "企业微信" in cn:
                            continue
                        try:
                            from src.utils.user_activity import is_user_active
                            if is_user_active(cooldown_ms=3000):
                                print("[任务栏] 避让：检测到用户活跃，跳过点击任务栏微信按钮")
                                return False

                            r = ctrl.BoundingRectangle
                            if r.right <= r.left or r.bottom <= r.top:
                                continue
                            x = (r.left + r.right) // 2
                            y = (r.top + r.bottom) // 2
                            if x >= right_zone_start:
                                continue
                            old = win32api.GetCursorPos()
                            win32api.SetCursorPos((x, y))
                            time.sleep(0.1)
                            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                            time.sleep(0.05)
                            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                            time.sleep(0.5)
                            print(f"[任务栏] ✓ 已点击微信任务栏按钮 ({x}, {y})")

                            if hwnd and user32.GetForegroundWindow() == hwnd:
                                win32api.SetCursorPos(old)
                                return True

                            from .taskbar_preview import click_first_taskbar_preview
                            preview_clicked = click_first_taskbar_preview(hwnd, x, y, old)
                            if preview_clicked:
                                return True

                            win32api.SetCursorPos(old)
                            return True
                        except Exception:
                            continue
        except Exception as e:
            print(f"[任务栏] 搜索微信按钮异常: {e}")
        return False

    res = _inner_search()
    if res:
        return True

    # 自动隐藏任务栏兼容：悬停鼠标到最底部滑出任务栏
    # 注意：GetCursorPos 在某些 Windows 权限策略下会抛出 ERROR_ACCESS_DENIED(5)，
    # 此时不能继续执行第二次 _inner_search，否则会浪费 1~5s 做无效 UIA 遍历。
    try:
        from src.utils.user_activity import is_user_active
        if is_user_active(cooldown_ms=3000):
            print("[任务栏] 避让：检测到用户活跃，跳过自动隐藏任务栏悬停滑出")
            return False

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        try:
            old_pos = win32api.GetCursorPos()
        except Exception as e_cur:
            # GetCursorPos 被权限策略拒绝，无法移动鼠标滑出任务栏，立即放弃
            print(f"[任务栏] 自动隐藏任务栏兼容滑出失败: {e_cur}")
            return False
        try:
            win32api.SetCursorPos((screen_w // 2, screen_h - 2))
        except Exception as e_set:
            print(f"[任务栏] 自动隐藏任务栏兼容滑出失败: {e_set}")
            return False
        time.sleep(0.3)
        res = _inner_search()
        try:
            win32api.SetCursorPos(old_pos)
        except Exception:
            pass
        if res:
            return True
    except Exception as e:
        print(f"[任务栏] 自动隐藏任务栏兼容滑出失败: {e}")

    print("[任务栏] 未找到微信任务栏按钮（可能微信未固定在任务栏）")
    return False
