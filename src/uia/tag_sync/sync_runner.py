import time
import logging
from typing import List, Optional
import uiautomation as uia
from .utils import (
    random_delay,
    exists_safe,
    open_settings_dialog_via_more_menu,
    fill_remark_name,
    fill_tags_via_search_and_select,
    click_save_button_in_dialog,
    close_profile_card_window,
    cleanup_profile_window,
    find_tag_button_on_profile_card,
    wait_for_label_popover,
    fill_phone_in_edit_window,
)
from .profile_helper import open_friend_profile

logger = logging.getLogger(__name__)

class WeChatTagRemarkSyncRunner:
    """微信聊天场景下同步打标签和改备注的具体 UIA 动作执行器"""

    def __init__(self, driver):
        self.driver = driver

    def apply_tags_from_chat(
        self,
        friend_name: str,
        tags: List[str],
    ) -> bool:
        """从聊天窗口给好友打标签（完整流程，支持已打过标签时的极速直连优化）"""
        if not tags:
            return True

        print(f"[标签同步] 开始给 {friend_name} 打标签: {tags}")

        try:
            from src.utils.uia_task_runner import run_uia_task
            from src.uia.input_guard import uia_lock as physical_lock
            with run_uia_task(f"给 {friend_name} 同步标签: {tags}", priority=10, use_physical_lock=True):
                print(f"[标签同步] UIA 锁已获取，微信连接自检...")
                physical_lock.update_status(f"正在准备同步 {friend_name} 的标签: {tags}...")
                if not self.driver._connected or not self.driver.root:
                    print("[标签同步] 微信未连接")
                    return False

                print(f"[标签同步] 切换微信主窗口到前台...")
                self.driver.SwitchToThisWindow()
                random_delay(0.3, 0.6)
                if hasattr(self.driver, "ChatWith"):
                    print(f"[标签同步] 切换至聊天会话: {friend_name}")
                    physical_lock.update_status(f"正在切换到 {friend_name} 的聊天窗口以同步标签: {tags}...")
                    self.driver.ChatWith(friend_name)
                    random_delay(0.5, 0.8)

                print(f"[标签同步] 准备打开好友资料弹窗...")
                physical_lock.update_status(f"正在寻找并点击 {friend_name} 的头像以修改标签: {tags}...")
                profile_win = open_friend_profile(self.driver)
                if not profile_win:
                    print("[标签同步] 警告：打开好友资料弹窗失败")
                    return False

                # 【极速直连优化】：若已打过标签，可直接点击名片上的标签项呼出下拉列表
                existing_tags = []
                tag_btn = find_tag_button_on_profile_card(profile_win)
                if tag_btn:
                    if tag_btn.Name:
                        existing_tags = [t.strip() for t in tag_btn.Name.split(",") if t.strip()]
                    
                    # 🌟 极具前瞻性的风控优化：若需要同步的标签均已存在，则直接返回 True 标记成功，无需再次物理点击修改
                    if all(t in existing_tags for t in tags):
                        print(f"[标签同步] 待打标签 {tags} 已完全存在于微信标签 {existing_tags} 中，跳过物理点击，直接标记同步成功。")
                        close_profile_card_window(profile_win, self.driver)
                        cleanup_profile_window(self.driver, profile_win)
                        return True

                    print(f"[标签同步] 探测到已有标签 {existing_tags}，采用极速直连直接点击标签栏修改...")
                    physical_lock.update_status(f"正在点击标签栏，准备同步标签: {tags}...")
                    tag_btn.Click(simulateMove=False)
                    random_delay(0.6, 0.8)
                    
                    popover = wait_for_label_popover(2.0)
                    
                    success = fill_tags_via_search_and_select(profile_win, tags, existing_tags)
                    if not success:
                        print("[标签同步] 警告：标签栏修改操作失败")
                        if popover and popover.Exists(0.2):
                            uia.SendKeys("{ESC}")
                        close_profile_card_window(profile_win, self.driver)
                        return False
                        
                    # 关闭下拉框
                    if popover and popover.Exists(0.2):
                        uia.SendKeys("{ESC}")
                        random_delay(0.3, 0.5)
                else:
                    # fallback 流程
                    print("[标签同步] 好友尚未打过标签，通过更多菜单打开独立修改对话框...")
                    physical_lock.update_status(f"通过菜单打开修改标签对话框以同步标签: {tags}...")
                    edit_win = open_settings_dialog_via_more_menu(profile_win)
                    if not edit_win:
                        print("[标签同步] 警告：未能打开设置备注 and 标签对话框")
                        close_profile_card_window(profile_win, self.driver)
                        return False

                    print("[标签同步] 开始在对话框内选择标签...")
                    success = fill_tags_via_search_and_select(edit_win, tags, existing_tags)
                    if not success:
                        print("[标签同步] 警告：打标签操作失败")
                        import win32gui, win32con
                        win32gui.PostMessage(edit_win.NativeWindowHandle, win32con.WM_CLOSE, 0, 0)
                        close_profile_card_window(profile_win, self.driver)
                        return False

                    print("[标签同步] 标签选择完成，点击确定按钮保存...")
                    physical_lock.update_status(f"正在保存修改后的标签: {tags}...")
                    save_ok = click_save_button_in_dialog(edit_win)
                    if save_ok:
                        print("[标签同步] 已成功保存修改后的标签")
                        random_delay(0.8, 1.2)
                    else:
                        print("[标签同步] 警告：保存备注标签操作未成功确认")

                print(f"[标签同步] 开始进行扫尾现场清理...")
                close_profile_card_window(profile_win, self.driver)
                cleanup_profile_window(self.driver, profile_win)
                print(f"[标签同步] 全部同步流程成功完成: {friend_name} ← {tags}")
                return True

        except Exception as e:
            logger.error(f"[标签同步] 流程中异常终止: {e}")
            try:
                p_win = locals().get("profile_win", None)
                cleanup_profile_window(self.driver, p_win)
            except Exception:
                try:
                    uia.SendKeys("{ESC}")
                    time.sleep(0.3)
                except Exception:
                    pass
            return False

    def apply_remark_and_tags_from_chat(
        self,
        friend_name: str,
        remark: Optional[str] = None,
        tags: Optional[List[str]] = None,
        phone: Optional[str] = None,
    ) -> bool:
        """从聊天窗口给好友修改备注、打标签并填写联系电话"""
        if not remark and not tags and not phone:
            return True

        print(f"[备注标签同步] 开始给 {friend_name} 同步修改：备注='{remark}'，标签={tags}，电话='{phone}'")

        try:
            from src.utils.uia_task_runner import run_uia_task
            from src.uia.input_guard import uia_lock as physical_lock
            with run_uia_task(f"给 {friend_name} 同步备注、标签和电话: 备注='{remark}', 标签={tags}, 电话='{phone}'", priority=10, use_physical_lock=True):
                print(f"[备注标签同步] UIA 锁已获取，微信连接自检...")
                physical_lock.update_status(f"正在准备同步 {friend_name} 的备注: '{remark}'，标签: {tags}，电话: '{phone}'...")
                if not self.driver._connected or not self.driver.root:
                    print("[备注标签同步] 微信未连接")
                    return False

                print(f"[备注标签同步] 切换微信主窗口到前台...")
                self.driver.SwitchToThisWindow()
                random_delay(0.3, 0.6)
                if hasattr(self.driver, "ChatWith"):
                    print(f"[备注标签同步] 切换至聊天会话: {friend_name}")
                    physical_lock.update_status(f"正在切换到 {friend_name} 的聊天窗口以同步备注和标签...")
                    self.driver.ChatWith(friend_name)
                    random_delay(0.5, 0.8)

                print(f"[备注标签同步] 准备打开好友资料弹窗...")
                physical_lock.update_status(f"正在寻找并点击 {friend_name} 的头像以修改资料...")
                profile_win = open_friend_profile(self.driver)
                if not profile_win:
                    print("[备注标签同步] 警告：打开好友资料名片失败")
                    return False

                print(f"[备注标签同步] 好友资料名片已打开，通过三个点菜单打开资料修改对话框...")
                physical_lock.update_status(f"已打开 {friend_name} 的资料弹窗，准备设置备注: '{remark}'，标签: {tags}，电话: '{phone}'...")
                
                existing_tags = []
                tag_btn = find_tag_button_on_profile_card(profile_win)
                if tag_btn and tag_btn.Name:
                    existing_tags = [t.strip() for t in tag_btn.Name.split(",") if t.strip()]
                    
                edit_win = open_settings_dialog_via_more_menu(profile_win)
                if not edit_win:
                    print("[备注标签同步] 警告：未能打开设置备注和标签对话框")
                    close_profile_card_window(profile_win, self.driver)
                    return False

                print(f"[备注标签同步] 成功识别到对话框，开始填入数据")
                if remark is not None:
                    fill_remark_name(edit_win, remark)
                if tags:
                    fill_tags_via_search_and_select(edit_win, tags, existing_tags)
                if phone:
                    fill_phone_in_edit_window(edit_win, phone)

                print(f"[备注标签同步] 填入完成，点击确定按钮保存...")
                physical_lock.update_status(f"正在保存修改后的备注: '{remark}'、标签: {tags}和电话: '{phone}'...")
                save_ok = click_save_button_in_dialog(edit_win)
                if save_ok:
                    print("[备注标签同步] 已成功保存修改后的备注、标签与电话")
                    random_delay(0.8, 1.2)
                else:
                    print("[备注标签同步] 警告：保存资料操作未成功确认")

                print(f"[备注标签同步] 开始进行扫尾现场清理...")
                close_profile_card_window(profile_win, self.driver)
                cleanup_profile_window(self.driver, profile_win)
                print(f"[备注标签同步] 全部同步流程成功完成: {friend_name}")
                return True

        except Exception as e:
            logger.error(f"[备注标签同步] 出现异常: {e}")
            try:
                p_win = locals().get("profile_win", None)
                cleanup_profile_window(self.driver, p_win)
            except Exception:
                try:
                    uia.SendKeys("{ESC}")
                    time.sleep(0.3)
                except Exception:
                    pass
            return False
