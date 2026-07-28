import time
import random
import logging
import ctypes
from typing import Optional, List
from collections import deque
import uiautomation as uia
import pyperclip

logger = logging.getLogger(__name__)

def random_delay(lo: float = 0.2, hi: float = 0.5):
    time.sleep(random.uniform(lo, hi))

def exists_safe(ctrl, timeout: float = 1.0) -> bool:
    """安全检查控件是否存在"""
    try:
        return ctrl.Exists(timeout, 0.3)
    except Exception:
        return False

def try_click(ctrl, max_retries: int = 3, delay: float = 0.3) -> bool:
    """安全点击控件"""
    for i in range(max_retries):
        try:
            ctrl.Click(simulateMove=False)
            return True
        except Exception:
            if i < max_retries - 1:
                time.sleep(delay)
    return False

def bfs_find(root, **match_attrs) -> Optional[object]:
    """BFS 搜索控件（避免 WalkControl 无限循环）"""
    queue = deque()
    queue.append((root, 0))
    max_depth = match_attrs.pop("max_depth", 8)
    max_count = match_attrs.pop("max_count", 500)
    count = 0

    while queue and count < max_count:
        ctrl, depth = queue.popleft()
        if depth > max_depth:
            continue
        count += 1

        matched = True
        for attr, val in match_attrs.items():
            ctrl_val = getattr(ctrl, attr, None)
            if ctrl_val != val:
                matched = False
                break
        if matched and depth > 0:
            return ctrl

        if depth < max_depth:
            try:
                children = ctrl.GetChildren()
                for child in children:
                    queue.append((child, depth + 1))
            except Exception:
                pass

    return None

def safe_set_clipboard(text: str, max_retries: int = 5, delay: float = 0.1) -> bool:
    """线程/进程安全的剪贴板写入助手，带重试与最终校验"""
    for attempt in range(max_retries):
        try:
            pyperclip.copy(text)
            time.sleep(delay)
            if pyperclip.paste() == text:
                return True
        except Exception as e:
            logger.warning(f"[剪贴板助手] 尝试第 {attempt + 1} 次写入失败: {e}")
            time.sleep(delay * 2)
    
    logger.error(f"[剪贴板助手] 剪贴板写入严重故障，无法在 {max_retries} 次尝试内成功写入目标文本")
    return False

def find_active_edit_control(edit_win) -> Optional[uia.Control]:
    """多层级定位当前的备注/标签编辑框（支持聚焦控件、直接控件及深层搜索）"""
    try:
        focused = uia.GetFocusedControl()
        if focused and (focused.ControlTypeName == "EditControl" or "Edit" in (focused.ClassName or "")):
            logger.info(f"[标签备注同步] 成功通过输入焦点定位到编辑框: Class={focused.ClassName}")
            return focused
    except Exception as e:
        logger.debug(f"[标签备注同步] 获取焦点控件异常: {e}")

    try:
        remark_edit = edit_win.EditControl(foundIndex=1)
        if exists_safe(remark_edit, 0.5):
            return remark_edit
    except Exception:
        pass

    try:
        edit_ctrl = bfs_find(edit_win, ControlTypeName="EditControl", max_depth=15)
        if edit_ctrl:
            return edit_ctrl
    except Exception:
        pass

    return None

def open_settings_dialog_via_more_menu(profile_win: uia.WindowControl) -> Optional[uia.WindowControl]:
    """步骤：点击'更多'三个点，按 DOWN + ENTER 打开 '设置备注和标签' 独立窗口"""
    more_btn = profile_win.ButtonControl(Name="更多")
    if not more_btn.Exists(1.5):
        print("[标签备注同步] 未找到 '更多' 按钮")
        return None
        
    print("[标签备注同步] 点击 '更多' 按钮")
    if not try_click(more_btn, max_retries=2, delay=0.3):
        return None
    random_delay(0.4, 0.6)
    
    print("[标签备注同步] 模拟键盘 Down + Enter 打开备注标签对话框...")
    uia.SendKeys("{DOWN}")
    time.sleep(0.2)
    uia.SendKeys("{ENTER}")
    
    desktop = uia.GetRootControl()
    for _ in range(15):
        edit_win = desktop.WindowControl(Name="设置备注和标签")
        if edit_win.Exists(0.1):
            return edit_win
        time.sleep(0.2)
        
    print("[标签备注同步] 等待独立设置弹窗超时")
    return None

