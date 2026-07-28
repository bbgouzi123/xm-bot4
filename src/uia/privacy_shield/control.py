import os
import logging
import threading
from .constants import user32, WM_CLOSE
from .base import PrivacyShieldBase

logger = logging.getLogger(__name__)

class ControlMixin(PrivacyShieldBase):
    """启用/停用等控制逻辑"""
    
    def _fetch_fallback_avatar(self):
        """直接从本地读取机器人先前已保存的真实微信头像作为启动兜底"""
        if not self._fallback_lock.acquire(blocking=False):
            return  # 另一个线程正在执行，跳过
        try:
            # 如果主头像已经有了，无需兜底
            if self._wechat_avatar_path and os.path.exists(self._wechat_avatar_path):
                return
            # 幂等：已经加载过兜底头像就不再重复
            if self._fallback_avatar_path and os.path.exists(self._fallback_avatar_path):
                return

            from src.crm.account_data import ACCOUNTS_DIR, _load_account_meta
            
            if not os.path.exists(ACCOUNTS_DIR):
                logger.info(f"[隐私遮罩] 兜底头像：账号目录不存在 {ACCOUNTS_DIR}")
                return
                
            best_avatar = ""
            best_nickname = ""
            best_mtime = 0
            
            # 遍历所有账号数据，找出最新保存的一个头像
            for name in os.listdir(ACCOUNTS_DIR):
                p = os.path.join(ACCOUNTS_DIR, name)
                if os.path.isfile(p) and name.endswith(".png") and name != "common.png" and name != "default.png":
                    mtime = os.path.getmtime(p)
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best_avatar = p
                        wxid = name[:-4]
                        meta = _load_account_meta(wxid)
                        best_nickname = meta.get("nickname", wxid)
            
            if best_avatar:
                self._fallback_avatar_path = best_avatar
                self._fallback_nickname = best_nickname
                logger.info(f"[隐私遮罩] V 兜底头像已加载: {best_avatar} ({best_nickname})")
                
                # 刷新显示
                if self._shield_hwnd and not self._wechat_avatar_path:
                    user32.InvalidateRect(self._shield_hwnd, None, True)
            else:
                logger.debug(f"[隐私遮罩] 兜底头像：未找到任何已保存的 avatar.png")
                    
        except Exception as e:
            logger.warning(f"[隐私遮罩] 获取本地兜底头像失败: {e}")
        finally:
            self._fallback_lock.release()

    def toggle(self, wechat_hwnd: int = None) -> bool:
        """切换隐私保护状态，返回切换后的状态"""
        if self._enabled:
            self.disable()
        else:
            self.enable(wechat_hwnd)
        return self._enabled

    def enable(self, wechat_hwnd: int = None):
        """启用隐私保护遮罩"""
        if self._enabled:
            return

        if wechat_hwnd:
            self._wechat_hwnd = wechat_hwnd

        if not self._wechat_hwnd:
            logger.warning("[隐私遮罩] 未指定微信窗口句柄，无法启用")
            return

        self._enabled = True
        self._save_config(True)
        self._stop_event.clear()

        # 在后台线程中创建和管理遮罩窗口
        self._tracking_thread = threading.Thread(
            target=self._run_shield_loop,
            daemon=True,
            name="privacy-shield"
        )
        self._tracking_thread.start()
        
        logger.info("[隐私遮罩] 隐私保护已开启")

    def disable(self):
        """关闭隐私保护遮罩"""
        if not self._enabled:
            return

        self._enabled = False
        self._save_config(False)
        self._stop_event.set()

        # 销毁遮罩窗口
        if self._shield_hwnd:
            try:
                user32.PostMessageW(self._shield_hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
            self._shield_hwnd = None

        logger.info("[隐私遮罩] 隐私保护已关闭")

    def auto_start(self, wechat_hwnd: int, config_path: str,
                   nickname: str = "", avatar_path: str = ""):
        """程序启动时恢复隐私遮罩状态"""
        self.set_config_path(config_path)
        self._wechat_hwnd = wechat_hwnd
        self._wechat_nickname = nickname
        self._wechat_avatar_path = avatar_path
        
        # 异步获取兜底头像
        threading.Thread(target=self._fetch_fallback_avatar, daemon=True).start()
        
        # 遵循用户最后一次的开关设置
        is_enabled = self._load_config_enabled()
        self._record_mode = self._load_config_record_mode()
        if is_enabled:
            logger.info("[隐私遮罩] 恢复用户的开启状态")
            self.enable(wechat_hwnd)
        else:
            logger.info("[隐私遮罩] 恢复用户的关闭状态")
            # 确保状态为停用
            self._enabled = False

    def update_wechat_hwnd(self, hwnd: int):
        """更新微信窗口句柄（多开切换/从托盘恢复新窗口时用）"""
        self._wechat_hwnd = hwnd
        if self._shield_hwnd:
            GWLP_HWNDPARENT = -8
            # 动态更新所有者 (Owner)，防止老的幽灵窗口被隐藏后遮罩连带被隐藏！
            try:
                user32.SetWindowLongPtrW(self._shield_hwnd, GWLP_HWNDPARENT, hwnd)
            except AttributeError:
                user32.SetWindowLongW(self._shield_hwnd, GWLP_HWNDPARENT, hwnd)

    def update_user_info(self, nickname: str, avatar_path: str = ""):
        """更新微信用户信息，并刷新遮罩显示"""
        # 只接受真实存在的头像文件
        if avatar_path and os.path.exists(avatar_path):
            self._wechat_avatar_path = avatar_path
        self._wechat_nickname = nickname
        # 重置调试标志，让下次绘制重新打印日志
        if hasattr(self, '_avatar_dbg'):
            del self._avatar_dbg
        logger.info(f"[隐私遮罩] 用户信息已更新: nickname={nickname!r}, avatar={self._wechat_avatar_path!r}")
        # 刷新遮罩显示
        if self._shield_hwnd:
            user32.InvalidateRect(self._shield_hwnd, None, True)

    def set_record_mode(self, enabled: bool):
        """设置录屏保护模式：True 时遮罩上隐藏真实昵称和头像（防录屏泄露身份）"""
        if self._record_mode == enabled:
            return
        self._record_mode = enabled
        self._save_record_mode(enabled)
        logger.info(f"[隐私遮罩] 录屏保护模式: {'开启' if enabled else '关闭'}")
        # 刷新遮罩显示
        if self._shield_hwnd:
            user32.InvalidateRect(self._shield_hwnd, None, True)

    def get_status(self) -> dict:
        """获取当前状态"""
        return {
            "enabled": self._enabled,
            "shield_hwnd": self._shield_hwnd,
            "wechat_hwnd": self._wechat_hwnd,
            "acrylic": self._acrylic_ok,
            "record_mode": self._record_mode,
        }

    def force_sync(self):
        """强制在主线程同步遮罩位置与层级（解决 GIL 限制下后台线程位置同步延迟）"""
        if not self._enabled or not self._shield_hwnd or not self._wechat_hwnd:
            return
        
        # 如果当前正处于穿透/隐藏上下文，则不强制同步
        if self._bypass_depth > 0:
            return

        try:
            import win32gui
            if win32gui.IsWindow(self._wechat_hwnd) and win32gui.IsWindowVisible(self._wechat_hwnd):
                rect = win32gui.GetWindowRect(self._wechat_hwnd)
                x, y, r, b = rect
                w = r - x
                h = b - y
                
                # 强行显示并置顶
                from .constants import HWND_TOPMOST, SWP_SHOWWINDOW, SWP_NOACTIVATE
                user32.ShowWindow(self._shield_hwnd, 8)  # SW_SHOWNA
                user32.SetWindowPos(
                    self._shield_hwnd, HWND_TOPMOST,
                    x, y, w, h,
                    SWP_SHOWWINDOW | SWP_NOACTIVATE
                )
        except Exception:
            pass
