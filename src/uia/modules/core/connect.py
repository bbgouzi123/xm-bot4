"""微信窗口发现、多开绑定与 UIA 连接。"""
import logging
import threading
from typing import Dict, List, Optional

try:
    import uiautomation as uia
    import win32gui
except ImportError:
    uia = None
    win32gui = None

from src.uia.elements import WxClass
from .connect_helper import (
    find_all_wechat_windows,
    find_wechat_window,
    try_restore_from_cache,
    init_privacy_shield_with_local_avatar,
)

logger = logging.getLogger("WeChatDriver")


def _is_wechat_title(title: str) -> bool:
    if not title:
        return False
    t = title.strip()
    if len(t) >= 3:
        if t.startswith("[#]") or t.startswith("[#]"):
            t = t[3:].strip()
    if len(t) >= 3:
        if t.endswith("[#]") or t.endswith("[#]"):
            t = t[:-3].strip()
    if t == "微信":
        return True
    if t.endswith("微信") and t.startswith("[") and "] " in t:
        suffix = t.split("] ", 1)[1]
        if suffix == "微信":
            return True
    if t.endswith("微信") and t.startswith("[") and "]" in t:
        parts = t.split("]", 1)
        if len(parts) == 2 and parts[1].strip() == "微信":
            return True
    return False


