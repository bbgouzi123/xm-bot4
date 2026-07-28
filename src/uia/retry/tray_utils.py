"""系统托盘操作的通用辅助函数"""

import ctypes
import time
import win32api

_TRAY_EXCLUDE_KEYWORDS = ("Docker", "docker", "OneDrive", "Teams", "Slack", "Discord", "Telegram", "钉钉", "飞书", "企业微信")


def _is_win11() -> bool:
    """检测是否为 Windows 11（Build >= 22000）"""
    import platform
    try:
        build = int(platform.version().split('.')[2])
        return build >= 22000
    except Exception:
        return False


def _do_click(ctrl, right_click: bool, user32, label: str = "") -> bool:
    """在给定控件的中心位置执行鼠标点击，成功返回 True。"""
    try:
        from src.utils.user_activity import is_user_active
        if is_user_active(cooldown_ms=3000):
            print(f"[托盘点击] 避让：检测到用户活跃，跳过点击 {label}")
            return False

        r = ctrl.BoundingRectangle
        if r.right <= r.left or r.bottom <= r.top:
            return False
        x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2

        screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        if x < screen_w // 2:
            print(f"[托盘点击] ⚠️ 检测到异常托盘坐标: ({x}, {y})，已被防错机制过滤")
            return False

        ctrl_name = getattr(ctrl, 'Name', '') or ''
        print(f"[托盘点击] 正在点击{label}: name={ctrl_name!r}, 坐标=({x}, {y}), rect=({r.left},{r.top},{r.right},{r.bottom})")
        old = win32api.GetCursorPos()
        win32api.SetCursorPos((x, y))
        time.sleep(0.1)
        if right_click:
            user32.mouse_event(0x0008, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTDOWN
            time.sleep(0.05)
            user32.mouse_event(0x0010, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTUP
        else:
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
            time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        time.sleep(0.3)
        win32api.SetCursorPos(old)
        return True
    except Exception:
        return False


def _is_wechat_tray_ctrl(ctrl) -> bool:
    """判断控件名称是否为微信托盘图标（严格排除企业微信、Docker 等干扰项）"""
    cn = getattr(ctrl, "Name", "") or ""
    if not cn:
        return False
    for kw in _TRAY_EXCLUDE_KEYWORDS:
        if kw in cn:
            return False
    # 校验尺寸限制（真实托盘图标应为正方形小按钮，限制在 80x80 以内，防止误匹配大面板容器）
    try:
        r = ctrl.BoundingRectangle
        w = r.right - r.left
        h = r.bottom - r.top
        if w > 80 or h > 80:
            return False
    except Exception:
        pass
    from src.utils.window_utils import is_wechat_name
    return is_wechat_name(cn)
