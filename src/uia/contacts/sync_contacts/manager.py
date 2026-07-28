import time
import uiautomation as uia
from ...elements import WxClass
from ...retry import (
    exists_with_timeout,
    random_delay,
    smooth_click_at,
)
from src.utils.stop_signal import stop_signal

class ContactManagerOpsMixin:
    """通讯录管理窗口操作"""

    def _open_contacts_manager(self):
        """
        打开微信「通讯录管理」窗口。
        Returns: (mgr_win, detail_list, error_msg)
        """
        if stop_signal.is_stopped:
            return None, None, "用户按下 ESC 键中断了操作"

        import win32gui as _w32

        # 1. 检查窗口是否已经打开 (使用 quick=True 快速检测，避免在未打开时卡等 2 秒)
        mgr_win = self._find_contacts_manager_window(quick=True)
        if mgr_win:
            detail_list = self._find_detail_list_in_manager(mgr_win)
            if detail_list:
                self._resize_manager_to_screen_height(mgr_win)
                return mgr_win, detail_list, None

        # 2. 打开通讯录页
        if not self._open_contacts_page():
            return None, None, "未找到通讯录按钮"

        # 3. 找通讯录列表
        contacts_list = self._find_contacts_list()
        if not contacts_list:
            return None, None, "未找到通讯录列表"

        # 4. 回到顶部 (优化等待时值)
        try:
            contacts_list.SetFocus()
            uia.SendKeys("{HOME}")
            time.sleep(0.2)
        except Exception:
            pass

        # 5. 找 ContactsCellMangerBtnView（通讯录管理按钮）
        items = contacts_list.GetChildren()
        mgr_btn = None
        for item in items[:8]:
            cls = item.ClassName or ""
            if "MangerBtn" in cls or "ManagerBtn" in cls:
                mgr_btn = item
                break
            # 备用：按子控件文字匹配
            try:
                name = (item.Name or "").strip()
                if name == "通讯录管理":
                    mgr_btn = item
                    break
                for ch in item.GetChildren():
                    ch_name = (ch.Name or "").strip()
                    if ch_name == "通讯录管理":
                        mgr_btn = item
                        break
                if mgr_btn:
                    break
            except Exception:
                pass

        if not mgr_btn:
            return None, None, "未找到通讯录管理按钮"

        # 6. 点击打开 (取消原 2-3 秒的大等待，转交高频轮询检查，大幅提升性能)
        print("[联系人同步] 正在打开通讯录管理窗口...")
        smooth_click_at(mgr_btn)
        random_delay(0.2, 0.3)

        # 7. 等待窗口出现 (使用 quick=True 以及 0.15s 高频轮询，窗口一弹出来立即捕捉进入下一步，杜绝强制等待)
        mgr_win = None
        for _ in range(30):
            if stop_signal.is_stopped:
                break
            mgr_win = self._find_contacts_manager_window(quick=True)
            if mgr_win:
                break
            time.sleep(0.15)

        if not mgr_win:
            return None, None, "通讯录管理窗口未出现"

        # 8. 找详情列表
        detail_list = self._find_detail_list_in_manager(mgr_win)
        if not detail_list:
            return None, None, "通讯录管理窗口中未找到联系人列表"

        # 8.5 调整窗口高度拉满屏幕
        self._resize_manager_to_screen_height(mgr_win)

        # 9. 确保列表回到顶部 (优化等待)
        try:
            detail_list.SetFocus()
            uia.SendKeys("{HOME}")
            time.sleep(0.2)
        except Exception:
            pass

        print("[联系人同步] [V2] 通讯录管理窗口就绪")
        return mgr_win, detail_list, None

    def _resize_manager_to_screen_height(self, mgr_win):
        """将通讯录管理窗口高度调整至与当前屏幕工作区高度一致"""
        try:
            hwnd = mgr_win.NativeWindowHandle
            if hwnd:
                import win32api
                import win32con
                import win32gui as _w32
                
                # 获取窗口所在显示器的工作区
                monitor_info = win32api.GetMonitorInfo(
                    win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
                )
                work_area = monitor_info['Work']
                screen_top = work_area[1]
                screen_height = work_area[3] - work_area[1]
                
                rect = _w32.GetWindowRect(hwnd)
                curr_x = rect[0]
                curr_w = rect[2] - rect[0]
                
                print(f"[联系人同步] 调整通讯录管理窗口高度拉满至: {screen_height}px")
                _w32.SetWindowPos(
                    hwnd,
                    0,
                    curr_x,
                    screen_top,
                    curr_w,
                    screen_height,
                    win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW
                )
                time.sleep(0.3)
        except Exception as e:
            print(f"[联系人同步] 调整通讯录管理窗口高度失败: {e}")

    def _find_contacts_manager_window(self, quick=False):
        """查找已打开的通讯录管理窗口"""
        try:
            mgr_win = uia.WindowControl(ClassName=WxClass.CONTACTS_MANAGER)
            timeout = 0.1 if quick else 2.0
            if mgr_win and mgr_win.Exists(timeout, 0.05):
                return mgr_win
        except Exception:
            pass
        return None

    def _find_detail_list_in_manager(self, mgr_win):
        """在通讯录管理窗口中找到联系人详情列表"""
        try:
            for ctrl, _ in uia.WalkControl(mgr_win, maxDepth=10):
                cls = ctrl.ClassName or ""
                if "ContactsManagerDetailView" in cls:
                    return ctrl
        except Exception:
            pass
        return None

    def _close_contacts_manager(self, mgr_win):
        """关闭通讯录管理窗口"""
        try:
            close_btn = mgr_win.ButtonControl(Name="关闭")
            if close_btn and exists_with_timeout(close_btn, 2):
                smooth_click_at(close_btn)
                random_delay(0.5, 0.8)
                print("[联系人同步] 通讯录管理窗口已关闭")
                return
        except Exception:
            pass
        # 备用：直接发送 Alt+F4
        try:
            mgr_win.SetFocus()
            mgr_win.SendKeys("{Alt}{F4}")
            if not stop_signal.is_stopped:
                random_delay(0.5, 0.8)
            else:
                time.sleep(0.1)
        except Exception:
            pass
