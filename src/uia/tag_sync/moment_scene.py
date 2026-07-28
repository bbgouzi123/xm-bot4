import time
import logging
from typing import List
import uiautomation as uia
from .utils import (
    random_delay,
    try_click,
    exists_safe,
    fill_remark_in_edit_window,
    close_profile_window,
)
from .moment_scene_helper import (
    open_profile_from_moment,
    open_edit_contact_from_profile,
    search_and_select_tag_in_edit_window,
    refind_moment_item,
    verify_profile_window_open,
)

logger = logging.getLogger(__name__)

class MomentSceneMixin:
    """微信朋友圈场景下标签同步的 Mixin"""

    def apply_tags_from_moment(
        self,
        moment_window,
        item_ctrl,
        author_name: str,
        tags: List[str],
        remark: str = "",
        inside_lock: bool = False,
    ) -> bool:
        """在朋友圈中给动态作者打标签（不离开朋友圈）

        Args:
            inside_lock: 调用方已持有 UIBus 锁时传 True，跳过内部 run_uia_task
                         避免嵌套申请锁导致死锁（典型场景：moment_interact@ 任务内调用）
        """
        import win32gui

        if not tags:
            return True

        print(f"[朋友圈标签] 开始给 {author_name} 打标签: {tags}")

        def _do_tag():
            profile_win = open_profile_from_moment(item_ctrl, author_name)
            if not profile_win:
                return False

            edit_win = open_edit_contact_from_profile(profile_win)
            if not edit_win:
                close_profile_window()
                return False

            try:
                from src.uia.retry.window_ops import force_foreground
                force_foreground(edit_win.NativeWindowHandle)
            except Exception:
                pass

            if remark:
                fill_remark_in_edit_window(edit_win, remark)

            success_tags = []
            for tag_text in tags:
                if search_and_select_tag_in_edit_window(edit_win, tag_text):
                    success_tags.append(tag_text)
                    print(f"[朋友圈标签] ✅ 标签已选: {tag_text}")
                else:
                    print(f"[朋友圈标签] ⚠️ 标签失败: {tag_text}")

            done_btn = edit_win.ButtonControl(Name="完成")
            if exists_safe(done_btn, 1.0):
                try_click(done_btn, max_retries=2, delay=0.3)
                random_delay(0.5, 0.8)
            else:
                for btn_name in ["保存", "确定"]:
                    alt_btn = edit_win.ButtonControl(Name=btn_name)
                    if exists_safe(alt_btn, 0.5):
                        try_click(alt_btn, max_retries=2, delay=0.3)
                        random_delay(0.5, 0.8)
                        break

            close_profile_window()

            moment_hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "朋友圈") or win32gui.FindWindow("mmui::SNSWindow", "朋友圈") or win32gui.FindWindow("SNSWnd", None)
            if moment_hwnd:
                from src.uia.retry.window_ops import force_foreground
                force_foreground(moment_hwnd)
                random_delay(0.3, 0.5)

            print(f"[朋友圈标签] 完成: {author_name} ← {success_tags}")
            return len(success_tags) > 0

        try:
            if inside_lock:
                # 调用方已持锁，直接执行，不再申请锁（避免嵌套死锁）
                return _do_tag()
            else:
                from src.utils.uia_task_runner import run_uia_task
                with run_uia_task(f"给 {author_name} 同步朋友圈标签", priority=10):
                    return _do_tag()

        except Exception as e:
            logger.error(f"[朋友圈标签] 异常: {e}")
            try:
                uia.SendKeys("{ESC}")
                time.sleep(0.3)
                uia.SendKeys("{ESC}")
                time.sleep(0.3)
                uia.SendKeys("{ESC}")
            except Exception:
                pass
            return False

    def _refind_moment_item(self, author_name: str):
        """在朋友圈列表中，重新根据作者名字寻找动态条目"""
        return refind_moment_item(author_name)

    def _verify_profile_window_open(self, author_name: str):
        """验证名片窗口是否已经成功打开"""
        return verify_profile_window_open(author_name)

    def _open_profile_from_moment(self, item_ctrl, author_name: str):
        """从朋友圈动态条目中点击头像打开名片弹窗"""
        return open_profile_from_moment(item_ctrl, author_name)

    def _open_edit_contact_from_profile(self, profile_win):
        """在名片弹窗中点击'...' → '设置备注和标签'"""
        return open_edit_contact_from_profile(profile_win)

    def _search_and_select_tag_in_edit_window(self, edit_win, tag_text: str) -> bool:
        """在'设置备注和标签'窗口中搜索并选择标签"""
        return search_and_select_tag_in_edit_window(edit_win, tag_text)
