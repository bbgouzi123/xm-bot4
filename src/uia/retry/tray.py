"""微信托盘图标相关操作。

平台感知策略：
  Win10: TrayNotifyWnd 主区搜索 → NotifyIconOverflowWindow 溢出区搜索 → 拖拽出小箭头常驻
  Win11: Shell_TrayWnd 通知区深度搜索 → 溢出弹窗搜索 → Ctrl+Alt+W 快捷键唤醒
"""

import ctypes
import platform
import threading
import time

from .tray_utils import _is_win11, _do_click, _is_wechat_tray_ctrl
from contextlib import contextmanager
from src.utils.safe_uia import safe_walk_control

@contextmanager
def safe_com_init():
    """安全地初始化 COM，如果已初始化则忽略，并确保退出时正确清理。"""
    hr = None
    try:
        # COINIT_APARTMENTTHREADED = 0x2
        hr = ctypes.windll.ole32.CoInitializeEx(None, 2)
    except Exception:
        pass
    try:
        yield
    finally:
        # S_OK = 0, S_FALSE = 1
        if hr in (0, 1):
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

def _search_win10_main_tray(auto, user32, right_click: bool) -> bool:
    """Win10: 在 TrayNotifyWnd 主托盘区域内搜索微信图标"""
    try:
        with safe_com_init():
            taskbar = auto.PaneControl(ClassName="Shell_TrayWnd")
            if not taskbar.Exists(1, 0.5):
                return False
            tray_notify = taskbar.PaneControl(ClassName="TrayNotifyWnd")
            if not tray_notify.Exists(1, 0.5):
                return False
            cnt = 0
            for ctrl, _ in safe_walk_control(tray_notify, max_depth=10):
                cnt += 1
                if cnt > 500:
                    break
                ct = getattr(ctrl, "ControlTypeName", "") or ""
                if _is_wechat_tray_ctrl(ctrl) and ct == "ButtonControl":
                    if _do_click(ctrl, right_click, user32):
                        return True
    except Exception as e:
        print(f"[托盘-UIA] Win10 主托盘搜索异常: {e}")
    return False

def _search_win10_overflow(auto, user32, right_click: bool) -> bool:
    """Win10: 在 NotifyIconOverflowWindow 溢出区搜索微信图标（增加了多语言及名称兼容）。"""
    try:
        with safe_com_init():
            overflow = auto.PaneControl(ClassName="NotifyIconOverflowWindow")
            if not overflow.Exists(0.5, 0.1):
                # 兼容：如果以类名找不到，尝试以窗口名/Name（通知溢出）查找
                overflow = auto.PaneControl(Name="通知溢出")
                if not overflow.Exists(0.5, 0.1):
                    # 英文 Windows 10 兼容
                    overflow = auto.PaneControl(Name="Notification Overflow")
            
            if not overflow.Exists(0.5, 0.1):
                return False

            cnt = 0
            for ctrl, _ in safe_walk_control(overflow, max_depth=10):
                cnt += 1
                if cnt > 200:
                    break
                if _is_wechat_tray_ctrl(ctrl):
                    if _do_click(ctrl, right_click, user32):
                        return True
    except Exception as e:
        print(f"[托盘-UIA] Win10 溢出区搜索异常: {e}")
    return False

def _search_win11_notification_area(auto, user32, right_click: bool) -> bool:
    """Win11: 在任务栏通知区域深度搜索微信图标。"""
    try:
        with safe_com_init():
            taskbar = auto.PaneControl(ClassName="Shell_TrayWnd")
            if not taskbar.Exists(1, 0.5):
                return False

            # 优化：优先定位到 TrayNotifyWnd 以极大程度缩小遍历范围，提高搜索响应速度
            tray_notify = taskbar.PaneControl(ClassName="TrayNotifyWnd")
            search_root = tray_notify if tray_notify.Exists(0.5, 0.1) else taskbar

            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            right_zone_start = screen_w * 2 // 3

            cnt = 0
            for ctrl, _ in safe_walk_control(search_root, max_depth=10):
                cnt += 1
                if cnt > 300:
                    break
                ct = getattr(ctrl, "ControlTypeName", "") or ""
                cn = getattr(ctrl, "Name", "") or ""
                if _is_wechat_tray_ctrl(ctrl) and ct == "ButtonControl":
                    try:
                        r = ctrl.BoundingRectangle
                        if r.left >= right_zone_start:
                            if _do_click(ctrl, right_click, user32, label="消息栏微信托盘图标"):
                                return True
                        else:
                            print(f"[托盘-UIA] Win11 跳过左侧控件: name={cn!r}, left={r.left}, 阈值={right_zone_start}")
                    except Exception:
                        pass
    except Exception as e:
        print(f"[托盘-UIA] Win11 通知区搜索异常: {e}")
    return False

def _search_win11_overflow_flyout(auto, user32, right_click: bool) -> bool:
    """Win11: 在溢出弹窗中搜索微信图标（增加了类名及多语言窗口名称兼容）。"""
    try:
        with safe_com_init():
            overflow = auto.WindowControl(ClassName="TopLevelWindowForOverflowXamlIsland")
            if not overflow.Exists(0.1, 0.05):
                # 兼容：如果以类名找不到，尝试以窗口名/Name（系统托盘溢出窗口。）查找
                overflow = auto.WindowControl(Name="系统托盘溢出窗口。")
                if not overflow.Exists(0.1, 0.05):
                    # 英文 Windows 11 兼容
                    overflow = auto.WindowControl(Name="System tray overflow window.")
            
            if not overflow.Exists(0.1, 0.05):
                return False

            cnt = 0
            for ctrl, _ in safe_walk_control(overflow, max_depth=8):
                cnt += 1
                if cnt > 150:
                    break
                if _is_wechat_tray_ctrl(ctrl):
                    if _do_click(ctrl, right_click, user32):
                        return True
    except Exception as e:
        print(f"[托盘-UIA] Win11 溢出弹窗搜索异常: {e}")
    return False

