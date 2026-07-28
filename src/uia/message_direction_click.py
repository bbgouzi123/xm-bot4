import time
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

def find_profile_hwnd() -> Optional[int]:
    """查找当前的微信资料名片窗口句柄"""
    import win32gui
    try:
        hwnd = win32gui.FindWindow("mmui::ProfileUniquePop", None)
        if hwnd and win32gui.IsWindow(hwnd):
            return hwnd
    except Exception:
        pass

    # 2. 物理/Win32 遍历作为备用兜底
    res = []
    def enum_cb(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd)
            if "ToolSaveBits" in cls or "ProfileUniquePop" in cls:
                title = win32gui.GetWindowText(hwnd)
                if title in ("", "Weixin", "微信"):
                    rect = win32gui.GetWindowRect(hwnd)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if 300 < w < 600 and 400 < h < 1000:
                        res.append(hwnd)
        except Exception:
            pass
    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass
    return res[0] if res else None


def detect_is_self_by_avatar_click(ctrl, session_name: Optional[str] = None) -> Optional[bool]:
    """
    通过物理点击头像并检测是否弹出好友资料窗口来判断消息是否是自己发送的。
    """
    import win32gui
    import win32con
    import uiautomation as uia
    
    from src.uia.message_direction_helper import get_dpi_scale, find_avatar_rect
    scale = get_dpi_scale()
    ctrl_rect = ctrl.BoundingRectangle
    if not ctrl_rect or ctrl_rect.width() <= 0:
        return None
        
    avatar_rect = find_avatar_rect(ctrl, scale)
    if avatar_rect:
        x = (avatar_rect.left + avatar_rect.right) // 2
        y = (avatar_rect.top + avatar_rect.bottom) // 2
    else:
        # 降级方案：对于不导出子头像节点的微信新版气泡控件，按物理左偏置位置估算好友头像中心坐标进行点击
        x = ctrl_rect.left + int(38 * scale)
        y = ctrl_rect.top + int(30 * scale)
        logger.info(f"[消息方向] 物理点击检测未找到头像子控件，启用降级左偏置偏好坐标判定: ({x}, {y})")

    logger.debug(f"[消息方向] 准备通过物理点击头像判定方向，头像坐标: ({x}, {y})")
    
    # 1. 清理可能残留的名片窗口，防止误判
    try:
        hwnd = find_profile_hwnd()
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.2)
    except Exception:
        pass
        
    # 2. 执行物理点击
    try:
        from src.uia.retry.clicks import physical_click
        physical_click(x, y)
    except Exception as click_ex:
        logger.error(f"[消息方向] 物理点击头像异常: {click_ex}")
        return None
        
    # 3. 等待名片弹窗并检验是否为好友名片
    profile_win = None
    is_friend = False
    hwnd = None
    for _ in range(8):
        hwnd = find_profile_hwnd()
        if hwnd:
            try:
                profile_win = uia.ControlFromHandle(hwnd)
                break
            except Exception:
                pass
        time.sleep(0.1)
        
    if profile_win:
        try:
            voice_btn = profile_win.ButtonControl(Name="语音聊天")
            video_btn = profile_win.ButtonControl(Name="视频聊天")
            if voice_btn.Exists(0.2) or video_btn.Exists(0.2):
                is_friend = True
        except Exception:
            pass
            
        # 顺手做用户信息完整度检验，若真实 wxid 则自动同步资料
        if is_friend and session_name:
            from src.uia.profile_sync_helper import sync_contact_profile_if_needed
            sync_contact_profile_if_needed(profile_win, session_name)
            
        # 4. 关闭名片窗口
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
            
    logger.debug(f"[消息方向] 物理点击检测完成. 是否好友名片: {is_friend} => is_self={not is_friend}")
    return not is_friend
