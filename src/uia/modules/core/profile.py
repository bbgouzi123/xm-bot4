"""当前账号资料：头像同步与用户信息（昵称 / wxid / 高清头像）提取。"""
import os
import ctypes
import time
import logging
import win32gui

from .profile_wnd_helper import (
    close_image_preview_if_needed,
    find_avatar_click_point,
    clear_old_profile_cards,
    trigger_profile_card,
)
from .profile_tree_helper import (
    wait_profile_tree_ready,
    scan_profile_fields,
    download_avatar_flow,
    close_profile_card,
)

logger = logging.getLogger("WeChatProfile")

def print(*args, **kwargs):
    try:
        msg = " ".join(str(arg) for arg in args)
        logger.debug(msg)
    except:
        pass


class WeChatCoreProfileMixin:
    def sync_avatar(self) -> dict:
        """手动同步头像（供前端按钮调用）"""
        if not self.is_connected():
            return {"success": False, "error": "微信未连接"}
        if not self._wxid:
            return {"success": False, "error": "微信号未提取，请先重新连接"}
        try:
            print(f"[UIA] 手动同步头像开始 (wxid={self._wxid})")
            self._extract_user_info(skip_avatar_if_exists=False)

            from src.crm.account_data import ACCOUNTS_DIR
            avatar_path = os.path.join(ACCOUNTS_DIR, f"{self._wxid}.png")
            if os.path.exists(avatar_path):
                return {"success": True, "message": "头像同步成功", "avatar_path": avatar_path}
            else:
                return {"success": False, "error": "头像文件未生成，UIA 操作可能被中断"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _close_image_preview_if_needed(self) -> bool:
        return close_image_preview_if_needed(self)

    def extract_user_info_with_isolation(self, skip_avatar_if_exists: bool = True):
        """带窗口隔离的账号信息提取"""
        all_wins = self.find_all_wechat_windows()
        other_hwnds = [w["hwnd"] for w in all_wins if w["hwnd"] != self.hwnd]

        minimized = []
        for h in other_hwnds:
            try:
                if win32gui.IsWindowVisible(h):
                    ctypes.windll.user32.ShowWindow(h, 6)  # SW_MINIMIZE
                    minimized.append(h)
            except Exception:
                pass
        if minimized:
            time.sleep(0.3)

        try:
            self._extract_user_info(skip_avatar_if_exists)
        finally:
            for h in minimized:
                try:
                    ctypes.windll.user32.ShowWindow(h, 9)  # SW_RESTORE
                except Exception:
                    pass

    def _extract_user_info(self, skip_avatar_if_exists: bool = False):
        """从微信窗口提取用户信息"""
        if skip_avatar_if_exists:
            try:
                from src.wechat_4x.db_profile_extractor import extract_profile_from_db
                res = extract_profile_from_db(self.hwnd)
                if res:
                    db_wxid, db_nickname = res
                    if db_wxid and db_nickname:
                        self._wxid = db_wxid
                        self._nickname = db_nickname
                        print(f"[UIA-DB-Fallback] ✅ 成功从数据库静默提取用户信息: wxid={db_wxid}, nickname={db_nickname}")
                        return
            except Exception as e_db:
                print(f"[UIA-DB-Fallback] ⚠️ 尝试从数据库提取用户信息失败: {e_db}")

        if not self.root:
            return

        import contextlib

        def _get_shield_ctx():
            try:
                from src.uia.privacy_shield import get_privacy_shield
                return get_privacy_shield().bypass_shield()
            except Exception:
                return contextlib.nullcontext()

        from src.utils.uia_task_runner import run_uia_task
        from src.uia.input_guard import uia_lock
        from silent_narrator import SilentNarrator
        from src.uia.startup_flow.narrator import start_narrator, stop_narrator

        success = False

        print("[UIA] 🔑 默认启用程序内部模拟讲述人 (SilentNarrator)...")
        try:
            SilentNarrator.activate()
        except Exception as e_act:
            print(f"[UIA] ⚠️ 激活 SilentNarrator 失败: {e_act}")

        try:
            with run_uia_task("提取微信账号信息", priority=10):
                with uia_lock("正在提取微信个人信息", hwnd=self.hwnd):
                    with _get_shield_ctx():
                        self._do_extract_user_info(skip_avatar_if_exists, is_silent=True)
            
            if self._wxid and self._nickname:
                success = True
                print("[UIA] ✅ 使用内部模拟讲述人成功提取微信账号数据")
        except Exception as e_uia:
            print(f"[UIA] ⚠️ 内部模拟讲述人提取异常: {e_uia}")

        if not success:
            print("[UIA] ⚠ 内部模拟讲述人提取失败，升级开启 Windows 自带讲述人...")
            try:
                start_narrator(force_physical=True)
            except Exception as e_start:
                print(f"[UIA] ⚠️ 启动物理讲述人异常: {e_start}")

            try:
                with run_uia_task("提取微信账号信息", priority=10):
                    with uia_lock("正在提取微信个人信息", hwnd=self.hwnd):
                        with _get_shield_ctx():
                            self._do_extract_user_info(skip_avatar_if_exists, is_silent=False)
                if self._wxid and self._nickname:
                    success = True
                    print("[UIA] ✅ 使用物理讲述人提取微信账号数据成功")
            except Exception as e_phys:
                print(f"[UIA] ⚠️ 物理讲述人提取异常: {e_phys}")

        try:
            SilentNarrator.deactivate()
        except Exception:
            pass

        try:
            stop_narrator(force_cleanup=True)
        except Exception:
            pass

    def _do_extract_user_info(self, skip_avatar_if_exists: bool = False, is_silent: bool = False):
        """实际执行 UIA 提取逻辑"""
        from src.uia.input_guard import uia_lock, UIAInterruptError
        import win32process
        import uiautomation as auto

        try:
            uia_lock.update_status("正在准备微信窗口，强制将微信置于最前台...")

            from src.uia.retry import ensure_wechat_foreground
            ensure_wechat_foreground(self.hwnd)
            time.sleep(0.3)

            uia_lock.update_status("正在检查并关闭残留的图片预览界面...")
            self._close_image_preview_if_needed()

            self.root = auto.ControlFromHandle(self.hwnd)
            if not self.root:
                print(f"[UIA] 微信窗口 (hwnd={self.hwnd}) 已失效或关闭，终止提取")
                return

            nav_timeout = 3.0 if is_silent else 15.0
            nav_toolbar = self.root.ToolBarControl(Name="导航")
            if not nav_toolbar.Exists(nav_timeout, 0.5):
                nav_toolbar = self.root.ToolBarControl(AutomationId="main_tabbar")
            if not nav_toolbar.Exists(nav_timeout, 0.5):
                raise LookupError("未找到导航栏，无法提取用户信息")

            target_x, target_y = find_avatar_click_point(self, nav_toolbar)

            _, main_pid = win32process.GetWindowThreadProcessId(self.hwnd)
            clear_old_profile_cards(self, main_pid)

            info_win_hwnd, info_win = trigger_profile_card(self, target_x, target_y, main_pid, uia_lock)

            if not info_win:
                print("[UIA] 资料窗口未弹出，请确认头像可见")
                return

            info_win = wait_profile_tree_ready(info_win_hwnd, info_win, uia_lock)

            nickname, wxid, head_view = scan_profile_fields(info_win, info_win_hwnd, uia_lock)
            if nickname:
                self._nickname = nickname
            if wxid:
                self._wxid = wxid

            download_avatar_flow(self, info_win, info_win_hwnd, head_view, skip_avatar_if_exists, nickname, wxid, uia_lock)

            print(f"[UIA] 用户信息提取完成: 昵称={self._nickname!r}, 微信号={self._wxid!r}")

        except UIAInterruptError as e_int:
            print(f"[UIA] 提取用户信息被用户手动中断: {e_int}")
            raise e_int
        except Exception as e:
            print(f"[UIA] 提取用户信息失败: {e}")
        finally:
            try:
                SPI_SETSCREENREADER = 0x0047
                ctypes.windll.user32.SystemParametersInfoW(SPI_SETSCREENREADER, False, None, 2)
            except Exception:
                pass

            close_profile_card(self, info_win_hwnd, target_x, target_y, uia_lock)

            try:
                uia_lock.update_status("正在安全释放当前活跃聊天窗口...")
                self.CloseActiveChat(check_last_msg=True)
            except Exception as close_ex:
                print(f"[UIA] 登录获取微信信息后关闭聊天窗口异常: {close_ex}")

            if self.hwnd and win32gui.IsWindow(self.hwnd):
                try:
                    from src.uia.retry import force_foreground
                    force_foreground(self.hwnd)
                    win32gui.InvalidateRect(self.hwnd, None, True)
                    win32gui.UpdateWindow(self.hwnd)
                except Exception:
                    pass
