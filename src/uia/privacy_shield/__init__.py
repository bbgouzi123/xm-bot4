"""
隐私保护遮罩 — 在微信窗口上覆盖毛玻璃遮罩，防止安装人员看到客户聊天记录

【核心原理】
UIA 自动化操作走的是 Windows Accessibility API，完全不依赖视觉层。
在微信窗口上方放一层毛玻璃遮罩，不影响任何自动化操作，但人眼完全看不到内容。
"""
import os
import logging
import contextlib
import threading
from typing import Dict
from .base import PrivacyShieldBase
from .config import ConfigMixin
from .control import ControlMixin
from .window import WindowMixin
from .rendering import RenderingMixin
from .tracking import TrackingMixin
from .bypass import BypassMixin

logger = logging.getLogger(__name__)


class SingleShield(
    ConfigMixin,
    ControlMixin,
    WindowMixin,
    RenderingMixin,
    TrackingMixin,
    BypassMixin,
    PrivacyShieldBase
):
    """
    单微信窗口的隐私保护遮罩实现 (不采用单例)
    """
    pass


class PrivacyShield:
    """
    全局隐私保护遮罩多实例总管理器 (单例模式)
    
    用于在微信多开模式下，自动为所有在线微信窗口提供独立的隐私遮罩覆盖，
    并提供一致性的批量穿透、同步、状态更新与控制能力。
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self._shields: Dict[int, SingleShield] = {}  # wechat_hwnd -> SingleShield
        self._enabled = False
        self._config_path = ""
        self._record_mode = False
        
        # 自动加载配置状态
        from pathlib import Path
        self._config_path = str(Path.home() / ".xm-ai-bot" / "privacy_shield.json")
        self._enabled = self._load_config_enabled()
        self._record_mode = self._load_config_record_mode()

    def _load_config_enabled(self) -> bool:
        import json
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("privacy_shield_enabled", True)
        except Exception:
            pass
        return True

    def _save_config(self, enabled: bool):
        import json
        try:
            data = {}
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["privacy_shield_enabled"] = enabled
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_config_record_mode(self) -> bool:
        import json
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("record_mode", False)
        except Exception:
            pass
        return False

    def _save_record_mode(self, enabled: bool):
        import json
        try:
            data = {}
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["record_mode"] = enabled
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_config_path(self, path: str):
        if path:
            self._config_path = path
        for s in self._shields.values():
            s.set_config_path(path)

    def enable(self, wechat_hwnd: int = None):
        """启用隐私保护遮罩（支持指定单个微信或启用全部在线微信）"""
        self._enabled = True
        self._save_config(True)

        hwnds = []
        if wechat_hwnd:
            hwnds.append(wechat_hwnd)
        
        # 总是把当前已上线的所有微信窗口加进来，确保多开微信无一漏网
        try:
            from app.state import account_manager
            if account_manager and hasattr(account_manager, "_instances"):
                for h in account_manager._instances.keys():
                    if h and h not in hwnds:
                        hwnds.append(h)
        except Exception:
            pass

        for hwnd in hwnds:
            if not hwnd:
                continue

            # 【关键】绝对禁止给登录界面的微信窗口（WeChatLoginWndForPC）加遮罩，防止阻挡自动/人工点击“进入微信”
            try:
                import win32gui
                cls_name = win32gui.GetClassName(hwnd)
                if "LoginWnd" in cls_name:
                    logger.info(f"[隐私遮罩] 过滤登录窗口 hwnd={hwnd} ({cls_name})，跳过遮罩启动")
                    continue
            except Exception:
                pass

            if hwnd not in self._shields:
                # 尝试获取该微信的昵称和头像
                nickname = ""
                avatar_path = ""
                try:
                    from app.state import account_manager
                    inst = account_manager._instances.get(hwnd)
                    if inst:
                        nickname = inst.nickname or ""
                        if inst.wxid:
                            from src.crm.account_data import ACCOUNTS_DIR
                            avatar_path = os.path.join(ACCOUNTS_DIR, f"{inst.wxid}.png")
                except Exception:
                    pass

                s = SingleShield()
                s.set_config_path(self._config_path)
                s._wechat_nickname = nickname
                s._wechat_avatar_path = avatar_path
                s._record_mode = self._record_mode
                s.enable(hwnd)
                self._shields[hwnd] = s
            else:
                self._shields[hwnd].enable(hwnd)

    def disable(self):
        """关闭所有微信的隐私保护遮罩"""
        self._enabled = False
        self._save_config(False)
        for s in list(self._shields.values()):
            try:
                s.disable()
            except Exception:
                pass
        self._shields.clear()
        logger.info("[隐私遮罩] 所有隐私保护已关闭")

    def destroy(self):
        """兼容 cleanup.py，销毁遮罩"""
        self.disable()

    def toggle(self, wechat_hwnd: int = None) -> bool:
        if self._enabled:
            self.disable()
        else:
            self.enable(wechat_hwnd)
        return self._enabled

    def auto_start(self, wechat_hwnd: int, config_path: str, nickname: str = "", avatar_path: str = ""):
        self.set_config_path(config_path)
        is_enabled = self._load_config_enabled()
        self._record_mode = self._load_config_record_mode()
        
        if is_enabled:
            self.enable(wechat_hwnd)
        else:
            self._enabled = False

    def update_wechat_hwnd(self, hwnd: int):
        if self._enabled and hwnd and hwnd not in self._shields:
            self.enable(hwnd)

    def update_user_info(self, nickname: str, avatar_path: str = ""):
        for s in self._shields.values():
            s.update_user_info(nickname, avatar_path)

    def set_record_mode(self, enabled: bool):
        self._record_mode = enabled
        self._save_record_mode(enabled)
        for s in self._shields.values():
            s.set_record_mode(enabled)

    def force_sync(self):
        for s in self._shields.values():
            try:
                s.force_sync()
            except Exception:
                pass

    def get_status(self) -> dict:
        has_shield_hwnd = any(s._shield_hwnd for s in self._shields.values())
        acrylic_ok = any(s._acrylic_ok for s in self._shields.values())
        wechat_hwnd = next(iter(self._shields.keys())) if self._shields else 0
        return {
            "enabled": self._enabled,
            "shield_hwnd": wechat_hwnd,
            "wechat_hwnd": wechat_hwnd,
            "acrylic": acrylic_ok,
            "record_mode": self._record_mode,
        }

    @contextlib.contextmanager
    def bypass_shield(self, hide: bool = False):
        """
        批量、可重入地穿透/隐藏所有在线微信的隐私遮罩。
        使用 contextlib.ExitStack 嵌套管理所有 SingleShield 的 bypass_shield 上下文
        """
        with contextlib.ExitStack() as stack:
            for s in list(self._shields.values()):
                try:
                    stack.enter_context(s.bypass_shield(hide=hide))
                except Exception:
                    pass
            yield


# ==================== 全局便捷函数 ====================

def get_privacy_shield() -> PrivacyShield:
    """获取全局总管理器单例"""
    return PrivacyShield()