class WeChatCoreConnectMixin:
    # ==================== 多开支持 ====================

    @staticmethod
    def find_all_wechat_windows() -> List[Dict]:
        """发现当前系统中所有可用的微信主窗口候选（多开支持）"""
        return find_all_wechat_windows()

    def connect_by_hwnd(self, target_hwnd: int, extract_info: bool = False, init_shield: bool = True, escalate: bool = True) -> bool:
        """绑定到指定的微信窗口（多开场景用）"""
        # 清除历史残留账号标识
        self._wxid = ""
        self._nickname = ""
        if hasattr(self, "bot_wxid"):
            self.bot_wxid = ""

        try:
            if not win32gui.IsWindow(target_hwnd):
                logger.debug(f"[UIA-多开] hwnd={target_hwnd} 不是有效窗口")
                return False

            self.hwnd = target_hwnd
            logger.debug(f"[UIA-多开] 微信窗口 hwnd={target_hwnd}")

            # 在独立线程中完成 UIA 绑定
            bind_result = [False, None]

            def _uia_bind_thread():
                try:
                    import comtypes
                    comtypes.CoInitialize()
                    is_visible = win32gui.IsWindowVisible(target_hwnd)
                    root = None
                    try:
                        # 先尝试静默获取
                        root = uia.ControlFromHandle(target_hwnd)
                        if root and not root.Exists(0.2, 0):
                            root = None
                    except Exception:
                        root = None

                    if is_visible and not root:
                        from src.uia.startup_flow import force_accessibility_refresh
                        force_accessibility_refresh(target_hwnd, escalate=escalate)
                        root = uia.ControlFromHandle(target_hwnd)

                    if root:
                        bind_result[0] = True
                    else:
                        bind_result[1] = "无法获取 UIA 根节点"
                except Exception as e:
                    bind_result[1] = str(e)

            t = threading.Thread(target=_uia_bind_thread, daemon=True)
            t.start()
            t.join(timeout=15 if escalate else 3)

            if t.is_alive():
                logger.debug(f"[UIA-多开] UIA 绑定超时 ({'15s' if escalate else '3s'})")
                return False

            if bind_result[1]:
                logger.debug(f"[UIA-多开] UIA 绑定失败: {bind_result[1]}")
                return False

            if not bind_result[0]:
                return False

            self._detect_resolution()
            if extract_info:
                if not self._try_restore_from_cache():
                    self._extract_user_info(skip_avatar_if_exists=True)
            else:
                if not self._try_restore_from_cache():
                    try:
                        from src.wechat_4x.db_profile_extractor import extract_profile_from_db
                        res = extract_profile_from_db(self.hwnd)
                        if res:
                            db_wxid, db_nickname = res
                            if db_wxid and db_nickname:
                                self._wxid = db_wxid
                                self._nickname = db_nickname
                                logger.info(f"[无感推断] 成功从数据库静默加载用户信息: {db_wxid} ({db_nickname})")
                    except Exception as e_db:
                        logger.debug(f"[无感推断] 尝试静默提取失败: {e_db}")
            
            if self._wxid:
                try:
                    from src.utils.wechat_key_store import get_persisted_wechat_key, persist_wechat_key
                    active_key = get_persisted_wechat_key(self._wxid)
                    if active_key:
                        persist_wechat_key(active_key, self._wxid)
                except Exception as bind_err:
                    logger.debug(f"[KeyStore] 绑定 wxid {self._wxid} 与密钥异常: {bind_err}")

            self._connected = True
            logger.debug(f"[UIA-多开] 已连接: {self._nickname or '未知'} (hwnd={target_hwnd})")

            # 自动启动隐私屏幕并加载本地已有头像
            if init_shield and win32gui.IsWindowVisible(target_hwnd):
                self._init_privacy_shield_with_local_avatar(target_hwnd)

            return True

        except Exception as e:
            logger.debug(f"[UIA-多开] 连接失败: {e}")
            self._connected = False
            return False

    def _init_privacy_shield_with_local_avatar(self, hwnd: int):
        init_privacy_shield_with_local_avatar(self, hwnd)

    def _try_restore_from_cache(self) -> bool:
        return try_restore_from_cache(self)

    # ==================== 连接管理 ====================

    def connect(self, extract_info: bool = False) -> bool:
        """连接微信窗口"""
        self._wxid = ""
        self._nickname = ""
        if hasattr(self, "bot_wxid"):
            self.bot_wxid = ""

        try:
            hwnd = self._find_wechat_window()
            if not hwnd:
                hwnd = win32gui.FindWindow(WxClass.WIN32_CLASS, "微信")
            if not hwnd:
                hwnd = win32gui.FindWindow(None, "微信")

            if not hwnd:
                logger.debug("未找到微信窗口")
                self._connected = False
                return False

            self.hwnd = hwnd
            logger.debug(f"[UIA] 微信窗口 hwnd={hwnd} class={win32gui.GetClassName(hwnd)!r}")

            # 在独立线程中完成 UIA 绑定
            bind_result = [False, None]

            def _uia_bind_thread():
                try:
                    import comtypes
                    comtypes.CoInitialize()
                    root = uia.ControlFromHandle(hwnd)
                    if root:
                        try:
                            exists = root.Exists(3, 1)
                        except Exception:
                            exists = True
                        if exists or root.Name:
                            bind_result[0] = True
                        else:
                            bind_result[1] = "UIA Exists 返回 False"
                    else:
                        bind_result[1] = "ControlFromHandle 返回 None"
                except Exception as e:
                    bind_result[1] = str(e)

            t = threading.Thread(target=_uia_bind_thread, daemon=True)
            t.start()
            t.join(timeout=15)

            if t.is_alive():
                logger.debug("[UIA] UIA 绑定超时 (15s)")
                self._connected = False
                return False

            if bind_result[1]:
                logger.debug(f"[UIA] UIA 绑定失败: {bind_result[1]}")
                self._connected = False
                return False

            if not bind_result[0]:
                logger.debug("[UIA] UIA 控件为空")
                self._connected = False
                return False

            self._detect_resolution()

            if extract_info:
                if not self._try_restore_from_cache():
                    self._extract_user_info(skip_avatar_if_exists=True)
            else:
                if not self._try_restore_from_cache():
                    try:
                        from src.wechat_4x.db_profile_extractor import extract_profile_from_db
                        res = extract_profile_from_db(self.hwnd)
                        if res:
                            db_wxid, db_nickname = res
                            if db_wxid and db_nickname:
                                self._wxid = db_wxid
                                self._nickname = db_nickname
                                logger.info(f"[无感推断] 成功从数据库静默加载用户信息: {db_wxid} ({db_nickname})")
                    except Exception as e_db:
                        logger.debug(f"[无感推断] 尝试静默提取失败: {e_db}")

            if self._wxid:
                try:
                    from src.utils.wechat_key_store import get_persisted_wechat_key, persist_wechat_key
                    active_key = get_persisted_wechat_key(self._wxid)
                    if active_key:
                        persist_wechat_key(active_key, self._wxid)
                except Exception as bind_err:
                    logger.debug(f"[KeyStore] 绑定 wxid {self._wxid} 与密钥异常: {bind_err}")

            self._connected = True
            logger.debug(f"[UIA] 微信已连接: {self._nickname or '未知用户'} (hwnd={hwnd})")

            self._init_privacy_shield_with_local_avatar(hwnd)

            return True

        except Exception as e:
            logger.debug(f"[UIA] 连接微信失败: {e}")
            import traceback
            traceback.print_exc()
            self._connected = False
            return False

    def _find_wechat_window(self) -> Optional[int]:
        return find_wechat_window()
