import ctypes
import ctypes.wintypes
import time
import logging
import win32gui
from .constants import (
    user32, kernel32, gdi32, WNDPROC, WNDCLASSEXW,
    WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_POPUP, WS_VISIBLE,
    HWND_NOTOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW, SWP_NOACTIVATE,
    WM_PAINT, WM_DESTROY, WM_CLOSE, WM_TIMER, LWA_ALPHA
)
from .acrylic import _apply_acrylic_blur
from .base import PrivacyShieldBase

logger = logging.getLogger(__name__)

class WindowMixin(PrivacyShieldBase):
    """窗口管理与消息循环相关逻辑"""
    
    def _run_shield_loop(self):
        """后台线程：创建遮罩窗口并持续跟踪微信窗口位置"""
        from src.uia.input_guard import uia_lock
        
        try:
            # 渲染启动锁定：防止渲染过程中用户操作导致坐标偏移或窗口失焦
            with uia_lock("正在初始化隐私保护遮罩..."):
                # ===== 注册窗口类 =====
                class_name = f"XM_PrivacyShield_{self._wechat_hwnd}"
                if not self._wndclass_registered:
                    # 保持 wndproc 回调引用，防止 GC 回收导致崩溃
                    self._wndproc_callback = WNDPROC(self._wnd_proc)

                    wc = WNDCLASSEXW()
                    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
                    wc.style = 0
                    wc.lpfnWndProc = ctypes.cast(self._wndproc_callback, ctypes.c_void_p)
                    wc.cbClsExtra = 0
                    wc.cbWndExtra = 0
                    wc.hInstance = kernel32.GetModuleHandleW(None)
                    wc.hIcon = None
                    wc.hCursor = user32.LoadCursorW(None, ctypes.cast(32512, ctypes.wintypes.LPCWSTR))  # IDC_ARROW
                    # 透明背景画刷（让毛玻璃效果穿透）
                    wc.hbrBackground = gdi32.CreateSolidBrush(0x00000000)
                    wc.lpszMenuName = None
                    wc.lpszClassName = class_name
                    wc.hIconSm = None

                    if user32.RegisterClassExW(ctypes.byref(wc)):
                        self._wndclass_registered = True
                        # 保持引用防止 GC
                        self._wc = wc
                    else:
                        err = kernel32.GetLastError()
                        if err == 1410:  # ERROR_CLASS_ALREADY_EXISTS
                            self._wndclass_registered = True
                        else:
                            logger.error(f"[隐私遮罩] 注册窗口类失败, GetLastError={err}")

                # ===== 获取微信窗口位置 =====
                try:
                    rect = win32gui.GetWindowRect(self._wechat_hwnd)
                    x, y, r, b = rect
                    w = r - x
                    h = b - y
                except Exception:
                    x, y, w, h = 0, 0, 800, 600

                # ===== 创建遮罩窗口 =====
                ex_style = WS_EX_LAYERED | WS_EX_TOOLWINDOW
                style = WS_POPUP | WS_VISIBLE

                self._shield_hwnd = user32.CreateWindowExW(
                    ex_style,
                    class_name,
                    "xm-bot4 · 隐私保护",
                    style,
                    x, y, w, h,
                    self._wechat_hwnd, None,
                    kernel32.GetModuleHandleW(None),
                    None,
                )

                if not self._shield_hwnd:
                    logger.error("[隐私遮罩] 创建窗口失败")
                    self._enabled = False
                    return

                # ===== 绝对物理防线：实心遮光板 =====
                # 【重要】绝对不能使用 DWM Acrylic 毛玻璃！
                # Windows 的 Aero Peek 机制在任务栏悬停预览时，为了省资源会强制关闭子窗口的 Acrylic 渲染。
                # 这会导致遮罩瞬间变成透明，直接泄露底层的微信聊天记录！
                # 必须禁用毛玻璃，使用纯色不透明背景兜底。
                self._acrylic_ok = False

                # WS_EX_LAYERED 全局不透明度：调节为 255，毛玻璃API会负责区域透明度
                alpha_b = 255
                user32.SetLayeredWindowAttributes(
                    self._shield_hwnd, 0, alpha_b, LWA_ALPHA
                )

                # 设置窗口层级（配合 owned window 取消全局强制的最顶层属性）
                user32.SetWindowPos(
                    self._shield_hwnd, HWND_NOTOPMOST,
                    0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE,
                )

                # 强制重绘（触发 WM_PAINT 绘制品牌文字）
                user32.InvalidateRect(self._shield_hwnd, None, True)
                user32.UpdateWindow(self._shield_hwnd)
                
                # 给予足够的渲染缓冲时间，确保遮罩已稳定覆盖
                time.sleep(0.15)

            logger.info(f"[隐私遮罩] 遮罩窗口已创建并渲染完成: hwnd={self._shield_hwnd}, 位置=({x},{y}) {w}x{h}")

            # ===== 消息循环 + 位置跟踪 =====
            # 设置定时器（每 200ms 跟踪微信窗口位置）
            TIMER_ID = 1001
            user32.SetTimer(self._shield_hwnd, TIMER_ID, 200, None)

            msg = ctypes.wintypes.MSG()
            while not self._stop_event.is_set():
                # PeekMessage 非阻塞
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                    if msg.message == WM_TIMER:
                        self._track_wechat_position()
                    elif msg.message == WM_CLOSE or msg.message == WM_DESTROY:
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.05)  # 避免 CPU 空转

            # 清理
            user32.KillTimer(self._shield_hwnd, TIMER_ID)
            if self._shield_hwnd:
                user32.DestroyWindow(self._shield_hwnd)
                self._shield_hwnd = None
            if self._wndclass_registered:
                user32.UnregisterClassW(class_name, kernel32.GetModuleHandleW(None))
                self._wndclass_registered = False

        except Exception as e:
            logger.error(f"[隐私遮罩] 运行异常: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            self._enabled = False

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        """窗口过程：处理绘制和鼠标拦截事件"""
        if msg == WM_PAINT:
            self._on_paint(hwnd)
            return 0
        elif msg == WM_DESTROY:
            return 0
        elif msg == 0x0021:  # WM_MOUSEACTIVATE
            # MA_NOACTIVATEANDEAT = 4
            # 阻止鼠标点击穿透并吃掉事件，严防误触底部的微信内容
            return 4

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
