"""任务栏多窗口预览缩略图自动点击。

当微信有多个窗口（主窗口 + 通讯录管理器等）时，点击任务栏图标会
弹出预览缩略图而非直接激活窗口。本模块检测该弹窗并点击第一个预览。
"""

import ctypes
import time
import win32api
import win32gui
import uiautomation as auto
from src.utils.safe_uia import safe_walk_control

def _is_win11() -> bool:
    """检测是否为 Windows 11（Build >= 22000）"""
    import platform
    try:
        build = int(platform.version().split('.')[2])
        return build >= 22000
    except Exception:
        return False


def click_first_taskbar_preview(hwnd: int, btn_x: int, btn_y: int, old_cursor_pos) -> bool:
    """当任务栏弹出多窗口预览缩略图时，自动点击第一个（最大的）预览来激活主窗口。

    包含了 UIA 识别点击 与 几何坐标偏置物理盲点 两个核心保障方案。
    """
    user32 = ctypes.windll.user32
    
    # ── 1. 尝试 UIA 自动识别方案 ──
    try:
        with auto.UIAutomationInitializerInThread(debug=False):
            # ── 方案 A：Win10 经典预览窗口 ──
            preview_wnd = auto.WindowControl(ClassName="TaskListThumbnailWnd")
            if preview_wnd.Exists(0.5, 0.2):
                print("[任务栏] 检测到多窗口预览弹窗 (TaskListThumbnailWnd)，正在点击第一个预览...")
                cnt = 0
                for ctrl, _ in safe_walk_control(preview_wnd, max_depth=5):
                    cnt += 1
                    if cnt > 100:
                        break
                    ct = getattr(ctrl, "ControlTypeName", "") or ""
                    cn = getattr(ctrl, "Name", "") or ""
                    if ct in ("ButtonControl", "ListItemControl", "WindowControl") and cn:
                        r = ctrl.BoundingRectangle
                        if r.right <= r.left or r.bottom <= r.top:
                            continue
                        x = (r.left + r.right) // 2
                        y = (r.top + r.bottom) // 2

                        from src.utils.user_activity import is_user_active
                        if is_user_active(cooldown_ms=3000):
                            print("[任务栏预览] 避让：检测到用户活跃，跳过点击缩略图")
                            return False

                        win32api.SetCursorPos((x, y))
                        time.sleep(0.1)
                        user32.mouse_event(0x0002, 0, 0, 0, 0)
                        time.sleep(0.05)
                        user32.mouse_event(0x0004, 0, 0, 0, 0)
                        time.sleep(0.3)
                        win32api.SetCursorPos(old_cursor_pos)
                        if hwnd and user32.GetForegroundWindow() == hwnd:
                            print(f"[任务栏] ✓ 物理点击第一个预览缩略图成功: name={cn!r}")
                            return True
                        
            # ── 方案 B：Win11 预览弹窗（XAML 渲染） ──
            if _is_win11():
                desktop = auto.GetRootControl()
                cnt = 0
                for ctrl, _ in safe_walk_control(desktop, max_depth=3):
                    cnt += 1
                    if cnt > 150:
                        break
                    cn = getattr(ctrl, "Name", "") or ""
                    cls = getattr(ctrl, "ClassName", "") or ""
                    ct = getattr(ctrl, "ControlTypeName", "") or ""
                    if ct in ("ToolTipControl", "WindowControl") and ("微信" in cn or "WeChat" in cn):
                        r = ctrl.BoundingRectangle
                        if r.right <= r.left or r.bottom <= r.top:
                            continue
                        screen_h = user32.GetSystemMetrics(1)
                        if r.top < screen_h // 2:
                            continue
                        x = (r.left + r.right) // 2
                        y = (r.top + r.bottom) // 2

                        from src.utils.user_activity import is_user_active
                        if is_user_active(cooldown_ms=3000):
                            print("[任务栏预览] 避让：检测到用户活跃，跳过点击 Win11 缩略图浮层")
                            return False

                        win32api.SetCursorPos((x, y))
                        time.sleep(0.1)
                        user32.mouse_event(0x0002, 0, 0, 0, 0)
                        time.sleep(0.05)
                        user32.mouse_event(0x0004, 0, 0, 0, 0)
                        time.sleep(0.3)
                        win32api.SetCursorPos(old_cursor_pos)
                        if hwnd and user32.GetForegroundWindow() == hwnd:
                            print(f"[任务栏] ✓ 已点击 Win11 预览浮层: name={cn!r}, cls={cls!r}")
                            return True
    except Exception as e:
        print(f"[任务栏] UIA 预览弹窗识别发生异常: {e}")

    # ── 2. 兜底方案：几何坐标偏置物理盲点点击 ──
    # 如果微信依然没有在前台，且已知点击了任务栏按钮，那说明必定是预览窗口挡住了主窗口激活。
    # 预览窗口一定显示在任务栏按钮 (btn_x, btn_y) 的垂直上方大约 80~180 像素的区域。
    if hwnd and user32.GetForegroundWindow() != hwnd:
        print(f"[任务栏-几何] UIA 方案未能激活目标窗口。启动几何盲点点击兜底...")
        # 依次在按钮上方 100, 140, 180 像素三个可能落点高度试探点击，覆盖各种 DPI 缩放下的缩略图区
        screen_h = user32.GetSystemMetrics(1)
        for offset in (110, 150, 80, 190):
            target_y = btn_y - offset
            # 安全防越界
            if target_y < 0 or target_y >= screen_h:
                continue
            
            from src.utils.user_activity import is_user_active
            if is_user_active(cooldown_ms=3000):
                print("[任务栏-几何] 避让：检测到用户活跃，跳过几何盲点点击")
                win32api.SetCursorPos(old_cursor_pos)
                return False

            print(f"[任务栏-几何] 正在盲点缩略图可能区域: ({btn_x}, {target_y})")
            win32api.SetCursorPos((btn_x, target_y))
            time.sleep(0.1)
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            time.sleep(0.3)
            
            if user32.GetForegroundWindow() == hwnd:
                print(f"[任务栏-几何] 🎯 几何偏置 {offset} 像素物理盲点点击成功激活微信窗口！")
                win32api.SetCursorPos(old_cursor_pos)
                return True
        
        # 恢复鼠标原位置
        win32api.SetCursorPos(old_cursor_pos)

    print("[任务栏] 多窗口预览检测与几何盲点方案均未能将微信置顶")
    return False
