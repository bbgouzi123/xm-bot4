import time
import random
import logging
from typing import Optional, List
import uiautomation as uia
import pyperclip

from .compat_helpers import (
    random_delay,
    exists_safe,
    try_click,
    bfs_find,
    safe_set_clipboard,
    click_remark_tag_entry,
    fill_remark_in_edit_window,
    fill_remark_and_tags_in_edit_window,
    save_tags,
    close_profile_window,
    find_and_click_tag_modify,
    cleanup_profile_window,
    find_active_edit_control,
    open_settings_dialog_via_more_menu,
    fill_remark_name,
)

def find_tag_button_on_profile_card(profile_win) -> Optional[uia.Control]:
    """在资料卡片上查找已存在的标签按钮。如果用户有打过标签，可直接点击此按钮展开下拉框"""
    rows = []
    def find_rows(ctrl):
        if ctrl.ClassName == "mmui::ProfileLineCommonView":
            rows.append(ctrl)
        try:
            for child in ctrl.GetChildren():
                find_rows(child)
        except Exception:
            pass
            
    find_rows(profile_win)
    for row in rows:
        title_ctrl = row.TextControl(Name="标签")
        if title_ctrl.Exists(0.2):
            tag_btn = row.ButtonControl(ClassName="mmui::XMouseEventView")
            if tag_btn.Exists(0.5):
                name = (tag_btn.Name or "").strip()
                if name and name != "添加标签":
                    return tag_btn
    return None

def find_search_edit_in_control(ctrl) -> Optional[uia.EditControl]:
    """递归查找控件下的标签搜索编辑框"""
    edit = ctrl.EditControl(ClassName="mmui::XValidatorTextEdit")
    if edit.Exists(0.1):
        return edit
    try:
        children = ctrl.GetChildren()
    except Exception:
        return None
    for child in children:
        if child.ControlTypeName == "EditControl" or child.ClassName == "mmui::XValidatorTextEdit":
            if child.Name == "发送添加朋友申请":
                continue
            return child
        res = find_search_edit_in_control(child)
        if res:
            return res
    return None

def wait_for_label_popover(timeout=2.0) -> Optional[uia.WindowControl]:
    """使用 Win32 FindWindow 轮询等待标签下拉框出现，完全避免 UIA desktop root 挂起"""
    import win32gui
    start = time.time()
    while time.time() - start < timeout:
        hwnd = win32gui.FindWindow("mmui::LabelPopover", None)
        if hwnd and win32gui.IsWindow(hwnd):
            try:
                ctrl = uia.ControlFromHandle(hwnd)
                if ctrl:
                    return ctrl
            except Exception:
                pass
        time.sleep(0.1)
    return None

