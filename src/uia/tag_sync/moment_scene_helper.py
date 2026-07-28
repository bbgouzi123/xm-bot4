import time
import logging
from collections import deque
import uiautomation as uia
import pyperclip
from .utils import (
    random_delay,
    try_click,
    exists_safe,
    bfs_find,
    fill_remark_in_edit_window,
)

logger = logging.getLogger(__name__)

def refind_moment_item(author_name: str):
    """在朋友圈列表中，重新根据作者名字寻找动态条目"""
    import win32gui
    moment_hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "朋友圈") or win32gui.FindWindow("mmui::SNSWindow", "朋友圈") or win32gui.FindWindow("SNSWnd", None)
    if not moment_hwnd:
        return None
    try:
        moment_window = uia.ControlFromHandle(moment_hwnd)
        list_ctrl = moment_window.ListControl(Name='朋友圈')
        if not exists_safe(list_ctrl, 0.5):
            list_ctrl = moment_window.ListControl(ClassName='mmui::TimeLineListView')
        if not exists_safe(list_ctrl, 0.5):
            list_ctrl = moment_window.ListControl()
        if not list_ctrl or not exists_safe(list_ctrl, 0.5):
            return None
            
        from src.monitor.moment_compat_aggregator import aggregate_timeline_cells
        children = list_ctrl.GetChildren()
        groups = aggregate_timeline_cells(children)
        for g in groups:
            if (author_name == g.publisher or author_name in g.publisher) and g.avatar_cell:
                return g.avatar_cell
                    
        # 传统层级兜底
        for item in children:
            name_attr = getattr(item, "Name", "") or ""
            if author_name in name_attr:
                return item
    except Exception as e:
        print(f"[朋友圈标签·异常] 重新定位动态条目发生异常: {e}")
    return None

def verify_profile_window_open(author_name: str):
    """验证名片窗口是否已经成功打开"""
    import win32gui
    for _ in range(6):
        hwnd = win32gui.FindWindow("ContactProfileWnd", None)
        if hwnd and win32gui.IsWindowVisible(hwnd):
            return uia.ControlFromHandle(hwnd)
        time.sleep(0.5)
    return None

