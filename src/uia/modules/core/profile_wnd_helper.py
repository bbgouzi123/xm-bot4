import ctypes
import time
import logging
import win32api
import win32con
import win32gui
import win32process
import uiautomation as uia

logger = logging.getLogger("WeChatProfile")

def print(*args, **kwargs):
    try:
        msg = " ".join(str(arg) for arg in args)
        logger.debug(msg)
    except:
        pass

def close_image_preview_if_needed(driver_obj) -> bool:
    """检测微信中是否打开了内置图片预览/遮罩，如果打开了则主动关闭它。"""
    if not driver_obj.root:
        return False
    try:
        from src.uia.retry.clicks import physical_click
        preview_btn = driver_obj.root.Control(ClassName="mmui::ChatlogMigrateView")
        if preview_btn.Exists(0.5):
            print("[UIA] 检测到微信当前处于图片预览/遮罩状态 (找到 mmui::ChatlogMigrateView)")
            main_rect = driver_obj.root.BoundingRectangle
            if main_rect and main_rect.right > main_rect.left:
                mx = main_rect.left + (main_rect.right - main_rect.left) // 2
                my = main_rect.top + (main_rect.bottom - main_rect.top) // 2
                print(f"[UIA] 正在物理点击微信窗口中心以安全关闭图片预览，坐标: ({mx}, {my})")
                from src.uia.retry import ensure_wechat_foreground
                ensure_wechat_foreground(driver_obj.hwnd)
                time.sleep(0.1)
                physical_click(mx, my, settle=0.3)
                for _ in range(5):
                    time.sleep(0.1)
                    if not preview_btn.Exists(0.1):
                        print("[UIA] 成功通过窗口中心物理点击关闭微信图片预览")
                        return True
                print("[UIA] 警告：点击主窗口中心后，图片预览依然未关闭")
    except Exception as e:
        print(f"[UIA] 检测并关闭图片预览时发生异常: {e}")
    return False

def find_avatar_click_point(driver_obj, nav_toolbar) -> tuple:
    """寻找微信头像按钮的精确坐标"""
    from src.uia.retry import get_dpi_scale
    _s = get_dpi_scale()
    rect = nav_toolbar.BoundingRectangle

    avatar_btn = None
    try:
        btn_temp = nav_toolbar.ButtonControl(Name="头像")
        if btn_temp.Exists(0.1):
            avatar_btn = btn_temp
        else:
            pane_temp = nav_toolbar.PaneControl(Name="头像")
            if pane_temp.Exists(0.1):
                avatar_btn = pane_temp
        
        if not avatar_btn:
            children = nav_toolbar.GetChildren()
            for ch in children:
                ct = getattr(ch, 'ControlTypeName', '') or ''
                c_name = getattr(ch, 'Name', '') or ''
                if c_name in ('微信', '聊天', '通讯录', '收藏', '朋友圈', '视频号', '看一看', '搜一搜', '小程序', '手机', '订阅号'):
                    continue
                if 'Button' in ct or 'Pane' in ct or 'Control' in ct:
                    r_ch = ch.BoundingRectangle
                    if r_ch.top >= rect.top and r_ch.top < rect.top + int(70 * _s):
                        avatar_btn = ch
                        break
    except Exception as e_ch:
        print(f"[UIA] 定位微信头像控件发生异常: {e_ch}")

    if avatar_btn and avatar_btn.Exists(0.1):
        r_btn = avatar_btn.BoundingRectangle
        tx = int((r_btn.left + r_btn.right) / 2)
        ty = int((r_btn.top + r_btn.bottom) / 2)
        print(f"[UIA] 精确识别到头像按钮, 采用控件中心坐标: ({tx}, {ty})")
        return tx, ty

    chats_btn = None
    try:
        btn_temp = nav_toolbar.ButtonControl(Name="微信")
        if btn_temp.Exists(0.1):
            chats_btn = btn_temp
        else:
            btn_temp = nav_toolbar.ButtonControl(Name="WeChat")
            if btn_temp.Exists(0.1):
                chats_btn = btn_temp
    except Exception:
        pass

    if chats_btn:
        cb = chats_btn.BoundingRectangle
        tb = nav_toolbar.BoundingRectangle
        tx = int((cb.left + cb.right) / 2)
        ty = int((tb.top + cb.top) / 2)
        print(f"[UIA] 成功通过 Chats 按钮几何反算头像坐标: ({tx}, {ty})")
        return tx, ty

    width = rect.right - rect.left
    offset_x = min(int(80 * _s), max(int(24 * _s), int(width * 0.45)))
    offset_y = int(42 * _s)
    tx = rect.left + offset_x
    ty = rect.top + offset_y
    print(f"[UIA] 未定位到头像子控件且锚定失败，使用安全头像中心兜底坐标: ({tx}, {ty})")
    return tx, ty

