"""
消息发送方向判定辅助方法（缓存与 DPI 获取）
"""
import time
import logging
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# 🌟 全局消息方向缓存：{ 消息内容: (is_self, 记录时间) }
_MESSAGE_DIRECTION_CACHE: Dict[str, Tuple[bool, float]] = {}

def mark_message_direction(content: str, is_self: bool, session_name: Optional[str] = None):
    """主动标记消息内容的发送方向，用于规避 UIA 物理点击风控"""
    if not content:
        return
    clean_content = content.strip()
    key = f"{session_name.strip()}:{clean_content}" if session_name else clean_content
    _MESSAGE_DIRECTION_CACHE[key] = (is_self, time.time())
    
    # 防止内存泄漏，限制缓存大小，只保留最近的 100 条消息方向
    if len(_MESSAGE_DIRECTION_CACHE) > 100:
        sorted_keys = sorted(_MESSAGE_DIRECTION_CACHE.keys(), key=lambda k: _MESSAGE_DIRECTION_CACHE[k][1])
        for k in sorted_keys[:20]:
            _MESSAGE_DIRECTION_CACHE.pop(k, None)

def get_cached_message_direction(content: str, session_name: Optional[str] = None) -> Optional[bool]:
    """获取消息内容的发送方向缓存"""
    if not content:
        return None
    clean_content = content.strip()
    
    # 先尝试带 session_name 的精确缓存
    if session_name:
        key = f"{session_name.strip()}:{clean_content}"
        if key in _MESSAGE_DIRECTION_CACHE:
            val, t = _MESSAGE_DIRECTION_CACHE[key]
            if time.time() - t < 45.0:
                return val
            else:
                _MESSAGE_DIRECTION_CACHE.pop(key, None)
                
    # 再尝试全局匹配
    if clean_content in _MESSAGE_DIRECTION_CACHE:
        val, t = _MESSAGE_DIRECTION_CACHE[clean_content]
        if time.time() - t < 45.0:
            return val
        else:
            _MESSAGE_DIRECTION_CACHE.pop(clean_content, None)
    return None

def get_dpi_scale() -> float:
    """获取系统 DPI 缩放比例（全局统一）"""
    try:
        import ctypes
        hdc = ctypes.windll.user32.GetDC(0)
        log_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX = 88
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return log_x / 96.0
    except Exception:
        return 1.0

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
            # 资料卡是 ToolSaveBits 类型的弹出窗口，并且类名包含 ProfileUniquePop 或者 ToolSaveBits
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

def find_wechat_window() -> int:
    """寻找新旧各版本微信主窗口句柄（且过滤隐藏与幽灵窗口）"""
    import win32gui
    visible_candidates = []
    def enum_cb(h, _):
        try:
            if win32gui.IsWindowVisible(h):
                cls = win32gui.GetClassName(h)
                if cls in ("Qt51514QWindowIcon", "WeChatMainWndForPC"):
                    rect = win32gui.GetWindowRect(h)
                    w = rect[2] - rect[0]
                    h_len = rect[3] - rect[1]
                    # 主窗口尺寸通常大于 500x500，以剔除可能隐藏在后台的空载幽灵小窗口
                    if w > 500 and h_len > 500:
                        visible_candidates.append(h)
        except Exception:
            pass
    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass

    if visible_candidates:
        return visible_candidates[0]

    hwnd = win32gui.FindWindow("Qt51514QWindowIcon", None)
    if hwnd:
        return hwnd
    hwnd = win32gui.FindWindow("WeChatMainWndForPC", None)
    if hwnd:
        return hwnd
    return 0