def fill_tags_via_search_and_select(
    edit_win: uia.WindowControl,
    tags: List[str],
    existing_tags: List[str] = None
) -> bool:
    """步骤：利用 Search-and-Select 流程添加或新建列表内的标签"""
    if existing_tags is None:
        existing_tags = []
        
    tag_form_btn = edit_win.ButtonControl(Name="修改标签")
    has_tag_form_btn = tag_form_btn.Exists(0.5)
    
    # 如果是从名片直接点击，没有修改标签按钮，我们先等等下拉栏 popover 轮询，缩短至最长 1.0 秒
    if not has_tag_form_btn:
        wait_for_label_popover(1.0)
        
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
            
        # 情况1：如果是已经在列表中勾选的标签，微信在输入时不会在下拉列表中显示，直接跳过输入
        if tag in existing_tags:
            print(f"[标签备注同步] 标签 '{tag}' 已处于勾选状态，跳过输入与搜索")
            continue
            
        # 尝试快速等待 popover（0.3秒即可，减少轮询耗时）
        popover = wait_for_label_popover(0.3)
        
        # 如果下拉栏未展开且有修改标签按钮，则点击展开
        if (not popover or not popover.Exists(0.1)) and has_tag_form_btn:
            print(f"[标签备注同步] 展开下拉栏准备添加标签: {tag}")
            tag_form_btn.Click(simulateMove=False)
            time.sleep(0.3)
            
        # 1. 尝试直接在 edit_win 中快速查找，避免 wait_for_label_popover 的无意义轮询延迟
        search_edit = edit_win.EditControl(ClassName="mmui::XValidatorTextEdit")
        if not search_edit.Exists(0.1):
            search_edit = find_search_edit_in_control(edit_win)
            
        # 2. 如果在 edit_win 里找不到，则尝试等待/定位独立的 popover 窗口 (只等待最长0.5秒)
        if not search_edit or not search_edit.Exists(0.1):
            popover = wait_for_label_popover(0.5)
            if popover and popover.Exists(0.3):
                search_edit = popover.EditControl(ClassName="mmui::XValidatorTextEdit")
                if not search_edit.Exists(0.2):
                    search_edit = find_search_edit_in_control(popover)
            
        if not search_edit or not search_edit.Exists(0.5):
            print("[标签备注同步] 找不到标签搜索输入框")
            return False
            
        # 聚焦并贴入文本
        search_edit.Click(simulateMove=False)
        time.sleep(0.15)
        search_edit.SetFocus()
        time.sleep(0.15)
        
        # 🛡️ 焦点验证：确认焦点落在标签搜索框而非打招呼/备注输入框，防止误填
        try:
            focused = uia.GetFocusedControl()
            focused_cls = getattr(focused, "ClassName", "") or ""
            focused_name = getattr(focused, "Name", "") or ""
            if focused_cls != "mmui::XValidatorTextEdit" and focused_name not in ("", "搜索标签"):
                print(f"[标签备注同步] ⚠️ 焦点验证失败，当前焦点控件为 cls={focused_cls} name={focused_name}，跳过本标签输入以防误填")
                continue
        except Exception:
            pass  # 焦点检查失败时不影响主流程，继续执行
        
        safe_set_clipboard(tag)
        uia.SendKeys("{Ctrl}a")
        time.sleep(0.1)
        uia.SendKeys("{Ctrl}v")
        time.sleep(0.3)

        # 💡 智能下拉检测：粘贴后根据下拉列表状态决定动作
        # 情况1：无下拉列表 → 该标签已处于勾选状态，无需 Down+Enter，直接处理下一个
        # 情况2：仅显示"创建新标签" → 执行 Down+Enter 创建并选中
        # 情况3：显示多个匹配项 → 执行 Down+Enter 选中第一项（精确匹配通常排第一）；
        #         若下拉仍存在，在外层调用者负责收起（如点击"发送添加朋友申请"输入框）
        popover_after_paste = wait_for_label_popover(0.5)

        if not popover_after_paste or not popover_after_paste.Exists(0.1):
            # 无下拉：已勾选，跳过
            print(f"[标签备注同步] 标签 '{tag}' 粘贴后无下拉列表，该标签已处于勾选状态，跳过 Down+Enter")
        else:
            # 检查下拉项内容，判断是否仅有"创建新标签"
            dropdown_names = []
            try:
                for ctrl, _ in uia.WalkControl(popover_after_paste, maxDepth=6):
                    n = (ctrl.Name or "").strip()
                    ct = ctrl.ControlTypeName
                    if n and ct in ("ListItemControl", "ButtonControl", "TextControl", "CustomControl"):
                        dropdown_names.append(n)
            except Exception:
                pass

            is_only_create_new = bool(dropdown_names) and all("创建新标签" in n for n in dropdown_names)

            if is_only_create_new:
                print(f"[标签备注同步] 标签 '{tag}' 下拉仅显示【创建新标签】，执行 Down+Enter 创建并选中...")
            else:
                item_count = len(dropdown_names)
                print(f"[标签备注同步] 标签 '{tag}' 下拉有 {item_count} 个匹配项，执行 Down+Enter 选中首项（精确匹配排首位）...")

            uia.SendKeys("{DOWN}{ENTER}")
            time.sleep(0.35)
            
    # 如果下拉列表依然可见且有修改标签按钮，点击修改标签按钮收起
    if has_tag_form_btn:
        popover = wait_for_label_popover(0.3)
        if popover and popover.Exists(0.2):
            print("[标签备注同步] 收起下拉栏")
            tag_form_btn.Click(simulateMove=False)
            time.sleep(0.3)
        
    return True

def click_save_button_in_dialog(edit_win: uia.WindowControl) -> bool:
    """步骤：点击确定/保存/完成按钮保存修改"""
    print("[标签备注同步] 正在定位 '确定' 保存按钮...")
    ok_btn = None
    buttons = []
    def find_buttons(ctrl):
        if ctrl.ControlTypeName == "ButtonControl":
            buttons.append(ctrl)
        try:
            for child in ctrl.GetChildren():
                find_buttons(child)
        except Exception:
            pass
            
    find_buttons(edit_win)
    for btn in buttons:
        for child in btn.GetChildren():
            if child.Name in ("确定", "保存", "完成"):
                ok_btn = btn
                break
        if ok_btn:
            break
            
    if ok_btn:
        print("[标签备注同步] 找到保存按钮，执行点击")
        if try_click(ok_btn, max_retries=2, delay=0.3):
            random_delay(0.5, 0.8)
            return True
            
    print("[标签备注同步] 未能定位到保存按钮，尝试回车键兜底")
    try:
        uia.SendKeys("{Enter}")
        random_delay(0.3, 0.5)
        return True
    except Exception:
        pass
    return False

