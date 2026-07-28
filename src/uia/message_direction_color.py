"""
message_direction_color.py
微信聊天气泡绿色像素采样判定是否为自己发的消息的逻辑模块
"""
import logging
from typing import Optional
from src.uia.message_direction_helper import find_wechat_window, mark_message_direction
from src.uia.message_direction_debug import draw_debug_cross, print_detect_result

logger = logging.getLogger(__name__)

def detect_is_self_by_color(ctrl, name: str, session_name: Optional[str], use_click_check: bool, scale: float) -> Optional[bool]:
    """
    通过对微信主窗口气泡的绿色像素在右侧进行采样以判断是否为自己发送的。
    """
    try:
        import ctypes
        import win32gui

        wx_hwnd = getattr(ctrl, "GetWindowHandle", lambda: None)()
        if not wx_hwnd or not win32gui.IsWindow(wx_hwnd):
            wx_hwnd = find_wechat_window()
        else:
            try:
                root_hwnd = win32gui.GetAncestor(wx_hwnd, 2)
                if root_hwnd:
                    wx_hwnd = root_hwnd
            except Exception:
                pass

        if wx_hwnd and not win32gui.IsIconic(wx_hwnd):
            win_rect = win32gui.GetWindowRect(wx_hwnd)
            if win_rect[2] > win_rect[0] and win_rect[3] > win_rect[1]:
                ctrl_rect = ctrl.BoundingRectangle
                if ctrl_rect and ctrl_rect.width() > 0:
                    hdc = win32gui.GetWindowDC(wx_hwnd)
                    is_green_found = False
                    y_start = ctrl_rect.top + int(15 * scale)
                    y_end = ctrl_rect.bottom - int(15 * scale)
                    step = max(1, int(6 * scale))
                    
                    sampled_points = []
                    
                    try:
                        if y_start < y_end:
                            for y_test in range(y_start, y_end, step):
                                for offset in (95, 120, 145):
                                    x = ctrl_rect.right - int(offset * scale)
                                    rel_x = x - win_rect[0]
                                    rel_y = y_test - win_rect[1]
                                    pixel = ctypes.windll.gdi32.GetPixel(hdc, int(rel_x), int(rel_y))
                                    if pixel == -1:
                                        continue
                                    r = pixel & 0xFF
                                    g = (pixel >> 8) & 0xFF
                                    b = (pixel >> 16) & 0xFF
                                    sampled_points.append((x, y_test, r, g, b))
                                    if g >= 110 and (g - r) >= 30 and (g - b) >= 30:
                                        is_green_found = True
                                        break
                                if is_green_found:
                                    break
                        else:
                            y_mid = (ctrl_rect.top + ctrl_rect.bottom) // 2
                            for offset in (95, 120, 145):
                                x = ctrl_rect.right - int(offset * scale)
                                rel_x = x - win_rect[0]
                                rel_y = y_mid - win_rect[1]
                                pixel = ctypes.windll.gdi32.GetPixel(hdc, int(rel_x), int(rel_y))
                                if pixel == -1:
                                    continue
                                r = pixel & 0xFF
                                g = (pixel >> 8) & 0xFF
                                b = (pixel >> 16) & 0xFF
                                sampled_points.append((x, y_mid, r, g, b))
                                if g >= 110 and (g - r) >= 30 and (g - b) >= 30:
                                    is_green_found = True
                                    break
                    finally:
                        win32gui.ReleaseDC(wx_hwnd, hdc)

                    if use_click_check and win32gui.GetForegroundWindow() == wx_hwnd:
                        screen_hdc = ctypes.windll.user32.GetDC(0)
                        try:
                            for sx, sy, _, _, _ in sampled_points:
                                draw_debug_cross(screen_hdc, sx, sy, scale)
                        finally:
                            ctypes.windll.user32.ReleaseDC(0, screen_hdc)
                    
                    if use_click_check:
                        print_detect_result(name, is_green_found, sampled_points)
                    
                    if is_green_found:
                        logger.debug(f"[消息解析] (颜色检测) 绿色气泡像素判定成功 => is_self=True")
                        if name:
                            mark_message_direction(name, True)
                            if session_name:
                                try:
                                    from src.utils.chat_history import ChatHistoryManager
                                    ChatHistoryManager().add_message(session_name, "我", "assistant", name)
                                except Exception:
                                    pass
                        return True
                    else:
                        logger.debug(f"[消息解析] (颜色检测) 未检测到右侧绿色气泡 => is_self=False")
                        if name:
                            mark_message_direction(name, False)
                            if session_name:
                                try:
                                    from src.utils.chat_history import ChatHistoryManager
                                    ChatHistoryManager().add_message(session_name, session_name, "user", name)
                                except Exception:
                                    pass
                        return False
    except Exception as col_ex:
        logger.debug(f"[消息解析] 颜色像素采样判断异常: {col_ex}")
    return None