def _try_open_overflow_window(auto, win11: bool) -> bool:
    """尝试点击 "显示隐藏的图标" (Win11) 或 "通知 V 形" (Win10) 按钮打开溢出弹窗。"""
    try:
        with safe_com_init():
            taskbar = auto.PaneControl(ClassName="Shell_TrayWnd")
            if not taskbar.Exists(1, 0.5):
                return False
            
            # 优化：优先在 TrayNotifyWnd 中搜索按钮，缩窄遍历节点数
            tray_notify = taskbar.PaneControl(ClassName="TrayNotifyWnd")
            search_root = tray_notify if tray_notify.Exists(0.5, 0.1) else taskbar

            cnt = 0
            for ctrl, _ in safe_walk_control(search_root, max_depth=10):
                cnt += 1
                if cnt > 150:
                    break
                cn = getattr(ctrl, "Name", "") or ""
                ct = getattr(ctrl, "ControlTypeName", "") or ""
                
                is_overflow_btn = False
                if ct == "ButtonControl":
                    if win11:
                        is_overflow_btn = "隐藏" in cn or "Hidden" in cn
                    else:
                        is_overflow_btn = "通知 V" in cn or "Chevron" in cn or "隐藏" in cn or "Hidden" in cn

                if is_overflow_btn:
                    try:
                        user32 = ctypes.windll.user32
                        if _do_click(ctrl, right_click=False, user32=user32, label="显示隐藏的图标按钮"):
                            time.sleep(0.5)
                            return True
                    except Exception:
                        pass
    except Exception as e:
        print(f"[托盘-UIA] 打开溢出弹窗异常: {e}")
    return False


def click_wechat_tray_icon(right_click: bool = False) -> bool:
    """模拟点击系统托盘中的微信图标（自动适配 Win10 / Win11）。"""
    user32 = ctypes.windll.user32
    win11 = _is_win11()

    def _inner_search() -> bool:
        try:
            import uiautomation as auto
            found = False
            if win11:
                found = _search_win11_notification_area(auto, user32, right_click)
            else:
                found = _search_win10_main_tray(auto, user32, right_click)

            if found:
                return True

            found2 = False
            if win11:
                if _search_win11_overflow_flyout(auto, user32, right_click):
                    found2 = True
                else:
                    with auto.UIAutomationInitializerInThread(debug=False):
                        if _try_open_overflow_window(auto, win11=True):
                            time.sleep(0.5)
                            found2 = _search_win11_overflow_flyout(auto, user32, right_click)
            else:
                if _search_win10_overflow(auto, user32, right_click):
                    found2 = True
                else:
                    with auto.UIAutomationInitializerInThread(debug=False):
                        if _try_open_overflow_window(auto, win11=False):
                            time.sleep(0.5)
                            found2 = _search_win10_overflow(auto, user32, right_click)

            if found2:
                return True
        except Exception as e:
            print(f"[托盘-UIA] UIA 搜索异常: {e}")
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
            print("[托盘] 避让：检测到用户活跃，跳过自动隐藏任务栏兼容悬停")
            return False

        import win32api
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        try:
            old_pos = win32api.GetCursorPos()
        except Exception as e_cur:
            # GetCursorPos 被权限策略拒绝（错误码 5），无法移动鼠标滑出任务栏，立即放弃
            print(f"[托盘] 自动隐藏任务栏兼容滑出失败: {e_cur}")
            return False
        try:
            win32api.SetCursorPos((screen_w // 2, screen_h - 2))
        except Exception as e_set:
            print(f"[托盘] 自动隐藏任务栏兼容滑出失败: {e_set}")
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
        print(f"[托盘] 自动隐藏任务栏兼容滑出失败: {e}")

    print(f"[托盘] 未在可见的系统托盘区域中找到微信图标 ({'Win11' if win11 else 'Win10'})")

    if getattr(click_wechat_tray_icon, "_is_retrying", False):
        return False

    if not win11:
        try:
            from .tray_drag import extract_wechat_from_overflow
            if extract_wechat_from_overflow():
                print("[托盘] 已将微信从折叠区拖出，重新尝试点击...")
                click_wechat_tray_icon._is_retrying = True
                res = click_wechat_tray_icon(right_click)
                click_wechat_tray_icon._is_retrying = False
                return res
        except Exception as e:
            print(f"[托盘] Win10 拖拽微信异常: {e}")
    else:
        print("[托盘] Win11 环境无折叠拖拽机制，将由上层快捷键策略接管")

    return False



def try_recover_wechat_from_whitescreen() -> bool:
    """
    当微信界面无响应、白屏或UIA元素丢失时，自动尝试点击托盘微信图标进行界面重绘唤醒。
    成功点击返回 True，并在内部做适当延迟避让。
    """
    import logging
    logger = logging.getLogger("WeChatRecovery")
    logger.warning("[恢复机制] 触发微信白屏自愈：正在尝试模拟点击托盘微信图标...")
    try:
        if click_wechat_tray_icon(right_click=False):
            logger.info("[恢复机制] 托盘微信图标模拟点击成功，等待 3.0s 避让微信重绘...")
            time.sleep(3.0)
            return True
        logger.error("[恢复机制] 未能在系统托盘中找到或点击微信图标")
    except Exception as e:
        logger.error(f"[恢复机制] 触发托盘自愈异常: {e}")
    return False