def is_profile_card_wnd(h, main_pid) -> bool:
    try:
        cls = win32gui.GetClassName(h)
        if not cls.startswith("Qt515") and cls != "mmui::ProfileUniquePop":
            return False
        title = win32gui.GetWindowText(h)
        if title not in ("Weixin", "微信", ""):
            return False
        style = win32gui.GetWindowLong(h, win32con.GWL_STYLE)
        if not (style & win32con.WS_CAPTION):
            return False
        r = win32gui.GetWindowRect(h)
        w = r[2] - r[0]
        ht = r[3] - r[1]
        if not (100 < w < 800 and 250 < ht < 800 and ht > w):
            return False
        _, p = win32process.GetWindowThreadProcessId(h)
        return p == main_pid
    except Exception:
        return False

def clear_old_profile_cards(driver_obj, main_pid):
    """快速清理历史残留的个人资料卡窗口"""
    try:
        def _close_old_profile_wnd(h, _):
            try:
                if h != driver_obj.hwnd and is_profile_card_wnd(h, main_pid):
                    print(f"[UIA] 温和关闭残留历史资料卡窗口 (WM_CLOSE): hwnd={h}")
                    win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
            return True
        win32gui.EnumWindows(_close_old_profile_wnd, None)
        time.sleep(0.2)
    except Exception as close_err:
        print(f"[UIA] 快速清理残留个人资料卡异常: {close_err}")

def trigger_profile_card(driver_obj, target_x, target_y, main_pid, uia_lock) -> tuple:
    """物理点击头像以弹出个人资料卡"""
    from src.uia.retry.clicks import physical_click
    from src.uia.retry import ensure_wechat_foreground

    info_win = None
    info_win_hwnd = None
    for _click_attempt in range(2):
        if _click_attempt > 0:
            uia_lock.update_status("上一次点击未弹出资料卡，正在防双击安全等待后重试...")
            time.sleep(1.2)
        uia_lock.update_status(f"正在物理点击头像以弹出个人资料卡 (第 {_click_attempt + 1} 次尝试)...")
        ensure_wechat_foreground(driver_obj.hwnd)
        time.sleep(0.2)

        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        if fg_hwnd != driver_obj.hwnd:
            print(f"[UIA] ⚠️ 微信窗口未成功置前 (当前前台={fg_hwnd})，跳过本次头像物理点击以防干扰用户")
            continue

        physical_click(target_x, target_y, settle=0.1)

        for _retry in range(20):
            uia_lock.update_status(f"正在侦察并获取个人资料窗口句柄 (重试 {_retry + 1}/20)...")
            _pid_candidates = []
            def _enum_cb(h, _):
                if h != driver_obj.hwnd and win32gui.IsWindowVisible(h) and is_profile_card_wnd(h, main_pid):
                    _pid_candidates.append(h)
                return True
            win32gui.EnumWindows(_enum_cb, None)

            if _pid_candidates:
                def _sort_key(h):
                    cls = win32gui.GetClassName(h)
                    r = win32gui.GetWindowRect(h)
                    area = (r[2] - r[0]) * (r[3] - r[1])
                    priority = 0 if "ToolSaveBits" in cls else 1
                    return (priority, -area)
                _pid_candidates.sort(key=_sort_key)
                _wh = _pid_candidates[0]
                _ctrl = uia.ControlFromHandle(_wh)
                info_win_hwnd, info_win = _wh, _ctrl
                break
            time.sleep(0.1)

        if info_win:
            break

    return info_win_hwnd, info_win