def close_profile_card_window(profile_win: uia.WindowControl, driver=None):
    """步骤：发送 WM_CLOSE 消息或按 ESC 键关闭头像资料卡片窗口"""
    try:
        import win32gui
        import win32con
        hwnd = None
        if profile_win:
            try:
                hwnd = profile_win.NativeWindowHandle
            except Exception:
                pass
        if not hwnd:
            from src.uia.message_direction_helper import find_profile_hwnd
            hwnd = find_profile_hwnd()
            
        if hwnd and win32gui.IsWindow(hwnd):
            print(f"[资料名片] 正在向资料卡窗口 hwnd={hwnd} 发送 WM_CLOSE 消息...")
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            for _ in range(10):
                time.sleep(0.05)
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    print("[资料名片] 通过 WM_CLOSE 消息成功关闭资料卡")
                    return
            
            print("[资料名片] WM_CLOSE 未生效，尝试发送 ESC 关闭...")
            try:
                if profile_win:
                    profile_win.SetFocus()
            except Exception:
                pass
            uia.SendKeys("{ESC}")
            time.sleep(0.3)
            return
    except Exception as e:
        print(f"[资料名片] 关闭资料卡异常: {e}")

    if exists_safe(profile_win, 0.2):
        uia.SendKeys("{ESC}")
        time.sleep(0.3)

def fill_phone_in_edit_window(edit_win: uia.WindowControl, phone: str) -> bool:
    """在设置备注和标签窗口中，定位并填写电话号码"""
    if not phone:
        return True
        
    phone = phone.strip()
    print(f"[标签备注同步] 准备在对话框中填写电话号码: {phone}")
    
    try:
        # 1. 尝试寻找“添加电话”按钮。如果存在该按钮，点击后微信会自动展开输入框并把焦点激活在此处
        add_phone_btn = edit_win.ButtonControl(Name="添加电话")
        if add_phone_btn.Exists(0.5):
            print("[标签备注同步] 发现 '添加电话' 按钮，点击以展开输入框...")
            add_phone_btn.Click(simulateMove=False)
            time.sleep(0.3)  # 等待输入框展开并自动激活焦点
            
            # 直接粘贴填入电话，省去任何 UIA 点击和定位的耗时与潜在偏差风险
            print("[标签备注同步] 输入框已激活，直接写入电话...")
            safe_set_clipboard(phone)
            uia.SendKeys("{Ctrl}v")
            time.sleep(0.25)
            print(f"[标签备注同步] 电话号码 {phone} 已通过焦点激活成功填入")
            return True
            
        # 2. 如果“添加电话”按钮不存在，说明输入框已被展开，我们需要精确寻找并点击它进行填入
        phone_edit = edit_win.EditControl(ClassName="mmui::XLineEdit")
        if not phone_edit.Exists(0.2):
            phone_edit = edit_win.EditControl(FullDescription="填写电话")
            
        # 3. 兜底遍历：排除备注名输入框以防止误填
        if not phone_edit.Exists(0.1):
            phone_edit = None
            edits = []
            def find_edits(ctrl):
                if ctrl.ControlTypeName == "EditControl":
                    edits.append(ctrl)
                try:
                    for child in ctrl.GetChildren():
                        find_edits(child)
                except Exception:
                    pass
            find_edits(edit_win)
            
            # 严格排除 ClassName 为 mmui::XValidatorTextEdit（备注名框）及 FullDescription 为“添加备注”的编辑框
            valid_phone_edits = [
                e for e in edits 
                if e.ClassName != "mmui::XValidatorTextEdit" 
                and getattr(e, "FullDescription", "") != "添加备注"
            ]
            if valid_phone_edits:
                phone_edit = valid_phone_edits[0]
            
        if not phone_edit or not phone_edit.Exists(0.5):
            print("[标签备注同步] 警告：未能找到展开的电话输入框")
            return False
            
        print("[标签备注同步] 找到展开的电话输入框，准备填入电话...")
        phone_edit.Click(simulateMove=False)
        time.sleep(0.15)
        phone_edit.SetFocus()
        time.sleep(0.15)
        
        # 剪贴板粘贴填入电话
        safe_set_clipboard(phone)
        uia.SendKeys("{Ctrl}a")
        time.sleep(0.1)
        uia.SendKeys("{Ctrl}v")
        time.sleep(0.25)
        print(f"[标签备注同步] 电话号码 {phone} 已成功填入输入框")
        return True
    except Exception as e:
        print(f"[标签备注同步] 填写电话时发生异常: {e}")
        return False