def open_profile_from_moment(item_ctrl, author_name: str):
    """从朋友圈动态条目中点击头像打开名片弹窗"""
    import win32gui
    moment_hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "朋友圈") or win32gui.FindWindow("mmui::SNSWindow", "朋友圈") or win32gui.FindWindow("SNSWnd", None)
    moment_window = None
    if moment_hwnd:
        from src.uia.retry.window_ops import force_foreground
        force_foreground(moment_hwnd)
        try:
            moment_window = uia.ControlFromHandle(moment_hwnd)
        except Exception:
            pass

    try:
        rect = item_ctrl.BoundingRectangle
        if rect.width() <= 0 or rect.height() <= 0:
            raise Exception("Invalid rect size")
    except Exception:
        logger.info(f"[朋友圈标签] 动态条目引用已失效，正在重新定位 {author_name} 的动态条目...")
        item_ctrl = refind_moment_item(author_name)
        if not item_ctrl:
            print(f"[朋友圈标签] 重新定位 {author_name} 的动态条目失败")
            return None

    try:
        if moment_window:
            list_ctrl = moment_window.ListControl(ClassName='mmui::TimeLineListView')
            if list_ctrl and list_ctrl.Exists(0.5):
                list_rect = list_ctrl.BoundingRectangle
                from src.monitor.moment_interact_helpers import _is_btn_visible
                if not _is_btn_visible(item_ctrl, list_rect):
                    logger.info(f"[朋友圈标签] 目标条目不在视口中，尝试 ScrollIntoView...")
                    try:
                        item_ctrl.ScrollIntoView()
                        random_delay(0.3, 0.5)
                    except Exception:
                        list_ctrl.WheelDown(wheelTimes=2)
                        random_delay(0.3, 0.5)
    except Exception as ve_ex:
        logger.debug(f"[朋友圈标签] 可见性滚动处理异常: {ve_ex}")

    avatar_btn = None
    try:
        children = item_ctrl.GetChildren()
        for child in children:
            ctrl_type = getattr(child, "ControlTypeName", "")
            if ctrl_type == "ButtonControl":
                avatar_btn = child
                break
    except Exception:
        pass

    if not avatar_btn:
        try:
            avatar_btn = item_ctrl.ButtonControl(foundIndex=1)
        except Exception:
            pass

    if not avatar_btn or not exists_safe(avatar_btn, 0.5):
        logger.warning(f"[朋友圈标签] UIA 方式未找到 {author_name} 的头像按钮，启用左上角像素偏移兜底...")
        try:
            rect = item_ctrl.BoundingRectangle
            cls_name = getattr(item_ctrl, "ClassName", "") or ""
            x = rect.left + 30
            y = rect.top + (rect.bottom - rect.top) // 2
            if rect.left > 0 and rect.top > 0 and moment_window:
                moment_rect = moment_window.BoundingRectangle
                if (moment_rect.left < x < moment_rect.right) and (moment_rect.top < y < moment_rect.bottom):
                    from src.uia.retry.clicks import physical_click
                    logger.info(f"[朋友圈标签] 正在像素点击头像({cls_name})，坐标: ({x}, {y})")
                    physical_click(x, y, restore_cursor=True)
                    random_delay(1.0, 1.5)
                    profile_win = verify_profile_window_open(author_name)
                    if profile_win:
                        return profile_win
        except Exception as coord_ex:
            logger.error(f"[朋友圈标签] 头像像素点击异常: {coord_ex}")

    if avatar_btn and exists_safe(avatar_btn, 0.5):
        try_click(avatar_btn, max_retries=3, delay=0.3)
        random_delay(1.0, 1.5)

    profile_win = verify_profile_window_open(author_name)
    if not profile_win:
        print(f"[朋友圈标签] {author_name} 名片弹窗未出现")
        return None

    print(f"[朋友圈标签] {author_name} 名片弹窗已打开")
    return profile_win

def open_edit_contact_from_profile(profile_win):
    """在名片弹窗中点击'...' → '设置备注和标签'"""
    import win32gui
    more_btn = bfs_find(
        profile_win,
        ControlTypeName="ButtonControl",
        Name="更多",
        max_depth=8,
    )

    if not more_btn:
        try:
            buttons = []
            queue = deque()
            queue.append((profile_win, 0))
            while queue:
                ctrl, depth = queue.popleft()
                if depth > 6:
                    continue
                ctrl_type = getattr(ctrl, "ControlTypeName", "")
                if ctrl_type == "ButtonControl":
                    buttons.append(ctrl)
                try:
                    for child in ctrl.GetChildren():
                        queue.append((child, depth + 1))
                except Exception:
                    pass
            if buttons:
                more_btn = buttons[-1]
        except Exception:
            pass

    if not more_btn or not exists_safe(more_btn, 0.5):
        try:
            from src.uia.retry.clicks import physical_click
            rect = profile_win.BoundingRectangle
            x = rect.right - 30
            y = rect.top + 30
            physical_click(x, y, restore_cursor=False)
            random_delay(0.5, 0.8)
        except Exception as e:
            print(f"[朋友圈标签] 坐标点击'...'失败: {e}")
            return None
    else:
        try_click(more_btn, max_retries=2, delay=0.3)
        random_delay(0.5, 0.8)

    menu = None
    for _ in range(4):
        menu_hwnd = win32gui.FindWindow("CMenuWnd", "")
        if menu_hwnd and win32gui.IsWindowVisible(menu_hwnd):
            menu = uia.ControlFromHandle(menu_hwnd)
            break
        time.sleep(0.3)

    if not menu:
        print("[朋友圈标签] 菜单未弹出")
        return None

    edit_item = menu.MenuItemControl(Name="设置备注和标签")
    if not exists_safe(edit_item, 1.0):
        edit_item = bfs_find(
            menu,
            ControlTypeName="MenuItemControl",
            Name="设置备注和标签",
            max_depth=5,
        )

    if not edit_item or not exists_safe(edit_item, 0.5):
        print("[朋友圈标签] 未找到'设置备注和标签'菜单项")
        uia.SendKeys("{ESC}")
        return None

    try_click(edit_item, max_retries=2, delay=0.3)
    random_delay(0.8, 1.2)

    edit_win = None
    for _ in range(6):
        hwnd = win32gui.FindWindow("WeUIDialog", "设置备注和标签")
        if hwnd and win32gui.IsWindowVisible(hwnd):
            edit_win = uia.ControlFromHandle(hwnd)
            break
        w = uia.WindowControl(Name="设置备注和标签")
        if w and exists_safe(w, 0.5):
            edit_win = w
            break
        time.sleep(0.5)

    if not edit_win:
        print("[朋友圈标签] '设置备注和标签'窗口未出现")
        uia.SendKeys("{ESC}")
        return None

    print("[朋友圈标签] '设置备注和标签'窗口已打开")
    return edit_win