def draw_debug_cross(hdc, x: int, y: int, scale: float):
    """在屏幕上绘制闪烁的红色十字星标，提供指示采样点（采用分层窗体对齐点击波纹以适应多分屏与高DPI）"""
    import win32gui
    import win32con
    import threading
    import time

    def _draw_thread():
        try:
            # 1. 注册专属窗口类
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = win32gui.DefWindowProc
            wc.lpszClassName = "WeChatDebugCrossOverlay"
            wc.hInstance = win32gui.GetModuleHandle(None)
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass

            # 2. 创建窗口。大小设为 30x30 保证十字星不被裁切
            width, height = 30, 30
            left = x - width // 2
            top = y - height // 2
            
            style = win32con.WS_POPUP
            ex_style = (win32con.WS_EX_LAYERED | 
                        win32con.WS_EX_TRANSPARENT | 
                        win32con.WS_EX_TOPMOST | 
                        win32con.WS_EX_NOACTIVATE)
            
            hwnd = win32gui.CreateWindowEx(
                ex_style,
                wc.lpszClassName,
                "DebugCrossOverlay",
                style,
                left, top, width, height,
                0, 0, wc.hInstance, None
            )
            
            # 设置黑色 (0x000000) 为完全透明色
            win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, 1)
            
            # 显示窗口且不激活它
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.UpdateWindow(hwnd)

            # 3. 绘制红色十字架
            cx, cy = width // 2, height // 2
            w_hdc = win32gui.GetDC(hwnd)
            
            red = 0x0000FF  # BGR 格式的纯红
            pen = win32gui.CreatePen(win32con.PS_SOLID, 2, red)
            old_pen = win32gui.SelectObject(w_hdc, pen)
            
            size = int(6 * scale)
            win32gui.MoveToEx(w_hdc, cx - size, cy)
            win32gui.LineTo(w_hdc, cx + size + 1, cy)
            win32gui.MoveToEx(w_hdc, cx, cy - size)
            win32gui.LineTo(w_hdc, cx, cy + size + 1)
            
            win32gui.UpdateWindow(hwnd)
            win32gui.PumpWaitingMessages()
            
            # 停留 0.4 秒，让用户有足够的时间看清
            time.sleep(0.4)
            
            # 清理 GDI 资源并注销窗口
            win32gui.SelectObject(w_hdc, old_pen)
            win32gui.DeleteObject(pen)
            win32gui.ReleaseDC(hwnd, w_hdc)
            win32gui.DestroyWindow(hwnd)
        except Exception:
            pass

    threading.Thread(target=_draw_thread, daemon=True).start()


def print_detect_result(name: str, is_green_found: bool, sampled_points: list):
    """终端显眼横幅打印最后一条消息的最终判定结果"""
    print(f"\n==================================================")
    print(f"[最后消息检测] (消息内容): '{name}'")
    print(f"[最后消息检测] (判定结果): {'[自己发送 (is_self=True)]' if is_green_found else '[对方发送 (is_self=False)]'}")
    print(f"[最后消息检测] (绿色匹配): {'成功 (找到绿色气泡)' if is_green_found else '失败 (未发现绿色气泡)'}")
    if sampled_points:
        print(f"[最后消息检测] (采样点数量): {len(sampled_points)}，首点坐标及色值: (x={int(sampled_points[0][0])}, y={int(sampled_points[0][1])}) RGB=({sampled_points[0][2]},{sampled_points[0][3]},{sampled_points[0][4]})")
    print(f"==================================================\n")

def print_fallback_result(name: str):
    """打印降级兜底信息"""
    print(f"\n==================================================")
    print(f"[最后消息检测] (消息内容): '{name}'")
    print(f"[最后消息检测] [警告] 处于降级状态 (窗口不满足采色条件)，默认判定为: [对方发送 (is_self=False)]")
    print(f"==================================================\n")

def find_avatar_node_and_rect(ctrl, scale: float) -> Tuple[Optional[object], Optional[object]]:
    # 微信 4.1.7+ 中无子头像节点，直接返回 None
    return None, None

def find_avatar_rect(ctrl, scale: float) -> Optional[object]:
    return None

def detect_is_self_by_avatar_location(ctrl, ctrl_rect, scale: float) -> Optional[bool]:
    return None

def detect_is_self_by_avatar_click(ctrl, session_name: Optional[str] = None) -> Optional[bool]:
    """
    通过物理点击头像并检测是否弹出好友资料窗口来判断消息是否是自己发送的。
    返回 True 代表是自己发的（未弹出好友资料窗口），返回 False 代表是好友发的（成功弹出好友资料窗口）。
    如果无法定位头像或发生异常，则返回 None。
    """
    import win32gui
    import win32con
    import uiautomation as uia
    import time
    
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
        # 🌟 核心修正：由于点击自己名片也有“发消息”按钮，只有好友名片才会有“语音聊天”或“视频聊天”按钮。
        # 因此，判断名片弹窗中是否存在“语音聊天”或“视频聊天”是区分好友与本人的最强特征。
        try:
            voice_btn = profile_win.ButtonControl(Name="语音聊天")
            video_btn = profile_win.ButtonControl(Name="视频聊天")
            if voice_btn.Exists(0.2) or video_btn.Exists(0.2):
                is_friend = True
        except Exception:
            pass
            
        # 🌟 顺手做用户信息完整度检验，若缺失真实 wxid 则自动同步资料
        if is_friend and session_name:
            from src.uia.profile_sync_helper import sync_contact_profile_if_needed
            sync_contact_profile_if_needed(profile_win, session_name)
            
        # 4. 关闭名片窗口（发送 WM_CLOSE，极其干净）
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
            
    logger.debug(f"[消息方向] 物理点击检测完成. 是否好友名片: {is_friend} => is_self={not is_friend}")
    return not is_friend