def fill_remark_name(edit_win: uia.WindowControl, remark: str) -> bool:
    """步骤：填写备注名"""
    try:
        remark_edit = edit_win.EditControl(ClassName="mmui::XLineEdit")
        if not remark_edit.Exists(1.0):
            remark_edit = edit_win.EditControl(Name="修改备注名")
        if not remark_edit.Exists(1.0):
            remark_edit = find_active_edit_control(edit_win)
            
        if not remark_edit or not exists_safe(remark_edit, 0.5):
            print("[标签备注同步] 未找到备注名输入框")
            return False
            
        try_click(remark_edit, max_retries=2, delay=0.2)
        random_delay(0.2, 0.3)
        remark_edit.SetFocus()
        
        # 内存写入
        try:
            val_pat = remark_edit.GetValuePattern()
            if val_pat:
                val_pat.SetValue(remark)
                print(f"[标签备注同步] 成功通过内存写入备注: '{remark}'")
                return True
        except Exception:
            pass
            
        # 物理写入兜底
        safe_set_clipboard(remark)
        remark_edit.SendKeys("{Ctrl}a")
        random_delay(0.1, 0.2)
        remark_edit.SendKeys("{Ctrl}v")
        random_delay(0.3, 0.5)
        print(f"[标签备注同步] 成功通过粘贴写入备注: '{remark}'")
        return True
    except Exception as e:
        logger.error(f"[标签备注同步] 写入备注失败: {e}")
        return False

def click_remark_tag_entry(profile_win) -> bool:
    """旧接口兼容：通过'更多'菜单进入独立备注编辑对话框"""
    dlg = open_settings_dialog_via_more_menu(profile_win)
    return dlg is not None

def fill_remark_in_edit_window(edit_win, remark: str):
    """旧接口兼容：填入备注"""
    fill_remark_name(edit_win, remark)

def fill_remark_and_tags_in_edit_window(edit_win, new_remark: Optional[str], tags: Optional[List[str]]) -> bool:
    """旧接口兼容：填入备注名并打上标签"""
    from .utils import fill_tags_via_search_and_select
    success = True
    if new_remark is not None:
        success = success and fill_remark_name(edit_win, new_remark)
    if tags:
        success = success and fill_tags_via_search_and_select(edit_win, tags)
    return success

def save_tags(win_control) -> bool:
    """旧接口兼容：保存设置"""
    from .utils import click_save_button_in_dialog
    return click_save_button_in_dialog(win_control)

def close_profile_window():
    """旧接口兼容：关闭所有相关窗口"""
    import win32gui, win32con
    hwnd = win32gui.FindWindow("WeUIDialog", "设置备注和标签")
    if hwnd and win32gui.IsWindowVisible(hwnd):
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        random_delay(0.3, 0.5)

    hwnd = win32gui.FindWindow("ContactProfileWnd", None)
    for _ in range(2):
        if hwnd and win32gui.IsWindowVisible(hwnd):
            uia.SendKeys("{ESC}")
            random_delay(0.3, 0.5)

def find_and_click_tag_modify(profile_win) -> bool:
    """旧接口兼容"""
    return click_remark_tag_entry(profile_win)

def cleanup_profile_window(driver, profile_win=None):
    """扫尾现场清理，关闭残留弹窗"""
    try:
        import win32gui, win32con
        for title in ["设置备注和标签", "设置备注及标签", "修改备注和标签", "修改备注及标签"]:
            hwnd = win32gui.FindWindow("WeUIDialog", title)
            if hwnd and win32gui.IsWindowVisible(hwnd):
                print(f"[标签同步] 清理现场：发现残留 '{title}' 对话框，发送 WM_CLOSE 关闭...")
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                random_delay(0.2, 0.4)

        # 清理资料卡窗口
        hwnd_card = None
        if profile_win:
            try:
                hwnd_card = profile_win.NativeWindowHandle
            except Exception:
                pass
        if not hwnd_card:
            from src.uia.message_direction_helper import find_profile_hwnd
            hwnd_card = find_profile_hwnd()

        if hwnd_card and win32gui.IsWindow(hwnd_card):
            print(f"[标签同步] 清理现场：发现资料卡窗口 hwnd={hwnd_card}，发送 WM_CLOSE 关闭...")
            win32gui.PostMessage(hwnd_card, win32con.WM_CLOSE, 0, 0)
            for _ in range(10):
                time.sleep(0.05)
                if not win32gui.IsWindow(hwnd_card) or not win32gui.IsWindowVisible(hwnd_card):
                    break
            else:
                print("[标签同步] 清理现场：WM_CLOSE 未生效，发送 ESC 兜底...")
                uia.SendKeys("{ESC}")
                random_delay(0.3, 0.5)

        root = driver.root
        if root:
            member_btn = root.ButtonControl(ClassName="mmui::ChatMemberCell")
            if exists_safe(member_btn, 0.5):
                chat_info_btn = root.ButtonControl(Name="聊天信息")
                if exists_safe(chat_info_btn, 0.5):
                    try_click(chat_info_btn, max_retries=1, delay=0.2)
                    random_delay(0.3, 0.5)
    except Exception as e:
        logger.debug(f"[标签同步] 清理现场异常: {e}")
