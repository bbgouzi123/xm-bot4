"""连接状态、分辨率、置前与当前用户摘要。"""
import logging
import os

import ctypes
import win32gui

logger = logging.getLogger("WeChatDriver")


class WeChatCoreStateMixin:
    def is_connected(self) -> bool:
        """检查是否已连接"""
        # 🌟 核心自愈：对于全局单例的 app.state.driver，若它自身未连接，则动态从 MultiAccountManager 获取主账号驱动的状态与属性
        # 这完美解决了多开/自动重连场景下，后台线程仅更新了多开实例但全局单例 driver 状态与属性未同步导致的 API 报错或 UIA 执行异常
        if not self._connected or not self.hwnd or not win32gui.IsWindow(self.hwnd):
            try:
                from app.state import account_manager
                if account_manager:
                    primary_drv = account_manager.primary_driver
                    # 避免对自身无限递归
                    if primary_drv and primary_drv is not self:
                        if primary_drv.is_connected():
                            # 同步其所有内部属性（如 hwnd, root, _wxid, _nickname 等），使全局单例驱动实例满血复活
                            self.__dict__.update(primary_drv.__dict__)
                            return True
            except Exception:
                pass
            return False

        try:
            return win32gui.IsWindow(self.hwnd)
        except Exception:
            self._connected = False
            return False

    def _detect_resolution(self):
        """根据屏幕高度自动选择分辨率参数"""
        try:
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            if screen_h >= 2000:
                self.resolution = "4k"
            elif screen_h >= 1300:
                self.resolution = "2k"
            else:
                self.resolution = "1080p"
        except Exception:
            self.resolution = "1080p"

    def SwitchToThisWindow(self):
        """确保微信窗口可见并置顶（统一使用全局公共函数）"""
        if not self.hwnd:
            return
        try:
            from src.uia.retry import ensure_wechat_foreground
            ensure_wechat_foreground(self.hwnd)
        except Exception as e:
            logger.debug(f"[UIA] SwitchToThisWindow 失败: {e}")

    # ==================== 账号信息 ====================

    def get_current_user(self) -> dict:
        """获取当前登录用户信息"""
        if self._nickname:
            user = {
                "nickname": self._nickname,
                "wxid": self._wxid,
                "hwnd": self.hwnd,
            }
            if self._wxid:
                from src.crm.account_data import ACCOUNTS_DIR, make_avatar_url
                avatar_path = os.path.join(ACCOUNTS_DIR, f"{self._wxid}.png")
                if os.path.exists(avatar_path):
                    user["avatar"] = make_avatar_url(self._wxid)
            return user

        if not self.is_connected():
            return {"nickname": "未连接"}

        # 不再隐式触发 _extract_user_info()，避免 API 请求意外触发
        # UIA 物理操作（点击微信头像影响用户体验）。
        # 信息提取改为由前端登录后主动调用 /api/user/extract-info 触发。
        return {
            "nickname": self._nickname or "当前用户",
            "wxid": self._wxid,
            "hwnd": self.hwnd,
        }
