import win32gui
from .constants import user32, HWND_TOPMOST, SWP_NOACTIVATE, SWP_SHOWWINDOW
from .base import PrivacyShieldBase

class TrackingMixin(PrivacyShieldBase):
    """窗口位置跟踪逻辑"""
    
    def _track_wechat_position(self):
        """跟踪微信窗口位置，同步遮罩窗口"""
        shield_hwnd = self._shield_hwnd
        wechat_hwnd = self._wechat_hwnd
        if not shield_hwnd or not wechat_hwnd:
            return

        # bypass_shield 期间暂停跟踪，避免与 UIA 操作竞争 Z-Order
        if self._bypass_depth > 0:
            return

        try:
            # 双重检验生存性：检查微信和遮罩窗口是否都还存在且有效
            if not win32gui.IsWindow(wechat_hwnd) or not win32gui.IsWindow(shield_hwnd):
                return

            # 微信不可见时隐藏遮罩
            if not win32gui.IsWindowVisible(wechat_hwnd):
                if win32gui.IsWindow(shield_hwnd):
                    user32.ShowWindow(shield_hwnd, 0)  # SW_HIDE
                return
            else:
                # 再次检查 bypass（防止竞态）
                if self._bypass_depth > 0:
                    return
                if win32gui.IsWindow(shield_hwnd):
                    user32.ShowWindow(shield_hwnd, 8)  # SW_SHOWNA (不激活)

            # 同步位置和大小
            if win32gui.IsWindow(wechat_hwnd):
                rect = win32gui.GetWindowRect(wechat_hwnd)
                x, y, r, b = rect
                w = r - x
                h = b - y
            else:
                return

            # bypass 期间不执行置顶（防止覆盖资料卡弹窗）
            if self._bypass_depth > 0:
                return

            # 同步微信最小化状态
            is_iconic = False
            if win32gui.IsWindow(wechat_hwnd):
                is_iconic = user32.IsIconic(wechat_hwnd)

            if is_iconic:
                if win32gui.IsWindow(shield_hwnd):
                    user32.ShowWindow(shield_hwnd, 0)  # SW_HIDE
                return

            # 只在窗口有效且没有 bypass 时更新位置
            if win32gui.IsWindow(shield_hwnd):
                user32.SetWindowPos(
                    shield_hwnd, HWND_TOPMOST,
                    x, y, w, h,
                    SWP_SHOWWINDOW | SWP_NOACTIVATE,
                )
        except Exception:
            pass
