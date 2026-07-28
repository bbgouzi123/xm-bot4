import contextlib
import time
import win32gui
from .base import PrivacyShieldBase


class BypassMixin(PrivacyShieldBase):
    """穿透/锁定规避相关逻辑"""

    def _wait_shield_hwnd(self, timeout: float = 2.0) -> bool:
        """等待遮罩窗口创建完成（解决与 auto_start 后台线程的时序竞态）。

        Returns:
            True=窗口已就绪, False=超时
        """
        if self._shield_hwnd:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._shield_hwnd:
                return True
            time.sleep(0.1)
        return False

    @contextlib.contextmanager
    def bypass_shield(self, hide: bool = False):
        """可重入的遮罩穿透/隐藏上下文管理器。

        1. 默认 (hide=False)：通过添加 WS_EX_TRANSPARENT 扩展样式让遮罩对鼠标点击完全透明，
           点击事件穿透遮罩到达后面的微信窗口。遮罩本身保持可见。
        2. 特殊场景 (hide=True)：将遮罩窗口隐藏（SW_HIDE），以便弹出并操作系统级对话框（如“打开”文件对话框），
           退出时恢复显示（SW_SHOWNA）。

        结合 _bypass_depth 暂停跟踪线程 of SetWindowPos 调用，
        避免 Z-Order 竞争干扰 EnumWindows 检测资料卡弹窗。

        支持嵌套调用：只有最外层执行样式切换或隐藏。
        """
        from .constants import user32 as _user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020

        is_outermost = (self._bypass_depth == 0)
        self._bypass_depth += 1
        did_modify = False
        did_hide = False

        if is_outermost:
            # 只有在启用了隐私遮罩但窗口尚未就绪时才需要等待
            if self._enabled and not self._shield_hwnd:
                self._wait_shield_hwnd(timeout=2.0)

            hwnd = self._shield_hwnd
            if hwnd and win32gui.IsWindow(hwnd):
                try:
                    if hide:
                        # 隐藏遮罩，方便交互被遮挡的系统弹窗
                        _user32.ShowWindow(hwnd, 0)  # SW_HIDE
                        did_hide = True
                        print(f"[隐私遮罩] bypass: 已隐藏遮罩窗口 (depth={self._bypass_depth})")
                    else:
                        old_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                        _user32.SetWindowLongW(
                            hwnd, GWL_EXSTYLE,
                            old_style | WS_EX_TRANSPARENT
                        )
                        # 【核心修复】必须调用 SetWindowPos 并传入 SWP_FRAMECHANGED 以便让样式更改立即生效（刷新 Win32 命中测试缓存）
                        _user32.SetWindowPos(
                            hwnd, 0, 0, 0, 0, 0,
                            0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020  # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
                        )
                        did_modify = True
                        print(f"[隐私遮罩] bypass: 已添加穿透样式 (depth={self._bypass_depth})")
                except Exception as e:
                    print(f"[隐私遮罩] bypass 失败: {e}")
            elif self._enabled:
                print(f"[隐私遮罩] bypass: 无遮罩窗口需穿透 "
                      f"(hwnd={self._shield_hwnd}, enabled={self._enabled})")

        try:
            yield
        finally:
            self._bypass_depth -= 1
            if self._bypass_depth == 0:
                hwnd = self._shield_hwnd
                if hwnd and win32gui.IsWindow(hwnd):
                    try:
                        if did_hide:
                            _user32.ShowWindow(hwnd, 8)  # SW_SHOWNA (不激活)
                            print("[隐私遮罩] bypass: 已重新显示遮罩窗口")
                        elif did_modify:
                            # 【核心修复】在恢复遮罩拦截前，必须给予微小的延迟（如 150ms），
                            # 确保 Windows 输入队列中的物理鼠标事件（包括 Click 的 MOUSEUP）已彻底分发并由目标窗口接收
                            time.sleep(0.15)
                            cur = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                            _user32.SetWindowLongW(
                                hwnd, GWL_EXSTYLE,
                                cur & ~WS_EX_TRANSPARENT
                            )
                            # 【核心修复】恢复样式时同样刷新
                            _user32.SetWindowPos(
                                hwnd, 0, 0, 0, 0, 0,
                                0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020  # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
                            )
                            print("[隐私遮罩] bypass: 已恢复遮罩拦截")
                    except Exception as e:
                        print(f"[隐私遮罩] bypass 恢复失败: {e}")