def search_and_select_tag_in_edit_window(edit_win, tag_text: str) -> bool:
    """在'设置备注和标签'窗口中搜索并选择标签"""
    tag_edit = None
    queue = deque()
    queue.append((edit_win, 0))
    edits_found = []
    count = 0
    while queue and count < 200:
        ctrl, depth = queue.popleft()
        if depth > 8:
            continue
        count += 1
        ctrl_type = getattr(ctrl, "ControlTypeName", "")
        if ctrl_type == "EditControl":
            edits_found.append(ctrl)
        try:
            for child in ctrl.GetChildren():
                queue.append((child, depth + 1))
        except Exception:
            pass

    if len(edits_found) >= 2:
        tag_edit = edits_found[1]
    elif len(edits_found) == 1:
        tag_edit = edits_found[0]

    if not tag_edit or not exists_safe(tag_edit, 0.5):
        print(f"[朋友圈标签] 未找到标签搜索框: {tag_text}")
        return False

    try_click(tag_edit, max_retries=2, delay=0.2)
    random_delay(0.2, 0.3)
    try:
        tag_edit.SetFocus()
    except Exception:
        pass

    tag_edit.SendKeys("{Ctrl}a")
    random_delay(0.1, 0.15)
    pyperclip.copy(tag_text)
    tag_edit.SendKeys("{Ctrl}v")
    random_delay(0.5, 0.8)

    target_item = None
    queue2 = deque()
    queue2.append((edit_win, 0))
    count2 = 0

    while queue2 and count2 < 300:
        ctrl, depth = queue2.popleft()
        if depth > 8:
            continue
        count2 += 1
        try:
            ctrl_type = getattr(ctrl, "ControlTypeName", "")
            if ctrl_type == "ListControl":
                for it in ctrl.GetChildren():
                    item_name = getattr(it, "Name", "").strip()
                    if not item_name:
                        continue
                    if item_name == tag_text.strip():
                        target_item = it
                        break
                    if item_name.startswith("创建新标签"):
                        target_item = it

            if target_item:
                break

            for child in ctrl.GetChildren():
                queue2.append((child, depth + 1))
        except Exception:
            pass

    if not target_item:
        print(f"[朋友圈标签] 未找到标签项: {tag_text}")
        try:
            tag_edit.SendKeys("{Ctrl}a{Delete}")
        except Exception:
            pass
        return False

    if not try_click(target_item, max_retries=3, delay=0.3):
        print(f"[朋友圈标签] 点击标签项失败: {tag_text}")
        return False

    random_delay(0.3, 0.5)

    try:
        tag_edit.SendKeys("{Ctrl}a{Delete}")
        random_delay(0.2, 0.3)
    except Exception:
        pass

    return True
