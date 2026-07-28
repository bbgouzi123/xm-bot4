import time
import random
import logging
from typing import Optional, Dict, Any

import win32gui
import uiautomation as uia
import pyperclip

from .add_friend_helpers import AddFriendHelper
from .retry import try_click, exists_with_timeout, random_delay

logger = logging.getLogger(__name__)


class GroupAddFriendEngine(AddFriendHelper):
    """微信群批量加好友自动化引擎"""

    def __init__(self, driver):
        self.driver = driver

    def add_group_members(
        self,
        group_name: str,
        max_add_count: int = 15,
        remark_prefix: Optional[str] = None,
        tags: Optional[str] = None,
        verify_message: Optional[str] = None,
        interval_range: tuple = (10, 20),
        task_state: dict = None,
    ) -> Dict[str, Any]:
        """批量加群好友任务封装入口"""
        if not self.driver.is_connected():
            return {"success": False, "message": "微信未连接", "added_count": 0}

        try:
            from src.uia.input_guard import uia_lock
            with uia_lock(f"正在群【{group_name}】批量加好友"):
                return self._do_add_group_members(
                    group_name, max_add_count, remark_prefix, tags, verify_message, interval_range, task_state
                )
        except Exception as e:
            logger.error(f"add_group_members 执行失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {"success": False, "message": str(e), "added_count": 0}

    def _do_add_group_members(
        self,
        group_name: str,
        max_add_count: int,
        remark_prefix: Optional[str],
        tags: Optional[str],
        verify_message: Optional[str],
        interval_range: tuple,
        task_state: dict,
    ) -> Dict[str, Any]:
        from src.uia.input_guard import uia_lock

        self.driver.SwitchToThisWindow()
        random_delay(0.5, 1.0)

        switched = self.driver.ChatWith(group_name)
        if not switched:
            return {"success": False, "message": f"无法切换到群聊会话【{group_name}】", "added_count": 0}

        main_win = uia.ControlFromHandle(self.driver.hwnd) if getattr(self.driver, 'hwnd', None) else uia.WindowControl(searchDepth=1, ClassName="WeChatMainWndForPC")
        chat_container = main_win.GroupControl(ClassName="mmui::ChatDetailView")
        
        chat_info_btn = None
        if chat_container.Exists(0.5):
            chat_info_btn = chat_container.ButtonControl(Name="聊天信息")
        if not chat_info_btn or not chat_info_btn.Exists(0.2):
            chat_info_btn = main_win.ButtonControl(Name="聊天信息")
        if not chat_info_btn or not chat_info_btn.Exists(0.2):
            search_root = chat_container if chat_container.Exists(0.3) else main_win
            for btn in search_root.ButtonControlList():
                if "聊天信息" in (btn.Name or "") or "聊天信息" in getattr(btn, 'HelpText', '') or "聊天信息" in getattr(btn, 'ToolTip', ''):
                    chat_info_btn = btn
                    break
        if not chat_info_btn or not chat_info_btn.Exists(0.2):
            search_root = chat_container if chat_container.Exists(0.3) else main_win
            candidates = [btn for btn in search_root.ButtonControlList() if btn.ClassName == "mmui::XImage"]
            if candidates:
                candidates.sort(key=lambda b: b.BoundingRectangle.left, reverse=True)
                chat_info_btn = candidates[0]

        if not chat_info_btn or not chat_info_btn.Exists(1.0):
            return {"success": False, "message": "未找到群详情【聊天信息】按钮", "added_count": 0}

        self._click_by_rect(chat_info_btn)
        random_delay(1.0, 1.5)

        chat_member_list = main_win.ListControl(Name="聊天成员", ClassName="QFReuseGridWidget")
        if not chat_member_list.Exists(0.5):
            chat_member_list = main_win.ListControl(AutomationId="chat_member_list", ClassName="QFReuseGridWidget")

        if not chat_member_list.Exists(1.5):
            # 尝试再次点击以防上一次没点开
            self._click_by_rect(chat_info_btn)
            return {"success": False, "message": "未找到群成员列表", "added_count": 0}

        from src.friend.group_friend_history import get_processed_names, add_history_record
        processed_names = get_processed_names(group_name)
        added_count = 0
        scroll_attempts = 0
        max_scroll_attempts = 15

        while added_count < max_add_count and scroll_attempts < max_scroll_attempts:
            uia_lock.check_interrupt()

            if task_state and not task_state.get("running", True):
                break

            if task_state and task_state.get("paused", False):
                while task_state.get("paused", False) and task_state.get("running", True):
                    uia_lock.check_interrupt()
                    time.sleep(1.0)
                if task_state and not task_state.get("running", True):
                    break

            items = chat_member_list.GetChildren()
            new_items_found = False

            for item in items:
                uia_lock.check_interrupt()
                if added_count >= max_add_count:
                    break

                if task_state and not task_state.get("running", True):
                    break
                if task_state and task_state.get("paused", False):
                    break

                name = (item.Name or "").strip()
                if not name or name in ("添加", "添加成员", "删除", "删除成员", "add", "del") or name in processed_names:
                    continue

                processed_names.add(name)
                new_items_found = True

                self._click_by_rect(item)
                random_delay(0.8, 1.2)

                # 兼容查找 ContactProfileWnd/mmui::ProfileUniquePop 独立窗口及 main_win 嵌入面板中的“添加到通讯录”与“发消息”按钮
                profile_win = uia.WindowControl(searchDepth=1, ClassName="ContactProfileWnd")
                if not profile_win.Exists(0.1):
                    profile_win = uia.WindowControl(searchDepth=1, ClassName="mmui::ProfileUniquePop")
                container = profile_win if profile_win.Exists(0.3) else main_win
                
                add_to_btn = container.ButtonControl(Name="添加到通讯录")
                send_btn = container.ButtonControl(Name="发消息")

                is_friend = False
                is_unknown = True

                for _ in range(5):
                    # 每次循环重新确认窗口，以应对弹窗显示的微弱延迟并兼容新旧不同版本的窗口类名
                    profile_win = uia.WindowControl(searchDepth=1, ClassName="ContactProfileWnd")
                    if not profile_win.Exists(0.05):
                        profile_win = uia.WindowControl(searchDepth=1, ClassName="mmui::ProfileUniquePop")
                    container = profile_win if profile_win.Exists(0.1) else main_win
                    add_to_btn = container.ButtonControl(Name="添加到通讯录")
                    send_btn = container.ButtonControl(Name="发消息")
                    
                    if add_to_btn.Exists(0.1):
                        is_unknown = False
                        break
                    if send_btn.Exists(0.1):
                        is_friend = True
                        is_unknown = False
                        break
                    time.sleep(0.2)

                if is_unknown or is_friend:
                    if is_friend:
                        add_history_record(group_name, name, "already_friend")
                    else:
                        add_history_record(group_name, name, "ignored")
                    uia.SendKeys("{Escape}")
                    random_delay(0.3, 0.5)
                    continue

                self._click_by_rect(add_to_btn)
                random_delay(1.0, 1.5)

                apply_hwnd = None
                possible_titles = ["申请添加朋友", "添加朋友请求", "添加到通讯录"]
                for _ in range(6):
                    for title in possible_titles:
                        hwnd = win32gui.FindWindow(None, title)
                        if hwnd and win32gui.IsWindowVisible(hwnd):
                            apply_hwnd = hwnd
                            break
                    if apply_hwnd:
                        break
                    time.sleep(0.3)

                if not apply_hwnd:
                    uia.SendKeys("{Escape}")
                    random_delay(0.3, 0.5)
                    continue

                apply_win = uia.ControlFromHandle(apply_hwnd)
                actual_remark = f"{remark_prefix}{name}" if remark_prefix else name

                # 设置备注
                try:
                    remark_edit = apply_win.EditControl(Name="修改备注")
                    if remark_edit.Exists(0.5):
                        try_click(remark_edit, max_retries=2, delay=0.1)
                        remark_edit.SendKeys("{Ctrl}a{Delete}")
                        pyperclip.copy(actual_remark)
                        random_delay(0.2, 0.3)
                        remark_edit.SendKeys("{Ctrl}v")
                        random_delay(0.2, 0.3)
                except Exception:
                    pass

                # 设置标签
                if tags:
                    try:
                        from src.uia.tag_sync.utils import fill_tags_via_search_and_select
                        tag_list = tags.split(",") if "," in tags else tags.split("，")
                        tag_list = [t.strip() for t in tag_list if t.strip()]
                        fill_tags_via_search_and_select(apply_win, tag_list)
                    except Exception:
                        pass

                # 设置验证消息
                if verify_message:
                    try:
                        verify_edit = apply_win.EditControl(Name="发送添加朋友申请")
                        if verify_edit.Exists(0.5):
                            try_click(verify_edit, max_retries=2, delay=0.1)
                            verify_edit.SendKeys("{Ctrl}a{Delete}")
                            pyperclip.copy(verify_message)
                            random_delay(0.2, 0.3)
                            verify_edit.SendKeys("{Ctrl}v")
                            random_delay(0.2, 0.3)
                    except Exception:
                        pass

                # 勾选朋友圈
                try:
                    cb = apply_win.CheckBoxControl(Name="允许对方看到你的朋友圈、状态、微信运动等")
                    if cb.Exists(0.5):
                        try_click(cb, max_retries=2, delay=0.1)
                        random_delay(0.2, 0.3)
                except Exception:
                    pass

                # 点击确定
                ok_btn = apply_win.ButtonControl(Name="确定")
                if ok_btn.Exists(0.5):
                    self._click_by_rect(ok_btn)
                    random_delay(1.0, 1.5)

                success_submit = False
                for _ in range(5):
                    if not self._any_window_exists(["申请添加朋友", "添加朋友请求", "添加到通讯录"]):
                        success_submit = True
                        break
                    time.sleep(0.5)

                self._close_add_friend_dialogs(main_win, apply_win)

                if success_submit:
                    added_count += 1
                    add_history_record(group_name, name, "success")
                    if task_state:
                        task_state["progress"]["processed"] = added_count
                        task_state["progress"]["succeeded"] = added_count
                        try:
                            from src.api.add_friend_api.task_engine import save_task_state_to_db
                            save_task_state_to_db()
                        except Exception:
                            pass

                    sleep_time = random.uniform(interval_range[0], interval_range[1])
                    for _ in range(int(sleep_time)):
                        uia_lock.check_interrupt()
                        if task_state and not task_state.get("running", True):
                            break
                        time.sleep(1)
                else:
                    add_history_record(group_name, name, "failed")
                    if task_state:
                        task_state["progress"]["failed"] += 1
                        task_state["progress"]["processed"] += 1
                        try:
                            from src.api.add_friend_api.task_engine import save_task_state_to_db
                            save_task_state_to_db()
                        except Exception:
                            pass

            if not new_items_found:
                chat_member_list.SendKeys("{PageDown}")
                random_delay(1.0, 1.5)
                scroll_attempts += 1
            else:
                scroll_attempts = 0

        if chat_info_btn and chat_info_btn.Exists(0.5):
            self._click_by_rect(chat_info_btn)

        return {"success": True, "message": f"批量加群好友任务结束，共添加 {added_count} 人", "added_count": added_count}
