import os
import time
import logging
import uiautomation as uia
import pyperclip

from src.uia.retry import random_delay, try_click

from .moments_helper import (
    find_sidebar_moments_nav,
    wait_moments_window,
    open_moments_window,
    close_moments,
    ensure_moments_foreground,
)

logger = logging.getLogger("WeChatDriver")


class WeChatMomentsMixin:
    def _find_sidebar_moments_nav(self):
        return find_sidebar_moments_nav(self)

    def _wait_moments_window(self, timeout: float = 5.0):
        return wait_moments_window(timeout)

    def _open_moments_window(self):
        return open_moments_window(self)

    def _close_moments(self, moment_window):
        close_moments(self, moment_window)

    def _ensure_moments_foreground(self):
        ensure_moments_foreground()

    def post_moment(self, text: str, image_paths: list = None) -> bool:
        """发布朋友圈（纯文字或图文）"""
        import win32gui as _w32

        if not self.is_connected():
            return False

        moment_window = None
        try:
            from src.uia.input_guard import uia_lock
            with self._lock, uia_lock("正在发布朋友圈"):
                moment_window = self._open_moments_window()
                if not moment_window:
                    logger.error("朋友圈窗口未出现")
                    return False

                hwnd = _w32.FindWindow(None, '朋友圈')
                if hwnd:
                    from src.uia.retry.window_ops import force_foreground
                    force_foreground(hwnd)
                random_delay(0.5, 1.0)

                publish_btn = None
                tab_items = []
                for ctrl, _ in uia.WalkControl(moment_window, maxDepth=8):
                    try:
                        if ctrl.ControlTypeName == 'ButtonControl':
                            if ctrl.ClassName == 'mmui::XTabBarItem':
                                tab_items.append(ctrl)
                            btn_name = ctrl.Name or ''
                            has_publish_chars = ('发' in btn_name and '表' in btn_name) or ('相机' in btn_name)
                            if has_publish_chars:
                                publish_btn = ctrl
                                break
                    except Exception:
                        continue

                if not publish_btn and len(tab_items) > 1:
                    publish_btn = tab_items[1]
                    logger.info("[朋友圈] 通过 Tab 索引匹配兜底成功，选中发表按钮")

                if not publish_btn:
                    logger.error("未找到'发表'按钮")
                    return False

                if not image_paths:
                    try:
                        from src.uia.retry.clicks import physical_long_press
                        rect = publish_btn.BoundingRectangle
                        x = (rect.left + rect.right) // 2
                        y = (rect.top + rect.bottom) // 2
                        physical_long_press(x, y, duration=2.0)
                        random_delay(1.0, 1.5)
                    except Exception:
                        try_click(publish_btn, max_retries=2, delay=0.5)
                        random_delay(1.0, 1.5)
                else:
                    from src.uia.retry.clicks import _get_shield_hide_ctx
                    with _get_shield_hide_ctx():
                        try_click(publish_btn, max_retries=2, delay=0.5)
                        random_delay(1.0, 1.5)
                        ok = self._select_files_via_dialog(image_paths, moment_window)
                        if not ok:
                            return False
                        random_delay(1.5, 2.5)

                pub_panel = None
                for ctrl, _ in uia.WalkControl(moment_window, maxDepth=10):
                    try:
                        if (ctrl.ClassName or '') == 'mmui::SnsPublishPanel':
                            pub_panel = ctrl
                            break
                    except Exception:
                        continue

                if not pub_panel:
                    logger.error("未找到 mmui::SnsPublishPanel 发布面板")
                    return False

                text_edit = None
                for ctrl, _ in uia.WalkControl(pub_panel, maxDepth=8):
                    try:
                        if (ctrl.ClassName or '') == 'mmui::XValidatorTextEdit':
                            text_edit = ctrl
                            break
                    except Exception:
                        continue

                if not text_edit:
                    logger.error("未找到 mmui::XValidatorTextEdit 输入框")
                    return False

                try_click(text_edit, max_retries=2, delay=0.3)
                random_delay(0.2, 0.4)
                if text:
                    pyperclip.copy(text)
                    uia.SendKeys('{Ctrl}v')
                random_delay(0.5, 1.0)

                submit_btn = None
                for ctrl, _ in uia.WalkControl(pub_panel, maxDepth=8):
                    try:
                        if (ctrl.ClassName or '') == 'mmui::XOutlineButton':
                            if ctrl.Name and '发表' in ctrl.Name:
                                submit_btn = ctrl
                                break
                            if not submit_btn:
                                submit_btn = ctrl
                    except Exception:
                        continue

                if not submit_btn:
                    logger.error("未找到发表提交按钮 mmui::XOutlineButton")
                    return False

                try_click(submit_btn, max_retries=2, delay=0.3)
                random_delay(2.0, 3.0)

                if not pub_panel.Exists(1):
                    logger.info(f"朋友圈已发布: {text[:30]}...")
                    self._ensure_chat_page(force=True)
                    return True
                else:
                    logger.warning("发布后面板未关闭，可能失败")
                    return False

        except Exception as e:
            logger.error(f"发布朋友圈失败: {e}")
            return False
        finally:
            if moment_window:
                try:
                    self._close_moments(moment_window)
                except Exception as fe:
                    logger.warning(f"[朋友圈] finally 关闭窗口异常: {fe}")

    def _select_files_via_dialog(self, image_paths: list, moment_window) -> bool:
        """在系统文件选择对话框中填入图片路径并点击打开"""
        from src.uia.modules.moments_file_dialog import select_files_via_dialog
        return select_files_via_dialog(image_paths, self._close_moments, moment_window)
