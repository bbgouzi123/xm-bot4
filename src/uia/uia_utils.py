import time
import logging
import threading
import win32gui
import uiautomation as uia

logger = logging.getLogger(__name__)

class UiaUtils:
    @staticmethod
    def _find_add_friend_hwnd():
        """用 win32gui 快速查找\"添加朋友\"窗口句柄"""
        results = []
        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                try:
                    title = win32gui.GetWindowText(hwnd)
                    if title == "添加朋友":
                        results.append(hwnd)
                except Exception:
                    pass
        win32gui.EnumWindows(cb, None)
        return results[0] if results else None

    @staticmethod
    def _click_by_rect(ctrl):
        """通过控件坐标进行真实鼠标点击"""
        import ctypes
        import win32api
        try:
            rect = ctrl.BoundingRectangle
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            win32api.SetCursorPos((cx, cy))
            time.sleep(0.1)
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            time.sleep(0.2)
        except Exception as e:
            logger.error(f"[UIA] 坐标点击失败: {e}")

    @staticmethod
    def _win32_window_exists(title: str) -> bool:
        """使用 Win32 API 检查窗口是否存在"""
        try:
            hwnd = win32gui.FindWindow(None, title)
            return bool(hwnd) and win32gui.IsWindowVisible(hwnd)
        except Exception:
            return False

    def _any_window_exists(self, titles: list) -> bool:
        """检查多个标题的窗口是否有任一存在"""
        for title in titles:
            if self._win32_window_exists(title):
                return True
        return False

    @staticmethod
    def _exists_threaded(control, timeout: float = 10.0) -> bool:
        """在子线程中调用 Exists，防止 UIA 卡死主线程"""
        result = {"value": False}
        err = {"e": None}

        def worker():
            try:
                result["value"] = control.Exists(timeout)
            except Exception as e:
                err["e"] = e

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout + 0.5)

        if t.is_alive():
            try:
                n = getattr(control, "Name", "")
                logger.error(f"UIA Exists timeout: {n}, t={timeout}")
            except Exception:
                logger.error(f"UIA Exists timeout: unknown, t={timeout}")
            return False

        if err["e"] is not None:
            logger.error(f"UIA Exists error: {err['e']}")
            return False

        return bool(result["value"])

    @staticmethod
    def _refresh_qt_tree(root, repeats: int = 3):
        """刷新 Qt 控件树"""
        for _ in range(max(1, repeats)):
            try:
                _ = root.GetChildren()
            except Exception:
                pass
            time.sleep(0.15)

    @staticmethod
    def _nudge_window(root):
        """微调窗口大小触发 Qt 重绘"""
        try:
            hwnd = getattr(root, "NativeWindowHandle", None)
            if not hwnd:
                return
            rect = win32gui.GetWindowRect(hwnd)
            x, y = rect[0], rect[1]
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            win32gui.MoveWindow(hwnd, x, y, w + 1, h + 1, True)
            time.sleep(0.08)
            win32gui.MoveWindow(hwnd, x, y, w, h, True)
            time.sleep(0.08)
        except Exception:
            pass

    @staticmethod
    def _hover_control(ctrl):
        """鼠标悬停控件触发 Qt 重绘"""
        try:
            import win32api as _winapi
            r = ctrl.BoundingRectangle
            cx = int((r.left + r.right) / 2)
            cy = int((r.top + r.bottom) / 2)
            _winapi.SetCursorPos((cx, cy))
            time.sleep(0.05)
            _winapi.SetCursorPos((cx + 2, cy + 2))
            time.sleep(0.05)
        except Exception:
            pass

    def _close_add_friend_dialogs(self, add_win, apply_win=None):
        """统一关闭\"添加朋友\"相关弹窗"""
        from .retry import random_delay
        try:
            if self._any_window_exists(["申请添加朋友", "添加朋友请求", "添加到通讯录"]):
                uia.SendKeys("{Escape}")
                random_delay(0.2, 0.5)
        except Exception as e:
            logger.debug(f"关闭申请添加朋友弹窗失败: {e}")

        try:
            if self._win32_window_exists("添加朋友"):
                uia.SendKeys("{Escape}")
                random_delay(0.2, 0.5)
                if self._win32_window_exists("添加朋友"):
                    uia.SendKeys("{Escape}")
                    random_delay(0.2, 0.5)
        except Exception as e:
            logger.debug(f"关闭添加朋友弹窗失败: {e}")

    @staticmethod
    def draw_click_ripple(x: int, y: int, color: int = 0x0000FF):
        """在指定屏幕坐标 (x, y) 绘制一个动态扩展的多层同心圆波纹进行视觉指示（采用分层透明窗口规避 DWM 重绘擦除）"""
        import win32gui
        import win32con
        import threading
        import time

        def _draw_thread():
            try:
                # 🌟 强制将绘图线程的 DPI 上下文设为 Unaware (-1)，防止因继承父线程的 DPI Aware Context 而导致坐标二次换算飞屏
                import ctypes
                try:
                    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))
                except Exception:
                    pass

                # 1. 注册专属窗口类
                wc = win32gui.WNDCLASS()
                wc.lpfnWndProc = win32gui.DefWindowProc
                wc.lpszClassName = "WeChatRippleOverlay"
                wc.hInstance = win32gui.GetModuleHandle(None)
                try:
                    win32gui.RegisterClass(wc)
                except Exception:
                    pass

                # 2. 创建窗口。大小设为 80x80 保证波纹不超过约 70 像素范围且不被边缘裁切
                width, height = 80, 80
                left = x - width // 2
                top = y - height // 2
                
                # 样式：无边框 popup，分层窗口，鼠标完全穿透，置顶，不激活
                style = win32con.WS_POPUP
                ex_style = (win32con.WS_EX_LAYERED | 
                            win32con.WS_EX_TRANSPARENT | 
                            win32con.WS_EX_TOPMOST | 
                            win32con.WS_EX_NOACTIVATE)
                
                hwnd = win32gui.CreateWindowEx(
                    ex_style,
                    wc.lpszClassName,
                    "RippleOverlay",
                    style,
                    left, top, width, height,
                    0, 0, wc.hInstance, None
                )
                
                # 设置黑色 (0x000000) 为完全透明色
                win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, 1)
                
                # 显示窗口且不激活它
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                win32gui.UpdateWindow(hwnd)

                # 3. 动画绘制
                cx, cy = width // 2, height // 2
                hdc = win32gui.GetDC(hwnd)
                
                # 自定义画笔颜色 (BGR格式中，默认 0x0000FF 代表红色，厚度设为 6 以在高分屏上清晰可见)
                pen = win32gui.CreatePen(win32con.PS_SOLID, 6, color)
                old_pen = win32gui.SelectObject(hdc, pen)
                
                # 空刷子（不填充内部）
                brush = win32gui.GetStockObject(win32con.NULL_BRUSH)
                old_brush = win32gui.SelectObject(hdc, brush)
                
                for step in range(28):
                    # 使用黑色（即透明色）清空画布以消除上一帧波纹遗留
                    bg_brush = win32gui.CreateSolidBrush(0x000000)
                    win32gui.FillRect(hdc, (0, 0, width, height), bg_brush)
                    win32gui.DeleteObject(bg_brush)
                    
                    # 第一层波纹 (最大半径 34, 直径 68)
                    if step >= 0 and step < 16:
                        r1 = 4 + step * 2.0
                        win32gui.Ellipse(hdc, int(cx - r1), int(cy - r1), int(cx + r1), int(cy + r1))
                    
                    # 第二层波纹 (延迟 5 帧)
                    if step >= 5 and step < 21:
                        r2 = 4 + (step - 5) * 2.0
                        win32gui.Ellipse(hdc, int(cx - r2), int(cy - r2), int(cx + r2), int(cy + r2))
                    
                    # 第三层波纹 (延迟 10 帧)
                    if step >= 10 and step < 26:
                        r3 = 4 + (step - 10) * 2.0
                        win32gui.Ellipse(hdc, int(cx - r3), int(cy - r3), int(cx + r3), int(cy + r3))
                    
                    # 更新窗口像素并让系统泵送窗口消息，规避未响应和白屏
                    win32gui.UpdateWindow(hwnd)
                    win32gui.PumpWaitingMessages()
                    time.sleep(0.016)
                
                # 清理 GDI 资源并注销窗口
                win32gui.SelectObject(hdc, old_pen)
                win32gui.DeleteObject(pen)
                win32gui.SelectObject(hdc, old_brush)
                win32gui.ReleaseDC(hwnd, hdc)
                win32gui.DestroyWindow(hwnd)
            except Exception as draw_err:
                logger.error(f"[波纹绘制异常] {draw_err}")

        threading.Thread(target=_draw_thread, daemon=True).start()

